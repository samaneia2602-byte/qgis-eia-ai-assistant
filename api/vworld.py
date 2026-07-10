# -*- coding: utf-8 -*-
"""VWorld 2D Data API loaders.

현재 구현 기능
- 활성 레이어 또는 선택 피처 범위를 기준으로 연속지적도(LP_PA_CBND_BUBUN)를 조회
- VWorld 2D Data API 2.0의 GetFeature 응답(JSON/GeoJSON)을 임시 GeoJSON으로 저장 후 QGIS 벡터 레이어로 추가
"""
from __future__ import absolute_import

import json
import math
import os
import tempfile
import time
import urllib.parse
import urllib.request

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)

from .geometry import active_or_selected_extent, extent_to_epsg4326
from ..settings import SettingsStore

VWORLD_DATA_URL = 'https://api.vworld.kr/req/data'
CADASTRAL_DATA_NAME = 'LP_PA_CBND_BUBUN'
CADASTRAL_COLUMNS = 'pnu,jibun,bonbun,bubun,addr,gosi_year,gosi_month,jiga'
MAX_QUERY_AREA_M2 = 1900000.0  # VWorld 제한 2km²보다 약간 작게 요청
DEFAULT_BUFFER_M = 50.0


class VWorldManager(object):
    def __init__(self, iface, log):
        self.iface = iface
        self.log = log
        self.store = SettingsStore()

    def _request_extent_4326(self):
        """활성 레이어/선택 피처 범위를 VWorld 요청 가능한 2km² 이하 BBOX로 변환."""
        extent, crs = active_or_selected_extent(self.iface)
        metric = QgsCoordinateReferenceSystem('EPSG:5179')
        to_metric = QgsCoordinateTransform(crs, metric, QgsProject.instance())
        from_metric = QgsCoordinateTransform(metric, QgsCoordinateReferenceSystem('EPSG:4326'), QgsProject.instance())

        try:
            mrect = to_metric.transformBoundingBox(extent)
            mrect.grow(DEFAULT_BUFFER_M)
            area = max(0.0, mrect.width()) * max(0.0, mrect.height())
            clipped = False
            if area > MAX_QUERY_AREA_M2:
                c = mrect.center()
                side = math.sqrt(MAX_QUERY_AREA_M2)
                half = side / 2.0
                mrect = QgsRectangle(c.x() - half, c.y() - half, c.x() + half, c.y() + half)
                clipped = True
            e4326 = from_metric.transformBoundingBox(mrect)
            return e4326, clipped, area
        except Exception:
            # 변환 실패 시 기존 geometry 유틸로 최소 동작 보장
            e4326 = extent_to_epsg4326(extent, crs)
            return e4326, False, 0.0

    def _extract_geojson(self, payload):
        """VWorld JSON 응답에서 QGIS가 읽을 수 있는 FeatureCollection을 추출."""
        if isinstance(payload, dict) and isinstance(payload.get('response'), dict):
            payload = payload.get('response')
        result = payload.get('result') if isinstance(payload, dict) else None
        if isinstance(result, dict):
            for key in ('featureCollection', 'features', 'geojson'):
                value = result.get(key)
                if isinstance(value, dict) and value.get('type') == 'FeatureCollection':
                    return value
                if key == 'features' and isinstance(value, list):
                    return {'type': 'FeatureCollection', 'features': value}
        if isinstance(payload, dict) and payload.get('type') == 'FeatureCollection':
            return payload
        return None

    def load_cadastral_by_active_layer(self):
        key = self.store.vworld_key
        if not key:
            self.log('오류: VWorld API Key가 없습니다. [API Key 설정]에서 VWorld API Key를 저장하세요.')
            return None

        e4326, clipped, original_area = self._request_extent_4326()
        bbox = '%.8f,%.8f,%.8f,%.8f' % (
            e4326.xMinimum(), e4326.yMinimum(), e4326.xMaximum(), e4326.yMaximum()
        )
        geom_filter = 'BOX(%s)' % bbox
        params = {
            'service': 'data',
            'version': '2.0',
            'request': 'GetFeature',
            'key': key,
            'format': 'json',
            'errorFormat': 'json',
            'size': 1000,
            'page': 1,
            'data': CADASTRAL_DATA_NAME,
            'geomFilter': geom_filter,
            'columns': CADASTRAL_COLUMNS,
            'geometry': 'true',
            'attribute': 'true',
            'crs': 'EPSG:4326',
        }
        url = VWORLD_DATA_URL + '?' + urllib.parse.urlencode(params)

        self.log('VWorld 연속지적도 조회 BBOX(EPSG:4326): %s' % bbox)
        if clipped:
            self.log('알림: 선택 SHP 범위가 VWorld 1회 조회 제한(2km²)을 초과하여 중심부 약 1.9km² 범위로 조회했습니다. 원범위 면적 약 %.2fkm²' % (original_area / 1000000.0))

        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'QGIS-EIA-AI-Assistant/0.2'})
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read().decode('utf-8')
            payload = json.loads(raw)
        except Exception as e:
            self.log('오류: VWorld 연속지적도 API 호출에 실패했습니다: %s' % e)
            self.log('호출 URL: %s' % url)
            return None

        root = payload.get('response') if isinstance(payload, dict) and isinstance(payload.get('response'), dict) else payload
        status = root.get('status') if isinstance(root, dict) else None
        if status and status != 'OK':
            msg = ''
            if isinstance(root.get('error'), dict):
                msg = root['error'].get('text') or root['error'].get('code') or ''
            self.log('오류: VWorld 응답 상태가 OK가 아닙니다. status=%s %s' % (status, msg))
            self.log('호출 URL: %s' % url)
            return None

        fc = self._extract_geojson(payload)
        if not fc or not fc.get('features'):
            self.log('조회 결과가 없습니다. 선택 SHP 위치, VWorld 인증키 서비스 URL, 조회 범위를 확인하세요.')
            return None

        out_dir = os.path.join(tempfile.gettempdir(), 'qgis_eia_ai_assistant')
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        out_path = os.path.join(out_dir, 'vworld_cadastral_%s.geojson' % int(time.time()))
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(fc, f, ensure_ascii=False)

        layer = QgsVectorLayer(out_path, 'VWorld_연속지적도_선택SHP인근', 'ogr')
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log('VWorld 연속지적도 레이어를 추가했습니다. 필지 수: %s' % layer.featureCount())
            self.log('임시 GeoJSON: %s' % out_path)
            return layer

        self.log('오류: VWorld 응답을 GeoJSON으로 저장했지만 QGIS 레이어로 열지 못했습니다.')
        self.log('임시 GeoJSON: %s' % out_path)
        return None
