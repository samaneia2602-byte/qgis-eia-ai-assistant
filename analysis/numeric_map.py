# -*- coding: utf-8 -*-

import math
import os
import re
import tempfile
from pathlib import Path

import numpy as np
from osgeo import ogr, osr

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QFileDialog, QInputDialog, QMessageBox
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsProject,
    QgsVectorLayer,
)


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

CAD_LAYER_FIELD_CANDIDATES = (
    "layer",
    "레이어",
    "lyr",
    "cad_layer",
)

CONTOUR_KEYWORDS = (
    "contour",
    "contours",
    "등고",
    "등고선",
    "주곡선",
    "계곡선",
    # 국토지리정보원 수치지도 지형/등고선 계열 코드
    "f00171",
)

EXCLUDE_KEYWORDS = (
    "road",
    "도로",
    "building",
    "건물",
    "boundary",
    "경계",
    "text",
    "문자",
    "주기",
    "river",
    "하천",
    "bridge",
    "교량",
)

# 한국에서 자주 사용하는 좌표계 후보.
CRS_CANDIDATES = (
    "EPSG:5179",
    "EPSG:5181",
    "EPSG:5182",
    "EPSG:5183",
    "EPSG:5184",
    "EPSG:5185",
    "EPSG:5186",
    "EPSG:5187",
    "EPSG:5188",
    "EPSG:5174",
    "EPSG:5175",
    "EPSG:5176",
    "EPSG:5177",
    "EPSG:5178",
    "EPSG:2096",
    "EPSG:2097",
    "EPSG:2098",
    "EPSG:4326",
)


def _flatten_ogr_geometry_type(geometry_type):
    """
    QGIS 3.16에 포함된 구형 GDAL/OGR 호환용 geometry type 평탄화.

    일부 GDAL 버전에는 ogr.wkbFlatten이 없으므로
    ogr.GT_Flatten을 우선 사용하고, 그것도 없으면
    Z/M 비트를 직접 제거합니다.
    """
    if hasattr(ogr, "GT_Flatten"):
        return ogr.GT_Flatten(geometry_type)

    if hasattr(ogr, "wkbFlatten"):
        return ogr.wkbFlatten(geometry_type)

    # OGR geometry type의 25D/Z/M 비트를 제거합니다.
    value = int(geometry_type)
    value = value & 0x0FFFFFFF

    # 일부 구형 OGR은 1000/2000/3000 오프셋으로 Z/M을 표현합니다.
    if value >= 3000:
        value -= 3000
    elif value >= 2000:
        value -= 2000
    elif value >= 1000:
        value -= 1000

    return value


def _log(callback, message):
    if callback:
        callback(message)


def choose_numeric_map_files(iface):
    paths, _ = QFileDialog.getOpenFileNames(
        iface.mainWindow(),
        "국토지리정보원 수치지도 선택",
        "",
        (
            "수치지도 (*.dxf *.DXF *.shp *.SHP *.gpkg *.GPKG "
            "*.geojson *.GeoJSON);;모든 파일 (*.*)"
        ),
    )
    return paths



def _business_center_wgs84(business_layer):
    transform = QgsCoordinateTransform(
        business_layer.crs(),
        QgsCoordinateReferenceSystem("EPSG:4326"),
        QgsProject.instance().transformContext(),
    )
    return transform.transform(
        business_layer.extent().center()
    )


def _recommend_korea_belt(lon):
    """
    사업지역 중심 경도에 따라 Korea 2000 TM 원점을 추천합니다.

    경계부에서는 사용자가 QGIS 좌표계 선택 경험에 따라
    직접 다른 EPSG를 선택할 수 있습니다.
    """
    if lon < 126.0:
        return "EPSG:5185"
    if lon < 128.0:
        return "EPSG:5186"
    if lon < 130.0:
        return "EPSG:5187"
    return "EPSG:5188"


def request_numeric_map_crs(
    iface,
    business_layer,
    log_callback=None,
):
    """
    사용 지역을 입력받고 추천 좌표계를 사용자에게 확인받습니다.
    주소는 판단 보조정보이며, 실제 추천은 사업지역 중심 경도를
    기준으로 합니다.
    """
    center = _business_center_wgs84(
        business_layer
    )
    recommended = _recommend_korea_belt(
        center.x()
    )

    address, accepted = QInputDialog.getText(
        iface.mainWindow(),
        "수치지도 좌표계 추천",
        (
            "수치지도의 제작지역 주소를 시·군·구 또는 읍·면·동 "
            "단위로 입력하세요.\n"
            "예: 강원특별자치도 강릉시 성산면"
        ),
    )

    if not accepted:
        raise RuntimeError(
            "수치지도 좌표계 선택이 취소되었습니다."
        )

    descriptions = {
        "EPSG:5185": "Korea 2000 / West Belt 2010",
        "EPSG:5186": "Korea 2000 / Central Belt 2010",
        "EPSG:5187": "Korea 2000 / East Belt 2010",
        "EPSG:5188": "Korea 2000 / East Sea Belt 2010",
        "EPSG:5179": "Korea 2000 / Unified CS",
    }

    ordered = [
        recommended,
        "EPSG:5185",
        "EPSG:5186",
        "EPSG:5187",
        "EPSG:5188",
        "EPSG:5179",
    ]

    unique = []
    for authid in ordered:
        if authid not in unique:
            unique.append(authid)

    items = [
        "%s — %s%s"
        % (
            authid,
            descriptions[authid],
            " (추천)" if authid == recommended else "",
        )
        for authid in unique
    ]

    selected, accepted = QInputDialog.getItem(
        iface.mainWindow(),
        "수치지도 좌표계 적용",
        (
            "입력 지역: %s\n"
            "사업지역 중심: 경도 %.6f, 위도 %.6f\n"
            "수치지도에 적용할 좌표계를 선택하세요."
            % (
                address.strip() or "미입력",
                center.x(),
                center.y(),
            )
        ),
        items,
        0,
        False,
    )

    if not accepted:
        raise RuntimeError(
            "수치지도 좌표계 적용이 취소되었습니다."
        )

    selected_authid = selected.split("—", 1)[0].strip()

    _log(
        log_callback,
        "수치지도 제작지역: %s"
        % (address.strip() or "미입력"),
    )
    _log(
        log_callback,
        "추천 좌표계: %s / 사용자 선택: %s"
        % (
            recommended,
            selected_authid,
        ),
    )

    return {
        "address": address.strip(),
        "recommended_authid": recommended,
        "selected_authid": selected_authid,
        "center_lon": center.x(),
        "center_lat": center.y(),
    }


class NumericMapProcessor:
    """
    국토지리정보원 수치지도 전처리기.

    - 여러 DXF/SHP/GPKG 동시 처리
    - 원본 좌표 범위와 사업지역 위치를 비교해 CRS 자동 판별
    - 3D 폴리라인의 일정한 Z값 또는 표고 필드 자동 검색
    - 도로·건물·경계 등 비등고선 객체 제외
    - DEM 보간용 표고 샘플점과 확인용 등고선 레이어 생성
    """

    TARGET_AUTHID = "EPSG:5179"

    def __init__(self, iface, log_callback=None):
        self.iface = iface
        self.log = log_callback
        self.target_crs = QgsCoordinateReferenceSystem(
            self.TARGET_AUTHID
        )

    def prepare(
        self,
        paths,
        business_layer,
        add_contour_layer=True,
        forced_source_authid=None,
    ):
        if not paths:
            raise RuntimeError("수치지도 파일을 선택하지 않았습니다.")

        business_center_wgs84 = self._business_center_wgs84(
            business_layer
        )
        business_extent_target = self._business_extent_target(
            business_layer
        )
        search_extent = business_extent_target
        search_extent.grow(5000.0)

        candidates = []
        source_summaries = []

        for path in paths:
            dataset = ogr.Open(path, 0)
            if dataset is None:
                _log(
                    self.log,
                    "경고: 수치지도를 열지 못했습니다: %s"
                    % path,
                )
                continue

            layer_count = dataset.GetLayerCount()
            _log(
                self.log,
                "수치지도 검사: %s | 내부 레이어 %s개"
                % (
                    os.path.basename(path),
                    layer_count,
                ),
            )

            for layer_index in range(layer_count):
                ogr_layer = dataset.GetLayerByIndex(layer_index)
                if ogr_layer is None:
                    continue

                layer_name = ogr_layer.GetName() or (
                    "layer_%s" % layer_index
                )
                geometry_type = _flatten_ogr_geometry_type(
                    ogr_layer.GetGeomType()
                )

                if geometry_type not in (
                    ogr.wkbLineString,
                    ogr.wkbMultiLineString,
                    ogr.wkbPoint,
                    ogr.wkbMultiPoint,
                ):
                    continue

                extent = ogr_layer.GetExtent()
                if not extent:
                    continue

                if forced_source_authid:
                    source_authid = forced_source_authid
                    distance_km = self._distance_for_authid(
                        extent,
                        source_authid,
                        business_center_wgs84,
                    )
                    swap_xy = False
                else:
                    source_authid, distance_km, swap_xy = self._resolve_source_crs(
                        ogr_layer,
                        extent,
                        business_center_wgs84,
                    )

                _log(
                    self.log,
                    "원본 좌표범위: X=%.3f~%.3f, Y=%.3f~%.3f"
                    % (
                        float(extent[0]),
                        float(extent[1]),
                        float(extent[2]),
                        float(extent[3]),
                    ),
                )

                if not source_authid:
                    _log(
                        self.log,
                        "경고: 좌표계를 판별하지 못해 제외합니다: "
                        "%s / %s"
                        % (
                            os.path.basename(path),
                            layer_name,
                        ),
                    )
                    continue

                _log(
                    self.log,
                    "좌표계 판별: %s / %s → %s%s "
                    "(사업지역 중심과 약 %.2fkm)"
                    % (
                        os.path.basename(path),
                        layer_name,
                        source_authid,
                        " + XY교환" if swap_xy else "",
                        distance_km,
                    ),
                )

                layer_candidates = self._collect_layer_candidates(
                    path,
                    ogr_layer,
                    layer_name,
                    source_authid,
                    search_extent,
                    swap_xy,
                )
                candidates.extend(layer_candidates)

                source_summaries.append(
                    {
                        "file": path,
                        "layer": layer_name,
                        "crs": source_authid,
                        "candidate_count": len(
                            layer_candidates
                        ),
                        "swap_xy": swap_xy,
                    }
                )

            dataset = None

        if not candidates:
            _log(
                self.log,
                "OGR에서 표고 Z값을 읽지 못했습니다. "
                "DXF 원문에서 LWPOLYLINE/POLYLINE 표고를 다시 검색합니다.",
            )

            for path in paths:
                if not path.lower().endswith(".dxf"):
                    continue

                fallback_authid = (
                    forced_source_authid
                    or "EPSG:5187"
                )

                raw_candidates = self._collect_raw_dxf_candidates(
                    path,
                    fallback_authid,
                    search_extent,
                )
                candidates.extend(raw_candidates)

        if not candidates:
            raise RuntimeError(
                "수치지도 위치는 확인했지만 등고선 표고값을 읽지 못했습니다. "
                "DXF 객체의 Layer 코드와 Elevation/Z 저장방식을 확인해야 합니다."
            )

        accepted = self._filter_contour_candidates(
            candidates
        )

        if not accepted:
            raise RuntimeError(
                "수치지도에서 등고선 후보는 찾았지만 도로·건물 등 "
                "비등고선 객체를 제외한 뒤 사용할 객체가 없습니다."
            )

        work_dir = os.path.join(
            tempfile.gettempdir(),
            "qgis_eia_ai_assistant",
            "numeric_map",
        )
        os.makedirs(work_dir, exist_ok=True)

        point_path = os.path.join(
            work_dir,
            "contour_sample_points.gpkg",
        )

        contour_layer, point_count = self._write_outputs(
            accepted,
            point_path,
            add_contour_layer,
        )

        elevations = [
            item["elevation"]
            for item in accepted
        ]

        _log(
            self.log,
            "수치지도 전처리 완료: 등고선 %s개, "
            "표고 샘플점 %s개, 표고 %.2f~%.2fm"
            % (
                len(accepted),
                point_count,
                min(elevations),
                max(elevations),
            ),
        )

        return {
            "point_path": point_path,
            "contour_layer": contour_layer,
            "contour_count": len(accepted),
            "point_count": point_count,
            "min_elevation": min(elevations),
            "max_elevation": max(elevations),
            "source_summaries": source_summaries,
        }

    def _business_center_wgs84(self, business_layer):
        transform = QgsCoordinateTransform(
            business_layer.crs(),
            QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsProject.instance().transformContext(),
        )
        return transform.transform(
            business_layer.extent().center()
        )

    def _business_extent_target(self, business_layer):
        transform = QgsCoordinateTransform(
            business_layer.crs(),
            self.target_crs,
            QgsProject.instance().transformContext(),
        )
        return transform.transformBoundingBox(
            business_layer.extent()
        )

    def _distance_for_authid(
        self,
        extent,
        authid,
        business_center_wgs84,
    ):
        center_x = (
            float(extent[0]) + float(extent[1])
        ) / 2.0
        center_y = (
            float(extent[2]) + float(extent[3])
        ) / 2.0

        result = self._transform_xy_to_wgs84(
            center_x,
            center_y,
            authid,
        )
        if result is None:
            return 9999.0

        lon, lat = result
        return self._haversine_km(
            lon,
            lat,
            business_center_wgs84.x(),
            business_center_wgs84.y(),
        )

    def _resolve_source_crs(
        self,
        ogr_layer,
        extent,
        business_center_wgs84,
    ):
        """
        내장 CRS가 없는 DXF를 위해 다음 두 축 순서를 모두 시험합니다.

        1. 일반 GIS 순서: X=동서(Easting), Y=남북(Northing)
        2. 국내 CAD에서 종종 쓰는 순서: X=남북, Y=동서

        온맵 XML 예시처럼 동부원점 좌표가
        E=151000, N=508000인데 DXF에 X=508000, Y=151000으로
        저장된 경우 두 번째 방식으로 정확히 판별됩니다.
        """
        embedded = self._embedded_authid(
            ogr_layer.GetSpatialRef()
        )

        candidates = []
        if embedded:
            candidates.append(embedded)

        for authid in CRS_CANDIDATES:
            if authid not in candidates:
                candidates.append(authid)

        center_x = (
            float(extent[0]) + float(extent[1])
        ) / 2.0
        center_y = (
            float(extent[2]) + float(extent[3])
        ) / 2.0

        best_authid = None
        best_distance = None
        best_swap_xy = False

        for authid in candidates:
            for swap_xy in (False, True):
                test_x = center_y if swap_xy else center_x
                test_y = center_x if swap_xy else center_y

                result = self._transform_xy_to_wgs84(
                    test_x,
                    test_y,
                    authid,
                )
                if result is None:
                    continue

                lon, lat = result

                if not (
                    123.0 <= lon <= 133.5
                    and 32.0 <= lat <= 40.5
                ):
                    continue

                distance = self._haversine_km(
                    lon,
                    lat,
                    business_center_wgs84.x(),
                    business_center_wgs84.y(),
                )

                # 정상 내장 CRS와 일반 축순서를 우선합니다.
                if authid == embedded:
                    distance *= 0.95
                if swap_xy:
                    distance += 0.01

                if (
                    best_distance is None
                    or distance < best_distance
                ):
                    best_distance = distance
                    best_authid = authid
                    best_swap_xy = swap_xy

        # 도엽 4장 정도를 고려해 허용거리를 넓히되,
        # 완전히 다른 지역 자료가 섞이는 것은 막습니다.
        if (
            best_authid is None
            or best_distance is None
            or best_distance > 200.0
        ):
            return None, 0.0, False

        return (
            best_authid,
            best_distance,
            best_swap_xy,
        )

    def _embedded_authid(self, spatial_ref):
        if spatial_ref is None:
            return None

        try:
            spatial_ref.AutoIdentifyEPSG()
        except Exception:
            pass

        authority_name = spatial_ref.GetAuthorityName(None)
        authority_code = spatial_ref.GetAuthorityCode(None)

        if authority_name and authority_code:
            return "%s:%s" % (
                authority_name.upper(),
                authority_code,
            )

        return None

    def _transform_xy_to_wgs84(
        self,
        x,
        y,
        source_authid,
    ):
        try:
            source = osr.SpatialReference()
            source.ImportFromEPSG(
                int(source_authid.split(":")[1])
            )
            target = osr.SpatialReference()
            target.ImportFromEPSG(4326)

            # GDAL 3 축 순서 차이를 방지합니다.
            if hasattr(
                source,
                "SetAxisMappingStrategy",
            ):
                source.SetAxisMappingStrategy(
                    osr.OAMS_TRADITIONAL_GIS_ORDER
                )
                target.SetAxisMappingStrategy(
                    osr.OAMS_TRADITIONAL_GIS_ORDER
                )

            transform = osr.CoordinateTransformation(
                source,
                target,
            )
            lon, lat, _ = transform.TransformPoint(
                float(x),
                float(y),
            )
            return lon, lat
        except Exception:
            return None

    def _haversine_km(
        self,
        lon1,
        lat1,
        lon2,
        lat2,
    ):
        radius = 6371.0088
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        value = (
            math.sin(delta_phi / 2.0) ** 2
            + math.cos(phi1)
            * math.cos(phi2)
            * math.sin(delta_lambda / 2.0) ** 2
        )
        return (
            2.0
            * radius
            * math.atan2(
                math.sqrt(value),
                math.sqrt(1.0 - value),
            )
        )

    def _swap_geometry_xy(
        self,
        geometry,
    ):
        """
        OGR 버전에 관계없이 geometry의 X/Y를 교환합니다.
        """
        if geometry is None:
            return

        if hasattr(geometry, "SwapXY"):
            geometry.SwapXY()
            return

        child_count = geometry.GetGeometryCount()

        if child_count > 0:
            for index in range(child_count):
                child = geometry.GetGeometryRef(
                    index
                )
                if child is not None:
                    self._swap_geometry_xy(
                        child
                    )
            return

        point_count = geometry.GetPointCount()

        for index in range(point_count):
            point = geometry.GetPoint(index)

            if len(point) >= 3:
                geometry.SetPoint(
                    index,
                    float(point[1]),
                    float(point[0]),
                    float(point[2]),
                )
            else:
                geometry.SetPoint_2D(
                    index,
                    float(point[1]),
                    float(point[0]),
                )

    def _collect_layer_candidates(
        self,
        path,
        ogr_layer,
        layer_name,
        source_authid,
        search_extent,
        swap_xy=False,
    ):
        source_ref = osr.SpatialReference()
        source_ref.ImportFromEPSG(
            int(source_authid.split(":")[1])
        )
        target_ref = osr.SpatialReference()
        target_ref.ImportFromEPSG(5179)

        if hasattr(
            source_ref,
            "SetAxisMappingStrategy",
        ):
            source_ref.SetAxisMappingStrategy(
                osr.OAMS_TRADITIONAL_GIS_ORDER
            )
            target_ref.SetAxisMappingStrategy(
                osr.OAMS_TRADITIONAL_GIS_ORDER
            )

        coordinate_transform = (
            osr.CoordinateTransformation(
                source_ref,
                target_ref,
            )
        )

        field_lookup = self._field_lookup(
            ogr_layer
        )
        elevation_field = self._find_elevation_field(
            field_lookup
        )
        cad_layer_field = self._find_first_field(
            field_lookup,
            CAD_LAYER_FIELD_CANDIDATES,
        )

        result = []
        ogr_layer.ResetReading()

        for feature in ogr_layer:
            geometry = feature.GetGeometryRef()
            if geometry is None or geometry.IsEmpty():
                continue

            elevation, z_spread, z_source = self._feature_elevation(
                feature,
                geometry,
                elevation_field,
            )

            if elevation is None:
                continue

            if not (-200.0 <= elevation <= 3000.0):
                continue

            cad_layer_name = ""
            if cad_layer_field:
                value = feature.GetField(
                    cad_layer_field
                )
                if value is not None:
                    cad_layer_name = str(value)

            transformed = geometry.Clone()

            if swap_xy:
                self._swap_geometry_xy(
                    transformed
                )

            try:
                transformed.Transform(
                    coordinate_transform
                )
            except Exception:
                continue

            envelope = transformed.GetEnvelope()
            if not self._envelope_intersects(
                envelope,
                search_extent,
            ):
                continue

            geometry_type = _flatten_ogr_geometry_type(
                transformed.GetGeometryType()
            )

            if geometry_type in (
                ogr.wkbLineString,
                ogr.wkbMultiLineString,
            ):
                length = transformed.Length()
                if length < 5.0:
                    continue
            else:
                length = 0.0

            score = self._contour_score(
                layer_name,
                cad_layer_name,
                elevation,
                z_spread,
                z_source,
                length,
                geometry_type,
            )

            result.append(
                {
                    "geometry": transformed,
                    "elevation": float(elevation),
                    "z_spread": z_spread,
                    "z_source": z_source,
                    "score": score,
                    "length": length,
                    "file": path,
                    "source_layer": layer_name,
                    "cad_layer": cad_layer_name,
                    "source_crs": source_authid,
                    "swap_xy": swap_xy,
                }
            )

        return result

    def _collect_raw_dxf_candidates(
        self,
        path,
        source_authid,
        search_extent,
    ):
        """
        OGR/QGIS 3.16이 AcDbPolyline의 고도값을 누락하는 경우를 위한
        ASCII DXF 직접 해석 fallback.

        지원:
        - LWPOLYLINE: 8=Layer, 38=Elevation, 10/20=XY, 30=Z
        - POLYLINE + VERTEX: 8=Layer, 10/20/30=XYZ
        """
        try:
            pairs = self._read_dxf_pairs(path)
        except Exception as exc:
            _log(
                self.log,
                "경고: DXF 원문 읽기 실패: %s" % exc,
            )
            return []

        entities = self._parse_dxf_entities(pairs)
        result = []

        source_ref = osr.SpatialReference()
        source_ref.ImportFromEPSG(
            int(source_authid.split(":")[1])
        )
        target_ref = osr.SpatialReference()
        target_ref.ImportFromEPSG(5179)

        if hasattr(source_ref, "SetAxisMappingStrategy"):
            source_ref.SetAxisMappingStrategy(
                osr.OAMS_TRADITIONAL_GIS_ORDER
            )
            target_ref.SetAxisMappingStrategy(
                osr.OAMS_TRADITIONAL_GIS_ORDER
            )

        transform = osr.CoordinateTransformation(
            source_ref,
            target_ref,
        )

        inspected = 0
        accepted_count = 0

        for entity in entities:
            inspected += 1
            layer_name = (
                entity.get("layer")
                or ""
            )
            points = entity.get("points") or []

            if len(points) < 2:
                continue

            elevation = entity.get("elevation")
            if elevation is None:
                z_values = [
                    point[2]
                    for point in points
                    if point[2] is not None
                    and math.isfinite(point[2])
                ]
                if z_values:
                    spread = max(z_values) - min(z_values)
                    if spread <= 0.20:
                        elevation = float(
                            np.median(z_values)
                        )

            if elevation is None:
                continue

            if not (-200.0 <= elevation <= 3000.0):
                continue

            # 국토지리정보원 등고선 계열 코드 또는 의미 있는 비영점 고도.
            layer_text = layer_name.lower()
            is_known_contour = (
                "f00171" in layer_text
                or any(
                    keyword in layer_text
                    for keyword in CONTOUR_KEYWORDS
                )
            )

            if (
                not is_known_contour
                and abs(elevation) < 0.01
            ):
                continue

            line = ogr.Geometry(
                ogr.wkbLineString
            )

            for x, y, z in points:
                try:
                    tx, ty, _ = transform.TransformPoint(
                        float(x),
                        float(y),
                        float(
                            elevation
                            if z is None
                            else z
                        ),
                    )
                except Exception:
                    continue

                line.AddPoint(
                    float(tx),
                    float(ty),
                    float(elevation),
                )

            if line.GetPointCount() < 2:
                continue

            envelope = line.GetEnvelope()
            if not self._envelope_intersects(
                envelope,
                search_extent,
            ):
                continue

            length = line.Length()
            if length < 5.0:
                continue

            score = 170 if is_known_contour else 90

            result.append(
                {
                    "geometry": line,
                    "elevation": float(elevation),
                    "z_spread": 0.0,
                    "z_source": "raw_dxf",
                    "score": score,
                    "length": length,
                    "file": path,
                    "source_layer": "DXF_ENTITIES",
                    "cad_layer": layer_name,
                    "source_crs": source_authid,
                    "swap_xy": False,
                }
            )
            accepted_count += 1

        _log(
            self.log,
            "DXF 원문 검사: %s | 객체 %s개 검사, "
            "등고선 후보 %s개"
            % (
                os.path.basename(path),
                inspected,
                accepted_count,
            ),
        )

        return result

    def _read_dxf_pairs(self, path):
        raw = Path(path).read_bytes()

        if raw.startswith(
            b"AutoCAD Binary DXF"
        ):
            raise RuntimeError(
                "Binary DXF는 직접 해석할 수 없습니다. "
                "ASCII DXF로 저장한 뒤 다시 시도하세요."
            )

        text = None
        for encoding in (
            "utf-8",
            "cp949",
            "euc-kr",
            "latin-1",
        ):
            try:
                text = raw.decode(encoding)
                break
            except Exception:
                continue

        if text is None:
            raise RuntimeError(
                "DXF 문자 인코딩을 해석하지 못했습니다."
            )

        lines = text.replace(
            "\r\n",
            "\n",
        ).replace(
            "\r",
            "\n",
        ).split("\n")

        pairs = []
        index = 0

        while index + 1 < len(lines):
            code_text = lines[index].strip()
            value = lines[index + 1].strip()
            index += 2

            try:
                code = int(code_text)
            except Exception:
                continue

            pairs.append(
                (code, value)
            )

        return pairs

    def _parse_dxf_entities(self, pairs):
        entities = []
        index = 0
        in_entities = False

        while index < len(pairs):
            code, value = pairs[index]

            if (
                code == 0
                and value == "SECTION"
                and index + 1 < len(pairs)
                and pairs[index + 1] == (2, "ENTITIES")
            ):
                in_entities = True
                index += 2
                continue

            if (
                in_entities
                and code == 0
                and value == "ENDSEC"
            ):
                break

            if not in_entities:
                index += 1
                continue

            if code == 0 and value == "LWPOLYLINE":
                entity, index = self._parse_lwpolyline(
                    pairs,
                    index + 1,
                )
                if entity:
                    entities.append(entity)
                continue

            if code == 0 and value == "POLYLINE":
                entity, index = self._parse_polyline(
                    pairs,
                    index + 1,
                )
                if entity:
                    entities.append(entity)
                continue

            index += 1

        return entities

    def _parse_lwpolyline(self, pairs, index):
        layer_name = ""
        elevation = None
        points = []
        current_x = None
        current_z = None

        while index < len(pairs):
            code, value = pairs[index]

            if code == 0:
                break

            if code == 8:
                layer_name = value
            elif code == 38:
                try:
                    elevation = float(value)
                except Exception:
                    pass
            elif code == 10:
                try:
                    current_x = float(value)
                    current_z = None
                except Exception:
                    current_x = None
            elif code == 20 and current_x is not None:
                try:
                    y = float(value)
                    points.append(
                        [
                            current_x,
                            y,
                            current_z,
                        ]
                    )
                except Exception:
                    pass
                current_x = None
            elif code == 30:
                try:
                    current_z = float(value)
                    if points:
                        points[-1][2] = current_z
                except Exception:
                    pass

            index += 1

        return (
            {
                "type": "LWPOLYLINE",
                "layer": layer_name,
                "elevation": elevation,
                "points": [
                    tuple(point)
                    for point in points
                ],
            },
            index,
        )

    def _parse_polyline(self, pairs, index):
        layer_name = ""
        header_elevation = None

        while index < len(pairs):
            code, value = pairs[index]

            if code == 0:
                break

            if code == 8:
                layer_name = value
            elif code == 30:
                try:
                    header_elevation = float(value)
                except Exception:
                    pass

            index += 1

        points = []

        while index < len(pairs):
            code, value = pairs[index]

            if code == 0 and value == "SEQEND":
                index += 1
                break

            if code == 0 and value == "VERTEX":
                vertex, index = self._parse_vertex(
                    pairs,
                    index + 1,
                )
                if vertex is not None:
                    points.append(vertex)
                continue

            if code == 0 and value not in (
                "VERTEX",
                "SEQEND",
            ):
                break

            index += 1

        return (
            {
                "type": "POLYLINE",
                "layer": layer_name,
                "elevation": header_elevation,
                "points": points,
            },
            index,
        )

    def _parse_vertex(self, pairs, index):
        x = None
        y = None
        z = None

        while index < len(pairs):
            code, value = pairs[index]

            if code == 0:
                break

            try:
                if code == 10:
                    x = float(value)
                elif code == 20:
                    y = float(value)
                elif code == 30:
                    z = float(value)
            except Exception:
                pass

            index += 1

        if x is None or y is None:
            return None, index

        return (
            (
                x,
                y,
                z,
            ),
            index,
        )

    def _field_lookup(self, ogr_layer):
        definition = ogr_layer.GetLayerDefn()
        lookup = {}

        for index in range(
            definition.GetFieldCount()
        ):
            field = definition.GetFieldDefn(index)
            lookup[
                field.GetName().strip().lower()
            ] = field.GetName()

        return lookup

    def _find_first_field(
        self,
        lookup,
        candidates,
    ):
        for candidate in candidates:
            actual = lookup.get(
                candidate.lower()
            )
            if actual:
                return actual
        return None

    def _find_elevation_field(
        self,
        lookup,
    ):
        return self._find_first_field(
            lookup,
            ELEVATION_FIELD_CANDIDATES,
        )

    def _feature_elevation(
        self,
        feature,
        geometry,
        elevation_field,
    ):
        if elevation_field:
            try:
                value = feature.GetField(
                    elevation_field
                )
                number = float(value)
                if math.isfinite(number):
                    return number, 0.0, "field"
            except Exception:
                pass

        z_values = []
        self._collect_z_values(
            geometry,
            z_values,
        )

        z_values = [
            value
            for value in z_values
            if math.isfinite(value)
        ]

        if not z_values:
            return None, None, None

        median = float(np.median(z_values))
        spread = float(
            max(z_values) - min(z_values)
        )

        # 등고선은 하나의 객체 안에서 Z가 거의 일정해야 합니다.
        tolerance = max(
            0.10,
            abs(median) * 0.0005,
        )

        if spread > tolerance:
            return None, spread, "geometry"

        return median, spread, "geometry"

    def _collect_z_values(
        self,
        geometry,
        output,
    ):
        count = geometry.GetGeometryCount()

        if count > 0:
            for index in range(count):
                child = geometry.GetGeometryRef(
                    index
                )
                if child is not None:
                    self._collect_z_values(
                        child,
                        output,
                    )
            return

        for index in range(
            geometry.GetPointCount()
        ):
            point = geometry.GetPoint(index)
            if len(point) >= 3:
                output.append(float(point[2]))

    def _contour_score(
        self,
        source_layer,
        cad_layer,
        elevation,
        z_spread,
        z_source,
        length,
        geometry_type,
    ):
        name_text = (
            "%s %s"
            % (
                source_layer,
                cad_layer,
            )
        ).lower()

        score = 0

        if z_source == "field":
            score += 120
        elif z_source == "geometry":
            score += 90
        elif z_source == "raw_dxf":
            score += 140

        if any(
            keyword in name_text
            for keyword in CONTOUR_KEYWORDS
        ):
            score += 60

        if any(
            keyword in name_text
            for keyword in EXCLUDE_KEYWORDS
        ):
            score -= 100

        if geometry_type in (
            ogr.wkbLineString,
            ogr.wkbMultiLineString,
        ):
            score += 20
        else:
            score += 5

        if length >= 20.0:
            score += 10

        # 1:5,000 등고선은 정수 또는 5/10m 간격인 경우가 많습니다.
        if abs(
            elevation - round(elevation)
        ) <= 0.05:
            score += 10

        if abs(
            elevation / 5.0
            - round(elevation / 5.0)
        ) <= 0.02:
            score += 15

        # 비등고선의 기본 Z=0을 강하게 제외합니다.
        if abs(elevation) < 0.01:
            score -= 120

        return score

    def _filter_contour_candidates(
        self,
        candidates,
    ):
        non_zero = [
            item
            for item in candidates
            if abs(item["elevation"]) >= 0.01
        ]

        if not non_zero:
            return []

        # 표고값별 객체 수를 계산합니다. 반복되는 일정 표고값은
        # 등고선일 가능성이 높습니다.
        frequency = {}
        for item in non_zero:
            key = round(
                item["elevation"],
                1,
            )
            frequency[key] = (
                frequency.get(key, 0) + 1
            )

        accepted = []

        for item in non_zero:
            elevation_key = round(
                item["elevation"],
                1,
            )
            score = item["score"]

            if frequency.get(
                elevation_key,
                0,
            ) >= 2:
                score += 20

            # 명시적 표고 필드 또는 일정한 Z를 가진 선형 중
            # 최소 점수를 충족한 객체만 사용합니다.
            if score < 70:
                continue

            item["score"] = score
            accepted.append(item)

        # 지나치게 넓은 표고 이상치를 완화합니다.
        if len(accepted) >= 20:
            values = np.array(
                [
                    item["elevation"]
                    for item in accepted
                ],
                dtype=np.float64,
            )
            lower = float(
                np.percentile(values, 0.5)
            )
            upper = float(
                np.percentile(values, 99.5)
            )

            accepted = [
                item
                for item in accepted
                if (
                    lower - 20.0
                    <= item["elevation"]
                    <= upper + 20.0
                )
            ]

        return accepted

    def _envelope_intersects(
        self,
        envelope,
        rectangle,
    ):
        min_x, max_x, min_y, max_y = envelope

        return not (
            max_x < rectangle.xMinimum()
            or min_x > rectangle.xMaximum()
            or max_y < rectangle.yMinimum()
            or min_y > rectangle.yMaximum()
        )

    def _write_outputs(
        self,
        accepted,
        point_path,
        add_contour_layer,
    ):
        driver = ogr.GetDriverByName("GPKG")
        if os.path.exists(point_path):
            driver.DeleteDataSource(
                point_path
            )

        data_source = driver.CreateDataSource(
            point_path
        )
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromEPSG(5179)

        point_layer = data_source.CreateLayer(
            "contour_points",
            spatial_ref,
            ogr.wkbPoint,
        )
        point_layer.CreateField(
            ogr.FieldDefn(
                "elev",
                ogr.OFTReal,
            )
        )

        contour_layer = None

        if add_contour_layer:
            contour_layer = QgsVectorLayer(
                "LineString?crs=EPSG:5179",
                "수치지도_등고선_자동추출",
                "memory",
            )
            provider = contour_layer.dataProvider()
            provider.addAttributes(
                [
                    QgsField(
                        "표고",
                        QVariant.Double,
                    ),
                    QgsField(
                        "원본파일",
                        QVariant.String,
                        len=200,
                    ),
                    QgsField(
                        "CAD레이어",
                        QVariant.String,
                        len=100,
                    ),
                    QgsField(
                        "원본좌표계",
                        QVariant.String,
                        len=20,
                    ),
                ]
            )
            contour_layer.updateFields()

        point_count = 0
        display_features = []

        for item in accepted:
            geometry = item["geometry"].Clone()
            geometry.FlattenTo2D()

            if _flatten_ogr_geometry_type(
                geometry.GetGeometryType()
            ) in (
                ogr.wkbLineString,
                ogr.wkbMultiLineString,
            ):
                sampled = geometry.Clone()
                try:
                    sampled.Segmentize(20.0)
                except Exception:
                    pass

                point_count += self._write_geometry_points(
                    sampled,
                    item["elevation"],
                    point_layer,
                )

                if contour_layer is not None:
                    qgs_geometry = QgsGeometry()
                    qgs_geometry.fromWkb(
                        bytes(geometry.ExportToWkb())
                    )

                    if qgs_geometry.isMultipart():
                        parts = qgs_geometry.asMultiPolyline()
                        for part in parts:
                            feature = QgsFeature(
                                contour_layer.fields()
                            )
                            feature.setGeometry(
                                QgsGeometry.fromPolylineXY(
                                    part
                                )
                            )
                            feature.setAttributes(
                                [
                                    item["elevation"],
                                    os.path.basename(
                                        item["file"]
                                    ),
                                    item["cad_layer"],
                                    item["source_crs"],
                                ]
                            )
                            display_features.append(
                                feature
                            )
                    else:
                        feature = QgsFeature(
                            contour_layer.fields()
                        )
                        feature.setGeometry(
                            qgs_geometry
                        )
                        feature.setAttributes(
                            [
                                item["elevation"],
                                os.path.basename(
                                    item["file"]
                                ),
                                item["cad_layer"],
                                item["source_crs"],
                            ]
                        )
                        display_features.append(
                            feature
                        )
            else:
                point_count += self._write_geometry_points(
                    geometry,
                    item["elevation"],
                    point_layer,
                )

        point_layer.SyncToDisk()
        data_source = None

        if point_count < 3:
            raise RuntimeError(
                "DEM 보간에 필요한 표고 샘플점이 부족합니다."
            )

        if contour_layer is not None:
            contour_layer.dataProvider().addFeatures(
                display_features
            )
            contour_layer.updateExtents()
            QgsProject.instance().addMapLayer(
                contour_layer
            )

        return contour_layer, point_count

    def _write_geometry_points(
        self,
        geometry,
        elevation,
        point_layer,
    ):
        count = geometry.GetGeometryCount()

        if count > 0:
            total = 0
            for index in range(count):
                child = geometry.GetGeometryRef(
                    index
                )
                if child is not None:
                    total += self._write_geometry_points(
                        child,
                        elevation,
                        point_layer,
                    )
            return total

        written = 0

        for index in range(
            geometry.GetPointCount()
        ):
            x, y, _ = geometry.GetPoint(
                index
            )

            feature = ogr.Feature(
                point_layer.GetLayerDefn()
            )
            point = ogr.Geometry(
                ogr.wkbPoint
            )
            point.AddPoint(
                float(x),
                float(y),
            )
            feature.SetGeometry(point)
            feature.SetField(
                "elev",
                float(elevation),
            )
            point_layer.CreateFeature(
                feature
            )
            feature = None
            written += 1

        return written
