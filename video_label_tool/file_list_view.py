"""Folder view: lists videos with their annotation status.

Scans the selected folder for ``*.mp4`` / ``*.mov``, runs ffprobe in a thread
pool to read each file's annotation, and displays results in a table. The UI
thread never blocks on ffprobe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from . import ui_strings as S
from .metadata import Annotation, MetadataError, read_annotation

VIDEO_SUFFIXES = {".mp4", ".mov"}


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


@dataclass
class VideoRow:
    path: Path
    size: int
    mtime: float
    status: str  # one of S.STATUS_*
    annotation: Annotation | None = None  # populated when status == STATUS_DONE


# --- Worker -----------------------------------------------------------------

class _ScanSignals(QObject):
    """Signals owned by a QObject so they can be emitted across threads."""
    done = Signal(int, int, object, object)
    # (generation, row, annotation_or_none, exception_or_none)


class _ScanTask(QRunnable):
    """Reads a single video's annotation in a worker thread."""

    def __init__(self, generation: int, row: int, path: Path, signals: _ScanSignals):
        super().__init__()
        self.setAutoDelete(True)
        self._gen = generation
        self._row = row
        self._path = path
        self._signals = signals

    def run(self) -> None:
        annotation: Annotation | None = None
        err: Exception | None = None
        try:
            annotation = read_annotation(self._path)
        except MetadataError as e:
            err = e
        except Exception as e:  # noqa: BLE001 — worker thread must not propagate
            err = e
        self._signals.done.emit(self._gen, self._row, annotation, err)


# --- Model ------------------------------------------------------------------

COL_FILENAME = 0
COL_STATUS = 1
COL_PROCESS = 2
COL_SIZE = 3
COL_MTIME = 4

_HEADERS = [S.COL_FILENAME, S.COL_STATUS, S.COL_PROCESS_NAME, S.COL_SIZE, S.COL_MTIME]


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
            if col == COL_SIZE:
                return _fmt_size(row.size)
            if col == COL_MTIME:
                return _fmt_mtime(row.mtime)
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
        if 0 <= row < len(self._rows):
            self._rows[row].status = status
            self._rows[row].annotation = annotation
            top = self.index(row, COL_STATUS)
            bot = self.index(row, COL_PROCESS)
            self.dataChanged.emit(top, bot, [Qt.DisplayRole, Qt.ForegroundRole, Qt.FontRole])

    def status_counts(self) -> tuple[int, int, int]:
        total = len(self._rows)
        done = sum(1 for r in self._rows if r.status == S.STATUS_DONE)
        return total, done, total - done


# --- Widget -----------------------------------------------------------------

class FileListView(QWidget):
    """Top-level widget: folder picker + video table."""

    video_double_clicked = Signal(Path)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._folder: Path | None = None
        self._generation = 0  # invalidates stale worker results
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(8)

        self._scan_signals = _ScanSignals(self)
        self._scan_signals.done.connect(self._on_scan_done)

        self.model = VideoTableModel(self)

        self._build_ui()
        self._refresh_counts_label()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_open = QPushButton(S.BTN_OPEN_FOLDER)
        self.btn_open.clicked.connect(self._on_open_folder)
        self.btn_refresh = QPushButton(S.BTN_REFRESH)
        self.btn_refresh.clicked.connect(self._on_refresh)
        self.btn_refresh.setEnabled(False)
        self.lbl_folder = QLabel(S.LABEL_NO_FOLDER)
        self.lbl_folder.setStyleSheet("color: #555;")
        self.lbl_folder.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.btn_open)
        top.addWidget(self.btn_refresh)
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
        hh.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(COL_MTIME, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self._on_double_clicked)
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

    # --- Slots --------------------------------------------------------------

    def _on_open_folder(self) -> None:
        start_dir = str(self._folder) if self._folder else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, S.BTN_OPEN_FOLDER, start_dir)
        if not chosen:
            return
        self._folder = Path(chosen)
        self.lbl_folder.setText(S.LABEL_FOLDER_PREFIX + str(self._folder))
        self.btn_refresh.setEnabled(True)
        self._rescan_folder()

    def _on_refresh(self) -> None:
        if self._folder is not None:
            self._rescan_folder()

    def _rescan_folder(self) -> None:
        assert self._folder is not None
        # Bump generation to invalidate any in-flight worker results.
        self._generation += 1
        gen = self._generation

        # Discover video files (non-recursive, sorted by name for predictability).
        try:
            entries = sorted(
                [p for p in self._folder.iterdir()
                 if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES],
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
        self, gen: int, row: int, annotation: Annotation | None, err: Exception | None
    ) -> None:
        if gen != self._generation:
            return  # stale result, ignore
        if err is not None:
            self.model.update_row_status(row, S.STATUS_ERROR, None)
        elif annotation is None:
            self.model.update_row_status(row, S.STATUS_TODO, None)
        else:
            self.model.update_row_status(row, S.STATUS_DONE, annotation)
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
