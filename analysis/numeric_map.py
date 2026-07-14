# -*- coding: utf-8 -*-

import math
import os
import re
import tempfile

import numpy as np
from osgeo import ogr, osr

from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QFileDialog
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
                geometry_type = ogr.wkbFlatten(
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

                source_authid, distance_km = self._resolve_source_crs(
                    ogr_layer,
                    extent,
                    business_center_wgs84,
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
                    "좌표계 판별: %s / %s → %s "
                    "(사업지역 중심과 약 %.2fkm)"
                    % (
                        os.path.basename(path),
                        layer_name,
                        source_authid,
                        distance_km,
                    ),
                )

                layer_candidates = self._collect_layer_candidates(
                    path,
                    ogr_layer,
                    layer_name,
                    source_authid,
                    search_extent,
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
                    }
                )

            dataset = None

        if not candidates:
            raise RuntimeError(
                "사업지역 주변에서 일정한 Z값을 가진 등고선이나 "
                "표고 필드를 찾지 못했습니다."
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

    def _resolve_source_crs(
        self,
        ogr_layer,
        extent,
        business_center_wgs84,
    ):
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

        for authid in candidates:
            result = self._transform_xy_to_wgs84(
                center_x,
                center_y,
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

            # 내장 CRS는 정상 위치일 때 약간 우선합니다.
            if authid == embedded:
                distance *= 0.95

            if (
                best_distance is None
                or distance < best_distance
            ):
                best_distance = distance
                best_authid = authid

        # 다른 도엽이 섞여 있을 수 있으므로 100km까지 허용합니다.
        if (
            best_authid is None
            or best_distance is None
            or best_distance > 100.0
        ):
            return None, 0.0

        return best_authid, best_distance

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

    def _collect_layer_candidates(
        self,
        path,
        ogr_layer,
        layer_name,
        source_authid,
        search_extent,
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

            geometry_type = ogr.wkbFlatten(
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
                }
            )

        return result

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

            if ogr.wkbFlatten(
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
