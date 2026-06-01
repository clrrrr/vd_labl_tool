"""Folder view: lists videos with their annotation status.

Scans the selected folder for ``*.mp4`` / ``*.mov``, runs ffprobe in a thread
pool to read each file's annotation, and displays results in a table. The UI
thread never blocks on ffprobe.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPoint,
    QRunnable,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from . import ui_strings as S
from .export import ExportRow, export_to_xlsx
from .metadata import (
    Annotation,
    MetadataError,
    read_video_info,
    write_annotation,
    write_annotation_and_rename,
)
from .project_info_dialog import ProjectInfoDialog

VIDEO_SUFFIXES = {".mp4", ".mov"}


def _is_listable_video(p: Path) -> bool:
    """Filter for ``iterdir()`` — accept only user-facing video files.

    Excludes anything starting with ``.`` (camera files never have leading
    dots; on macOS ``._filename`` resource forks also get filtered out) and
    in particular our own intermediate write/rename temp files
    (``.{name}.writing.{hex}<ext>`` and ``.__renaming__{hex}<ext>``) which
    would otherwise be picked up by the scanner if the previous run crashed
    mid-write.
    """
    if not p.is_file():
        return False
    if p.name.startswith("."):
        return False
    return p.suffix.lower() in VIDEO_SUFFIXES


def _fmt_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:,.1f} {u}" if u != "B" else f"{int(f)} B"
        f /= 1024
    return f"{n} B"


def _fmt_mtime(ts: float) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    total = int(round(seconds))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _extract_video_number(stem: str) -> str:
    """Pull the trailing ``NNNNN`` out of a renamed filename stem.

    Inputs like ``FAC123_00005`` → ``"00005"``. If the stem doesn't follow
    the rename pattern (rare; only possible if the rename pass left
    something behind), fall back to the whole stem so the row still has
    *something* identifying it.
    """
    parts = stem.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[1]
    return stem


@dataclass
class VideoRow:
    path: Path
    size: int
    mtime: float
    status: str  # one of S.STATUS_*
    annotation: Annotation | None = None  # populated when status == STATUS_DONE
    duration_seconds: float | None = None  # filled in by the scan task


# --- Rename -----------------------------------------------------------------

def rename_videos_to_factory_pattern(
    folder: Path,
    factory_id: str,
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Rename every video in ``folder`` to ``{factory_id}_NNNNN<ext>``.

    Ordering: original filenames sorted case-insensitively (matches the file
    list view's display order). Index N is zero-padded to 5 digits starting
    at 00000. The original extension is preserved verbatim (case included).

    Idempotent — if all files already match their expected names, this does
    nothing. When any rename is needed, all sources are first renamed to
    unique temp names (``.__renaming__<hex>.<ext>``) and then to their final
    targets. This two-phase shuffle avoids collisions when the new and old
    names overlap (e.g. running with a different factory_id over an already
    renamed folder).

    Returns ``(final_paths_in_order, errors)`` where ``errors`` is a list of
    ``(original_path, message)`` for any file that couldn't be renamed
    (typically because of a Windows file lock or permission issue). Failed
    files are left untouched; their original entry is omitted from
    ``final_paths_in_order``.
    """
    try:
        videos = sorted(
            [p for p in folder.iterdir() if _is_listable_video(p)],
            key=lambda p: p.name.lower(),
        )
    except OSError:
        return [], []

    if not videos:
        return [], []

    targets = [
        folder / f"{factory_id}_{i:05d}{p.suffix}"
        for i, p in enumerate(videos)
    ]
    if all(p == t for p, t in zip(videos, targets)):
        return list(videos), []  # already correctly named

    # Phase 1: move every source to a unique temp name.
    temps: list[tuple[Path, Path]] = []  # (temp_path, intended_target)
    errors: list[tuple[Path, str]] = []
    for src, tgt in zip(videos, targets):
        if src == tgt:
            # Already at the right name — skip both phases for this file.
            temps.append((src, tgt))
            continue
        temp = folder / f".__renaming__{uuid.uuid4().hex[:12]}{src.suffix}"
        try:
            os.replace(src, temp)
            temps.append((temp, tgt))
        except OSError as e:
            errors.append((src, str(e)))

    # Phase 2: rename temps to their final targets.
    final: list[Path] = []
    for temp, tgt in temps:
        if temp == tgt:
            final.append(tgt)
            continue
        try:
            os.replace(temp, tgt)
            final.append(tgt)
        except OSError as e:
            errors.append((temp, str(e)))
            # Leave the temp file in place rather than risk losing data —
            # the user can recover it from disk.

    return final, errors


# --- Worker -----------------------------------------------------------------

class _ScanSignals(QObject):
    """Signals owned by a QObject so they can be emitted across threads."""
    done = Signal(int, int, object, object, object)
    # (generation, row, annotation_or_none, duration_or_none, exception_or_none)


class _ScanTask(QRunnable):
    """Reads a single video's annotation and duration in a worker thread."""

    def __init__(self, generation: int, row: int, path: Path, signals: _ScanSignals):
        super().__init__()
        self.setAutoDelete(True)
        self._gen = generation
        self._row = row
        self._path = path
        self._signals = signals

    def run(self) -> None:
        annotation: Annotation | None = None
        duration: float | None = None
        err: Exception | None = None
        try:
            annotation, duration = read_video_info(self._path)
        except MetadataError as e:
            err = e
        except Exception as e:  # noqa: BLE001 — worker thread must not propagate
            err = e
        self._signals.done.emit(self._gen, self._row, annotation, duration, err)


class _PasteSaveWorker(QObject):
    """Writes an annotation in a worker thread (used by right-click paste).

    The ``finished`` signal carries ``(video_path, final_path, exception_or_none)``.
    ``video_path`` is the path the user pasted onto; ``final_path`` is the
    same path or its renamed version after the process-name suffix is
    appended. Each worker owns its own signal so signal/slot connections
    are scoped to a single paste-save lifecycle and auto-disconnect cleanly
    when the worker is destroyed.
    """

    finished = Signal(object, object, object)  # (video_path, final_path, exception_or_none)

    def __init__(self, video_path: Path, annotation: Annotation, factory_id: str):
        super().__init__()
        self._video_path = video_path
        self._annotation = annotation
        self._factory_id = factory_id

    def run(self) -> None:
        err: Exception | None = None
        final_path = self._video_path
        try:
            final_path = write_annotation_and_rename(
                self._video_path, self._annotation, self._factory_id,
            )
        except Exception as e:  # noqa: BLE001 — surfaced to the UI
            err = e
        self.finished.emit(self._video_path, final_path, err)


# --- Model ------------------------------------------------------------------

COL_FILENAME = 0
COL_STATUS = 1
COL_PROCESS = 2
COL_DURATION = 3
COL_SIZE = 4
COL_MTIME = 5

_HEADERS = [
    S.COL_FILENAME,
    S.COL_STATUS,
    S.COL_PROCESS_NAME,
    S.COL_DURATION,
    S.COL_SIZE,
    S.COL_MTIME,
]


class VideoTableModel(QAbstractTableModel):
    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._rows: list[VideoRow] = []

    # Qt model API
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return _HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == COL_FILENAME:
                return row.path.name
            if col == COL_STATUS:
                return row.status
            if col == COL_PROCESS:
                return row.annotation.process_name if row.annotation else ""
            if col == COL_DURATION:
                return _fmt_duration(row.duration_seconds)
            if col == COL_SIZE:
                return _fmt_size(row.size)
            if col == COL_MTIME:
                return _fmt_mtime(row.mtime)
        elif role == Qt.TextAlignmentRole and col == COL_DURATION:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == Qt.ForegroundRole and col == COL_STATUS:
            if row.status == S.STATUS_DONE:
                return QBrush(QColor("#1b7f3b"))  # green
            if row.status == S.STATUS_TODO:
                return QBrush(QColor("#b54708"))  # orange
            if row.status == S.STATUS_ERROR:
                return QBrush(QColor("#b00020"))  # red
        elif role == Qt.FontRole and col == COL_STATUS:
            f = QFont()
            f.setBold(True)
            return f
        elif role == Qt.ToolTipRole and col == COL_FILENAME:
            return str(row.path)
        return None

    # Convenience accessors
    def row_for_index(self, index: QModelIndex) -> VideoRow | None:
        if not index.isValid():
            return None
        return self._rows[index.row()]

    def find_row_by_path(self, path: Path) -> int:
        for i, r in enumerate(self._rows):
            if r.path == path:
                return i
        return -1

    def replace_rows(self, rows: list[VideoRow]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def update_row_status(self, row: int, status: str, annotation: Annotation | None) -> None:
        """For transient states (SCANNING/SAVING) — keeps existing duration."""
        if 0 <= row < len(self._rows):
            self._rows[row].status = status
            self._rows[row].annotation = annotation
            top = self.index(row, COL_STATUS)
            bot = self.index(row, COL_PROCESS)
            self.dataChanged.emit(top, bot, [Qt.DisplayRole, Qt.ForegroundRole, Qt.FontRole])

    def update_row_after_scan(
        self,
        row: int,
        status: str,
        annotation: Annotation | None,
        duration: float | None,
    ) -> None:
        """For scan-completion — replaces annotation and duration together."""
        if 0 <= row < len(self._rows):
            r = self._rows[row]
            r.status = status
            r.annotation = annotation
            r.duration_seconds = duration
            top = self.index(row, COL_STATUS)
            bot = self.index(row, COL_DURATION)
            self.dataChanged.emit(top, bot, [Qt.DisplayRole, Qt.ForegroundRole, Qt.FontRole])

    def status_counts(self) -> tuple[int, int, int]:
        total = len(self._rows)
        done = sum(1 for r in self._rows if r.status == S.STATUS_DONE)
        return total, done, total - done


# --- Widget -----------------------------------------------------------------

class FileListView(QWidget):
    """Top-level widget: folder picker + video table."""

    video_double_clicked = Signal(Path)
    # Emitted when user pastes a parts list onto an un-annotated video; carries
    # (path, parts_list). The main window opens the annotate dialog pre-filled
    # so the user can supply the Process Name before saving.
    paste_to_unannotated = Signal(Path, list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._folder: Path | None = None
        self._generation = 0  # invalidates stale worker results
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(8)

        # Project info — set via the "填写项目信息" button. Must be filled
        # before the user can pick a folder, since the folder selection step
        # renames files using the factory_id.
        self._factory_id: str | None = None
        self._factory_name: str | None = None

        self._scan_signals = _ScanSignals(self)
        self._scan_signals.done.connect(self._on_scan_done)

        # In-memory clipboard for the right-click copy/paste-parts feature.
        # Just app-internal — does not touch the OS clipboard.
        self._parts_clipboard: list[str] | None = None

        # Track active paste-save workers so they aren't garbage collected
        # mid-flight. Keyed by file path.
        self._active_saves: dict[Path, tuple[QThread, _PasteSaveWorker]] = {}

        self.model = VideoTableModel(self)

        self._build_ui()
        self._refresh_counts_label()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_project_info = QPushButton(S.BTN_PROJECT_INFO_PROMPT)
        self.btn_project_info.clicked.connect(self._on_project_info)
        self.btn_open = QPushButton(S.BTN_OPEN_FOLDER)
        self.btn_open.clicked.connect(self._on_open_folder)
        self.btn_refresh = QPushButton(S.BTN_REFRESH)
        self.btn_refresh.clicked.connect(self._on_refresh)
        self.btn_refresh.setEnabled(False)
        self.btn_export = QPushButton(S.BTN_EXPORT_XLSX)
        self.btn_export.clicked.connect(self._on_export_xlsx)
        self.btn_export.setEnabled(False)
        self.lbl_folder = QLabel(S.LABEL_NO_FOLDER)
        self.lbl_folder.setStyleSheet("color: #555;")
        self.lbl_folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.btn_project_info)
        top.addWidget(self.btn_open)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_export)
        top.addSpacing(12)
        top.addWidget(self.lbl_folder, 1)
        root.addLayout(top)

        self.lbl_counts = QLabel("")
        self.lbl_counts.setStyleSheet("color: #555; padding: 2px 0;")
        root.addWidget(self.lbl_counts)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_FILENAME, QHeaderView.Stretch)
        hh.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_PROCESS, QHeaderView.Stretch)
        hh.setSectionResizeMode(COL_DURATION, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_MTIME, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self._on_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self.table, 1)

    # --- Public API ---------------------------------------------------------

    def current_folder(self) -> Path | None:
        return self._folder

    def rescan_path(self, path: Path) -> None:
        """Re-read a single video's annotation (called after save)."""
        row = self.model.find_row_by_path(path)
        if row < 0:
            return
        self.model.update_row_status(row, S.STATUS_SCANNING, None)
        self._refresh_counts_label()
        self._pool.start(_ScanTask(self._generation, row, path, self._scan_signals))

    def refresh_after_external_rename(self) -> None:
        """Re-list the current folder when a file's path has changed.

        Used by save flows that rename the underlying file (process-name
        suffix). Does NOT re-run the factory-id rename pass — just scans.
        """
        if self._folder is not None:
            self._rescan_folder()

    # --- Slots --------------------------------------------------------------

    def _prompt_project_info(self) -> bool:
        """Show the project info dialog. Returns True if the user accepted.

        Side effects on accept: updates ``_factory_id`` / ``_factory_name``,
        refreshes the button label, and if the factory id actually changed
        while a folder is already loaded, triggers a re-rename + rescan.
        """
        dlg = ProjectInfoDialog(
            self,
            initial_factory_id=self._factory_id or "",
            initial_factory_name=self._factory_name or "",
        )
        if dlg.exec() != QDialog.Accepted:
            return False
        new_id, new_name = dlg.values()
        id_changed = new_id != self._factory_id
        self._factory_id = new_id
        self._factory_name = new_name
        self.btn_project_info.setText(
            S.BTN_PROJECT_INFO_TEMPLATE.format(
                factory_id=new_id, factory_name=new_name,
            )
        )
        if id_changed and self._folder is not None:
            self._prepare_and_scan_folder()
        return True

    def _on_project_info(self) -> None:
        self._prompt_project_info()

    def _on_open_folder(self) -> None:
        # Both buttons are clickable in any order — if the user hasn't filled
        # project info yet, prompt for it now and chain into folder selection.
        if self._factory_id is None:
            if not self._prompt_project_info():
                return
        start_dir = str(self._folder) if self._folder else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, S.BTN_OPEN_FOLDER, start_dir)
        if not chosen:
            return
        self._folder = Path(chosen)
        self.lbl_folder.setText(S.LABEL_FOLDER_PREFIX + str(self._folder))
        self.btn_refresh.setEnabled(True)
        self.btn_export.setEnabled(True)
        self._prepare_and_scan_folder()

    def _on_refresh(self) -> None:
        if self._folder is not None and self._factory_id is not None:
            self._prepare_and_scan_folder()

    def _on_export_xlsx(self) -> None:
        if self._folder is None or self.model.rowCount() == 0:
            QMessageBox.information(
                self, S.DLG_EXPORT_NOTHING_TITLE, S.DLG_EXPORT_NOTHING_MSG,
            )
            return

        # Build a default filename. ``QFileDialog.getSaveFileName`` handles
        # special characters fine on each platform — no need to scrub.
        if self._factory_id and self._factory_name:
            default_stem = f"{self._factory_id}_{self._factory_name}"
        else:
            default_stem = self._folder.name or "videos"
        default_path = str(self._folder / f"{default_stem}.xlsx")

        path_str, _ = QFileDialog.getSaveFileName(
            self,
            S.BTN_EXPORT_XLSX,
            default_path,
            "Excel Workbook (*.xlsx)",
        )
        if not path_str:
            return
        out_path = Path(path_str)
        if out_path.suffix.lower() != ".xlsx":
            out_path = out_path.with_suffix(".xlsx")

        rows = [
            ExportRow(
                video_number=_extract_video_number(r.path.stem),
                process_name=(r.annotation.process_name if r.annotation else ""),
                duration_text=_fmt_duration(r.duration_seconds),
            )
            for r in self.model._rows
        ]

        try:
            export_to_xlsx(rows, out_path)
        except Exception as e:  # noqa: BLE001 — surface any failure to the user
            QMessageBox.critical(self, S.DLG_EXPORT_FAIL_TITLE, str(e))
            return

        QMessageBox.information(
            self,
            S.DLG_EXPORT_OK_TITLE,
            S.DLG_EXPORT_OK_TEMPLATE.format(count=len(rows), path=str(out_path)),
        )

    def _prepare_and_scan_folder(self) -> None:
        """Rename videos to the factory pattern, then trigger an async scan."""
        assert self._folder is not None
        assert self._factory_id is not None
        _, errors = rename_videos_to_factory_pattern(self._folder, self._factory_id)
        if errors:
            lines = "\n".join(f"  · {p.name}: {msg}" for p, msg in errors)
            QMessageBox.warning(
                self,
                S.RENAME_WARNING_TITLE,
                S.RENAME_WARNING_TEMPLATE.format(lines=lines),
            )
        self._rescan_folder()

    def _rescan_folder(self) -> None:
        assert self._folder is not None
        # Bump generation to invalidate any in-flight worker results.
        self._generation += 1
        gen = self._generation

        # Discover video files (non-recursive, sorted by name for predictability).
        try:
            entries = sorted(
                [p for p in self._folder.iterdir() if _is_listable_video(p)],
                key=lambda p: p.name.lower(),
            )
        except OSError:
            entries = []

        rows: list[VideoRow] = []
        for p in entries:
            try:
                st = p.stat()
                rows.append(VideoRow(
                    path=p,
                    size=st.st_size,
                    mtime=st.st_mtime,
                    status=S.STATUS_SCANNING,
                    annotation=None,
                ))
            except OSError:
                continue

        self.model.replace_rows(rows)
        self._refresh_counts_label()

        for i, r in enumerate(rows):
            self._pool.start(_ScanTask(gen, i, r.path, self._scan_signals))

    def _on_scan_done(
        self,
        gen: int,
        row: int,
        annotation: Annotation | None,
        duration: float | None,
        err: Exception | None,
    ) -> None:
        if gen != self._generation:
            return  # stale result, ignore
        if err is not None:
            self.model.update_row_after_scan(row, S.STATUS_ERROR, None, duration)
        elif annotation is None:
            self.model.update_row_after_scan(row, S.STATUS_TODO, None, duration)
        else:
            self.model.update_row_after_scan(row, S.STATUS_DONE, annotation, duration)
        self._refresh_counts_label()

    def _on_double_clicked(self, index: QModelIndex) -> None:
        row = self.model.row_for_index(index)
        if row is None:
            return
        if row.status == S.STATUS_SCANNING:
            return  # avoid opening while we're still figuring out current state
        self.video_double_clicked.emit(row.path)

    def _refresh_counts_label(self) -> None:
        total, done, todo = self.model.status_counts()
        if total == 0:
            self.lbl_counts.setText("")
        else:
            self.lbl_counts.setText(
                S.LABEL_COUNT_TEMPLATE.format(total=total, done=done, todo=todo)
            )

    # --- Context menu: copy / paste parts list ------------------------------

    def _on_context_menu(self, pos: QPoint) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row = self.model.row_for_index(index)
        if row is None:
            return

        menu = QMenu(self.table)

        can_copy = row.status == S.STATUS_DONE and row.annotation is not None
        act_copy = QAction(S.MENU_COPY_PARTS, menu)
        act_copy.setEnabled(can_copy)
        act_copy.triggered.connect(lambda: self._copy_parts(row))
        menu.addAction(act_copy)

        # Paste available when we have a buffer AND the target is not
        # currently being scanned or saved.
        clip = self._parts_clipboard
        busy = row.status in (S.STATUS_SCANNING, S.STATUS_SAVING)
        can_paste = clip is not None and not busy
        label = (
            S.MENU_PASTE_PARTS_TEMPLATE.format(n=len(clip)) if clip is not None
            else S.MENU_PASTE_PARTS
        )
        act_paste = QAction(label, menu)
        act_paste.setEnabled(can_paste)
        act_paste.triggered.connect(lambda: self._paste_parts(row))
        menu.addAction(act_paste)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_parts(self, row: VideoRow) -> None:
        if row.annotation is None:
            return
        self._parts_clipboard = list(row.annotation.parts_involved)
        # Brief feedback through the counts label so it doesn't block the user.
        self._flash_status(S.TOAST_PARTS_COPIED.format(n=len(self._parts_clipboard)))

    def _paste_parts(self, row: VideoRow) -> None:
        if self._parts_clipboard is None:
            return
        parts = list(self._parts_clipboard)

        if row.status != S.STATUS_DONE or row.annotation is None:
            # Un-annotated target: punt to the main window so it can open the
            # annotate dialog with the parts pre-filled, letting the user enter
            # a Process Name before committing.
            self.paste_to_unannotated.emit(row.path, parts)
            return

        # Annotated target: keep its Process Name, replace parts, save in the
        # background. We don't need a file-lock dance here because nothing in
        # the file list view holds an open handle on the file.
        if row.path in self._active_saves:
            return  # already saving this file
        new_anno = Annotation(
            process_name=row.annotation.process_name,
            parts_involved=parts,
        )
        row_idx = self.model.find_row_by_path(row.path)
        if row_idx >= 0:
            self.model.update_row_status(row_idx, S.STATUS_SAVING, row.annotation)
            self._refresh_counts_label()

        thread = QThread(self)
        worker = _PasteSaveWorker(row.path, new_anno, self._factory_id)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_paste_save_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._active_saves[row.path] = (thread, worker)
        thread.start()

    def _on_paste_save_finished(
        self, video_path: Path, final_path: Path, err: object,
    ) -> None:
        self._active_saves.pop(video_path, None)
        if err is not None:
            QMessageBox.critical(self, S.DLG_SAVE_FAIL_TITLE, str(err))
        # If the file was also renamed (process-name suffix), the row's path
        # in the model is now stale — refresh the whole folder.
        if final_path != video_path:
            self.refresh_after_external_rename()
        else:
            self.rescan_path(video_path)
        if err is None:
            self._flash_status(S.TOAST_PARTS_PASTED.format(filename=final_path.name))

    def _flash_status(self, message: str, ms: int = 2500) -> None:
        """Temporarily overlay a message on the counts label."""
        self.lbl_counts.setText(message)
        QTimer.singleShot(ms, self._refresh_counts_label)
