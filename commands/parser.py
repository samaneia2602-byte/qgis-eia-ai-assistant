# -*- coding: utf-8 -*-


def normalize(text):
    return (text or "").replace(" ", "").lower()


def parse_command(text):
    t = normalize(text)

    if (
        (
            "사업지역좌표" in t
            or "사업구역좌표" in t
            or "aermodareasource" in t
            or "aermod좌표" in t
            or "areasource생성" in t
        )
        and (
            "추출" in t
            or "생성" in t
            or "저장" in t
            or "만들" in t
        )
    ):
        return "aermod_area_source"

    if "api" in t and ("설정" in t or "저장" in t):
        return "api_settings"

    if "qgis종료" in t or "종료해줘" in t:
        return "quit"

    if "파일열기" in t:
        return "open_file"

    if (
        ("사업지역" in t or "사업구역" in t or "사업지" in t)
        and (
            "종합분석" in t
            or ("종합" in t and "분석" in t)
            or "전체분석" in t
        )
    ):
        return "full_analysis"

    if (
        "생태자연도" in t
        and "분석" in t
        and (
            "사업지역" in t
            or "사업구역" in t
            or "사업지" in t
        )
    ):
        return "ecology_analysis"

    if (
        (
            "온맵" in t
            or "onmap" in t
            or "국토지리정보원지도" in t
            or "국토정보플랫폼지도" in t
        )
        and (
            "불러" in t
            or "가져" in t
            or "추가" in t
            or "열어" in t
        )
    ):
        return "onmap_load"

    if (
        "수치지도" in t
        and (
            "불러" in t
            or "가져" in t
            or "전처리" in t
            or "등고선" in t
        )
    ):
        return "numeric_map_load"

    if (
        "표고" in t
        and "경사" in t
        and (
            "분석" in t
            or "산출" in t
            or "지도" in t
            or "엑셀" in t
            or "출력" in t
        )
    ):
        return "terrain_both"

    if (
        "표고" in t
        and (
            "분석" in t
            or "산출" in t
            or "지도" in t
            or "엑셀" in t
            or "출력" in t
        )
    ):
        return "elevation_analysis"

    if (
        "경사" in t
        and (
            "분석" in t
            or "산출" in t
            or "지도" in t
            or "엑셀" in t
            or "출력" in t
        )
    ):
        return "slope_analysis"

    if (
        "지목" in t
        and (
            "면적" in t
            or "산출" in t
            or "집계" in t
            or "엑셀" in t
        )
    ):
        return "cadastral_stats"

    if (
        "지목" in t
        and (
            "정리" in t
            or "필드" in t
            or "속성" in t
            or "테이블" in t
        )
    ):
        return "jimok_cleanup"

    if (
        "지적도" in t
        and ("불러" in t or "가져" in t)
    ):
        return "cadastral_load"

    if (
        "생태자연도" in t
        and ("불러" in t or "가져" in t)
    ):
        return "ecology_wfs"

    if (
        "환경영향평가" in t
        and "사업구역" in t
        and ("불러" in t or "가져" in t)
    ):
        return "eia_wfs"

    if "줌아웃" in t or "10km" in t:
        return "zoomout_10km"

    return "unknown"
