# -*- coding: utf-8 -*-

import os
from collections import defaultdict

import processing
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsDistanceArea,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProcessingFeatureSourceDefinition,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
)


JIMOK_SOURCE_FIELDS = ("지목", "jimok_cls", "jibun", "bonbun", "bubun", "addr")


def _is_polygon_layer(layer):
    return (
        isinstance(layer, QgsVectorLayer)
        and layer.isValid()
        and QgsWkbTypes.geometryType(layer.wkbType()) == QgsWkbTypes.PolygonGeometry
    )


def _field_names(layer):
    return {field.name().lower(): field.name() for field in layer.fields()}


def _cadastral_score(layer):
    names = _field_names(layer)
    score = 0

    for candidate in JIMOK_SOURCE_FIELDS:
        if candidate.lower() in names:
            score += 15

    layer_name = layer.name().lower()
    for keyword in ("지적", "연속지적", "vworld", "cadastral"):
        if keyword in layer_name:
            score += 30

    return score


def _business_score(layer):
    score = 0
    layer_name = layer.name().lower()

    for keyword in ("사업", "사업지역", "경계", "부지", "구역", "boundary", "site", "bo"):
        if keyword in layer_name:
            score += 15

    if layer.selectedFeatureCount() > 0:
        score += 40

    if _cadastral_score(layer) == 0:
        score += 5

    return score


def find_analysis_layers(iface):
    """프로젝트에서 사업지역 레이어와 연속지적도 레이어를 자동 선택합니다."""
    polygon_layers = [
        layer
        for layer in QgsProject.instance().mapLayers().values()
        if _is_polygon_layer(layer)
    ]

    if len(polygon_layers) < 2:
        raise RuntimeError(
            "폴리곤 레이어가 2개 이상 필요합니다. "
            "사업지역 SHP와 연속지적도 레이어를 먼저 불러오세요."
        )

    active = iface.activeLayer()
    cadastral_ranked = sorted(
        polygon_layers,
        key=_cadastral_score,
        reverse=True,
    )
    cadastral = cadastral_ranked[0]

    if _cadastral_score(cadastral) <= 0:
        raise RuntimeError(
            "연속지적도 레이어를 찾지 못했습니다. "
            "지적도 레이어를 선택한 뒤 '지목별로 테이블 정리해줘'를 먼저 실행하세요."
        )

    business_candidates = [
        layer for layer in polygon_layers if layer.id() != cadastral.id()
    ]

    if active in business_candidates:
        business = active
    else:
        business = sorted(
            business_candidates,
            key=_business_score,
            reverse=True,
        )[0]

    return business, cadastral


def _overlay_input(layer):
    if layer.selectedFeatureCount() > 0:
        return QgsProcessingFeatureSourceDefinition(
            layer.id(),
            selectedFeaturesOnly=True,
        )
    return layer


def _resolve_jimok_field(layer):
    names = _field_names(layer)
    for candidate in ("지목", "jimok_cls"):
        if candidate.lower() in names:
            return names[candidate.lower()]

    raise RuntimeError(
        "연속지적도에 '지목' 필드가 없습니다. "
        "지적도 레이어를 활성화하고 '지목별로 테이블 정리해줘'를 먼저 실행하세요."
    )


def clip_cadastral(business_layer, cadastral_layer):
    """CRS를 맞춘 뒤 연속지적도를 사업지역으로 자릅니다."""
    overlay = business_layer

    if business_layer.crs() != cadastral_layer.crs():
        overlay = processing.run(
            "native:reprojectlayer",
            {
                "INPUT": _overlay_input(business_layer),
                "TARGET_CRS": cadastral_layer.crs(),
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]
    else:
        overlay = _overlay_input(business_layer)

    clipped = processing.run(
        "native:clip",
        {
            "INPUT": cadastral_layer,
            "OVERLAY": overlay,
            "OUTPUT": "memory:",
        },
    )["OUTPUT"]

    clipped.setName("사업지역_연속지적도_클립")
    QgsProject.instance().addMapLayer(clipped)
    return clipped


def summarize_by_jimok(clipped_layer):
    """클립 결과를 지목별 필지 수와 면적으로 집계합니다."""
    jimok_field = _resolve_jimok_field(clipped_layer)

    distance = QgsDistanceArea()
    distance.setSourceCrs(
        clipped_layer.crs(),
        QgsProject.instance().transformContext(),
    )
    distance.setEllipsoid("GRS80")

    summary = defaultdict(lambda: {"count": 0, "area_m2": 0.0})

    for feature in clipped_layer.getFeatures():
        geometry = feature.geometry()
        if not geometry or geometry.isEmpty():
            continue

        jimok = str(feature[jimok_field] or "").strip()
        if not jimok:
            jimok = "미분류"

        area_m2 = abs(distance.measureArea(geometry))
        if area_m2 <= 0:
            continue

        summary[jimok]["count"] += 1
        summary[jimok]["area_m2"] += area_m2

    if not summary:
        raise RuntimeError("사업지역과 연속지적도의 중첩 결과가 없습니다.")

    total_area = sum(item["area_m2"] for item in summary.values())

    rows = []
    for jimok, item in sorted(
        summary.items(),
        key=lambda pair: pair[1]["area_m2"],
        reverse=True,
    ):
        area_m2 = item["area_m2"]
        rows.append(
            {
                "지목": jimok,
                "필지수": item["count"],
                "면적_m2": area_m2,
                "면적_ha": area_m2 / 10000.0,
                "구성비_pct": (area_m2 / total_area * 100.0) if total_area else 0.0,
            }
        )

    return rows, total_area


def create_summary_layer(rows):
    """Excel 내보내기용 속성 전용 메모리 레이어를 만듭니다."""
    layer = QgsVectorLayer("None", "사업지역_지목별_면적", "memory")
    provider = layer.dataProvider()

    fields = QgsFields()
    fields.append(QgsField("순번", QVariant.Int))
    fields.append(QgsField("지목", QVariant.String, len=30))
    fields.append(QgsField("필지수", QVariant.Int))
    fields.append(QgsField("면적_m2", QVariant.Double, len=20, prec=2))
    fields.append(QgsField("면적_ha", QVariant.Double, len=20, prec=4))
    fields.append(QgsField("구성비_pct", QVariant.Double, len=10, prec=2))

    provider.addAttributes(fields)
    layer.updateFields()

    features = []
    for index, row in enumerate(rows, start=1):
        feature = QgsFeature(layer.fields())
        feature.setAttributes(
            [
                index,
                row["지목"],
                row["필지수"],
                round(row["면적_m2"], 2),
                round(row["면적_ha"], 4),
                round(row["구성비_pct"], 2),
            ]
        )
        features.append(feature)

    provider.addFeatures(features)
    layer.updateExtents()
    QgsProject.instance().addMapLayer(layer)
    return layer


def export_summary_xlsx(summary_layer, output_path):
    """QGIS/GDAL XLSX 드라이버를 사용하여 결과표를 저장합니다."""
    if not output_path.lower().endswith(".xlsx"):
        output_path += ".xlsx"

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "XLSX"
    options.fileEncoding = "UTF-8"
    options.layerName = "지목별면적"
    options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

    result = QgsVectorFileWriter.writeAsVectorFormatV2(
        summary_layer,
        output_path,
        QgsProject.instance().transformContext(),
        options,
    )

    error_code = result[0] if isinstance(result, tuple) else result
    if error_code != QgsVectorFileWriter.NoError:
        error_message = result[1] if isinstance(result, tuple) and len(result) > 1 else ""
        raise RuntimeError(
            "Excel 파일 저장에 실패했습니다. %s" % error_message
        )

    return output_path


def run_cadastral_area_analysis(iface, output_path):
    business, cadastral = find_analysis_layers(iface)
    clipped = clip_cadastral(business, cadastral)
    rows, total_area = summarize_by_jimok(clipped)
    summary_layer = create_summary_layer(rows)
    saved_path = export_summary_xlsx(summary_layer, output_path)

    return {
        "business_layer": business.name(),
        "cadastral_layer": cadastral.name(),
        "clipped_layer": clipped.name(),
        "summary_layer": summary_layer.name(),
        "category_count": len(rows),
        "total_area_m2": total_area,
        "output_path": saved_path,
    }
