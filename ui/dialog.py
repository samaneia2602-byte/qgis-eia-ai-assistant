# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLineEdit, QLabel, QToolButton, QMenu
from qgis.PyQt.QtCore import Qt
from .help_dialog import show_help
from .api_settings import ApiSettingsDialog
from .geoinfo_dialog import GeoInfoDialog
from ..api.api_manager import ApiManager
from ..commands.executor import CommandExecutor

class AssistantDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle('QGIS 대화형 명령 실행')
        # 상시 맨 위 고정 해제 + 최소화 버튼 허용
        self.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)
        self.resize(720, 430)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel('명령을 입력하면 QGIS 작업을 실행합니다.'))
        top.addStretch(1)
        self.geo_btn = QToolButton()
        self.geo_btn.setText('지리정보 불러오기')
        self.geo_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.geo_btn)
        menu.addAction('지리정보 선택창 열기', self.open_geoinfo)
        self.geo_btn.setMenu(menu)
        self.help_btn = QPushButton('? 도움말')
        self.help_btn.clicked.connect(lambda: show_help(self))
        top.addWidget(self.geo_btn); top.addWidget(self.help_btn)
        layout.addLayout(top)

        self.output = QTextEdit(); self.output.setReadOnly(True)
        layout.addWidget(self.output)
        bottom = QHBoxLayout()
        self.input = QLineEdit(); self.input.setPlaceholderText('명령을 입력하세요')
        self.run_btn = QPushButton('실행')
        self.api_btn = QPushButton('API Key 설정')
        self.min_btn = QPushButton('최소화')
        self.close_btn = QPushButton('닫기')
        bottom.addWidget(self.input, 1)
        for b in [self.run_btn, self.api_btn, self.min_btn, self.close_btn]: bottom.addWidget(b)
        layout.addLayout(bottom)
        self.executor = CommandExecutor(iface, self.log)
        self.run_btn.clicked.connect(self.run_command)
        self.input.returnPressed.connect(self.run_command)
        self.api_btn.clicked.connect(lambda: ApiSettingsDialog(self).exec_())
        self.min_btn.clicked.connect(self.showMinimized)
        self.close_btn.clicked.connect(self.close)

    def log(self, msg):
        self.output.append(str(msg))

    def run_command(self):
        text = self.input.text().strip()
        if not text: return
        self.log('사용자: ' + text)
        self.input.clear()
        self.executor.execute(text)

    def open_geoinfo(self):
        dlg = GeoInfoDialog(self, ApiManager(self.iface, self.log))
        dlg.exec_()
