# -*- coding: utf-8 -*-

import os

from qgis.PyQt.QtWidgets import QFileDialog
from qgis.core import QgsProject, QgsVectorLayer, QgsRectangle

from .parser import parse_command
from ..analysis.cadastral_stats import run_cadastral_area_analysis
from ..analysis.jimok import cleanup_jimok
from ..api.api_manager import ApiManager
from ..ui.api_settings import ApiSettingsDialog


class CommandExecutor:
    def __init__(self, iface, log):
        self.iface = iface
        self.log = log
        self.api = ApiManager(iface, log)

    def execute(self, text):
        cmd = parse_command(text)

        if cmd == "api_settings":
            return ApiSettingsDialog(
                self.iface.mainWindow()
            ).exec_()

        if cmd == "quit":
            self.iface.mainWindow().close()
            return

        if cmd == "open_file":
            return self.open_file()

        if cmd == "jimok_cleanup":
            return self.jimok_cleanup()

        if cmd == "eia_wfs":
            return self.api.load_eia_bsnsarea_wfs()

        if cmd == "ecology_wfs":
            return self.api.load_ecology_wfs()

        if cmd == "terrain_excel":
            self.log(
                "표고·경사 분석은 analysis/terrain.py "
                "확장 모듈에서 실행하도록 구조화되어 있습니다."
            )
            return

        if cmd == "cadastral_load":
            return self.api.load_vworld_cadastral()

        if cmd == "cadastral_stats":
            return self.cadastral_stats()

        if cmd == "zoomout_10km":
            return self.zoomout_10km()

        self.log("알 수 없는 명령입니다. [? 도움말]을 확인하세요.")

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            "벡터 파일 열기",
            "",
            "Vector files (*.shp *.gpkg *.geojson *.dxf);;All files (*.*)",
        )
        if not path:
            return

        layer = QgsVectorLayer(
            path,
            os.path.basename(path),
            "ogr",
        )
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log("파일을 열었습니다: %s" % path)
        else:
            self.log("오류: 파일을 열 수 없습니다.")

    def jimok_cleanup(self):
        layer = self.iface.activeLayer()
        if not layer:
            self.log("오류: 현재 선택된 레이어가 없습니다.")
            return

        result = cleanup_jimok(layer)
        if isinstance(result, dict):
            self.log(
                "'지목' 필드를 생성/갱신했습니다. "
                "변경 %s건, 미분류 %s건"
                % (
                    result.get("updated", 0),
                    result.get("unclassified", 0),
                )
            )
        else:
            self.log(
                "'지목' 필드를 생성/갱신했습니다. "
                "처리 건수: %s" % result
            )

    def cadastral_stats(self):
        default_name = "사업지역_지목별_면적.xlsx"
        output_path, _ = QFileDialog.getSaveFileName(
            self.iface.mainWindow(),
            "지목별 면적 결과 Excel 저장",
            default_name,
            "Excel 통합문서 (*.xlsx)",
        )
        if not output_path:
            self.log("지목별 면적 산출을 취소했습니다.")
            return

        self.log("사업지역과 연속지적도를 중첩 분석하는 중입니다...")

        try:
            result = run_cadastral_area_analysis(
                self.iface,
                output_path,
            )
        except Exception as exc:
            self.log("오류: 지목별 면적 산출에 실패했습니다.")
            self.log(str(exc))
            return

        self.log(
            "지목별 면적 산출 완료: %s개 지목, 총면적 %.2f㎡"
            % (
                result["category_count"],
                result["total_area_m2"],
            )
        )
        self.log(
            "사업지역 레이어: %s"
            % result["business_layer"]
        )
        self.log(
            "연속지적도 레이어: %s"
            % result["cadastral_layer"]
        )
        self.log(
            "Excel 저장: %s"
            % result["output_path"]
        )

    def zoomout_10km(self):
        layer = self.iface.activeLayer()
        if not layer:
            self.log("오류: 현재 선택된 레이어가 없습니다.")
            return

        rect = QgsRectangle(layer.extent())
        rect.grow(10000)
        self.iface.mapCanvas().setExtent(rect)
        self.iface.mapCanvas().refresh()
        self.log("선택 레이어 범위 기준 10km 줌아웃했습니다.")
