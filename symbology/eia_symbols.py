# -*- coding: utf-8 -*-

from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsRendererCategory,
)


# 환경영향평가 플러그인 공통 색상 체계
EIA_COLORS = {
    # 생태자연도
    "ecology_grade_1": "#1EA725",
    "ecology_grade_2": "#CFE3C9",
    "ecology_grade_3": None,
    "ecology_special": "#F37E16",
    "ecology_unclassified": "#808080",

    # 보호지역
    "protected_area": "#D7191C",
    "wetland": "#2C7BB6",
    "baekdudaegan": "#7B3294",
    "cultural_heritage": "#8C510A",

    # 토지피복 예비 표준색
    "forest": "#4D9221",
    "agriculture": "#DFC27D",
    "urban": "#D73027",
    "water": "#4575B4",
    "grassland": "#A6D96A",
    "bareland": "#FEE08B",

    # 공통
    "boundary": "#000000",
    "transparent": "0,0,0,0",
}


def _fill_symbol(
    fill_color,
    outline_color=None,
    outline_width=0.20,
    opacity=1.0,
    outline_style="solid",
):
    properties = {
        "color": (
            fill_color
            if fill_color is not None
            else EIA_COLORS["transparent"]
        ),
        "outline_color": (
            outline_color
            if outline_color is not None
            else (
                fill_color
                if fill_color is not None
                else EIA_COLORS["transparent"]
            )
        ),
        "outline_width": str(outline_width),
        "outline_style": outline_style,
    }

    symbol = QgsFillSymbol.createSimple(properties)
    symbol.setOpacity(opacity)
    return symbol


def apply_ecology_style(layer, field_name="생태자연도"):
    """
    생태자연도 표준 분류 스타일을 적용합니다.

    1등급        #1EA725
    2등급        #CFE3C9
    3등급        완전 투명
    별도관리지역 #F37E16
    미분류        투명 채움 + 회색 점선
    """
    categories = [
        QgsRendererCategory(
            "1등급",
            _fill_symbol(
                EIA_COLORS["ecology_grade_1"],
                EIA_COLORS["ecology_grade_1"],
                outline_width=0.20,
            ),
            "1등급",
        ),
        QgsRendererCategory(
            "2등급",
            _fill_symbol(
                EIA_COLORS["ecology_grade_2"],
                "#9EBC96",
                outline_width=0.15,
            ),
            "2등급",
        ),
        QgsRendererCategory(
            "3등급",
            _fill_symbol(
                None,
                None,
                outline_width=0.0,
                opacity=0.0,
                outline_style="no",
            ),
            "3등급",
        ),
        QgsRendererCategory(
            "별도관리지역",
            _fill_symbol(
                EIA_COLORS["ecology_special"],
                EIA_COLORS["ecology_special"],
                outline_width=0.25,
            ),
            "별도관리지역",
        ),
        QgsRendererCategory(
            "미분류",
            _fill_symbol(
                None,
                EIA_COLORS["ecology_unclassified"],
                outline_width=0.15,
                outline_style="dot",
            ),
            "미분류",
        ),
    ]

    renderer = QgsCategorizedSymbolRenderer(
        field_name,
        categories,
    )
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return renderer


def apply_protected_area_style(
    layer,
    field_name,
    protected_value="보호지역",
):
    categories = [
        QgsRendererCategory(
            protected_value,
            _fill_symbol(
                EIA_COLORS["protected_area"],
                EIA_COLORS["protected_area"],
                opacity=0.45,
            ),
            protected_value,
        )
    ]
    renderer = QgsCategorizedSymbolRenderer(
        field_name,
        categories,
    )
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return renderer


def apply_wetland_style(
    layer,
    field_name,
    wetland_value="습지보호지역",
):
    categories = [
        QgsRendererCategory(
            wetland_value,
            _fill_symbol(
                EIA_COLORS["wetland"],
                EIA_COLORS["wetland"],
                opacity=0.45,
            ),
            wetland_value,
        )
    ]
    renderer = QgsCategorizedSymbolRenderer(
        field_name,
        categories,
    )
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return renderer


def apply_landcover_style(layer, field_name):
    """
    향후 토지피복지도 분석에서 재사용할 기본 분류 스타일입니다.
    값은 한국어 표준 분류명을 기준으로 합니다.
    """
    mapping = {
        "산림": EIA_COLORS["forest"],
        "농업지역": EIA_COLORS["agriculture"],
        "시가화건조지역": EIA_COLORS["urban"],
        "수역": EIA_COLORS["water"],
        "초지": EIA_COLORS["grassland"],
        "나지": EIA_COLORS["bareland"],
    }

    categories = []
    for label, color in mapping.items():
        categories.append(
            QgsRendererCategory(
                label,
                _fill_symbol(
                    color,
                    color,
                    opacity=0.75,
                ),
                label,
            )
        )

    renderer = QgsCategorizedSymbolRenderer(
        field_name,
        categories,
    )
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return renderer
