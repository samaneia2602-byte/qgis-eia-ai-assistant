# -*- coding: utf-8 -*-

import hashlib
import os
import tempfile

from qgis.PyQt.QtWidgets import (
    QFileDialog,
    QMessageBox,
)
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)

try:
    from osgeo import gdal, ogr, osr
except Exception:
    gdal = None
    ogr = None
    osr = None


class NgiiManager:
    """
    국토지리정보원 온맵 GeoPDF 로더.

    개선 기능
    1. 여러 GeoPDF 동시 선택
    2. GeoPDF의 NEATLINE을 이용해 지도 본문만 GeoTIFF로 추출
    3. 포함 좌표계를 우선 사용하고, 잘못된 EPSG:3857 등은
       현재 사업지역 위치와 비교하여 한국 좌표계 후보 중 자동 보정
    4. 모든 온맵을 하나의 그룹 아래에 정리
    """

    GROUP_NAME = "국토지리정보원 온맵"
    CRS_CANDIDATES = (
        "EPSG:5179",
        "EPSG:5186",
        "EPSG:5187",
        "EPSG:5185",
        "EPSG:5181",
        "EPSG:4326",
        "EPSG:3857",
    )

    def __init__(self, iface, log):
        self.iface = iface
        self.log = log

    def load_onmap(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self.iface.mainWindow(),
            "국토지리정보원 온맵 GeoPDF 선택",
            "",
            "GeoPDF/PDF 파일 (*.pdf *.PDF);;모든 파일 (*.*)",
        )

        if not file_paths:
            self.log("온맵 불러오기가 취소되었습니다.")
            return []

        crop_answer = QMessageBox.question(
            self.iface.mainWindow(),
            "온맵 지도부분만 추출",
            "PDF의 제목란·범례·여백을 제외하고\n"
            "좌표가 지정된 지도 부분만 추출할까요?\n\n"
            "GeoPDF에 지도 경계(NEATLINE)가 들어 있으면 "
            "자동으로 잘라냅니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        crop_map_only = crop_answer == QMessageBox.Yes

        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = root.findGroup(self.GROUP_NAME)
        if group is None:
            group = root.insertGroup(
                len(root.children()),
                self.GROUP_NAME,
            )

        loaded_layers = []
        failed_files = []

        for index, file_path in enumerate(file_paths, start=1):
            if not os.path.isfile(file_path):
                failed_files.append(file_path)
                self.log(
                    "[%s/%s] 파일을 찾을 수 없습니다: %s"
                    % (index, len(file_paths), file_path)
                )
                continue

            self.log(
                "[%s/%s] 온맵 처리 시작: %s"
                % (
                    index,
                    len(file_paths),
                    os.path.basename(file_path),
                )
            )

            try:
                prepared_path, embedded_authid, cropped = (
                    self._prepare_geopdf(
                        file_path,
                        crop_map_only,
                    )
                )
            except Exception as exc:
                prepared_path = file_path
                embedded_authid = ""
                cropped = False
                self.log(
                    "경고: 지도부분 추출에 실패하여 "
                    "원본 PDF를 직접 엽니다: %s" % exc
                )

            layer_name = os.path.splitext(
                os.path.basename(file_path)
            )[0]

            layer = QgsRasterLayer(
                prepared_path,
                layer_name,
                "gdal",
            )

            if not layer.isValid():
                failed_files.append(file_path)
                self.log(
                    "오류: GeoPDF를 공간 레이어로 "
                    "불러오지 못했습니다: %s"
                    % file_path
                )
                continue

            corrected_crs = self._correct_layer_crs(
                layer,
                embedded_authid,
            )

            project.addMapLayer(layer, False)
            group.insertLayer(
                len(group.children()),
                layer,
            )
            loaded_layers.append(layer)

            self.log(
                "온맵 추가 완료: %s | CRS=%s | 지도부분 추출=%s"
                % (
                    layer_name,
                    corrected_crs or "미지정",
                    "예" if cropped else "아니오",
                )
            )

        if loaded_layers:
            self._zoom_to_layers(loaded_layers)
            self.log(
                "온맵 %s개를 불러왔습니다."
                % len(loaded_layers)
            )

        if failed_files:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "일부 온맵 불러오기 실패",
                "선택한 %s개 파일 중 %s개를 불러왔고, "
                "%s개는 실패했습니다.\n\n"
                "실패한 파일은 플러그인 로그를 확인하세요."
                % (
                    len(file_paths),
                    len(loaded_layers),
                    len(failed_files),
                ),
            )

        return loaded_layers

    def _prepare_geopdf(
        self,
        file_path,
        crop_map_only,
    ):
        """
        GeoPDF를 임시 GeoTIFF로 변환합니다.

        NEATLINE이 있으면 지도 본문만 잘라내며,
        없으면 전체 페이지를 GeoTIFF로 변환합니다.
        """
        if gdal is None:
            raise RuntimeError(
                "QGIS Python에서 GDAL 모듈을 사용할 수 없습니다."
            )

        dataset = gdal.OpenEx(
            file_path,
            gdal.OF_RASTER,
        )
        if dataset is None:
            raise RuntimeError(
                "GDAL이 PDF를 열지 못했습니다."
            )

        projection_wkt = dataset.GetProjection() or ""
        embedded_authid = self._authid_from_wkt(
            projection_wkt
        )
        neatline = dataset.GetMetadataItem("NEATLINE")

        cache_dir = os.path.join(
            tempfile.gettempdir(),
            "qgis_eia_ai_assistant",
            "onmap",
        )
        os.makedirs(cache_dir, exist_ok=True)

        fingerprint = hashlib.sha1(
            (
                os.path.abspath(file_path)
                + str(os.path.getmtime(file_path))
                + str(crop_map_only)
            ).encode("utf-8", errors="replace")
        ).hexdigest()[:12]

        output_path = os.path.join(
            cache_dir,
            "%s_%s.tif"
            % (
                os.path.splitext(
                    os.path.basename(file_path)
                )[0],
                fingerprint,
            ),
        )

        if os.path.exists(output_path):
            return (
                output_path,
                embedded_authid,
                bool(crop_map_only and neatline),
            )

        creation_options = [
            "TILED=YES",
            "COMPRESS=DEFLATE",
            "BIGTIFF=IF_SAFER",
        ]

        cropped = False

        if (
            crop_map_only
            and neatline
            and ogr is not None
            and osr is not None
        ):
            cutline_path = self._create_cutline(
                neatline,
                projection_wkt,
                cache_dir,
                fingerprint,
            )

            warp_options = gdal.WarpOptions(
                format="GTiff",
                cutlineDSName=cutline_path,
                cropToCutline=True,
                dstSRS=projection_wkt or None,
                multithread=True,
                creationOptions=creation_options,
            )
            result = gdal.Warp(
                output_path,
                dataset,
                options=warp_options,
            )
            cropped = result is not None

            if result is not None:
                result = None

        if not cropped:
            translate_options = gdal.TranslateOptions(
                format="GTiff",
                creationOptions=creation_options,
            )
            result = gdal.Translate(
                output_path,
                dataset,
                options=translate_options,
            )

            if result is None:
                raise RuntimeError(
                    "GeoPDF를 GeoTIFF로 변환하지 못했습니다."
                )
            result = None

            if crop_map_only and not neatline:
                self.log(
                    "참고: 이 PDF에는 NEATLINE 정보가 없어 "
                    "지도부분 자동 자르기를 적용하지 못했습니다."
                )

        dataset = None

        return (
            output_path,
            embedded_authid,
            cropped,
        )

    def _create_cutline(
        self,
        neatline_wkt,
        projection_wkt,
        cache_dir,
        fingerprint,
    ):
        cutline_path = os.path.join(
            cache_dir,
            "onmap_cutline_%s.geojson"
            % fingerprint,
        )

        driver = ogr.GetDriverByName("GeoJSON")
        if os.path.exists(cutline_path):
            driver.DeleteDataSource(cutline_path)

        data_source = driver.CreateDataSource(
            cutline_path
        )

        spatial_ref = None
        if projection_wkt:
            spatial_ref = osr.SpatialReference()
            spatial_ref.ImportFromWkt(
                projection_wkt
            )

        layer = data_source.CreateLayer(
            "cutline",
            spatial_ref,
            ogr.wkbPolygon,
        )

        geometry = ogr.CreateGeometryFromWkt(
            neatline_wkt
        )
        if geometry is None:
            data_source = None
            raise RuntimeError(
                "GeoPDF NEATLINE을 해석하지 못했습니다."
            )

        feature = ogr.Feature(
            layer.GetLayerDefn()
        )
        feature.SetGeometry(geometry)
        layer.CreateFeature(feature)

        feature = None
        data_source = None
        return cutline_path

    def _authid_from_wkt(self, wkt):
        if not wkt:
            return ""

        crs = QgsCoordinateReferenceSystem()
        if crs.createFromWkt(wkt):
            return crs.authid()
        return ""

    def _correct_layer_crs(
        self,
        layer,
        embedded_authid,
    ):
        current_authid = (
            layer.crs().authid()
            if layer.crs().isValid()
            else ""
        )

        extent = layer.extent()
        suspicious = self._is_suspicious_crs(
            current_authid,
            extent,
        )

        if not suspicious:
            return current_authid

        reference_point = self._reference_point_wgs84()
        best_authid = self._best_candidate_crs(
            extent,
            reference_point,
        )

        if not best_authid:
            best_authid = (
                embedded_authid
                if embedded_authid
                else "EPSG:5179"
            )

        corrected = QgsCoordinateReferenceSystem(
            best_authid
        )
        if corrected.isValid():
            self.log(
                "온맵 좌표계 자동 보정: %s → %s"
                % (
                    current_authid or "미지정",
                    best_authid,
                )
            )
            layer.setCrs(corrected)
            return best_authid

        return current_authid

    def _is_suspicious_crs(
        self,
        authid,
        extent,
    ):
        if not authid:
            return True

        # 한국의 Web Mercator 좌표는 대략
        # X=13~15백만, Y=3~5백만 범위입니다.
        if authid == "EPSG:3857":
            center = extent.center()
            if (
                abs(center.x()) < 5000000
                or abs(center.y()) < 2000000
            ):
                return True

        return False

    def _reference_point_wgs84(self):
        reference_layer = self.iface.activeLayer()
        if (
            reference_layer is None
            or not reference_layer.isValid()
            or not reference_layer.crs().isValid()
        ):
            return None

        try:
            center = reference_layer.extent().center()
            transform = QgsCoordinateTransform(
                reference_layer.crs(),
                QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance().transformContext(),
            )
            return transform.transform(center)
        except Exception:
            return None

    def _best_candidate_crs(
        self,
        extent,
        reference_point,
    ):
        if reference_point is None:
            return None

        center = extent.center()
        best_authid = None
        best_distance = None

        for authid in self.CRS_CANDIDATES:
            candidate = QgsCoordinateReferenceSystem(
                authid
            )
            if not candidate.isValid():
                continue

            try:
                transform = QgsCoordinateTransform(
                    candidate,
                    QgsCoordinateReferenceSystem(
                        "EPSG:4326"
                    ),
                    QgsProject.instance().transformContext(),
                )
                transformed = transform.transform(
                    center
                )
            except Exception:
                continue

            # 한국 영역 밖 후보는 제외합니다.
            if not (
                123.0 <= transformed.x() <= 133.5
                and 32.0 <= transformed.y() <= 40.5
            ):
                continue

            distance = (
                abs(
                    transformed.x()
                    - reference_point.x()
                )
                + abs(
                    transformed.y()
                    - reference_point.y()
                )
            )

            if (
                best_distance is None
                or distance < best_distance
            ):
                best_distance = distance
                best_authid = authid

        return best_authid

    def _zoom_to_layers(self, layers):
        combined = None

        for layer in layers:
            try:
                layer_extent = layer.extent()

                if layer.crs() != self.iface.mapCanvas().mapSettings().destinationCrs():
                    transform = QgsCoordinateTransform(
                        layer.crs(),
                        self.iface.mapCanvas().mapSettings().destinationCrs(),
                        QgsProject.instance().transformContext(),
                    )
                    layer_extent = transform.transformBoundingBox(
                        layer_extent
                    )

                if combined is None:
                    combined = QgsRectangle(
                        layer_extent
                    )
                else:
                    combined.combineExtentWith(
                        layer_extent
                    )
            except Exception as exc:
                self.log(
                    "경고: 온맵 화면 범위 계산 실패: %s"
                    % exc
                )

        if combined and not combined.isEmpty():
            self.iface.mapCanvas().setExtent(
                combined
            )
            self.iface.mapCanvas().refresh()
            self.log(
                "선택한 온맵 전체 범위로 이동했습니다."
            )
