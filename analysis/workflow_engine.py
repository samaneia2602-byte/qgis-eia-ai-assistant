# -*- coding: utf-8 -*-

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import QgsVectorLayer, QgsWkbTypes

from .cadastral_stats import run_cadastral_area_analysis
from .jimok import cleanup_jimok


class FullAnalysisWorkflow:
    """
    현재 구현된 사업지역 분석 기능을 한 번에 실행합니다.

    실행 순서
    1. 활성 사업지역 폴리곤 확인
    2. VWorld 연속지적도 조회
    3. 지목 필드 자동 정리
    4. 사업지역-지적도 중첩 및 지목별 면적 산출
    5. 결과 레이어·Excel·대화창 보고서 생성
    """

    def __init__(self, iface, log, api_manager):
        self.iface = iface
        self.log = log
        self.api = api_manager

    def run(self, output_path=None, options=None):
        options = dict(options or {})
        result = {
            "success": False,
            "business_layer": None,
            "cadastral_layer": None,
            "cadastral_cleanup": None,
            "area_analysis": None,
        }

        self._step(1, 6, "사업지역 레이어를 확인합니다.")
        business = self._validate_business_layer()
        result["business_layer"] = business
        self.log("사업지역 레이어: %s" % business.name())

        self._step(2, 6, "VWorld 연속지적도를 불러옵니다.")
        self.iface.setActiveLayer(business)
        QCoreApplication.processEvents()

        cadastral = self.api.load_vworld_cadastral()
        if not cadastral or not cadastral.isValid():
            raise RuntimeError(
                "연속지적도를 불러오지 못했습니다. "
                "VWorld API Key와 사업지역 범위를 확인하세요."
            )

        result["cadastral_layer"] = cadastral
        self.log(
            "연속지적도 불러오기 완료: %s개 객체"
            % cadastral.featureCount()
        )

        self._step(3, 6, "연속지적도의 지목 필드를 정리합니다.")
        cleanup_result = cleanup_jimok(
            cadastral,
            field_name="지목",
        )
        result["cadastral_cleanup"] = cleanup_result

        if isinstance(cleanup_result, dict):
            self.log(
                "지목 필드 정리 완료: 변경 %s건, 미분류 %s건"
                % (
                    cleanup_result.get("updated", 0),
                    cleanup_result.get("unclassified", 0),
                )
            )
        else:
            self.log(
                "지목 필드 정리 완료: 처리 %s건"
                % cleanup_result
            )

        cadastral.triggerRepaint()
        QCoreApplication.processEvents()

        self._step(4, 6, "사업지역과 연속지적도를 중첩 분석합니다.")
        # cadastral_stats가 활성 레이어를 사업지역으로 우선 사용하므로 복원합니다.
        self.iface.setActiveLayer(business)
        QCoreApplication.processEvents()

        area_result = run_cadastral_area_analysis(
            self.iface,
            output_path=output_path,
            options=options,
            log_callback=self.log,
        )
        result["area_analysis"] = area_result

        self._step(5, 6, "분석 결과를 정리합니다.")
        if options.get("show_chat_table", True):
            for line in area_result.get("chat_lines", []):
                self.log(line)

        self._step(6, 6, "사업지역 종합분석을 완료했습니다.")
        self.log(
            "총 %s개 지목, %s필지, %.2f㎡를 분석했습니다."
            % (
                area_result.get("category_count", 0),
                area_result.get("total_count", 0),
                area_result.get("total_area_m2", 0.0),
            )
        )

        if area_result.get("output_path"):
            self.log(
                "종합분석 보고서 저장: %s"
                % area_result["output_path"]
            )

        result["success"] = True
        return result

    def _validate_business_layer(self):
        layer = self.iface.activeLayer()

        if not isinstance(layer, QgsVectorLayer):
            raise RuntimeError(
                "사업지역 폴리곤 레이어를 먼저 클릭하여 활성화하세요."
            )

        if not layer.isValid():
            raise RuntimeError(
                "현재 활성 레이어가 유효하지 않습니다."
            )

        if (
            QgsWkbTypes.geometryType(layer.wkbType())
            != QgsWkbTypes.PolygonGeometry
        ):
            raise RuntimeError(
                "사업지역은 폴리곤 SHP 또는 폴리곤 벡터 레이어여야 합니다."
            )

        if not layer.crs().isValid():
            raise RuntimeError(
                "사업지역 레이어의 좌표계가 지정되지 않았습니다. "
                "좌표계를 먼저 지정한 뒤 다시 실행하세요."
            )

        if layer.featureCount() <= 0:
            raise RuntimeError(
                "사업지역 레이어에 객체가 없습니다."
            )

        return layer

    def _step(self, current, total, message):
        self.log(
            "[%s/%s] %s"
            % (current, total, message)
        )
        QCoreApplication.processEvents()
