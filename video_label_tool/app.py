"""Application entry point — main window wiring file list to annotate window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

from . import ffbin, ui_strings as S
from .annotate_window import AnnotateWindow
from .file_list_view import FileListView


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(S.APP_TITLE)
        self.resize(1100, 650)

        self.file_list = FileListView(self)
        self.file_list.video_double_clicked.connect(self._open_annotate_window)
        self.file_list.paste_to_unannotated.connect(self._open_annotate_window_with_prefill)
        self.setCentralWidget(self.file_list)

    def _open_annotate_window(self, path: Path) -> None:
        self._show_annotate(path, prefilled_parts=None)

    def _open_annotate_window_with_prefill(self, path: Path, parts: list) -> None:
        self._show_annotate(path, prefilled_parts=list(parts))

    def _show_annotate(self, path: Path, *, prefilled_parts: list | None) -> None:
        dlg = AnnotateWindow(path, self, prefilled_parts=prefilled_parts)
        # Save is fire-and-forget — AnnotateWindow emits, FileListView owns
        # the background worker and refreshes its row when the file lands.
        dlg.save_requested.connect(self.file_list.request_save)
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.exec()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 — Qt API
        # Refuse to close while background saves are still running. Letting
        # the app exit mid-write could orphan a temp file or corrupt the
        # rename, especially on external drives where the disk may also be
        # ejected by the user as a follow-up.
        active = sum(1 for _ in self.file_list._active_saves)
        if active > 0:
            QMessageBox.warning(
                self,
                S.DLG_CLOSE_DURING_SAVE_TITLE,
                S.DLG_CLOSE_DURING_SAVE_MSG.format(n=active),
            )
            event.ignore()
            return
        super().closeEvent(event)


def _check_dependencies(parent: QMainWindow | None = None) -> bool:
    if ffbin.ensure_available() is None:
        QMessageBox.critical(
            parent,
            S.DLG_FFMPEG_MISSING_TITLE,
            S.DLG_FFMPEG_MISSING_MSG,
        )
        return False
    return True


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(S.APP_TITLE)
    win = MainWindow()
    win.show()
    if not _check_dependencies(win):
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
