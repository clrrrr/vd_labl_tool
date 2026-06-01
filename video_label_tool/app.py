"""Application entry point — main window wiring file list to annotate window."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
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
        dlg = AnnotateWindow(
            path,
            self,
            prefilled_parts=prefilled_parts,
            factory_id=self.file_list._factory_id,
        )
        dlg.setWindowModality(Qt.ApplicationModal)
        dlg.exec()
        # The save flow may have renamed the file (appended _<process_name>),
        # so re-list the whole folder rather than the original row.
        if dlg.final_path() != path:
            self.file_list.refresh_after_external_rename()
        else:
            self.file_list.rescan_path(path)


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
