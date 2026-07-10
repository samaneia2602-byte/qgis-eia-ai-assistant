# -*- coding: utf-8 -*-


def normalize(text):
    return (text or "").replace(" ", "").lower()


def parse_command(text):
    t = normalize(text)

    if "api" in t and ("설정" in t or "저장" in t):
        return "api_settings"

    if "qgis종료" in t or "종료해줘" in t:
        return "quit"

    if "파일열기" in t:
        return "open_file"

    # 면적/산출/집계 명령을 지목 정리보다 먼저 판정합니다.
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
        "표고" in t
        and "경사" in t
        and ("엑셀" in t or "출력" in t)
    ):
        return "terrain_excel"

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
