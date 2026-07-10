# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QDialogButtonBox, QLabel
from ..settings import SettingsStore

class ApiSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('API Key 설정')
        self.store = SettingsStore()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('API Key는 QGIS 사용자 설정에 저장되며 플러그인 ZIP에는 저장되지 않습니다.'))
        form = QFormLayout()
        self.vworld = QLineEdit(self.store.vworld_key)
        self.eia = QLineEdit(self.store.eia_key)
        self.ecology = QLineEdit(self.store.ecology_key)
        form.addRow('VWorld API Key', self.vworld)
        form.addRow('환경영향평가 API serviceKey', self.eia)
        form.addRow('생태자연도 API serviceKey', self.ecology)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        self.store.set('vworld_key', self.vworld.text().strip())
        self.store.set('eia_key', self.eia.text().strip())
        self.store.set('ecology_key', self.ecology.text().strip())
        self.accept()
