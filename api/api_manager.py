# -*- coding: utf-8 -*-

import hashlib
import json
import math
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from xml.etree import ElementTree

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.PyQt.QtGui import QColor

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsRectangle,
    QgsFeature,
    QgsField,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)

from .geometry import (
    active_or_selected_extent,
    center_to_epsg4326,
    extent_to_epsg4326,
)
from .vworld import VWorldManager
from ..settings import SettingsStore
from ..symbology.eia_symbols import apply_ecology_style


class ApiManager:
    def __init__(self, iface, log):
        self.iface = iface
        self.log = log
        self.store = SettingsStore()
        self.vworld = VWorldManager(iface, log)

    def _add_wms(
        self,
        name,
        base_url,
        layers,
        crs="EPSG:3857",
        styles="",
        fmt="image/png",
        extra=None,
    ):
        params = {
            "url": base_url,
            "layers": layers,
            "styles": styles,
            "format": fmt,
            "crs": crs,
        }

        if extra:
            params.update(extra)

        uri = "&".join(
            "%s=%s"
            % (
                key,
                urllib.parse.quote(
                    str(value),
                    safe=":/?&=,%",
                ),
            )
            for key, value in params.items()
        )

        layer = QgsRasterLayer(uri, name, "wms")

        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log("레이어 추가: %s" % name)
            return layer

        self.log(
            "오류: WMS 레이어를 추가하지 못했습니다. "
            "API URL/파라미터를 확인하세요."
        )
        return None

    def load_eia_bsnsarea_wfs(self):
        key = self.store.eia_key

        if not key:
            self.log(
                "오류: 환경영향평가 API Key가 없습니다. "
                "API Key 설정에서 저장하세요."
            )
            return None

        extent, crs = active_or_selected_extent(
            self.iface
        )
        center = center_to_epsg4326(
            extent,
            crs,
        )

        base = (
            "https://apis.data.go.kr/1480523/"
            "BsnsAreaService/getInfoWFS"
        )
        url = base + "?" + urllib.parse.urlencode(
            {
                "serviceKey": key,
                "centerX": center.x(),
                "centerY": center.y(),
            }
        )

        self.log(
            "환경영향평가 사업구역 WFS 조회 중심좌표 "
            "EPSG:4326 = %.6f, %.6f"
            % (
                center.x(),
                center.y(),
            )
        )

        layer = QgsVectorLayer(
            url,
            "환경영향평가_사업구역_WFS",
            "ogr",
        )

        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            return layer

        self.log(
            "오류: WFS를 직접 레이어로 추가하지 못했습니다."
        )
        self.log("호출 URL: %s" % url)
        return None

    def load_eia_bsnsarea_wms(self):
        key = self.store.eia_key

        if not key:
            self.log(
                "오류: 환경영향평가 API Key가 없습니다. "
                "API Key 설정에서 저장하세요."
            )
            return None

        extent, crs = active_or_selected_extent(
            self.iface
        )
        center = center_to_epsg4326(
            extent,
            crs,
        )

        base = (
            "https://apis.data.go.kr/1480523/"
            "BsnsAreaService/getInfoWMS"
        )
        query = {
            "serviceKey": key,
            "centerX": center.x(),
            "centerY": center.y(),
            "coordType": "EPSG:4326",
            "width": 1024,
            "height": 1024,
            "imgType": "png",
            "background": "transparent",
        }

        url = base + "?" + urllib.parse.urlencode(
            query
        )

        layer = QgsRasterLayer(
            url,
            "환경영향평가_사업구역_WMS",
        )

        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log(
                "환경영향평가 사업구역 WMS 이미지를 "
                "추가했습니다."
            )
            return layer

        self.log(
            "오류: WMS 이미지를 추가하지 못했습니다."
        )
        self.log("호출 URL: %s" % url)
        return None

    def load_ecology_wfs(self):
        """
        생태자연도 WFS를 EPSG:5186 격자로 분할 조회한 뒤
        중복 객체를 제거하고 하나의 메모리 레이어로 병합합니다.

        기술문서 기준:
        - typeName=tbl_opn_eczm
        - bbox=EPSG:5186의 minX,minY,maxX,maxY
        - maxFeatures 최대 500
        """
        raw_key = (self.store.ecology_key or "").strip()

        if not raw_key:
            self.log(
                "오류: 생태자연도 API Key가 없습니다. "
                "API Key 설정에서 저장하세요."
            )
            return None

        service_key = urllib.parse.unquote(raw_key)
        extent, source_crs = active_or_selected_extent(self.iface)

        target_crs = QgsCoordinateReferenceSystem("EPSG:5186")
        if not target_crs.isValid():
            self.log("오류: EPSG:5186 좌표계를 생성하지 못했습니다.")
            return None

        try:
            transform = QgsCoordinateTransform(
                source_crs,
                target_crs,
                QgsProject.instance().transformContext(),
            )
            extent_5186 = transform.transformBoundingBox(extent)
        except Exception as exc:
            self.log(
                "오류: 사업지역 범위를 EPSG:5186으로 변환하지 못했습니다: %s"
                % exc
            )
            return None

        base = (
            "http://apis.data.go.kr/B553084/"
            "ecoapi/EcologyzmpService/"
            "wfs/getEcologyzmpWFS"
        )

        initial_tiles = self._build_ecology_grid(
            extent_5186,
            preferred_size=5000.0,
            max_initial_tiles=100,
        )

        self.log(
            "생태자연도 격자 분할조회 시작: "
            "초기 %s개 격자, EPSG:5186"
            % len(initial_tiles)
        )

        queue = list(initial_tiles)
        accepted_layers = []
        request_count = 0
        failed_count = 0
        split_count = 0
        max_requests = 300
        min_tile_size = 250.0

        while queue:
            if request_count >= max_requests:
                self.log(
                    "경고: 최대 요청 횟수 %s회에 도달하여 "
                    "남은 격자 조회를 중단합니다."
                    % max_requests
                )
                break

            tile = queue.pop(0)
            request_count += 1

            bbox = "%.4f,%.4f,%.4f,%.4f" % (
                tile.xMinimum(),
                tile.yMinimum(),
                tile.xMaximum(),
                tile.yMaximum(),
            )

            params = {
                "serviceKey": service_key,
                "typeName": "tbl_opn_eczm",
                "bbox": bbox,
                "maxFeatures": 500,
            }

            self.log(
                "생태자연도 격자 조회 %s "
                "(대기 %s): %s"
                % (
                    request_count,
                    len(queue),
                    bbox,
                )
            )
            QCoreApplication.processEvents()

            try:
                tile_layer = self._request_ecology_vector(
                    base,
                    params,
                    attempt=request_count,
                )
            except Exception as exc:
                failed_count += 1
                self.log(
                    "경고: 격자 조회 실패, 계속 진행합니다: %s"
                    % exc
                )
                continue

            if not tile_layer or not tile_layer.isValid():
                failed_count += 1
                self.log(
                    "경고: 유효하지 않은 격자 응답을 건너뜁니다."
                )
                continue

            # 생태자연도 WFS 응답의 좌표값은 EPSG:5186인데,
            # GML 메타데이터가 없거나 잘못되어 QGIS가 EPSG:3857로
            # 오인하는 경우가 있습니다. 좌표를 변환하지 않고
            # 원 좌표값에 EPSG:5186을 강제로 지정합니다.
            detected_crs = (
                tile_layer.crs().authid()
                if tile_layer.crs().isValid()
                else "미지정"
            )
            if detected_crs != target_crs.authid():
                self.log(
                    "생태자연도 응답 CRS를 %s에서 "
                    "EPSG:5186으로 바로잡습니다."
                    % detected_crs
                )

            tile_layer.setCrs(target_crs)

            feature_count = tile_layer.featureCount()

            # API 최대 허용값인 500건에 도달하면 잘렸을 가능성이 있으므로
            # 해당 격자를 4분할하여 다시 조회합니다.
            if (
                feature_count >= 500
                and tile.width() > min_tile_size
                and tile.height() > min_tile_size
            ):
                children = self._split_rectangle(tile)
                queue = children + queue
                split_count += 1
                self.log(
                    "격자 응답이 500건에 도달하여 "
                    "4개 하위 격자로 재분할합니다."
                )
                continue

            if feature_count >= 500:
                self.log(
                    "경고: 최소 격자에서도 500건이 반환되었습니다. "
                    "일부 객체가 누락될 가능성이 있습니다."
                )

            if feature_count > 0:
                accepted_layers.append(tile_layer)

        if not accepted_layers:
            self.log(
                "오류: 생태자연도 격자 조회 결과가 없습니다."
            )
            return None

        self.log(
            "격자 조회 완료: 요청 %s회, 재분할 %s회, "
            "실패 %s회. 병합 및 중복 제거를 시작합니다."
            % (
                request_count,
                split_count,
                failed_count,
            )
        )

        merged, raw_count, unique_count = (
            self._merge_ecology_layers_deduplicated(
                accepted_layers,
                target_crs,
            )
        )

        if not merged or not merged.isValid():
            self.log(
                "오류: 생태자연도 격자 레이어 병합에 실패했습니다."
            )
            return None

        merged.setName("생태자연도_WFS_분할병합")
        merged.setCrs(target_crs)

        # 속성표에 표준 생태자연도 필드를 만들고,
        # 등급별 자동 분류 심볼을 적용합니다.
        self._prepare_ecology_grade_and_style(merged)

        QgsProject.instance().addMapLayer(merged)

        self.log(
            "최종 생태자연도 레이어 좌표계: %s"
            % merged.crs().authid()
        )

        self.log(
            "생태자연도 병합 완료: 원본 %s건 → "
            "중복 제거 후 %s건"
            % (
                raw_count,
                unique_count,
            )
        )

        return merged

    def _prepare_ecology_grade_and_style(self, layer):
        """
        병합된 생태자연도 레이어에 '생태자연도' 필드를 추가하고
        각 객체를 1등급·2등급·3등급·별도관리지역으로 표준화합니다.
        """
        source_field = self._resolve_ecology_grade_field(layer)

        field_index = layer.fields().indexOf("생태자연도")
        if field_index < 0:
            provider = layer.dataProvider()
            provider.addAttributes(
                [
                    QgsField(
                        "생태자연도",
                        QVariant.String,
                        len=30,
                    )
                ]
            )
            layer.updateFields()
            field_index = layer.fields().indexOf("생태자연도")

        changes = {}
        counts = {
            "1등급": 0,
            "2등급": 0,
            "3등급": 0,
            "별도관리지역": 0,
            "미분류": 0,
        }

        for feature in layer.getFeatures():
            raw_value = (
                feature[source_field]
                if source_field
                else None
            )
            grade = self._normalize_ecology_grade(raw_value)
            changes[feature.id()] = {
                field_index: grade
            }
            counts[grade] = counts.get(grade, 0) + 1

        if changes:
            layer.dataProvider().changeAttributeValues(changes)
            layer.updateFields()

        apply_ecology_style(layer, field_name="생태자연도")
        layer.triggerRepaint()

        self.log(
            "생태자연도 속성 정리 완료: "
            "1등급 %s건, 2등급 %s건, 3등급 %s건, "
            "별도관리지역 %s건, 미분류 %s건"
            % (
                counts.get("1등급", 0),
                counts.get("2등급", 0),
                counts.get("3등급", 0),
                counts.get("별도관리지역", 0),
                counts.get("미분류", 0),
            )
        )

    def _resolve_ecology_grade_field(self, layer):
        """원본 WFS 속성에서 생태자연도 등급 필드를 찾습니다."""
        candidates = (
            "생태자연도",
            "등급",
            "생태자연도등급",
            "자연도등급",
            "평가등급",
            "dgre",
            "grade",
            "grd",
            "eczm_grade",
            "eczm_grd",
            "ecology_grade",
            "ecol_grade",
            "nature_grade",
            "rank",
        )

        lookup = {
            field.name().lower(): field.name()
            for field in layer.fields()
        }

        # 새로 만드는 대상 필드는 원본 후보에서 제외합니다.
        for candidate in candidates:
            actual = lookup.get(candidate.lower())
            if actual and actual != "생태자연도":
                return actual

        # 필드명이 예상과 다를 경우, 실제 값에서 등급 패턴을 찾습니다.
        best_field = None
        best_score = 0

        for field in layer.fields():
            if field.name() == "생태자연도":
                continue

            score = 0
            checked = 0

            for feature in layer.getFeatures():
                value = feature[field.name()]
                if value in (None, ""):
                    continue

                checked += 1
                normalized = self._normalize_ecology_grade(value)
                if normalized != "미분류":
                    score += 1

                if checked >= 100:
                    break

            if score > best_score:
                best_score = score
                best_field = field.name()

        if best_field and best_score >= 3:
            self.log(
                "생태자연도 등급 원본 필드를 자동 탐지했습니다: %s"
                % best_field
            )
            return best_field

        self.log(
            "경고: 생태자연도 등급 원본 필드를 찾지 못해 "
            "모든 객체를 미분류로 처리합니다."
        )
        return None

    def _normalize_ecology_grade(self, value):
        """원본 등급값을 네 가지 표준 명칭으로 변환합니다."""
        compact = (
            str(value or "")
            .strip()
            .lower()
            .replace(" ", "")
            .replace("_", "")
            .replace("-", "")
        )

        if not compact:
            return "미분류"

        if (
            "별도관리지역" in compact
            or "별도관리" in compact
            or compact in (
                "별도",
                "4",
                "04",
                "9",
                "09",
                "special",
                "separate",
            )
        ):
            return "별도관리지역"

        if (
            "1등급" in compact
            or compact in (
                "1",
                "01",
                "i",
                "grade1",
                "class1",
            )
        ):
            return "1등급"

        if (
            "2등급" in compact
            or compact in (
                "2",
                "02",
                "ii",
                "grade2",
                "class2",
            )
        ):
            return "2등급"

        if (
            "3등급" in compact
            or compact in (
                "3",
                "03",
                "iii",
                "grade3",
                "class3",
            )
        ):
            return "3등급"

        return "미분류"

    def _build_ecology_grid(
        self,
        extent,
        preferred_size=5000.0,
        max_initial_tiles=100,
    ):
        width = max(extent.width(), 1.0)
        height = max(extent.height(), 1.0)
        tile_size = float(preferred_size)

        columns = max(1, int(math.ceil(width / tile_size)))
        rows = max(1, int(math.ceil(height / tile_size)))

        while columns * rows > max_initial_tiles:
            tile_size *= 1.25
            columns = max(
                1,
                int(math.ceil(width / tile_size)),
            )
            rows = max(
                1,
                int(math.ceil(height / tile_size)),
            )

        tiles = []

        for row in range(rows):
            y_min = extent.yMinimum() + row * tile_size
            y_max = min(
                y_min + tile_size,
                extent.yMaximum(),
            )

            for column in range(columns):
                x_min = extent.xMinimum() + column * tile_size
                x_max = min(
                    x_min + tile_size,
                    extent.xMaximum(),
                )

                if x_max <= x_min or y_max <= y_min:
                    continue

                tiles.append(
                    QgsRectangle(
                        x_min,
                        y_min,
                        x_max,
                        y_max,
                    )
                )

        return tiles

    def _split_rectangle(self, rectangle):
        x_mid = (
            rectangle.xMinimum()
            + rectangle.xMaximum()
        ) / 2.0
        y_mid = (
            rectangle.yMinimum()
            + rectangle.yMaximum()
        ) / 2.0

        return [
            QgsRectangle(
                rectangle.xMinimum(),
                rectangle.yMinimum(),
                x_mid,
                y_mid,
            ),
            QgsRectangle(
                x_mid,
                rectangle.yMinimum(),
                rectangle.xMaximum(),
                y_mid,
            ),
            QgsRectangle(
                rectangle.xMinimum(),
                y_mid,
                x_mid,
                rectangle.yMaximum(),
            ),
            QgsRectangle(
                x_mid,
                y_mid,
                rectangle.xMaximum(),
                rectangle.yMaximum(),
            ),
        ]

    def _merge_ecology_layers_deduplicated(
        self,
        layers,
        target_crs,
    ):
        first_layer = next(
            (
                layer
                for layer in layers
                if layer and layer.isValid()
            ),
            None,
        )

        if not first_layer:
            return None, 0, 0

        geometry_name = QgsWkbTypes.displayString(
            first_layer.wkbType()
        )
        # 각 격자 응답의 실제 좌표값은 EPSG:5186이므로
        # 병합 레이어도 반드시 EPSG:5186으로 생성합니다.
        auth_id = target_crs.authid()

        merged = QgsVectorLayer(
            "%s?crs=%s"
            % (
                geometry_name,
                auth_id,
            ),
            "생태자연도_WFS_분할병합",
            "memory",
        )

        provider = merged.dataProvider()
        provider.addAttributes(
            list(first_layer.fields())
        )
        merged.updateFields()

        seen = set()
        output_features = []
        raw_count = 0

        for layer in layers:
            if not layer or not layer.isValid():
                continue

            for source_feature in layer.getFeatures():
                raw_count += 1
                geometry = source_feature.geometry()

                if not geometry or geometry.isEmpty():
                    continue

                key = self._ecology_feature_key(
                    source_feature
                )

                if key in seen:
                    continue

                seen.add(key)

                output_feature = QgsFeature(
                    merged.fields()
                )
                output_feature.setGeometry(
                    geometry
                )
                output_feature.setAttributes(
                    source_feature.attributes()
                )
                output_features.append(
                    output_feature
                )

                if len(output_features) >= 1000:
                    provider.addFeatures(
                        output_features
                    )
                    output_features = []
                    QCoreApplication.processEvents()

        if output_features:
            provider.addFeatures(output_features)

        merged.updateExtents()
        return merged, raw_count, len(seen)

    def _ecology_feature_key(self, feature):
        """
        격자 경계에서 반복 반환된 동일 피처를 제거합니다.
        우선 GML/FID 계열 식별자를 사용하고,
        없으면 도형 WKB와 속성값의 SHA-1 해시를 사용합니다.
        """
        field_lookup = {
            field.name().lower(): field.name()
            for field in feature.fields()
        }

        for candidate in (
            "gml_id",
            "gmlid",
            "fid",
            "objectid",
            "ogc_fid",
            "id",
        ):
            actual = field_lookup.get(candidate)
            if actual:
                value = feature[actual]
                if value not in (None, ""):
                    return "id:%s:%s" % (
                        candidate,
                        value,
                    )

        digest = hashlib.sha1()
        geometry = feature.geometry()

        try:
            digest.update(bytes(geometry.asWkb()))
        except Exception:
            digest.update(
                geometry.asWkt().encode(
                    "utf-8",
                    errors="replace",
                )
            )

        for value in feature.attributes():
            digest.update(b"|")
            digest.update(
                str(value).encode(
                    "utf-8",
                    errors="replace",
                )
            )

        return "hash:%s" % digest.hexdigest()

    def _request_ecology_vector(
        self,
        base_url,
        params,
        attempt=1,
    ):
        url = (
            base_url
            + "?"
            + urllib.parse.urlencode(
                params,
                doseq=True,
            )
        )

        # 인증키 전체는 로그에 남기지 않습니다.
        safe_params = dict(params)
        safe_params["serviceKey"] = "***"
        safe_url = (
            base_url
            + "?"
            + urllib.parse.urlencode(
                safe_params
            )
        )
        self.log(
            "생태자연도 WFS 호출 %s차: %s"
            % (
                attempt,
                safe_url,
            )
        )

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "QGIS-EIA-AI-Assistant/0.5"
                ),
                "Accept": (
                    "application/json,"
                    "application/geo+json,"
                    "application/xml,text/xml,*/*"
                ),
            },
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=40,
            ) as response:
                body = response.read()
                content_type = (
                    response.headers.get(
                        "Content-Type",
                        "",
                    )
                    .split(";")[0]
                    .strip()
                    .lower()
                )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(
                "utf-8",
                errors="replace",
            )
            detail_text = self._short_text(detail)

            if not detail_text:
                detail_text = (
                    "응답 본문 없음. 서버가 요청변수 조합을 "
                    "처리하지 못했을 가능성이 큽니다."
                )

            raise RuntimeError(
                "HTTP %s %s: %s"
                % (
                    exc.code,
                    getattr(exc, "reason", ""),
                    detail_text,
                )
            )
        except urllib.error.URLError as exc:
            raise RuntimeError(
                "네트워크 오류: %s"
                % exc.reason
            )

        if not body:
            raise RuntimeError(
                "API 응답이 비어 있습니다."
            )

        text = body.decode(
            "utf-8",
            errors="replace",
        ).strip()

        error_message = self._extract_service_error(
            text
        )
        if error_message:
            raise RuntimeError(
                "API 오류 응답: %s"
                % error_message
            )

        suffix = self._detect_vector_suffix(
            content_type,
            text,
        )

        temp_dir = os.path.join(
            tempfile.gettempdir(),
            "qgis_eia_ai_assistant",
        )
        os.makedirs(
            temp_dir,
            exist_ok=True,
        )

        temp_path = os.path.join(
            temp_dir,
            "ecology_wfs_%s%s"
            % (
                attempt,
                suffix,
            ),
        )

        with open(
            temp_path,
            "wb",
        ) as output:
            output.write(body)

        layer = QgsVectorLayer(
            temp_path,
            "생태자연도_WFS",
            "ogr",
        )

        if layer.isValid():
            return layer

        # JSON이 wrapper 구조인 경우 안쪽 FeatureCollection을 추출합니다.
        if suffix == ".geojson":
            extracted = self._extract_geojson(
                text
            )
            if extracted:
                extracted_path = os.path.join(
                    temp_dir,
                    "ecology_wfs_extracted_%s.geojson"
                    % attempt,
                )
                with open(
                    extracted_path,
                    "w",
                    encoding="utf-8",
                ) as output:
                    json.dump(
                        extracted,
                        output,
                        ensure_ascii=False,
                    )

                extracted_layer = QgsVectorLayer(
                    extracted_path,
                    "생태자연도_WFS",
                    "ogr",
                )
                if extracted_layer.isValid():
                    return extracted_layer

        raise RuntimeError(
            "응답 파일은 생성됐지만 QGIS가 "
            "벡터 데이터로 열지 못했습니다. "
            "응답 형식=%s, 임시파일=%s"
            % (
                content_type or "알 수 없음",
                temp_path,
            )
        )

    def _extract_service_error(self, text):
        if not text:
            return None

        lowered = text.lower()

        # JSON 오류 응답
        if text.startswith("{") or text.startswith("["):
            try:
                payload = json.loads(text)
            except Exception:
                payload = None

            if isinstance(payload, dict):
                candidates = [
                    payload.get("message"),
                    payload.get("msg"),
                    payload.get("resultMsg"),
                    payload.get("returnAuthMsg"),
                    payload.get("errMsg"),
                ]

                header = (
                    payload.get("response", {})
                    .get("header", {})
                    if isinstance(
                        payload.get("response"),
                        dict,
                    )
                    else {}
                )
                candidates.extend(
                    [
                        header.get("resultMsg"),
                        header.get("resultCode"),
                    ]
                )

                for value in candidates:
                    if value:
                        value_text = str(value)
                        if (
                            "normal" not in value_text.lower()
                            and value_text not in ("00", "0")
                        ):
                            return value_text

        # XML 오류 응답
        if text.startswith("<"):
            try:
                root = ElementTree.fromstring(
                    text
                )
            except Exception:
                root = None

            if root is not None:
                values = {}
                for element in root.iter():
                    tag = element.tag.split("}")[-1]
                    if element.text:
                        values[tag.lower()] = (
                            element.text.strip()
                        )

                code = (
                    values.get("resultcode")
                    or values.get("returnreasoncode")
                    or values.get("errcode")
                )
                message = (
                    values.get("resultmsg")
                    or values.get("returnauthmsg")
                    or values.get("errmsg")
                    or values.get("message")
                )

                if code and code not in (
                    "00",
                    "0",
                    "NORMAL SERVICE.",
                ):
                    return "%s (%s)" % (
                        message or "서비스 오류",
                        code,
                    )

                if message and (
                    "normal service"
                    not in message.lower()
                ):
                    if any(
                        keyword in message.lower()
                        for keyword in (
                            "error",
                            "invalid",
                            "unauthorized",
                            "등록되지",
                            "인증",
                            "오류",
                        )
                    ):
                        return message

        if any(
            keyword in lowered
            for keyword in (
                "service key is not registered",
                "invalid service key",
                "application error",
                "access denied",
            )
        ):
            return self._short_text(text)

        return None

    def _detect_vector_suffix(
        self,
        content_type,
        text,
    ):
        lowered = text.lstrip().lower()

        if (
            "json" in content_type
            or lowered.startswith("{")
            or lowered.startswith("[")
        ):
            return ".geojson"

        if (
            "gml" in content_type
            or "featurecollection" in lowered[:500]
            or "wfs:" in lowered[:500]
        ):
            return ".gml"

        return ".xml"

    def _extract_geojson(self, text):
        try:
            payload = json.loads(text)
        except Exception:
            return None

        return self._find_feature_collection(
            payload
        )

    def _find_feature_collection(
        self,
        value,
    ):
        if isinstance(value, dict):
            if (
                value.get("type")
                == "FeatureCollection"
                and isinstance(
                    value.get("features"),
                    list,
                )
            ):
                return value

            for child in value.values():
                found = self._find_feature_collection(
                    child
                )
                if found:
                    return found

        elif isinstance(value, list):
            for child in value:
                found = self._find_feature_collection(
                    child
                )
                if found:
                    return found

        return None

    def _short_text(
        self,
        text,
        limit=300,
    ):
        compact = " ".join(
            (text or "").split()
        )
        if len(compact) > limit:
            return compact[:limit] + "..."
        return compact

    def load_ecology_wms(self):
        key = (self.store.ecology_key or "").strip()

        if not key:
            self.log(
                "오류: 생태자연도 API Key가 없습니다."
            )
            return None

        service_key = urllib.parse.unquote(key)

        extent, source_crs = active_or_selected_extent(self.iface)
        target_crs = QgsCoordinateReferenceSystem("EPSG:5186")

        try:
            transform = QgsCoordinateTransform(
                source_crs,
                target_crs,
                QgsProject.instance().transformContext(),
            )
            extent_5186 = transform.transformBoundingBox(extent)
        except Exception as exc:
            self.log(
                "오류: 생태자연도 WMS 범위 변환 실패: %s"
                % exc
            )
            return None

        bbox = "%.4f,%.4f,%.4f,%.4f" % (
            extent_5186.xMinimum(),
            extent_5186.yMinimum(),
            extent_5186.xMaximum(),
            extent_5186.yMaximum(),
        )

        base = (
            "http://apis.data.go.kr/B553084/"
            "ecoapi/EcologyzmpService/"
            "wms/getEcologyzmpWMS"
        )
        query = {
            "serviceKey": service_key,
            "layers": "tbl_opn_eczm",
            "srs": "EPSG:5186",
            "bbox": bbox,
            "width": 1024,
            "height": 1024,
            "format": "png",
            "transparent": "true",
            "bgcolor": "0xFFFFFF",
            "exceptions": "XML",
        }

        url = base + "?" + urllib.parse.urlencode(query)

        layer = QgsRasterLayer(
            url,
            "생태자연도_WMS",
        )

        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log(
                "생태자연도 WMS 이미지를 추가했습니다."
            )
            return layer

        self.log(
            "오류: 생태자연도 WMS를 추가하지 못했습니다."
        )
        return None

    def load_vworld_cadastral(self):
        return (
            self.vworld
            .load_cadastral_by_active_layer()
        )
