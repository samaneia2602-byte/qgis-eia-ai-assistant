# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import QTimer
from .ui.dialog import AssistantDialog
from .ui.api_settings import ApiSettingsDialog
import os

class EiaAiAssistantPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dialog = None
        self.actions = []

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), 'icon.png'))
        act = QAction(icon, 'QGIS EIA AI Assistant', self.iface.mainWindow())
        act.triggered.connect(self.show_dialog)
        self.iface.addToolBarIcon(act)
        self.iface.addPluginToMenu('QGIS EIA AI Assistant', act)
        self.actions.append(act)

        api_act = QAction('API Key 설정', self.iface.mainWindow())
        api_act.triggered.connect(self.show_api_settings)
        self.iface.addPluginToMenu('QGIS EIA AI Assistant', api_act)
        self.actions.append(api_act)

        # QGIS 시작 후 자동 표시. 활성화된 플러그인일 때만 작동.
        QTimer.singleShot(900, self.show_dialog)

    def unload(self):
        for act in self.actions:
            self.iface.removePluginMenu('QGIS EIA AI Assistant', act)
            self.iface.removeToolBarIcon(act)
        self.actions = []
        if self.dialog:
            self.dialog.close()
            self.dialog = None

    def show_dialog(self):
        if not self.dialog:
            self.dialog = AssistantDialog(self.iface)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def show_api_settings(self):
        dlg = ApiSettingsDialog(self.iface.mainWindow())
        dlg.exec_()
