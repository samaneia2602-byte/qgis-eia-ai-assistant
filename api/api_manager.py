# -*- coding: utf-8 -*-

import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from xml.etree import ElementTree

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from .geometry import (
    active_or_selected_extent,
    center_to_epsg4326,
    extent_to_epsg4326,
)
from .vworld import VWorldManager
from ..settings import SettingsStore


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
        국립생태원 생태자연도 WFS REST API를 호출합니다.

        핵심 개선:
        - 정확한 /wfs/getEcologyzmpWFS 경로 사용
        - 저장 키가 Encoding 키여도 한 번 디코딩
        - URL을 QgsVectorLayer로 직접 열지 않고 응답을 내려받아 검사
        - GeoJSON/GML/XML 응답을 임시 파일로 저장한 뒤 OGR로 로드
        - 서비스 오류 메시지를 대화창 로그에 표시
        """
        raw_key = (self.store.ecology_key or "").strip()

        if not raw_key:
            self.log(
                "오류: 생태자연도 API Key가 없습니다. "
                "API Key 설정에서 저장하세요."
            )
            return None

        # Encoding 키가 저장돼 있으면 먼저 Decoding 형태로 변환합니다.
        service_key = urllib.parse.unquote(
            raw_key
        )

        extent, crs = active_or_selected_extent(
            self.iface
        )
        e4326 = extent_to_epsg4326(
            extent,
            crs,
        )

        bbox = "%.8f,%.8f,%.8f,%.8f" % (
            e4326.xMinimum(),
            e4326.yMinimum(),
            e4326.xMaximum(),
            e4326.yMaximum(),
        )

        base = (
            "https://apis.data.go.kr/B553084/"
            "ecopias/EcologyzmpService/"
            "wfs/getEcologyzmpWFS"
        )

        # 기술문서 버전에 따라 파라미터 표기가 달라질 수 있어
        # 가장 일반적인 두 조합을 순차적으로 시험합니다.
        parameter_sets = [
            {
                "serviceKey": service_key,
                "bbox": bbox,
                "coordType": "EPSG:4326",
                "resultType": "json",
                "pageNo": 1,
                "numOfRows": 1000,
            },
            {
                "serviceKey": service_key,
                "bbox": bbox,
                "crs": "EPSG:4326",
                "type": "json",
                "pageNo": 1,
                "numOfRows": 1000,
            },
            {
                "serviceKey": service_key,
                "service": "WFS",
                "request": "GetFeature",
                "version": "1.1.0",
                "bbox": bbox,
                "srsName": "EPSG:4326",
                "outputFormat": "application/json",
                "maxFeatures": 1000,
            },
            {
                "serviceKey": service_key,
                "minX": e4326.xMinimum(),
                "minY": e4326.yMinimum(),
                "maxX": e4326.xMaximum(),
                "maxY": e4326.yMaximum(),
                "coordType": "EPSG:4326",
                "resultType": "json",
                "pageNo": 1,
                "numOfRows": 1000,
            },
        ]

        self.log(
            "생태자연도 WFS BBOX EPSG:4326 = %s"
            % bbox
        )

        errors = []

        for index, params in enumerate(
            parameter_sets,
            start=1,
        ):
            try:
                layer = self._request_ecology_vector(
                    base,
                    params,
                    attempt=index,
                )
            except Exception as exc:
                errors.append(str(exc))
                self.log(
                    "생태자연도 WFS 호출 %s차 실패: %s"
                    % (
                        index,
                        exc,
                    )
                )
                continue

            if layer and layer.isValid():
                layer.setName(
                    "생태자연도_WFS"
                )
                QgsProject.instance().addMapLayer(
                    layer
                )
                self.log(
                    "생태자연도 WFS 레이어를 추가했습니다: "
                    "%s개 객체"
                    % layer.featureCount()
                )
                return layer

        self.log(
            "오류: 생태자연도 WFS 응답을 "
            "벡터 레이어로 변환하지 못했습니다."
        )

        if errors:
            self.log(
                "마지막 오류: %s"
                % errors[-1]
            )

        self.log(
            "공공데이터포털 기술문서의 필수 파라미터명을 "
            "확인해야 합니다."
        )
        return None

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
        key = (
            self.store.ecology_key
            or ""
        ).strip()

        if not key:
            self.log(
                "오류: 생태자연도 API Key가 없습니다."
            )
            return None

        service_key = urllib.parse.unquote(
            key
        )

        extent, crs = active_or_selected_extent(
            self.iface
        )
        e4326 = extent_to_epsg4326(
            extent,
            crs,
        )

        base = (
            "https://apis.data.go.kr/B553084/"
            "ecopias/EcologyzmpService/"
            "wms/getEcologyzmpWMS"
        )
        query = {
            "serviceKey": service_key,
            "minx": e4326.xMinimum(),
            "miny": e4326.yMinimum(),
            "maxx": e4326.xMaximum(),
            "maxy": e4326.yMaximum(),
            "coordType": "EPSG:4326",
            "width": 1024,
            "height": 1024,
            "imgType": "png",
            "background": "transparent",
        }

        url = (
            base
            + "?"
            + urllib.parse.urlencode(
                query
            )
        )

        layer = QgsRasterLayer(
            url,
            "생태자연도_WMS",
        )

        if layer.isValid():
            QgsProject.instance().addMapLayer(
                layer
            )
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
