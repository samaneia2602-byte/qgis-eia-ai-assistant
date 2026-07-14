# -*- coding: utf-8 -*-

import math
import os
import tempfile

import numpy as np
from osgeo import gdal, ogr, osr

from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    QgsColorRampShader,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .numeric_map import (
    NumericMapProcessor,
    choose_numeric_map_files,
    request_numeric_map_crs,
)
from .report_engine import ReportEngine, ReportSection


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
        raise RuntimeError(
            "사업지역 레이어가 유효하지 않습니다."
        )

    if (
        QgsWkbTypes.geometryType(layer.wkbType())
        != QgsWkbTypes.PolygonGeometry
    ):
        raise RuntimeError(
            "사업지역은 폴리곤 레이어여야 합니다."
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

    business = _validate_business_layer(
        iface
    )

    _log(
        log_callback,
        "[1/6] 수치지도 파일을 여러 개 선택합니다.",
    )
    paths = choose_numeric_map_files(iface)

    if not paths:
        raise RuntimeError(
            "수치지도 선택이 취소되었습니다."
        )

    _log(
        log_callback,
        "선택한 수치지도: %s개"
        % len(paths),
    )

    _log(
        log_callback,
        "[2/6] 원본 좌표계를 판별하고 등고선을 자동 추출합니다.",
    )
    crs_selection = request_numeric_map_crs(
        iface,
        business,
        log_callback,
    )

    processor = NumericMapProcessor(
        iface,
        log_callback,
    )
    sample_result = processor.prepare(
        paths,
        business,
        add_contour_layer=True,
        forced_source_authid=crs_selection[
            "selected_authid"
        ],
    )

    contour_layer = sample_result.get(
        "contour_layer"
    )
    if contour_layer is not None:
        iface.setActiveLayer(contour_layer)
        iface.zoomToActiveLayer()
        iface.mapCanvas().refresh()

    answer = QMessageBox.question(
        iface.mainWindow(),
        "수치지도 위치 확인",
        (
            "자동 추출한 등고선이 사업지역과 올바르게 겹칩니까?

"
            "예: 분석을 계속합니다.
"
            "아니오: 분석을 중단하고 다른 좌표계로 다시 실행합니다."
        ),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )

    if answer != QMessageBox.Yes:
        if contour_layer is not None:
            QgsProject.instance().removeMapLayer(
                contour_layer.id()
            )
        raise RuntimeError(
            "사용자가 수치지도 위치가 맞지 않다고 확인하여 "
            "분석을 중단했습니다. 다른 좌표계를 선택해 다시 실행하세요."
        )

    work_dir = os.path.join(
        tempfile.gettempdir(),
        "qgis_eia_ai_assistant",
        "terrain_vector_v2",
    )
    os.makedirs(work_dir, exist_ok=True)

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
        "[3/6] 자동 추출한 등고선으로 DEM을 생성합니다.",
    )
    pixel_size = _create_dem(
        sample_result["point_path"],
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
        raise RuntimeError(
            "생성된 DEM을 QGIS에서 열지 못했습니다."
        )

    QgsProject.instance().addMapLayer(
        dem_layer
    )

    results = {}

    if mode in ("elevation", "both"):
        _log(
            log_callback,
            "[4/6] 표고 구간별 면적을 계산합니다.",
        )
        values, cell_area = _read_values(
            dem_path
        )
        rows, stats = _elevation_rows(
            values,
            cell_area,
        )
        dem_layer.setName(
            "사업지역_표고"
        )
        _apply_style(
            dem_layer,
            rows,
            "elevation",
        )

        saved_path = None
        if output_path:
            elevation_path = output_path
            if mode == "both":
                root, extension = os.path.splitext(
                    output_path
                )
                elevation_path = (
                    root
                    + "_표고"
                    + (
                        extension
                        or ".xlsx"
                    )
                )

            saved_path = _build_report(
                "elevation",
                rows,
                stats,
                business.name(),
                paths,
            ).export_excel(
                elevation_path
            )

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
                root, extension = os.path.splitext(
                    output_path
                )
                slope_output = (
                    root
                    + "_경사"
                    + (
                        extension
                        or ".xlsx"
                    )
                )

            saved_path = _build_report(
                "slope",
                rows,
                stats,
                business.name(),
                paths,
            ).export_excel(
                slope_output
            )

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
        "DEM 셀크기 약 %.2fm"
        % pixel_size,
    )

    return {
        "business_layer": business.name(),
        "source_files": paths,
        "sample_result": sample_result,
        "dem_layer": dem_layer.name(),
        "pixel_size": pixel_size,
        "results": results,
    }


def load_and_prepare_numeric_maps(
    iface,
    log_callback=None,
):
    business = _validate_business_layer(
        iface
    )
    paths = choose_numeric_map_files(
        iface
    )

    if not paths:
        raise RuntimeError(
            "수치지도 선택이 취소되었습니다."
        )

    crs_selection = request_numeric_map_crs(
        iface,
        business,
        log_callback,
    )

    processor = NumericMapProcessor(
        iface,
        log_callback,
    )
    result = processor.prepare(
        paths,
        business,
        add_contour_layer=True,
        forced_source_authid=crs_selection[
            "selected_authid"
        ],
    )

    contour_layer = result.get(
        "contour_layer"
    )
    if contour_layer is not None:
        iface.setActiveLayer(contour_layer)
        iface.zoomToActiveLayer()
        iface.mapCanvas().refresh()

    answer = QMessageBox.question(
        iface.mainWindow(),
        "수치지도 위치 확인",
        (
            "자동 추출한 등고선이 사업지역과 올바르게 겹칩니까?

"
            "예: 전처리를 완료합니다.
"
            "아니오: 결과를 삭제하고 다시 좌표계를 선택합니다."
        ),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )

    if answer != QMessageBox.Yes:
        if contour_layer is not None:
            QgsProject.instance().removeMapLayer(
                contour_layer.id()
            )
        raise RuntimeError(
            "사용자가 수치지도 위치가 맞지 않다고 확인했습니다."
        )

    result["crs_selection"] = crs_selection
    return result
