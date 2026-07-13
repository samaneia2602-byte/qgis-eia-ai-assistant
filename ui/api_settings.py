# -*- coding: utf-8 -*-

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ..settings import SettingsStore


class ApiSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("API Key 설정")
        self.resize(620, 300)

        self.store = SettingsStore()

        layout = QVBoxLayout(self)
        guide = QLabel(
            "API Key는 QGIS 사용자 설정에 저장되며 "
            "플러그인 ZIP이나 GitHub에는 저장되지 않습니다."
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)

        form = QFormLayout()

        self.vworld = self._key_edit(
            self.store.vworld_key
        )
        self.eia = self._key_edit(
            self.store.eia_key
        )
        self.ecology = self._key_edit(
            self.store.ecology_key
        )
        self.ngii = self._key_edit(
            self.store.ngii_key
        )

        self.ngii.setPlaceholderText(
            "국토정보플랫폼에서 발급된 OpenAPI 인증키"
        )

        form.addRow(
            "VWorld API Key",
            self.vworld,
        )
        form.addRow(
            "환경영향평가 API serviceKey",
            self.eia,
        )
        form.addRow(
            "생태자연도 API serviceKey",
            self.ecology,
        )
        form.addRow(
            "국토지리정보원 API Key",
            self.ngii,
        )

        layout.addLayout(form)

        self.show_keys = QCheckBox(
            "입력한 API Key 표시"
        )
        self.show_keys.toggled.connect(
            self._toggle_key_visibility
        )
        layout.addWidget(self.show_keys)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save
            | QDialogButtonBox.Cancel
        )
        buttons.button(
            QDialogButtonBox.Save
        ).setText("저장")
        buttons.button(
            QDialogButtonBox.Cancel
        ).setText("취소")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _key_edit(self, value):
        edit = QLineEdit(value or "")
        edit.setEchoMode(
            QLineEdit.Password
        )
        edit.setClearButtonEnabled(True)
        return edit

    def _toggle_key_visibility(self, checked):
        mode = (
            QLineEdit.Normal
            if checked
            else QLineEdit.Password
        )

        for edit in (
            self.vworld,
            self.eia,
            self.ecology,
            self.ngii,
        ):
            edit.setEchoMode(mode)

    def save(self):
        self.store.set(
            "vworld_key",
            self.vworld.text().strip(),
        )
        self.store.set(
            "eia_key",
            self.eia.text().strip(),
        )
        self.store.set(
            "ecology_key",
            self.ecology.text().strip(),
        )
        self.store.set(
            "ngii_key",
            self.ngii.text().strip(),
        )
        self.accept()
