# -*- coding: utf-8 -*-

import os

from qgis.core import (
    QgsGeometry,
    QgsVectorLayer,
    QgsWkbTypes,
)


SOURCE_ID_FIELDS = (
    "SRC_ID",
    "SOURCE_ID",
    "SRCID",
    "AERMOD_ID",
    "ID",
    "NAME",
    "명칭",
    "이름",
)


def _log(callback, message):
    if callback:
        callback(message)


def _format_number(value, decimals=4):
    return ("%%.%df" % decimals) % float(value)


def _clean_ring(points):
    """
    QGIS polygon ring은 마지막 점이 첫 점과 중복되는 폐합 구조입니다.
    AERMOD vertex 개수에는 중복 폐합점을 포함하지 않습니다.
    """
    cleaned = list(points)

    if len(cleaned) >= 2:
        first = cleaned[0]
        last = cleaned[-1]

        if (
            abs(first.x() - last.x()) < 1e-9
            and abs(first.y() - last.y()) < 1e-9
        ):
            cleaned.pop()

    return cleaned


def _safe_source_id(value):
    text = str(value or "").strip().upper()
    allowed = []

    for char in text:
        if char.isalnum() or char == "_":
            allowed.append(char)

    result = "".join(allowed)
    return result[:12]


def _find_source_id_field(layer):
    lookup = {
        field.name().strip().upper(): field.name()
        for field in layer.fields()
    }

    for candidate in SOURCE_ID_FIELDS:
        actual = lookup.get(candidate.upper())
        if actual:
            return actual

    return None


def _iter_polygon_parts(feature):
    geometry = feature.geometry()

    if geometry is None or geometry.isEmpty():
        return

    if geometry.isMultipart():
        polygons = geometry.asMultiPolygon()
    else:
        polygons = [geometry.asPolygon()]

    for polygon in polygons:
        if not polygon:
            continue

        exterior = polygon[0]
        holes = polygon[1:]

        yield exterior, holes


def _selected_or_all_features(layer):
    selected = layer.selectedFeatures()

    if selected:
        return selected, True

    return list(layer.getFeatures()), False


def validate_area_layer(iface):
    layer = iface.activeLayer()

    if not isinstance(layer, QgsVectorLayer):
        raise RuntimeError(
            "사업지역 폴리곤 레이어를 먼저 클릭하여 활성화하세요."
        )

    if not layer.isValid():
        raise RuntimeError(
            "선택한 사업지역 레이어가 유효하지 않습니다."
        )

    if (
        QgsWkbTypes.geometryType(layer.wkbType())
        != QgsWkbTypes.PolygonGeometry
    ):
        raise RuntimeError(
            "AERMOD AREA SOURCE는 폴리곤 레이어에서만 생성할 수 있습니다."
        )

    if not layer.crs().isValid():
        raise RuntimeError(
            "사업지역 레이어의 좌표계가 지정되지 않았습니다."
        )

    if layer.crs().isGeographic():
        raise RuntimeError(
            "현재 레이어가 경위도 좌표계입니다. "
            "AERMOD 입력에는 미터 단위 투영좌표가 필요하므로 "
            "EPSG:5179, 5186, 5187 등의 투영좌표계로 변환한 뒤 실행하세요."
        )

    return layer


def collect_area_sources(
    iface,
    prefix="A",
    start_number=1,
    use_attribute_id=True,
    log_callback=None,
):
    layer = validate_area_layer(iface)
    features, selected_only = _selected_or_all_features(layer)

    if not features:
        raise RuntimeError(
            "사업지역 레이어에 사용할 폴리곤 객체가 없습니다."
        )

    id_field = (
        _find_source_id_field(layer)
        if use_attribute_id
        else None
    )

    used_ids = set()
    sources = []
    sequence = int(start_number)

    for feature in features:
        feature_id = None

        if id_field:
            feature_id = _safe_source_id(
                feature[id_field]
            )

        part_index = 0

        for exterior, holes in _iter_polygon_parts(feature):
            part_index += 1
            points = _clean_ring(exterior)

            if len(points) < 3:
                _log(
                    log_callback,
                    "경고: 꼭지점이 3개 미만인 객체를 제외했습니다. "
                    "Feature ID=%s"
                    % feature.id(),
                )
                continue

            if feature_id:
                source_id = feature_id

                if part_index > 1:
                    source_id = (
                        "%s_%s"
                        % (
                            feature_id,
                            part_index,
                        )
                    )[:12]
            else:
                source_id = "%s%02d" % (
                    prefix.upper(),
                    sequence,
                )

            while source_id in used_ids:
                sequence += 1
                source_id = "%s%02d" % (
                    prefix.upper(),
                    sequence,
                )

            used_ids.add(source_id)
            sequence += 1

            if holes:
                _log(
                    log_callback,
                    "참고: %s에는 내부 공백(hole) %s개가 있습니다. "
                    "AERMOD AREAPOLY 형식에는 외곽선만 기록합니다."
                    % (
                        source_id,
                        len(holes),
                    ),
                )

            sources.append(
                {
                    "source_id": source_id,
                    "feature_id": feature.id(),
                    "part_index": part_index,
                    "points": points,
                    "vertex_count": len(points),
                    "start_x": points[0].x(),
                    "start_y": points[0].y(),
                }
            )

    if not sources:
        raise RuntimeError(
            "AERMOD 입력으로 변환할 수 있는 폴리곤이 없습니다."
        )

    _log(
        log_callback,
        "%s개 폴리곤을 AREA SOURCE로 변환합니다. "
        "선택 객체만 사용=%s, 좌표계=%s"
        % (
            len(sources),
            "예" if selected_only else "아니오",
            layer.crs().authid(),
        ),
    )

    return layer, sources


def build_area_source_text(
    sources,
    emission_rate="6.122E-05",
    release_height="0.5",
    initial_sigma="5",
    base_elevation="25",
    emisfact="HROFDY  9*0  3*1  1*0  5*1  6*0",
    include_wrapper=True,
    include_emisfact=True,
    coordinate_decimals=4,
):
    lines = []

    if include_wrapper:
        lines.append("SO STARTING")

    for source in sources:
        source_id = source["source_id"]
        points = source["points"]
        vertex_count = source["vertex_count"]

        lines.append(
            "SO LOCATION  {source_id}  AREAPOLY  {x}  {y}  {elevation}".format(
                source_id=source_id,
                x=_format_number(
                    source["start_x"],
                    coordinate_decimals,
                ),
                y=_format_number(
                    source["start_y"],
                    coordinate_decimals,
                ),
                elevation=base_elevation,
            )
        )

        lines.append(
            "SO SRCPARAM  {source_id}  {rate}  {height}  {count} {sigma}".format(
                source_id=source_id,
                rate=emission_rate,
                height=release_height,
                count=vertex_count,
                sigma=initial_sigma,
            )
        )

        # AERMOD 요청 규칙: 한 줄에 최대 3개 좌표점.
        for start in range(0, len(points), 3):
            chunk = points[start:start + 3]
            coordinate_tokens = []

            for point in chunk:
                coordinate_tokens.extend(
                    [
                        _format_number(
                            point.x(),
                            coordinate_decimals,
                        ),
                        _format_number(
                            point.y(),
                            coordinate_decimals,
                        ),
                    ]
                )

            lines.append(
                "SO AREAVERT  {source_id}  {coordinates}".format(
                    source_id=source_id,
                    coordinates="  ".join(
                        coordinate_tokens
                    ),
                )
            )

        if include_emisfact:
            lines.append(
                "SO EMISFACT  {source_id}  {factor}".format(
                    source_id=source_id,
                    factor=emisfact,
                )
            )

        lines.append("")

    if include_wrapper:
        lines.append("SO SRCGROUP  ALL")
        lines.append("SO FINISHED")

    return "\n".join(lines).rstrip() + "\n"


def export_area_sources(
    iface,
    output_path,
    options,
    log_callback=None,
):
    layer, sources = collect_area_sources(
        iface,
        prefix=options["prefix"],
        start_number=options["start_number"],
        use_attribute_id=options["use_attribute_id"],
        log_callback=log_callback,
    )

    text = build_area_source_text(
        sources,
        emission_rate=options["emission_rate"],
        release_height=options["release_height"],
        initial_sigma=options["initial_sigma"],
        base_elevation=options["base_elevation"],
        emisfact=options["emisfact"],
        include_wrapper=options["include_wrapper"],
        include_emisfact=options["include_emisfact"],
        coordinate_decimals=options["coordinate_decimals"],
    )

    # AERMOD 입력자료는 일반적으로 ASCII 호환이 안전합니다.
    # 생성 내용은 영문과 숫자만 사용하므로 UTF-8/ASCII 모두 호환됩니다.
    with open(
        output_path,
        "w",
        encoding="utf-8",
        newline="\n",
    ) as stream:
        stream.write(text)

    _log(
        log_callback,
        "AERMOD AREA SOURCE 저장 완료: %s"
        % output_path,
    )

    for source in sources:
        _log(
            log_callback,
            "%s: 시작점=(%s, %s), 꼭지점=%s개, "
            "AREAVERT 줄=%s개"
            % (
                source["source_id"],
                _format_number(
                    source["start_x"],
                    options["coordinate_decimals"],
                ),
                _format_number(
                    source["start_y"],
                    options["coordinate_decimals"],
                ),
                source["vertex_count"],
                (
                    source["vertex_count"] + 2
                ) // 3,
            ),
        )

    return {
        "layer_name": layer.name(),
        "crs": layer.crs().authid(),
        "output_path": output_path,
        "source_count": len(sources),
        "sources": sources,
        "text": text,
    }
