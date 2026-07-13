# -*- coding: utf-8 -*-

from collections import defaultdict
import re

import processing
from qgis.PyQt.QtCore import QVariant
from qgis.core import (
    QgsDistanceArea,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsProject,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .ecology_report import build_ecology_report


GRADE_FIELD_CANDIDATES = (
    "등급",
    "생태자연도",
    "생태자연도등급",
    "자연도등급",
    "평가등급",
    "grade",
    "grd",
    "ecology_grade",
    "ecol_grade",
    "nature_grade",
    "dgre",
    "rank",
)


def _log(callback, message):
    if callback:
        callback(message)


def _is_polygon_layer(layer):
    return (
        isinstance(layer, QgsVectorLayer)
        and layer.isValid()
        and QgsWkbTypes.geometryType(layer.wkbType())
        == QgsWkbTypes.PolygonGeometry
    )


def _validate_business_layer(iface):
    layer = iface.activeLayer()

    if not _is_polygon_layer(layer):
        raise RuntimeError(
            "사업지역 폴리곤 레이어를 먼저 클릭하여 활성화하세요."
        )

    if not layer.crs().isValid():
        raise RuntimeError(
            "사업지역 레이어의 좌표계가 지정되지 않았습니다."
        )

    if layer.featureCount() <= 0:
        raise RuntimeError(
            "사업지역 레이어에 객체가 없습니다."
        )

    return layer


def _find_existing_ecology_layer():
    candidates = []

    for layer in QgsProject.instance().mapLayers().values():
        if not _is_polygon_layer(layer):
            continue

        name = layer.name().lower()
        score = 0

        if "생태자연도" in name:
            score += 100
        if "ecology" in name or "ecological" in name:
            score += 80

        field_names = {
            field.name().lower()
            for field in layer.fields()
        }
        for candidate in GRADE_FIELD_CANDIDATES:
            if candidate.lower() in field_names:
                score += 20

        if score > 0:
            candidates.append((score, layer))

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )
    return candidates[0][1]


def _fix_geometries(layer, name, log_callback=None):
    _log(
        log_callback,
        "%s 도형 오류를 자동 수정하는 중입니다..." % name,
    )

    fixed = processing.run(
        "native:fixgeometries",
        {
            "INPUT": layer,
            "OUTPUT": "memory:",
        },
    )["OUTPUT"]

    fixed.setName("%s_도형수정" % name)
    return fixed


def _resolve_grade_field(layer):
    names = {
        field.name().lower(): field.name()
        for field in layer.fields()
    }

    for candidate in GRADE_FIELD_CANDIDATES:
        if candidate.lower() in names:
            return names[candidate.lower()]

    # 필드명이 예상과 달라도 값에서 등급 패턴이 반복되는 필드를 탐색합니다.
    best_field = None
    best_score = 0

    for field in layer.fields():
        score = 0
        checked = 0

        for feature in layer.getFeatures():
            value = feature[field.name()]
            text = str(value or "").strip()
            if not text:
                continue

            checked += 1
            normalized = _normalize_grade(text)
            if normalized != "미분류":
                score += 1

            if checked >= 100:
                break

        if score > best_score:
            best_score = score
            best_field = field.name()

    if best_field and best_score >= 3:
        return best_field

    raise RuntimeError(
        "생태자연도 등급 필드를 찾지 못했습니다. "
        "생태자연도 레이어 속성표의 등급 필드명을 확인하세요."
    )


def _normalize_grade(value):
    """
    생태자연도 등급값을 보고서용 표준 명칭으로 정리합니다.

    예:
    1, 01, 1등급, I  -> 1등급
    2, 02, 2등급, II -> 2등급
    3, 03, 3등급, III -> 3등급
    별도, 별도관리, 별도관리지역 -> 별도관리지역
    """
    text = str(value or "").strip()

    if not text:
        return "미분류"

    compact = re.sub(r"[\s_\-]+", "", text).lower()

    if (
        "별도관리지역" in compact
        or "별도관리" in compact
        or compact in ("별도", "special", "separate")
    ):
        return "별도관리지역"

    if (
        "1등급" in compact
        or compact in ("1", "01", "i", "grade1", "class1")
    ):
        return "1등급"

    if (
        "2등급" in compact
        or compact in ("2", "02", "ii", "grade2", "class2")
    ):
        return "2등급"

    if (
        "3등급" in compact
        or compact in ("3", "03", "iii", "grade3", "class3")
    ):
        return "3등급"

    match = re.search(r"(?<!\d)([123])(?!\d)", compact)
    if match:
        return "%s등급" % match.group(1)

    return "미분류"


def clip_ecology(
    business_layer,
    ecology_layer,
    add_clip_layer=True,
    log_callback=None,
):
    overlay = business_layer

    if business_layer.crs() != ecology_layer.crs():
        _log(
            log_callback,
            "사업지역 CRS를 생태자연도 CRS에 맞추는 중입니다...",
        )

        overlay = processing.run(
            "native:reprojectlayer",
            {
                "INPUT": business_layer,
                "TARGET_CRS": ecology_layer.crs(),
                "OUTPUT": "memory:",
            },
        )["OUTPUT"]

    fixed_business = _fix_geometries(
        overlay,
        "사업지역",
        log_callback,
    )
    fixed_ecology = _fix_geometries(
        ecology_layer,
        "생태자연도",
        log_callback,
    )

    _log(
        log_callback,
        "사업지역과 생태자연도를 중첩하여 자르는 중입니다...",
    )

    clipped = processing.run(
        "native:clip",
        {
            "INPUT": fixed_ecology,
            "OVERLAY": fixed_business,
            "OUTPUT": "memory:",
        },
    )["OUTPUT"]

    clipped.setName("사업지역_생태자연도_클립")

    if add_clip_layer:
        QgsProject.instance().addMapLayer(clipped)

    _log(
        log_callback,
        "생태자연도 Clip 완료: %s개 객체"
        % clipped.featureCount(),
    )

    return clipped


def summarize_ecology(clipped_layer, log_callback=None):
    grade_field = _resolve_grade_field(clipped_layer)

    _log(
        log_callback,
        "등급 필드 '%s'를 기준으로 면적을 집계합니다."
        % grade_field,
    )

    distance = QgsDistanceArea()
    distance.setSourceCrs(
        clipped_layer.crs(),
        QgsProject.instance().transformContext(),
    )
    distance.setEllipsoid("GRS80")

    summary = defaultdict(float)

    for feature in clipped_layer.getFeatures():
        geometry = feature.geometry()
        if not geometry or geometry.isEmpty():
            continue

        grade = _normalize_grade(feature[grade_field])
        area_m2 = abs(distance.measureArea(geometry))

        if area_m2 > 0:
            summary[grade] += area_m2

    if not summary:
        raise RuntimeError(
            "사업지역과 생태자연도의 중첩 결과가 없습니다."
        )

    total_area = sum(summary.values())

    standard_grades = (
        "1등급",
        "2등급",
        "3등급",
        "별도관리지역",
    )

    rows = []
    for grade in standard_grades:
        area_m2 = summary.get(grade, 0.0)
        rows.append(
            {
                "등급": grade,
                "면적_m2": area_m2,
                "면적_ha": area_m2 / 10000.0,
                "구성비_pct": (
                    area_m2 / total_area * 100.0
                    if total_area else 0.0
                ),
            }
        )

    unclassified_area = summary.get("미분류", 0.0)
    if unclassified_area > 0:
        rows.append(
            {
                "등급": "미분류",
                "면적_m2": unclassified_area,
                "면적_ha": unclassified_area / 10000.0,
                "구성비_pct": (
                    unclassified_area / total_area * 100.0
                    if total_area else 0.0
                ),
            }
        )

    _log(
        log_callback,
        "생태자연도 등급 표준화 완료: "
        "1등급·2등급·3등급·별도관리지역",
    )

    return rows, total_area


def create_summary_layer(
    rows,
    total_area,
    add_to_project=True,
):
    """
    속성테이블 구조:
    순번 | 생태자연도 | 면적_m2 | 면적_ha | 구성비_pct
    """
    layer = QgsVectorLayer(
        "None",
        "사업지역 생태자연도 면적",
        "memory",
    )
    provider = layer.dataProvider()

    fields = QgsFields()
    fields.append(QgsField("순번", QVariant.Int))
    fields.append(
        QgsField(
            "생태자연도",
            QVariant.String,
            len=30,
        )
    )
    fields.append(
        QgsField(
            "면적_m2",
            QVariant.Double,
            len=20,
            prec=2,
        )
    )
    fields.append(
        QgsField(
            "면적_ha",
            QVariant.Double,
            len=20,
            prec=4,
        )
    )
    fields.append(
        QgsField(
            "구성비_pct",
            QVariant.Double,
            len=10,
            prec=2,
        )
    )

    provider.addAttributes(fields)
    layer.updateFields()

    features = []

    for index, row in enumerate(rows, start=1):
        feature = QgsFeature(layer.fields())
        feature.setAttributes(
            [
                index,
                row["등급"],
                round(row["면적_m2"], 2),
                round(row["면적_ha"], 4),
                round(row["구성비_pct"], 2),
            ]
        )
        features.append(feature)

    total_feature = QgsFeature(layer.fields())
    total_feature.setAttributes(
        [
            len(rows) + 1,
            "합계",
            round(total_area, 2),
            round(total_area / 10000.0, 4),
            100.0,
        ]
    )
    features.append(total_feature)

    provider.addFeatures(features)
    layer.updateExtents()

    if add_to_project:
        QgsProject.instance().addMapLayer(layer)

    return layer


def format_chat_table(rows, total_area):
    lines = [
        "",
        "사업지역 생태자연도 분석현황",
        "------------------------------------------",
        "등급 | 면적(㎡) | 면적(ha) | 구성비(%)",
        "------------------------------------------",
    ]

    for row in rows:
        lines.append(
            "%s | %s | %.4f | %.2f"
            % (
                row["등급"],
                format(row["면적_m2"], ",.2f"),
                row["면적_ha"],
                row["구성비_pct"],
            )
        )

    lines.extend(
        [
            "------------------------------------------",
            "합계 | %s | %.4f | 100.00"
            % (
                format(total_area, ",.2f"),
                total_area / 10000.0,
            ),
            "",
        ]
    )
    return lines


def run_ecology_analysis(
    iface,
    api_manager,
    output_path=None,
    options=None,
    log_callback=None,
):
    options = dict(options or {})

    _log(
        log_callback,
        "[1/5] 사업지역 레이어를 확인합니다.",
    )
    business = _validate_business_layer(iface)

    _log(
        log_callback,
        "[2/5] 생태자연도 레이어를 준비합니다.",
    )

    ecology = _find_existing_ecology_layer()

    if ecology:
        _log(
            log_callback,
            "기존 생태자연도 레이어를 사용합니다: %s"
            % ecology.name(),
        )
    else:
        iface.setActiveLayer(business)
        ecology = api_manager.load_ecology_wfs()

    if not ecology or not ecology.isValid():
        raise RuntimeError(
            "생태자연도 레이어를 불러오지 못했습니다. "
            "API Key와 생태자연도 서비스 응답을 확인하세요."
        )

    _log(
        log_callback,
        "[3/5] 사업지역과 생태자연도를 중첩합니다.",
    )

    clipped = clip_ecology(
        business,
        ecology,
        add_clip_layer=options.get(
            "add_clip_layer",
            True,
        ),
        log_callback=log_callback,
    )

    _log(
        log_callback,
        "[4/5] 생태자연도 등급별 면적을 집계합니다.",
    )

    rows, total_area = summarize_ecology(
        clipped,
        log_callback,
    )

    summary_layer = create_summary_layer(
        rows,
        total_area,
        add_to_project=options.get(
            "add_summary_layer",
            True,
        ),
    )

    saved_path = None
    engine = build_ecology_report(
        rows=rows,
        total_area_m2=total_area,
        business_layer_name=business.name(),
        ecology_layer_name=ecology.name(),
        project_name=business.name(),
    )

    if (
        options.get("save_excel", True)
        and output_path
    ):
        _log(
            log_callback,
            "[5/5] 생태자연도 분석보고서를 저장합니다.",
        )
        saved_path = engine.export_excel(
            output_path
        )

    return {
        "business_layer": business.name(),
        "ecology_layer": ecology.name(),
        "clipped_layer": clipped.name(),
        "summary_layer": summary_layer.name(),
        "category_count": len(rows),
        "total_area_m2": total_area,
        "output_path": saved_path,
        "chat_lines": format_chat_table(
            rows,
            total_area,
        ),
    }
