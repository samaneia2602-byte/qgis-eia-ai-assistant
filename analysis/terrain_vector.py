# -*- coding: utf-8 -*-

import math
import os
import tempfile
from collections import defaultdict

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QFileDialog
from qgis.core import (
    QgsColorRampShader,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .report_engine import ReportEngine, ReportSection


ELEVATION_FIELD_CANDIDATES = (
    "elev",
    "elevation",
    "el",
    "height",
    "altitude",
    "alt",
    "z",
    "contour",
    "contour_elev",
    "표고",
    "고도",
    "등고",
    "등고값",
)

CONTOUR_NAME_KEYWORDS = (
    "contour",
    "contours",
    "등고",
    "등고선",
    "5000",
    "수치지도",
)

SLOPE_BREAKS = (
    (0.0, 5.0, "0~5°"),
    (5.0, 10.0, "5~10°"),
    (10.0, 15.0, "10~15°"),
    (15.0, 20.0, "15~20°"),
    (20.0, 25.0, "20~25°"),
    (25.0, 30.0, "25~30°"),
    (30.0, float("inf"), "30° 이상"),
)


def _log(callback, message):
    if callback:
        callback(message)


def _validate_business_layer(iface):
    layer = iface.activeLayer()

    if not isinstance(layer, QgsVectorLayer):
        raise RuntimeError(
            "사업지역 폴리곤 레이어를 먼저 클릭하여 활성화하세요."
        )

    if not layer.isValid():
        raise RuntimeError("사업지역 레이어가 유효하지 않습니다.")

    if (
        QgsWkbTypes.geometryType(layer.wkbType())
        != QgsWkbTypes.PolygonGeometry
    ):
        raise RuntimeError("사업지역은 폴리곤 레이어여야 합니다.")

    if not layer.crs().isValid():
        raise RuntimeError(
            "사업지역 레이어의 좌표계가 지정되지 않았습니다."
        )

    if layer.featureCount() <= 0:
        raise RuntimeError("사업지역 레이어에 객체가 없습니다.")

    return layer


def _choose_numeric_map_files(iface):
    paths, _ = QFileDialog.getOpenFileNames(
        iface.mainWindow(),
        "수치지도 파일 선택",
        "",
        (
            "수치지도 (*.dxf *.DXF *.shp *.SHP *.gpkg *.GPKG "
            "*.geojson *.GeoJSON);;모든 파일 (*.*)"
        ),
    )
    return paths


def _load_vector_layers(paths, log_callback=None):
    layers = []
    failed = []

    for path in paths:
        name = os.path.splitext(os.path.basename(path))[0]
        layer = QgsVectorLayer(path, name, "ogr")

        if not layer.isValid():
            failed.append(path)
            _log(
                log_callback,
                "경고: 수치지도를 열지 못했습니다: %s" % path,
            )
            continue

        geometry_type = QgsWkbTypes.geometryType(layer.wkbType())
        if geometry_type not in (
            QgsWkbTypes.LineGeometry,
            QgsWkbTypes.PointGeometry,
        ):
            _log(
                log_callback,
                "참고: 선·점 레이어가 아니므로 제외합니다: %s"
                % name,
            )
            continue

        layers.append(layer)

    if not layers:
        raise RuntimeError(
            "선형 또는 점형 수치지도 레이어를 찾지 못했습니다."
        )

    _log(
        log_callback,
        "수치지도 %s개를 열었습니다. 실패 %s개."
        % (len(layers), len(failed)),
    )
    return layers


def _normalized_field_lookup(layer):
    return {
        field.name().strip().lower(): field.name()
        for field in layer.fields()
    }


def _find_elevation_field(layer):
    lookup = _normalized_field_lookup(layer)

    for candidate in ELEVATION_FIELD_CANDIDATES:
        actual = lookup.get(candidate.lower())
        if actual:
            return actual

    # 후보 이름이 없어도 숫자형 필드를 일부 표본 검사합니다.
    best_field = None
    best_score = -1

    for field in layer.fields():
        if field.type() not in (
            QVariant.Int,
            QVariant.UInt,
            QVariant.LongLong,
            QVariant.ULongLong,
            QVariant.Double,
        ):
            continue

        score = 0
        checked = 0

        for feature in layer.getFeatures():
            value = feature[field.name()]
            if value in (None, ""):
                continue

            checked += 1
            try:
                number = float(value)
            except Exception:
                continue

            # 일반적인 국내 표고 범위에 들어오면 점수를 줍니다.
            if -100.0 <= number <= 3000.0:
                score += 1

            if checked >= 100:
                break

        if score > best_score:
            best_score = score
            best_field = field.name()

    if best_score >= 5:
        return best_field

    return None


def _feature_z_from_geometry(geometry):
    if not geometry or geometry.isEmpty():
        return None

    values = []

    try:
        for vertex in geometry.vertices():
            z_value = vertex.z()
            if z_value is None:
                continue
            number = float(z_value)
            if math.isfinite(number):
                values.append(number)
    except Exception:
        return None

    if not values:
        return None

    # 3D 등고선은 보통 한 객체의 모든 Z가 동일합니다.
    median = float(np.median(values))
    spread = max(values) - min(values)

    if spread <= max(0.5, abs(median) * 0.001):
        return median

    return median


def _feature_elevation(feature, geometry, field_name):
    if field_name:
        value = feature[field_name]
        try:
            number = float(value)
            if math.isfinite(number):
                return number
        except Exception:
            pass

    return _feature_z_from_geometry(geometry)


def _layer_score(layer):
    name = layer.name().lower()
    score = 0

    for keyword in CONTOUR_NAME_KEYWORDS:
        if keyword in name:
            score += 20

    if _find_elevation_field(layer):
        score += 100

    if QgsWkbTypes.hasZ(layer.wkbType()):
        score += 100

    if (
        QgsWkbTypes.geometryType(layer.wkbType())
        == QgsWkbTypes.LineGeometry
    ):
        score += 20

    return score


def _transform_geometry(geometry, source_crs, target_crs):
    transformed = QgsGeometry(geometry)

    if source_crs != target_crs:
        transform = QgsCoordinateTransform(
            source_crs,
            target_crs,
            QgsProject.instance().transformContext(),
        )
        transformed.transform(transform)

    return transformed


def _sample_contours_to_points(
    layers,
    target_crs,
    output_path,
    log_callback=None,
):
    driver = ogr.GetDriverByName("GPKG")

    if os.path.exists(output_path):
        driver.DeleteDataSource(output_path)

    data_source = driver.CreateDataSource(output_path)
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(
        int(target_crs.authid().split(":")[1])
    )

    output_layer = data_source.CreateLayer(
        "contour_points",
        spatial_ref,
        ogr.wkbPoint,
    )
    output_layer.CreateField(
        ogr.FieldDefn("elev", ogr.OFTReal)
    )

    selected = sorted(
        layers,
        key=_layer_score,
        reverse=True,
    )

    total_features = 0
    accepted_features = 0
    point_count = 0
    elevations = []

    for layer in selected:
        if not layer.crs().isValid():
            _log(
                log_callback,
                "경고: 좌표계가 없는 수치지도는 제외합니다: %s"
                % layer.name(),
            )
            continue

        field_name = _find_elevation_field(layer)
        score = _layer_score(layer)

        _log(
            log_callback,
            "등고선 후보: %s | 점수=%s | 표고필드=%s | Z=%s"
            % (
                layer.name(),
                score,
                field_name or "없음",
                "있음" if QgsWkbTypes.hasZ(layer.wkbType()) else "없음",
            ),
        )

        for feature in layer.getFeatures():
            total_features += 1
            geometry = feature.geometry()

            if not geometry or geometry.isEmpty():
                continue

            elevation = _feature_elevation(
                feature,
                geometry,
                field_name,
            )

            if elevation is None:
                continue

            if not (-500.0 <= elevation <= 9000.0):
                continue

            transformed = _transform_geometry(
                geometry,
                layer.crs(),
                target_crs,
            )

            # 선을 적당히 조밀하게 만든 뒤 꼭짓점을 표고 샘플로 사용합니다.
            if (
                QgsWkbTypes.geometryType(
                    transformed.wkbType()
                )
                == QgsWkbTypes.LineGeometry
            ):
                try:
                    transformed = transformed.densifyByDistance(20.0)
                except Exception:
                    pass

            local_points = 0

            try:
                vertices = transformed.vertices()
            except Exception:
                continue

            for vertex in vertices:
                ogr_feature = ogr.Feature(
                    output_layer.GetLayerDefn()
                )
                point = ogr.Geometry(ogr.wkbPoint)
                point.AddPoint(
                    float(vertex.x()),
                    float(vertex.y()),
                )
                ogr_feature.SetGeometry(point)
                ogr_feature.SetField(
                    "elev",
                    float(elevation),
                )
                output_layer.CreateFeature(
                    ogr_feature
                )
                ogr_feature = None
                local_points += 1
                point_count += 1

            if local_points > 0:
                accepted_features += 1
                elevations.append(float(elevation))

    output_layer.SyncToDisk()
    data_source = None

    if point_count < 3:
        raise RuntimeError(
            "등고선 또는 표고 Z값을 충분히 찾지 못했습니다. "
            "수치지도에 3D Z값 또는 ELEV·EL·표고 필드가 있는지 확인하세요."
        )

    _log(
        log_callback,
        "표고 샘플 생성 완료: 전체 객체 %s개 중 %s개 사용, "
        "샘플점 %s개, 표고 %.2f~%.2fm"
        % (
            total_features,
            accepted_features,
            point_count,
            min(elevations),
            max(elevations),
        ),
    )

    return {
        "point_path": output_path,
        "feature_count": accepted_features,
        "point_count": point_count,
        "min_elevation": min(elevations),
        "max_elevation": max(elevations),
    }


def _business_extent_5179(business):
    target_crs = QgsCoordinateReferenceSystem("EPSG:5179")
    transform = QgsCoordinateTransform(
        business.crs(),
        target_crs,
        QgsProject.instance().transformContext(),
    )
    return transform.transformBoundingBox(
        business.extent()
    )


def _write_business_cutline(business, output_path):
    driver = ogr.GetDriverByName("GPKG")
    if os.path.exists(output_path):
        driver.DeleteDataSource(output_path)

    data_source = driver.CreateDataSource(output_path)
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(5179)

    layer = data_source.CreateLayer(
        "business",
        spatial_ref,
        ogr.wkbMultiPolygon,
    )

    transform = QgsCoordinateTransform(
        business.crs(),
        QgsCoordinateReferenceSystem("EPSG:5179"),
        QgsProject.instance().transformContext(),
    )

    for feature in business.getFeatures():
        geometry = QgsGeometry(feature.geometry())
        geometry.transform(transform)

        ogr_geometry = ogr.CreateGeometryFromWkb(
            bytes(geometry.asWkb())
        )

        output_feature = ogr.Feature(
            layer.GetLayerDefn()
        )
        output_feature.SetGeometry(
            ogr_geometry
        )
        layer.CreateFeature(
            output_feature
        )
        output_feature = None

    layer.SyncToDisk()
    data_source = None


def _create_dem(
    contour_point_path,
    business,
    output_path,
    cutline_path,
    log_callback=None,
):
    extent = _business_extent_5179(business)

    width_m = max(extent.width(), 1.0)
    height_m = max(extent.height(), 1.0)

    # 5~20m 범위에서 사업지역 크기에 따라 자동 결정합니다.
    pixel_size = max(
        5.0,
        min(
            20.0,
            max(width_m, height_m) / 800.0,
        ),
    )

    columns = max(
        10,
        int(math.ceil(width_m / pixel_size)),
    )
    rows = max(
        10,
        int(math.ceil(height_m / pixel_size)),
    )

    uncropped_path = os.path.splitext(
        output_path
    )[0] + "_uncropped.tif"

    _log(
        log_callback,
        "등고선 표고점으로 DEM을 보간합니다. "
        "셀크기 약 %.2fm, %sx%s"
        % (
            pixel_size,
            columns,
            rows,
        ),
    )

    options = gdal.GridOptions(
        format="GTiff",
        outputBounds=(
            extent.xMinimum(),
            extent.yMinimum(),
            extent.xMaximum(),
            extent.yMaximum(),
        ),
        width=columns,
        height=rows,
        outputType=gdal.GDT_Float32,
        outputSRS="EPSG:5179",
        zfield="elev",
        algorithm=(
            "invdist:power=2.0:smoothing=0.0:"
            "radius1=0.0:radius2=0.0:"
            "max_points=16:min_points=1:nodata=-9999"
        ),
        creationOptions=[
            "TILED=YES",
            "COMPRESS=DEFLATE",
            "BIGTIFF=IF_SAFER",
        ],
    )

    result = gdal.Grid(
        uncropped_path,
        contour_point_path,
        options=options,
    )

    if result is None:
        raise RuntimeError("등고선으로 DEM을 생성하지 못했습니다.")

    result = None

    _write_business_cutline(
        business,
        cutline_path,
    )

    warp_options = gdal.WarpOptions(
        format="GTiff",
        cutlineDSName=cutline_path,
        cropToCutline=True,
        dstNodata=-9999,
        multithread=True,
        creationOptions=[
            "TILED=YES",
            "COMPRESS=DEFLATE",
            "BIGTIFF=IF_SAFER",
        ],
    )

    clipped = gdal.Warp(
        output_path,
        uncropped_path,
        options=warp_options,
    )

    if clipped is None:
        raise RuntimeError(
            "사업지역 경계로 DEM을 자르지 못했습니다."
        )

    clipped = None
    return pixel_size


def _create_slope(dem_path, slope_path):
    options = gdal.DEMProcessingOptions(
        format="GTiff",
        slopeFormat="degree",
        computeEdges=True,
        creationOptions=[
            "TILED=YES",
            "COMPRESS=DEFLATE",
        ],
    )

    result = gdal.DEMProcessing(
        slope_path,
        dem_path,
        "slope",
        options=options,
    )

    if result is None:
        raise RuntimeError(
            "DEM에서 경사도 래스터를 생성하지 못했습니다."
        )

    result = None


def _read_values(raster_path):
    dataset = gdal.Open(raster_path)
    if dataset is None:
        raise RuntimeError("분석 래스터를 열지 못했습니다.")

    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray().astype(np.float64)
    nodata = band.GetNoDataValue()

    valid = np.isfinite(array)
    if nodata is not None:
        valid &= ~np.isclose(array, nodata)

    values = array[valid]

    if values.size == 0:
        dataset = None
        raise RuntimeError(
            "사업지역 안에 분석 가능한 값이 없습니다."
        )

    geotransform = dataset.GetGeoTransform()
    cell_area = abs(
        geotransform[1] * geotransform[5]
    )
    dataset = None

    return values, cell_area


def _elevation_rows(values, cell_area):
    minimum = float(np.min(values))
    maximum = float(np.max(values))
    mean = float(np.mean(values))
    stddev = float(np.std(values))

    interval = 20.0
    start = math.floor(minimum / interval) * interval
    end = math.ceil(maximum / interval) * interval

    if end <= start:
        end = start + interval

    rows = []
    lower = start

    while lower < end:
        upper = lower + interval
        if upper >= end:
            mask = (values >= lower) & (values <= maximum)
        else:
            mask = (values >= lower) & (values < upper)

        count = int(np.count_nonzero(mask))
        area = count * cell_area

        rows.append(
            {
                "구간": "%s~%sm"
                % (
                    int(lower) if lower.is_integer() else lower,
                    int(upper) if upper.is_integer() else upper,
                ),
                "상한": upper,
                "셀수": count,
                "면적_m2": area,
                "면적_ha": area / 10000.0,
                "구성비_pct": (
                    count / float(values.size) * 100.0
                ),
            }
        )
        lower = upper

    return rows, {
        "minimum": minimum,
        "maximum": maximum,
        "mean": mean,
        "stddev": stddev,
    }


def _slope_rows(values, cell_area):
    rows = []

    for lower, upper, label in SLOPE_BREAKS:
        if math.isinf(upper):
            mask = values >= lower
        else:
            mask = (values >= lower) & (values < upper)

        count = int(np.count_nonzero(mask))
        area = count * cell_area

        rows.append(
            {
                "구간": label,
                "상한": 9999.0 if math.isinf(upper) else upper,
                "셀수": count,
                "면적_m2": area,
                "면적_ha": area / 10000.0,
                "구성비_pct": (
                    count / float(values.size) * 100.0
                ),
            }
        )

    return rows, {
        "minimum": float(np.min(values)),
        "maximum": float(np.max(values)),
        "mean": float(np.mean(values)),
        "stddev": float(np.std(values)),
    }


def _apply_style(layer, rows, mode):
    if mode == "elevation":
        colors = (
            "#1A9850",
            "#66BD63",
            "#A6D96A",
            "#D9EF8B",
            "#FFFFBF",
            "#FEE08B",
            "#FDAE61",
            "#F46D43",
            "#A6611A",
            "#FFFFFF",
        )
    else:
        colors = (
            "#E5F5E0",
            "#A1D99B",
            "#FFFFB2",
            "#FECC5C",
            "#FD8D3C",
            "#F03B20",
            "#BD0026",
        )

    items = []

    for index, row in enumerate(rows):
        items.append(
            QgsColorRampShader.ColorRampItem(
                row["상한"],
                QColor(
                    colors[
                        min(index, len(colors) - 1)
                    ]
                ),
                row["구간"],
            )
        )

    ramp = QgsColorRampShader()
    ramp.setColorRampType(
        QgsColorRampShader.Discrete
    )
    ramp.setColorRampItemList(items)

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)

    renderer = QgsSingleBandPseudoColorRenderer(
        layer.dataProvider(),
        1,
        shader,
    )
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def _build_report(
    mode,
    rows,
    stats,
    business_name,
    source_files,
):
    title = (
        "사업지역 표고 분석"
        if mode == "elevation"
        else "사업지역 경사 분석"
    )
    unit = "m" if mode == "elevation" else "°"

    report_rows = []

    for index, row in enumerate(rows, start=1):
        report_rows.append(
            {
                "순번": index,
                "구간": row["구간"],
                "셀수": row["셀수"],
                "면적(㎡)": round(row["면적_m2"], 2),
                "면적(ha)": round(row["면적_ha"], 4),
                "구성비(%)": round(row["구성비_pct"], 2),
            }
        )

    section = ReportSection(
        section_id=mode,
        title=title,
        columns=[
            "순번",
            "구간",
            "셀수",
            "면적(㎡)",
            "면적(ha)",
            "구성비(%)",
        ],
        rows=report_rows,
        summary={
            "최솟값": "%.2f%s" % (stats["minimum"], unit),
            "최댓값": "%.2f%s" % (stats["maximum"], unit),
            "평균값": "%.2f%s" % (stats["mean"], unit),
            "표준편차": "%.2f%s" % (stats["stddev"], unit),
        },
        metadata={
            "사업지역 레이어": business_name,
            "수치지도 파일": ", ".join(
                os.path.basename(path)
                for path in source_files
            ),
        },
    )

    engine = ReportEngine(
        project_title=title + " 보고서",
        project_name=business_name,
    )
    engine.add_section(section)
    return engine


def _chat_lines(mode, rows, stats):
    title = (
        "사업지역 표고 분석현황"
        if mode == "elevation"
        else "사업지역 경사 분석현황"
    )
    unit = "m" if mode == "elevation" else "°"

    lines = [
        "",
        title,
        "------------------------------------------",
        "구간 | 면적(㎡) | 면적(ha) | 구성비(%)",
        "------------------------------------------",
    ]

    for row in rows:
        lines.append(
            "%s | %s | %.4f | %.2f"
            % (
                row["구간"],
                format(row["면적_m2"], ",.2f"),
                row["면적_ha"],
                row["구성비_pct"],
            )
        )

    lines.extend(
        [
            "------------------------------------------",
            "최소 %.2f%s / 최대 %.2f%s / 평균 %.2f%s"
            % (
                stats["minimum"],
                unit,
                stats["maximum"],
                unit,
                stats["mean"],
                unit,
            ),
            "",
        ]
    )
    return lines


def run_vector_terrain_analysis(
    iface,
    mode,
    output_path=None,
    log_callback=None,
):
    """
    mode:
      elevation - 표고 분석
      slope     - 경사 분석
      both      - 표고와 경사 분석
    """
    if mode not in ("elevation", "slope", "both"):
        raise ValueError(
            "mode는 elevation, slope, both 중 하나여야 합니다."
        )

    business = _validate_business_layer(iface)

    _log(
        log_callback,
        "[1/6] 수치지도 파일을 선택합니다.",
    )
    paths = _choose_numeric_map_files(iface)

    if not paths:
        raise RuntimeError("수치지도 선택이 취소되었습니다.")

    _log(
        log_callback,
        "선택한 수치지도: %s개"
        % len(paths),
    )

    layers = _load_vector_layers(
        paths,
        log_callback,
    )

    work_dir = os.path.join(
        tempfile.gettempdir(),
        "qgis_eia_ai_assistant",
        "terrain_vector",
    )
    os.makedirs(work_dir, exist_ok=True)

    target_crs = QgsCoordinateReferenceSystem(
        "EPSG:5179"
    )

    contour_points_path = os.path.join(
        work_dir,
        "contour_points.gpkg",
    )
    dem_path = os.path.join(
        work_dir,
        "사업지역_DEM.tif",
    )
    cutline_path = os.path.join(
        work_dir,
        "business_cutline.gpkg",
    )
    slope_path = os.path.join(
        work_dir,
        "사업지역_경사도.tif",
    )

    _log(
        log_callback,
        "[2/6] 등고선과 표고 Z값을 자동 탐색합니다.",
    )
    sample_result = _sample_contours_to_points(
        layers,
        target_crs,
        contour_points_path,
        log_callback,
    )

    _log(
        log_callback,
        "[3/6] 수치지도로 DEM을 생성합니다.",
    )
    pixel_size = _create_dem(
        contour_points_path,
        business,
        dem_path,
        cutline_path,
        log_callback,
    )

    dem_layer = QgsRasterLayer(
        dem_path,
        "사업지역_DEM",
        "gdal",
    )
    if not dem_layer.isValid():
        raise RuntimeError("생성된 DEM을 QGIS에서 열지 못했습니다.")

    QgsProject.instance().addMapLayer(dem_layer)

    results = {}

    if mode in ("elevation", "both"):
        _log(
            log_callback,
            "[4/6] 표고 구간별 면적을 계산합니다.",
        )
        values, cell_area = _read_values(dem_path)
        rows, stats = _elevation_rows(
            values,
            cell_area,
        )
        dem_layer.setName("사업지역_표고")
        _apply_style(
            dem_layer,
            rows,
            "elevation",
        )

        saved_path = None
        if output_path:
            elevation_path = output_path
            if mode == "both":
                root, extension = os.path.splitext(output_path)
                elevation_path = root + "_표고" + (
                    extension or ".xlsx"
                )

            saved_path = _build_report(
                "elevation",
                rows,
                stats,
                business.name(),
                paths,
            ).export_excel(elevation_path)

        results["elevation"] = {
            "result_layer": dem_layer.name(),
            "rows": rows,
            "stats": stats,
            "output_path": saved_path,
            "chat_lines": _chat_lines(
                "elevation",
                rows,
                stats,
            ),
        }

    if mode in ("slope", "both"):
        _log(
            log_callback,
            "[5/6] DEM에서 경사도를 계산합니다.",
        )
        _create_slope(
            dem_path,
            slope_path,
        )

        slope_layer = QgsRasterLayer(
            slope_path,
            "사업지역_경사도",
            "gdal",
        )
        if not slope_layer.isValid():
            raise RuntimeError(
                "생성된 경사도 레이어를 QGIS에서 열지 못했습니다."
            )

        QgsProject.instance().addMapLayer(
            slope_layer
        )

        values, cell_area = _read_values(
            slope_path
        )
        rows, stats = _slope_rows(
            values,
            cell_area,
        )
        _apply_style(
            slope_layer,
            rows,
            "slope",
        )

        saved_path = None
        if output_path:
            slope_output = output_path
            if mode == "both":
                root, extension = os.path.splitext(output_path)
                slope_output = root + "_경사" + (
                    extension or ".xlsx"
                )

            saved_path = _build_report(
                "slope",
                rows,
                stats,
                business.name(),
                paths,
            ).export_excel(slope_output)

        results["slope"] = {
            "result_layer": slope_layer.name(),
            "rows": rows,
            "stats": stats,
            "output_path": saved_path,
            "chat_lines": _chat_lines(
                "slope",
                rows,
                stats,
            ),
        }

    _log(
        log_callback,
        "[6/6] 표고·경사 분석을 완료했습니다. "
        "DEM 셀크기 약 %.2fm" % pixel_size,
    )

    return {
        "business_layer": business.name(),
        "source_files": paths,
        "sample_result": sample_result,
        "dem_layer": dem_layer.name(),
        "pixel_size": pixel_size,
        "results": results,
    }
