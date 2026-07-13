# -*- coding: utf-8 -*-

from .report_engine import ReportEngine, ReportSection


def build_ecology_report(
    rows,
    total_area_m2,
    business_layer_name="",
    ecology_layer_name="",
    project_name="",
):
    report_rows = []

    for index, row in enumerate(rows, start=1):
        report_rows.append(
            {
                "순번": index,
                "생태자연도 등급": row["등급"],
                "면적(㎡)": round(row["면적_m2"], 2),
                "면적(ha)": round(row["면적_ha"], 4),
                "구성비(%)": round(row["구성비_pct"], 2),
            }
        )

    report_rows.append(
        {
            "순번": len(report_rows) + 1,
            "생태자연도 등급": "합계",
            "면적(㎡)": round(total_area_m2, 2),
            "면적(ha)": round(total_area_m2 / 10000.0, 4),
            "구성비(%)": 100.0,
        }
    )

    major = rows[:3]
    major_text = ", ".join(
        "%s %.2f%%" % (row["등급"], row["구성비_pct"])
        for row in major
    )

    section = ReportSection(
        section_id="ecology",
        title="사업지역 생태자연도 분석현황",
        columns=[
            "순번",
            "생태자연도 등급",
            "면적(㎡)",
            "면적(ha)",
            "구성비(%)",
        ],
        rows=report_rows,
        summary={
            "총면적": "%s㎡" % format(total_area_m2, ",.2f"),
            "총면적(ha)": "%.4fha" % (total_area_m2 / 10000.0),
            "주요 등급": major_text or "해당 없음",
        },
        metadata={
            "사업지역 레이어": business_layer_name,
            "생태자연도 레이어": ecology_layer_name,
        },
    )

    engine = ReportEngine(
        project_title="환경영향평가 사업지역 생태자연도 분석보고서",
        project_name=project_name,
    )
    engine.add_section(section)
    return engine
