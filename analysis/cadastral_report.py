# -*- coding: utf-8 -*-

from .report_engine import (
    ReportEngine,
    build_cadastral_section,
)


def build_cadastral_report(
    rows,
    total_count,
    total_area_m2,
    business_layer_name="",
    cadastral_layer_name="",
    project_name="",
):
    engine = ReportEngine(
        project_title="환경영향평가 사업지역 분석보고서",
        project_name=project_name,
    )

    section = build_cadastral_section(
        rows=rows,
        total_count=total_count,
        total_area_m2=total_area_m2,
        business_layer_name=business_layer_name,
        cadastral_layer_name=cadastral_layer_name,
    )
    engine.add_section(section)

    engine.set_metadata(
        "business_layer",
        business_layer_name,
    )
    engine.set_metadata(
        "cadastral_layer",
        cadastral_layer_name,
    )

    return engine
