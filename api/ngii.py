# -*- coding: utf-8 -*-

import hashlib
import os
import tempfile
import traceback

from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsTask,
)

try:
    from osgeo import gdal, ogr, osr
except Exception:
    gdal = None
    ogr = None
    osr = None


class OnMapPrepareTask(QgsTask):
    """
    GeoPDF 변환/자르기를 QGIS 메인 화면이 아닌 백그라운드에서 실행합니다.
    """

    def __init__(
        self,
        manager,
        file_paths,
        crop_map_only,
    ):
        super().__init__(
            "국토지리정보원 온맵 준비",
            QgsTask.CanCancel,
        )
        self.manager = manager
        self.file_paths = list(file_paths)
        self.crop_map_only = bool(crop_map_only)
        self.results = []
        self.error_text = ""

    def run(self):
        try:
            total = max(len(self.file_paths), 1)

            for index, file_path in enumerate(
                self.file_paths,
                start=1,
            ):
                if self.isCanceled():
                    return False

                self.setProgress(
                    (index - 1) / float(total) * 100.0
                )

                result = {
                    "source_path": file_path,
                    "prepared_path": file_path,
                    "embedded_authid": "",
                    "projection_wkt": "",
                    "geotransform": None,
                    "cropped": False,
                    "error": "",
                }

                try:
                    (
                        prepared_path,
                        embedded_authid,
                        projection_wkt,
                        geotransform,
                        cropped,
                    ) = self.manager._prepare_geopdf_worker(
                        file_path,
                        self.crop_map_only,
                    )

                    result.update(
                        {
                            "prepared_path": prepared_path,
                            "embedded_authid": embedded_authid,
                            "projection_wkt": projection_wkt,
                            "geotransform": geotransform,
                            "cropped": cropped,
                        }
                    )
                except Exception as exc:
                    # 변환 실패 시 원본 PDF 직접 열기를 시도하도록 결과에 남깁니다.
                    result["error"] = str(exc)

                self.results.append(result)
                self.setProgress(
                    index / float(total) * 100.0
                )

            return True

        except Exception:
            self.error_text = traceback.format_exc()
            return False

    def finished(self, success):
        self.manager._on_prepare_finished(
            self,
            success,
        )


class NgiiManager:
    """
    국토지리정보원 온맵 GeoPDF 로더.

    핵심 개선:
    - 여러 파일 동시 선택
    - GeoPDF 변환/NEATLINE 자르기를 QgsTask 백그라운드에서 실행
    - QGIS '응답 없음' 방지
    - 작업 완료 후 메인 스레드에서 레이어 추가
    """

    GROUP_NAME = "국토지리정보원 온맵"

    # Acrobat에서 기본적으로 끄는 온맵 선택 레이어.
    # GDAL PDF 드라이버의 LAYERS_OFF 옵션으로 동일하게 처리합니다.
    DEFAULT_PDF_LAYERS_OFF = (
        "기본",
        "항공영상",
        "경계",
        "주기",
        "도곽",
    )

    def __init__(self, iface, log):
        self.iface = iface
        self.log = log
        self._tasks = []

    def load_onmap(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self.iface.mainWindow(),
            "국토지리정보원 온맵 GeoPDF 선택",
            "",
            "GeoPDF/PDF 파일 (*.pdf *.PDF);;모든 파일 (*.*)",
        )

        if not file_paths:
            self.log("온맵 불러오기가 취소되었습니다.")
            return None

        crop_answer = QMessageBox.question(
            self.iface.mainWindow(),
            "온맵 지도부분만 추출",
            "PDF의 제목란·범례·여백을 제외하고\n"
            "좌표가 지정된 지도 부분만 추출할까요?\n\n"
            "파일이 크면 처리에 시간이 걸리지만 "
            "QGIS 화면은 멈추지 않습니다.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )

        crop_map_only = (
            crop_answer == QMessageBox.Yes
        )

        self.log(
            "온맵 PDF 레이어 숨김: 기본, 항공영상, 경계, 주기, 도곽"
        )

        if gdal is None:
            self.log(
                "경고: GDAL Python 모듈을 찾지 못해 "
                "원본 GeoPDF를 직접 불러옵니다."
            )
            return self._add_layers_from_results(
                [
                    {
                        "source_path": path,
                        "prepared_path": path,
                        "embedded_authid": "",
                        "projection_wkt": "",
                        "geotransform": None,
                        "cropped": False,
                        "error": "",
                    }
                    for path in file_paths
                ]
            )

        task = OnMapPrepareTask(
            self,
            file_paths,
            crop_map_only,
        )
        self._tasks.append(task)

        QgsApplication.taskManager().addTask(task)

        self.log(
            "온맵 %s개를 백그라운드에서 준비합니다."
            % len(file_paths)
        )
        self.log(
            "오른쪽 아래 작업 진행률에서 상태를 확인할 수 있습니다."
        )

        return task

    def _on_prepare_finished(
        self,
        task,
        success,
    ):
        try:
            if task in self._tasks:
                self._tasks.remove(task)

            if task.isCanceled():
                self.log(
                    "온맵 불러오기 작업이 취소되었습니다."
                )
                return

            if not success:
                self.log(
                    "오류: 온맵 백그라운드 작업에 실패했습니다."
                )
                if task.error_text:
                    self.log(task.error_text)
                return

            self._add_layers_from_results(
                task.results
            )

        except Exception as exc:
            self.log(
                "오류: 온맵 레이어 추가 중 문제가 발생했습니다: %s"
                % exc
            )

    def _add_layers_from_results(
        self,
        results,
    ):
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        group = root.findGroup(
            self.GROUP_NAME
        )
        if group is None:
            group = root.insertGroup(
                len(root.children()),
                self.GROUP_NAME,
            )

        loaded_layers = []
        failed_files = []

        for result in results:
            source_path = result["source_path"]
            prepared_path = result["prepared_path"]

            if result.get("error"):
                self.log(
                    "참고: 지도부분 추출에 실패하여 "
                    "원본 PDF를 직접 엽니다: %s"
                    % result["error"]
                )

            layer_name = os.path.splitext(
                os.path.basename(source_path)
            )[0]

            layer = QgsRasterLayer(
                prepared_path,
                layer_name,
                "gdal",
            )

            if not layer.isValid():
                # 변환본이 잘못되었을 때 원본을 한 번 더 시도합니다.
                if prepared_path != source_path:
                    layer = QgsRasterLayer(
                        source_path,
                        layer_name,
                        "gdal",
                    )

            if not layer.isValid():
                failed_files.append(
                    source_path
                )
                self.log(
                    "오류: GeoPDF를 공간 레이어로 "
                    "불러오지 못했습니다: %s"
                    % source_path
                )
                continue

            corrected_crs = self._apply_exact_georeferencing(
                layer,
                result.get("projection_wkt", ""),
                result.get("embedded_authid", ""),
            )

            project.addMapLayer(
                layer,
                False,
            )
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
                    (
                        "예"
                        if result.get("cropped")
                        else "아니오"
                    ),
                )
            )

        if loaded_layers:
            self._zoom_to_layers(
                loaded_layers
            )
            self.log(
                "온맵 %s개를 불러왔습니다."
                % len(loaded_layers)
            )

        if failed_files:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "일부 온맵 불러오기 실패",
                "%s개 파일을 불러오지 못했습니다.\n"
                "대화창 로그에서 실패한 파일을 확인하세요."
                % len(failed_files),
            )

        return loaded_layers

    def _prepare_geopdf_worker(
        self,
        file_path,
        crop_map_only,
    ):
        if not os.path.isfile(file_path):
            raise RuntimeError(
                "파일을 찾을 수 없습니다."
            )

        open_options = [
            "LAYERS_OFF=%s"
            % ",".join(self.DEFAULT_PDF_LAYERS_OFF),
            "RENDERING_OPTIONS=RASTER,VECTOR,TEXT",
        ]

        dataset = gdal.OpenEx(
            file_path,
            gdal.OF_RASTER,
            open_options=open_options,
        )
        if dataset is None:
            raise RuntimeError(
                "GDAL이 PDF를 열지 못했습니다."
            )

        projection_wkt = dataset.GetProjection() or ""
        geotransform = dataset.GetGeoTransform(
            can_return_null=True
        )
        embedded_authid = self._authid_from_wkt(
            projection_wkt
        )
        neatline = dataset.GetMetadataItem("NEATLINE")

        layer_metadata = dataset.GetMetadata("LAYERS") or {}
        if layer_metadata:
            available_layers = []
            for key in sorted(layer_metadata):
                if key.endswith("_NAME"):
                    available_layers.append(
                        layer_metadata[key]
                    )
            if available_layers:
                self.log(
                    "GeoPDF 포함 레이어: %s"
                    % ", ".join(available_layers)
                )

        cache_dir = os.path.join(
            tempfile.gettempdir(),
            "qgis_eia_ai_assistant",
            "onmap",
        )
        os.makedirs(
            cache_dir,
            exist_ok=True,
        )

        fingerprint = hashlib.sha1(
            (
                os.path.abspath(file_path)
                + str(
                    os.path.getmtime(
                        file_path
                    )
                )
                + str(crop_map_only)
            ).encode(
                "utf-8",
                errors="replace",
            )
        ).hexdigest()[:12]

        output_path = os.path.join(
            cache_dir,
            "%s_%s.tif"
            % (
                os.path.splitext(
                    os.path.basename(
                        file_path
                    )
                )[0],
                fingerprint,
            ),
        )

        if os.path.exists(output_path):
            dataset = None
            return (
                output_path,
                embedded_authid,
                projection_wkt,
                geotransform,
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
            cutline_path = (
                self._create_cutline_worker(
                    neatline,
                    projection_wkt,
                    cache_dir,
                    fingerprint,
                )
            )

            warp_options = gdal.WarpOptions(
                format="GTiff",
                cutlineDSName=cutline_path,
                cropToCutline=True,
                multithread=True,
                warpOptions=[
                    "NUM_THREADS=ALL_CPUS"
                ],
                creationOptions=(
                    creation_options
                ),
            )

            result = gdal.Warp(
                output_path,
                dataset,
                options=warp_options,
            )

            cropped = (
                result is not None
            )
            result = None

        if not cropped:
            translate_options = (
                gdal.TranslateOptions(
                    format="GTiff",
                    creationOptions=(
                        creation_options
                    ),
                )
            )
            result = gdal.Translate(
                output_path,
                dataset,
                options=translate_options,
            )

            if result is None:
                dataset = None
                raise RuntimeError(
                    "GeoPDF를 GeoTIFF로 "
                    "변환하지 못했습니다."
                )

            result = None

        # 변환 결과의 좌표정보가 실제로 보존되었는지 검사합니다.
        output_dataset = gdal.OpenEx(
            output_path,
            gdal.OF_RASTER,
        )
        if output_dataset is None:
            dataset = None
            raise RuntimeError(
                "변환된 GeoTIFF를 다시 열지 못했습니다."
            )

        output_projection = (
            output_dataset.GetProjection()
            or ""
        )
        output_geotransform = (
            output_dataset.GetGeoTransform(
                can_return_null=True
            )
        )

        if not output_projection and projection_wkt:
            output_dataset.SetProjection(
                projection_wkt
            )

        if (
            output_geotransform is None
            and geotransform is not None
        ):
            output_dataset.SetGeoTransform(
                geotransform
            )

        output_dataset = None
        dataset = None

        return (
            output_path,
            embedded_authid,
            projection_wkt,
            geotransform,
            cropped,
        )

    def _create_cutline_worker(
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

        driver = ogr.GetDriverByName(
            "GeoJSON"
        )

        if os.path.exists(
            cutline_path
        ):
            driver.DeleteDataSource(
                cutline_path
            )

        data_source = (
            driver.CreateDataSource(
                cutline_path
            )
        )

        spatial_ref = None
        if projection_wkt:
            spatial_ref = (
                osr.SpatialReference()
            )
            spatial_ref.ImportFromWkt(
                projection_wkt
            )

        cutline_layer = (
            data_source.CreateLayer(
                "cutline",
                spatial_ref,
                ogr.wkbPolygon,
            )
        )

        geometry = (
            ogr.CreateGeometryFromWkt(
                neatline_wkt
            )
        )
        if geometry is None:
            data_source = None
            raise RuntimeError(
                "GeoPDF NEATLINE을 "
                "해석하지 못했습니다."
            )

        feature = ogr.Feature(
            cutline_layer.GetLayerDefn()
        )
        feature.SetGeometry(
            geometry
        )
        cutline_layer.CreateFeature(
            feature
        )

        feature = None
        data_source = None

        return cutline_path

    def _authid_from_wkt(
        self,
        wkt,
    ):
        if not wkt:
            return ""

        crs = QgsCoordinateReferenceSystem()
        if crs.createFromWkt(wkt):
            return crs.authid()

        return ""

    def _apply_exact_georeferencing(
        self,
        layer,
        projection_wkt,
        embedded_authid,
    ):
        """
        GeoPDF/GDAL이 제공한 원본 좌표계 정의를 그대로 적용합니다.

        이전 버전처럼 EPSG 후보를 추정하여 강제로 바꾸지 않습니다.
        CRS 추정은 좌표가 맞는 자료까지 틀어지게 할 수 있으므로,
        원본 WKT → 원본 EPSG → 레이어 자체 CRS 순서로만 사용합니다.
        """
        exact_crs = QgsCoordinateReferenceSystem()

        if projection_wkt:
            try:
                exact_crs.createFromWkt(
                    projection_wkt
                )
            except Exception:
                pass

        if (
            not exact_crs.isValid()
            and embedded_authid
        ):
            exact_crs = QgsCoordinateReferenceSystem(
                embedded_authid
            )

        if exact_crs.isValid():
            current_authid = (
                layer.crs().authid()
                if layer.crs().isValid()
                else "미지정"
            )

            layer.setCrs(exact_crs)

            exact_name = (
                exact_crs.authid()
                or exact_crs.description()
                or "원본 WKT 좌표계"
            )

            if current_authid != exact_crs.authid():
                self.log(
                    "온맵 원본 좌표계 적용: %s → %s"
                    % (
                        current_authid,
                        exact_name,
                    )
                )

            return exact_name

        if layer.crs().isValid():
            return (
                layer.crs().authid()
                or layer.crs().description()
            )

        self.log(
            "경고: GeoPDF에서 유효한 좌표계를 읽지 못했습니다."
        )
        return "미지정"

    def _zoom_to_layers(
        self,
        layers,
    ):
        combined = None
        canvas_crs = (
            self.iface.mapCanvas()
            .mapSettings()
            .destinationCrs()
        )

        for layer in layers:
            try:
                layer_extent = (
                    layer.extent()
                )

                if (
                    layer.crs().isValid()
                    and layer.crs()
                    != canvas_crs
                ):
                    transform = (
                        QgsCoordinateTransform(
                            layer.crs(),
                            canvas_crs,
                            QgsProject.instance()
                            .transformContext(),
                        )
                    )
                    layer_extent = (
                        transform
                        .transformBoundingBox(
                            layer_extent
                        )
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
                    "경고: 온맵 화면 범위 "
                    "계산 실패: %s"
                    % exc
                )

        if (
            combined is not None
            and not combined.isEmpty()
        ):
            self.iface.mapCanvas().setExtent(
                combined
            )
            self.iface.mapCanvas().refresh()
            self.log(
                "선택한 온맵 전체 범위로 이동했습니다."
            )
