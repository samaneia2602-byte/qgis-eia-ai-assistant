# -*- coding: utf-8 -*-

import hashlib
import os
import tempfile
import traceback
import xml.etree.ElementTree as ET

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
    from osgeo import gdal, osr
except Exception:
    gdal = None
    osr = None


class OnMapXmlPrepareTask(QgsTask):
    def __init__(self, manager, pdf_paths):
        super().__init__(
            "국토지리정보원 온맵 XML 지리참조",
            QgsTask.CanCancel,
        )
        self.manager = manager
        self.pdf_paths = list(pdf_paths)
        self.results = []
        self.error_text = ""

    def run(self):
        try:
            total = max(len(self.pdf_paths), 1)

            for index, pdf_path in enumerate(
                self.pdf_paths,
                start=1,
            ):
                if self.isCanceled():
                    return False

                self.setProgress(
                    (index - 1) / float(total) * 100.0
                )

                result = {
                    "pdf_path": pdf_path,
                    "output_path": "",
                    "crs_authid": "",
                    "error": "",
                    "messages": [],
                }

                try:
                    result.update(
                        self.manager._prepare_one(
                            pdf_path
                        )
                    )
                except Exception as exc:
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
        self.manager._finished(self, success)


class NgiiManager:
    """
    국토지리정보원 온맵 전용 로더.

    PDF와 같은 폴더에 있는 XML 메타데이터에서
    도곽 좌표와 좌표계를 읽고, PDF의 지도 본문만 잘라
    정확한 GeoTIFF로 생성합니다.

    현재 국토지리정보원 1:5,000 온맵 표준 지면 배치를 기준으로
    지도 본문 비율을 사용합니다.
    """

    GROUP_NAME = "국토지리정보원 온맵"

    # 분석한 국토지리정보원 1:5,000 온맵 표준 지면에서
    # 실제 지도 본문이 차지하는 정규화 좌표.
    # PDF 좌상단 기준:
    # x=84.104~1493.931 / 1559
    # y=104.943~1795.236 / 2183
    MAP_LEFT_RATIO = 84.10423278808594 / 1559.0
    MAP_TOP_RATIO = 104.94342041015625 / 2183.0
    MAP_RIGHT_RATIO = 1493.931396484375 / 1559.0
    MAP_BOTTOM_RATIO = 1795.236083984375 / 2183.0

    PDF_LAYERS_OFF = (
        "기본",
        "항공영상",
        "경계",
        "주기",
        "도곽",
    )

    ORIGIN_TO_EPSG = {
        "서부": "EPSG:5185",
        "중부": "EPSG:5186",
        "동부": "EPSG:5187",
        "동해": "EPSG:5188",
        "동해원점": "EPSG:5188",
    }

    def __init__(self, iface, log):
        self.iface = iface
        self.log = log
        self._tasks = []

    def load_onmap(self):
        pdf_paths, _ = QFileDialog.getOpenFileNames(
            self.iface.mainWindow(),
            "국토지리정보원 온맵 PDF 선택",
            "",
            "온맵 PDF (*.pdf *.PDF);;모든 파일 (*.*)",
        )

        if not pdf_paths:
            self.log("온맵 불러오기가 취소되었습니다.")
            return None

        if gdal is None:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "온맵 불러오기 실패",
                "QGIS의 GDAL Python 모듈을 사용할 수 없습니다.",
            )
            return None

        missing_xml = [
            path
            for path in pdf_paths
            if not self._find_xml(path)
        ]

        if missing_xml:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "온맵 XML 필요",
                "선택한 PDF 중 같은 이름의 XML 메타데이터를 "
                "찾지 못한 파일이 있습니다.\n\n"
                "PDF와 XML을 같은 폴더에 두고 다시 실행하세요.",
            )

        task = OnMapXmlPrepareTask(
            self,
            pdf_paths,
        )
        self._tasks.append(task)
        QgsApplication.taskManager().addTask(task)

        self.log(
            "온맵 %s개를 XML 도곽 좌표로 지리참조합니다."
            % len(pdf_paths)
        )
        return task

    def _finished(self, task, success):
        if task in self._tasks:
            self._tasks.remove(task)

        if task.isCanceled():
            self.log("온맵 불러오기가 취소되었습니다.")
            return

        if not success:
            self.log("오류: 온맵 준비 작업에 실패했습니다.")
            if task.error_text:
                self.log(task.error_text)
            return

        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group = root.findGroup(self.GROUP_NAME)

        if group is None:
            group = root.insertGroup(
                len(root.children()),
                self.GROUP_NAME,
            )

        loaded = []
        failed = []

        for result in task.results:
            for message in result.get("messages", []):
                self.log(message)

            if result.get("error"):
                failed.append(result["pdf_path"])
                self.log(
                    "오류: %s"
                    % result["error"]
                )
                continue

            layer_name = os.path.splitext(
                os.path.basename(
                    result["pdf_path"]
                )
            )[0]

            layer = QgsRasterLayer(
                result["output_path"],
                layer_name,
                "gdal",
            )

            if not layer.isValid():
                failed.append(result["pdf_path"])
                self.log(
                    "오류: 생성된 온맵 GeoTIFF를 열지 못했습니다."
                )
                continue

            expected_crs = QgsCoordinateReferenceSystem(
                result["crs_authid"]
            )
            if expected_crs.isValid():
                layer.setCrs(expected_crs)

            if not self._is_korea_extent(layer):
                failed.append(result["pdf_path"])
                self.log(
                    "오류: 지리참조 결과가 대한민국 영역에 "
                    "들어오지 않아 추가하지 않았습니다."
                )
                continue

            project.addMapLayer(layer, False)
            group.insertLayer(
                len(group.children()),
                layer,
            )
            loaded.append(layer)

            self.log(
                "온맵 추가 완료: %s | %s"
                % (
                    layer_name,
                    result["crs_authid"],
                )
            )

        if loaded:
            self._zoom_to_layers(loaded)
            self.log(
                "온맵 %s개를 정상적으로 불러왔습니다."
                % len(loaded)
            )

        if failed:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "일부 온맵 실패",
                "%s개 온맵을 불러오지 못했습니다. "
                "플러그인 로그를 확인하세요."
                % len(failed),
            )

        return loaded

    def _prepare_one(self, pdf_path):
        xml_path = self._find_xml(pdf_path)
        if not xml_path:
            raise RuntimeError(
                "같은 이름의 XML 메타데이터를 찾지 못했습니다: %s"
                % os.path.basename(pdf_path)
            )

        metadata = self._read_xml(xml_path)
        crs_authid = self._resolve_crs(metadata)

        off_layers = ",".join(
            self.PDF_LAYERS_OFF
        )

        old_layers_off = gdal.GetConfigOption(
            "GDAL_PDF_LAYERS_OFF"
        )
        old_rendering = gdal.GetConfigOption(
            "GDAL_PDF_RENDERING_OPTIONS"
        )
        old_launder = gdal.GetConfigOption(
            "GDAL_PDF_LAUNDER_LAYER_NAMES"
        )

        try:
            gdal.SetConfigOption(
                "GDAL_PDF_LAYERS_OFF",
                off_layers,
            )
            gdal.SetConfigOption(
                "GDAL_PDF_RENDERING_OPTIONS",
                "RASTER,VECTOR,TEXT",
            )
            gdal.SetConfigOption(
                "GDAL_PDF_LAUNDER_LAYER_NAMES",
                "NO",
            )

            dataset = gdal.OpenEx(
                pdf_path,
                gdal.OF_RASTER,
                open_options=[
                    "LAYERS_OFF=%s" % off_layers,
                    "RENDERING_OPTIONS=RASTER,VECTOR,TEXT",
                ],
            )

            if dataset is None:
                raise RuntimeError(
                    "GDAL이 PDF를 열지 못했습니다."
                )

            raster_width = dataset.RasterXSize
            raster_height = dataset.RasterYSize

            x_off = int(
                round(
                    raster_width
                    * self.MAP_LEFT_RATIO
                )
            )
            y_off = int(
                round(
                    raster_height
                    * self.MAP_TOP_RATIO
                )
            )
            x_end = int(
                round(
                    raster_width
                    * self.MAP_RIGHT_RATIO
                )
            )
            y_end = int(
                round(
                    raster_height
                    * self.MAP_BOTTOM_RATIO
                )
            )

            crop_width = x_end - x_off
            crop_height = y_end - y_off

            if crop_width <= 0 or crop_height <= 0:
                raise RuntimeError(
                    "PDF 지도 본문 자르기 범위가 잘못되었습니다."
                )

            cache_dir = os.path.join(
                tempfile.gettempdir(),
                "qgis_eia_ai_assistant",
                "onmap_xml",
            )
            os.makedirs(
                cache_dir,
                exist_ok=True,
            )

            fingerprint = hashlib.sha1(
                (
                    os.path.abspath(pdf_path)
                    + str(os.path.getmtime(pdf_path))
                    + str(metadata)
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
                        os.path.basename(pdf_path)
                    )[0],
                    fingerprint,
                ),
            )

            if not os.path.exists(output_path):
                translate_options = gdal.TranslateOptions(
                    format="GTiff",
                    srcWin=[
                        x_off,
                        y_off,
                        crop_width,
                        crop_height,
                    ],
                    creationOptions=[
                        "TILED=YES",
                        "COMPRESS=DEFLATE",
                        "BIGTIFF=IF_SAFER",
                    ],
                )

                output = gdal.Translate(
                    output_path,
                    dataset,
                    options=translate_options,
                )

                if output is None:
                    raise RuntimeError(
                        "PDF 지도 본문을 GeoTIFF로 변환하지 못했습니다."
                    )

                xmin = metadata["xmin"]
                ymin = metadata["ymin"]
                xmax = metadata["xmax"]
                ymax = metadata["ymax"]

                pixel_width = (
                    xmax - xmin
                ) / float(output.RasterXSize)
                pixel_height = (
                    ymax - ymin
                ) / float(output.RasterYSize)

                output.SetGeoTransform(
                    (
                        xmin,
                        pixel_width,
                        0.0,
                        ymax,
                        0.0,
                        -pixel_height,
                    )
                )

                spatial_ref = osr.SpatialReference()
                epsg_number = int(
                    crs_authid.split(":")[1]
                )
                spatial_ref.ImportFromEPSG(
                    epsg_number
                )
                output.SetProjection(
                    spatial_ref.ExportToWkt()
                )
                output.FlushCache()
                output = None

            dataset = None

            return {
                "output_path": output_path,
                "crs_authid": crs_authid,
                "messages": [
                    "온맵 XML: %s"
                    % os.path.basename(xml_path),
                    "XML 도곽: %.3f, %.3f, %.3f, %.3f"
                    % (
                        metadata["xmin"],
                        metadata["ymin"],
                        metadata["xmax"],
                        metadata["ymax"],
                    ),
                    "좌표계: %s (%s 원점)"
                    % (
                        crs_authid,
                        metadata["origin"],
                    ),
                    "PDF 지도 본문 픽셀: x=%s, y=%s, w=%s, h=%s"
                    % (
                        x_off,
                        y_off,
                        crop_width,
                        crop_height,
                    ),
                    "PDF 레이어 숨김: %s"
                    % off_layers,
                ],
            }

        finally:
            gdal.SetConfigOption(
                "GDAL_PDF_LAYERS_OFF",
                old_layers_off,
            )
            gdal.SetConfigOption(
                "GDAL_PDF_RENDERING_OPTIONS",
                old_rendering,
            )
            gdal.SetConfigOption(
                "GDAL_PDF_LAUNDER_LAYER_NAMES",
                old_launder,
            )

    def _find_xml(self, pdf_path):
        base = os.path.splitext(pdf_path)[0]

        candidates = [
            base + ".xml",
            base + ".XML",
        ]

        # 일부 다운로드 파일은 PDF와 XML의 접두사가 조금 다를 수 있으므로
        # 도엽번호가 같은 XML도 검색합니다.
        stem = os.path.basename(base)
        folder = os.path.dirname(pdf_path)

        sheet_number = ""
        digits = "".join(
            char
            for char in stem
            if char.isdigit()
        )
        if len(digits) >= 8:
            sheet_number = digits[-8:]

        if sheet_number:
            for name in os.listdir(folder):
                if (
                    name.lower().endswith(".xml")
                    and sheet_number in name
                ):
                    candidates.append(
                        os.path.join(
                            folder,
                            name,
                        )
                    )

        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate

        return ""

    def _read_xml(self, xml_path):
        root = ET.parse(xml_path).getroot()

        def text_of(tag):
            element = root.find(".//%s" % tag)
            if element is None or element.text is None:
                return ""
            return element.text.strip()

        lower_left = self._parse_xy(
            text_of("좌측하단")
        )
        upper_left = self._parse_xy(
            text_of("좌측상단")
        )
        lower_right = self._parse_xy(
            text_of("우측하단")
        )
        upper_right = self._parse_xy(
            text_of("우측상단")
        )

        if not all(
            (
                lower_left,
                upper_left,
                lower_right,
                upper_right,
            )
        ):
            raise RuntimeError(
                "XML에서 네 모서리 도곽 좌표를 읽지 못했습니다."
            )

        xmin = min(
            lower_left[0],
            upper_left[0],
        )
        xmax = max(
            lower_right[0],
            upper_right[0],
        )
        ymin = min(
            lower_left[1],
            lower_right[1],
        )
        ymax = max(
            upper_left[1],
            upper_right[1],
        )

        return {
            "xmin": xmin,
            "ymin": ymin,
            "xmax": xmax,
            "ymax": ymax,
            "projection": text_of("투영"),
            "datum": text_of("기준계"),
            "ellipsoid": text_of("타원체"),
            "origin": text_of("기준원점"),
            "sheet_name": text_of("제품도엽명"),
            "sheet_number": text_of("제품도엽번호"),
        }

    def _parse_xy(self, value):
        if not value or "/" not in value:
            return None

        x_text, y_text = value.split(
            "/",
            1,
        )

        try:
            return (
                float(x_text.strip()),
                float(y_text.strip()),
            )
        except ValueError:
            return None

    def _resolve_crs(self, metadata):
        datum = metadata["datum"].replace(
            " ",
            "",
        )
        origin = metadata["origin"].replace(
            " ",
            "",
        )
        projection = metadata["projection"].upper()

        if "Korea_2000".replace("_", "") not in datum.replace("_", ""):
            raise RuntimeError(
                "지원하지 않는 기준계입니다: %s"
                % metadata["datum"]
            )

        if projection != "TM":
            raise RuntimeError(
                "지원하지 않는 투영법입니다: %s"
                % metadata["projection"]
            )

        for name, authid in self.ORIGIN_TO_EPSG.items():
            if name in origin:
                return authid

        raise RuntimeError(
            "지원하지 않는 기준원점입니다: %s"
            % metadata["origin"]
        )

    def _is_korea_extent(self, layer):
        if not layer.crs().isValid():
            return False

        try:
            center = layer.extent().center()
            transform = QgsCoordinateTransform(
                layer.crs(),
                QgsCoordinateReferenceSystem(
                    "EPSG:4326"
                ),
                QgsProject.instance()
                .transformContext(),
            )
            point = transform.transform(
                center
            )

            return (
                123.0 <= point.x() <= 133.5
                and 32.0 <= point.y() <= 40.5
            )
        except Exception:
            return False

    def _zoom_to_layers(self, layers):
        combined = None
        canvas_crs = (
            self.iface.mapCanvas()
            .mapSettings()
            .destinationCrs()
        )

        for layer in layers:
            try:
                extent = layer.extent()

                if layer.crs() != canvas_crs:
                    transform = QgsCoordinateTransform(
                        layer.crs(),
                        canvas_crs,
                        QgsProject.instance()
                        .transformContext(),
                    )
                    extent = transform.transformBoundingBox(
                        extent
                    )

                if combined is None:
                    combined = QgsRectangle(
                        extent
                    )
                else:
                    combined.combineExtentWith(
                        extent
                    )
            except Exception as exc:
                self.log(
                    "경고: 온맵 범위 계산 실패: %s"
                    % exc
                )

        if combined and not combined.isEmpty():
            self.iface.mapCanvas().setExtent(
                combined
            )
            self.iface.mapCanvas().refresh()
