# -*- coding: utf-8 -*-

import os

from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from qgis.core import QgsProject, QgsRasterLayer


class NgiiManager:
    """
    국토지리정보원 온맵(GeoPDF) 로더.

    '온맵 불러와줘' 명령을 실행하면 사용자가 내려받은
    GeoPDF/PDF 파일을 선택하고 QGIS 래스터 레이어로 추가합니다.

    이 기능은 로컬 파일을 여는 방식이므로 API Key를 사용하지 않습니다.
    """

    DEFAULT_LAYER_NAME = "국토지리정보원_온맵"

    def __init__(self, iface, log):
        self.iface = iface
        self.log = log

    def load_onmap(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            "국토지리정보원 온맵 GeoPDF 선택",
            "",
            "GeoPDF/PDF 파일 (*.pdf *.PDF);;모든 파일 (*.*)",
        )

        if not file_path:
            self.log("온맵 불러오기가 취소되었습니다.")
            return None

        if not os.path.isfile(file_path):
            self.log("오류: 선택한 온맵 파일을 찾을 수 없습니다.")
            return None

        layer_name = os.path.splitext(
            os.path.basename(file_path)
        )[0]

        # GeoPDF는 GDAL PDF 드라이버를 통해 래스터로 불러옵니다.
        layer = QgsRasterLayer(
            file_path,
            layer_name or self.DEFAULT_LAYER_NAME,
            "gdal",
        )

        if not layer.isValid():
            self.log(
                "오류: 선택한 PDF를 GeoPDF 공간 레이어로 "
                "불러오지 못했습니다."
            )
            self.log(
                "일반 PDF가 아니라 국토지리정보원에서 내려받은 "
                "좌표정보 포함 GeoPDF인지 확인하세요."
            )

            QMessageBox.warning(
                self.iface.mainWindow(),
                "온맵 불러오기 실패",
                "선택한 파일을 GeoPDF 레이어로 열 수 없습니다.\n\n"
                "확인사항\n"
                "1. 국토지리정보원 온맵 GeoPDF 파일인지\n"
                "2. 파일이 손상되지 않았는지\n"
                "3. QGIS/GDAL에서 PDF 드라이버를 지원하는지",
            )
            return None

        project = QgsProject.instance()
        project.addMapLayer(layer, False)

        # 배경지도 성격이므로 레이어 트리의 아래쪽에 배치합니다.
        root = project.layerTreeRoot()
        root.insertLayer(
            len(root.children()),
            layer,
        )

        crs_text = (
            layer.crs().authid()
            if layer.crs().isValid()
            else "좌표계 정보 없음"
        )

        self.log(
            "국토지리정보원 온맵 GeoPDF를 불러왔습니다."
        )
        self.log(
            "파일: %s" % file_path
        )
        self.log(
            "레이어 좌표계: %s" % crs_text
        )

        try:
            extent = layer.extent()
            if not extent.isEmpty():
                self.iface.mapCanvas().setExtent(extent)
                self.iface.mapCanvas().refresh()
                self.log(
                    "온맵 표시 범위로 지도를 이동했습니다."
                )
        except Exception as exc:
            self.log(
                "경고: 온맵 레이어는 추가되었지만 "
                "자동 화면 이동에 실패했습니다: %s" % exc
            )

        return layer
