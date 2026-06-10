"""Project info dialog — collects factory id and factory name.

Factory id is constrained to ASCII alphanumerics plus ``-`` and ``_`` because
it gets embedded into renamed filenames (``{factory_id}NNNNN.mp4``); allowing
arbitrary characters would invite filesystem quirks across Windows/macOS.
Factory name is free-form (just non-empty) since it's only used for display.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from . import ui_strings as S


_FACTORY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,30}$")


def is_valid_factory_id(value: str) -> bool:
    return bool(_FACTORY_ID_RE.match(value))


class ProjectInfoDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        initial_factory_id: str = "",
        initial_factory_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(S.DLG_PROJECT_INFO_TITLE)
        self.setModal(True)
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.edt_factory_id = QLineEdit(initial_factory_id)
        self.edt_factory_id.setMaxLength(30)
        self.edt_factory_id.setPlaceholderText("e.g. FAC123")
        form.addRow(S.DLG_PROJECT_INFO_FACTORY_ID, self.edt_factory_id)

        help_id = QLabel(S.DLG_PROJECT_INFO_HELP_ID)
        help_id.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow("", help_id)

        self.edt_factory_name = QLineEdit(initial_factory_name)
        self.edt_factory_name.setMaxLength(100)
        form.addRow(S.DLG_PROJECT_INFO_FACTORY_NAME, self.edt_factory_name)

        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal,
            self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.edt_factory_id.setFocus()

    def _on_accept(self) -> None:
        factory_id = self.edt_factory_id.text().strip()
        factory_name = self.edt_factory_name.text().strip()
        if not is_valid_factory_id(factory_id):
            QMessageBox.warning(self, S.DLG_PROJECT_INFO_TITLE, S.DLG_PROJECT_INFO_VALIDATE_ID)
            self.edt_factory_id.setFocus()
            return
        if not factory_name:
            QMessageBox.warning(self, S.DLG_PROJECT_INFO_TITLE, S.DLG_PROJECT_INFO_VALIDATE_NAME)
            self.edt_factory_name.setFocus()
            return
        self._factory_id = factory_id
        self._factory_name = factory_name
        self.accept()

    def values(self) -> tuple[str, str]:
        """Returns (factory_id, factory_name). Valid only after accept()."""
        return self._factory_id, self._factory_name
