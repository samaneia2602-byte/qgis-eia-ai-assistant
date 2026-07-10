# -*- coding: utf-8 -*-
import os

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsVectorLayer,
)

from .analysis.crs_advisor import CRSAdvisor
from .ui.api_settings import ApiSettingsDialog
from .ui.dialog import AssistantDialog


class EiaAiAssistantPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.dialog = None
        self.actions = []

        self.crs_advisor = CRSAdvisor(iface)
        self.layers_signal_connected = False

    def initGui(self):
        icon = QIcon(
            os.path.join(
                os.path.dirname(__file__),
                "icon.png",
            )
        )

        act = QAction(
            icon,
            "QGIS EIA AI Assistant",
            self.iface.mainWindow(),
        )
        act.triggered.connect(self.show_dialog)

        self.iface.addToolBarIcon(act)
        self.iface.addPluginToMenu(
            "QGIS EIA AI Assistant",
            act,
        )
        self.actions.append(act)

        api_act = QAction(
            "API Key 설정",
            self.iface.mainWindow(),
        )
        api_act.triggered.connect(
            self.show_api_settings
        )

        self.iface.addPluginToMenu(
            "QGIS EIA AI Assistant",
            api_act,
        )
        self.actions.append(api_act)

        # 새 레이어가 추가되면 좌표계 누락 여부를 검사합니다.
        QgsProject.instance().layersAdded.connect(
            self.on_layers_added
        )
        self.layers_signal_connected = True

        # QGIS 시작 후 플러그인 대화창 자동 표시
        QTimer.singleShot(
            900,
            self.show_dialog,
        )

    def unload(self):
        # 플러그인 재로딩 시 신호가 중복 연결되지 않도록 해제합니다.
        if self.layers_signal_connected:
            try:
                QgsProject.instance().layersAdded.disconnect(
                    self.on_layers_added
                )
            except (TypeError, RuntimeError):
                pass

            self.layers_signal_connected = False

        for act in self.actions:
            self.iface.removePluginMenu(
                "QGIS EIA AI Assistant",
                act,
            )
            self.iface.removeToolBarIcon(act)

        self.actions = []

        if self.dialog:
            self.dialog.close()
            self.dialog = None

    def on_layers_added(self, layers):
        """
        QGIS에 새로 추가된 레이어 중에서
        좌표계 정의 파일이 없는 SHP를 검사합니다.
        """
        for layer in layers:
            if not isinstance(layer, QgsVectorLayer):
                continue

            if not layer.isValid():
                continue

            source_path = layer.source().split("|")[0]

            if source_path.lower().endswith(".shp"):
                base_path = os.path.splitext(source_path)[0]

                has_prj = os.path.exists(base_path + ".prj")
                has_qpj = os.path.exists(base_path + ".qpj")

                # 실제 좌표계 정의 파일이 있으면 검사하지 않습니다.
                if has_prj or has_qpj:
                    continue
            else:
                # SHP 이외의 벡터 레이어는 기존 CRS 검사 사용
                if layer.crs().isValid():
                    continue

            QTimer.singleShot(
                0,
                lambda current_layer=layer:
                self.advise_crs(current_layer),
            )

    def advise_crs(self, layer):
        """
        CRSAdvisor 분석 결과를 사용자에게 보여주고
        승인을 받은 뒤 레이어 CRS를 지정합니다.
        """
        if not layer:
            return

        if not layer.isValid():
            return

        result = self.crs_advisor.recommend(layer)

        if not result:
            QMessageBox.information(
                self.iface.mainWindow(),
                "좌표계 검색",
                "좌표계가 지정되지 않은 레이어입니다.\n\n"
                "현재 좌표값만으로는 좌표계를 확실하게 "
                "판단하기 어렵습니다.\n\n"
                "다음 단계에서 대상지역의 읍·면·동 주소를 "
                "입력받아 좌표계를 검색하도록 연결합니다.",
            )
            return

        epsg = int(result["epsg"])

        crs = QgsCoordinateReferenceSystem.fromEpsgId(
            epsg
        )

        if not crs.isValid():
            QMessageBox.warning(
                self.iface.mainWindow(),
                "좌표계 추천 오류",
                "추천된 EPSG 좌표계를 QGIS에서 "
                "불러오지 못했습니다.",
            )
            return

        message = (
            "좌표계가 지정되지 않은 레이어를 발견했습니다.\n\n"
            f"레이어: {layer.name()}\n"
            f"추천 좌표계: EPSG:{epsg}\n"
            f"좌표계 이름: {crs.description()}\n"
            f"추천 점수: {result.get('score', 0)}/100\n"
            f"판단 근거: {result.get('reason', '')}\n\n"
            "이 좌표계를 레이어에 지정하시겠습니까?"
        )

        answer = QMessageBox.question(
            self.iface.mainWindow(),
            "좌표계 추천",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        if answer != QMessageBox.Yes:
            return

        # 원본 좌표값은 변경하지 않고 CRS 정의만 지정합니다.
        layer.setCrs(crs)
        layer.triggerRepaint()

        self.iface.mapCanvas().refresh()

        QMessageBox.information(
            self.iface.mainWindow(),
            "좌표계 적용 완료",
            f"레이어 '{layer.name()}'에 "
            f"EPSG:{epsg} 좌표계를 지정했습니다.\n\n"
            "이 작업은 좌표값을 재투영한 것이 아니라 "
            "원본 좌표계 정의를 지정한 것입니다.",
        )

    def show_dialog(self):
        if not self.dialog:
            self.dialog = AssistantDialog(
                self.iface
            )

        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def show_api_settings(self):
        dlg = ApiSettingsDialog(
            self.iface.mainWindow()
        )
        dlg.exec_()