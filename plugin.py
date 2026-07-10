# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer

from .analysis.crs_advisor import CRSAdvisor
from .ui.api_settings import ApiSettingsDialog
from .ui.crs_address_dialog import CrsAddressDialog
from .ui.dialog import AssistantDialog


class EiaAiAssistantPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dialog = None
        self.actions = []
        self.crs_advisor = CRSAdvisor(iface)
        self.layers_signal_connected = False

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.png"))
        act = QAction(icon, "QGIS EIA AI Assistant", self.iface.mainWindow())
        act.triggered.connect(self.show_dialog)
        self.iface.addToolBarIcon(act)
        self.iface.addPluginToMenu("QGIS EIA AI Assistant", act)
        self.actions.append(act)

        api_act = QAction("API Key 설정", self.iface.mainWindow())
        api_act.triggered.connect(self.show_api_settings)
        self.iface.addPluginToMenu("QGIS EIA AI Assistant", api_act)
        self.actions.append(api_act)

        QgsProject.instance().layersAdded.connect(self.on_layers_added)
        self.layers_signal_connected = True
        QTimer.singleShot(900, self.show_dialog)

    def unload(self):
        if self.layers_signal_connected:
            try:
                QgsProject.instance().layersAdded.disconnect(self.on_layers_added)
            except (TypeError, RuntimeError):
                pass
            self.layers_signal_connected = False

        for act in self.actions:
            self.iface.removePluginMenu("QGIS EIA AI Assistant", act)
            self.iface.removeToolBarIcon(act)
        self.actions = []

        if self.dialog:
            self.dialog.close()
            self.dialog = None

    def on_layers_added(self, layers):
        for layer in layers:
            if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
                continue

            source_path = layer.source().split("|")[0]
            if source_path.lower().endswith(".shp"):
                base_path = os.path.splitext(source_path)[0]
                if os.path.exists(base_path + ".prj") or os.path.exists(base_path + ".qpj"):
                    continue
            elif layer.crs().isValid():
                continue

            QTimer.singleShot(
                0,
                lambda current_layer=layer: self.advise_crs(current_layer),
            )

    def advise_crs(self, layer):
        if not layer or not layer.isValid():
            return

        result = self.crs_advisor.recommend(layer)
        if not result:
            dialog = CrsAddressDialog(
                self.iface.mainWindow(),
                layer,
                self.crs_advisor,
            )
            if not dialog.exec_() or not dialog.result:
                return
            result = dialog.result

        self.apply_crs_result(layer, result)

    def apply_crs_result(self, layer, result):
        epsg = int(result["epsg"])
        crs = QgsCoordinateReferenceSystem.fromEpsgId(epsg)
        if not crs.isValid():
            QMessageBox.warning(
                self.iface.mainWindow(),
                "좌표계 추천 오류",
                "추천된 EPSG 좌표계를 QGIS에서 불러오지 못했습니다.",
            )
            return

        message = (
            "좌표계가 지정되지 않은 레이어를 발견했습니다.\n\n"
            f"레이어: {layer.name()}\n"
            f"추천 좌표계: EPSG:{epsg}\n"
            f"좌표계 이름: {crs.description()}\n"
        )
        if "distance_m" in result:
            message += f"주소와의 예상 오차: {result['distance_m']:.1f} m\n"
        else:
            message += (
                f"추천 점수: {result.get('score', 0)}/100\n"
                f"판단 근거: {result.get('reason', '')}\n"
            )
        message += "\n이 좌표계를 레이어에 지정하시겠습니까?"

        answer = QMessageBox.question(
            self.iface.mainWindow(),
            "좌표계 추천",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return

        layer.setCrs(crs)
        layer.triggerRepaint()
        self.iface.mapCanvas().refresh()
        QMessageBox.information(
            self.iface.mainWindow(),
            "좌표계 적용 완료",
            f"레이어 '{layer.name()}'에 EPSG:{epsg} 좌표계를 지정했습니다.\n\n"
            "원본 좌표값은 변경하지 않았습니다.",
        )

    def show_dialog(self):
        if not self.dialog:
            self.dialog = AssistantDialog(self.iface)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def show_api_settings(self):
        dlg = ApiSettingsDialog(self.iface.mainWindow())
        dlg.exec_()
