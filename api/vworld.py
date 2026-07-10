# -*- coding: utf-8 -*-
"""VWorld 2D Data API loaders.

기능
- 활성 레이어 또는 선택 피처의 전체 범위를 EPSG:5179 기준 격자로 분할
- VWorld 연속지적도(LP_PA_CBND_BUBUN)를 격자별·페이지별 조회
- PNU 기준 중복 제거 후 하나의 GeoJSON/QGIS 레이어로 병합

QGIS 3.16 / Python 3 호환.
"""
from __future__ import absolute_import

import json
import math
import os
import tempfile
import time
import urllib.parse
import urllib.request

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)

from .geometry import active_or_selected_extent
from ..settings import SettingsStore

VWORLD_DATA_URL = 'https://api.vworld.kr/req/data'
CADASTRAL_DATA_NAME = 'LP_PA_CBND_BUBUN'
CADASTRAL_COLUMNS = 'pnu,jibun,bonbun,bubun,addr,gosi_year,gosi_month,jiga'

# VWorld 1회 공간조회 제한(2km²)보다 여유 있게 작은 타일을 사용합니다.
TILE_AREA_M2 = 1600000.0
TILE_SIDE_M = math.sqrt(TILE_AREA_M2)
DEFAULT_BUFFER_M = 50.0
PAGE_SIZE = 1000
MAX_PAGES_PER_TILE = 30
MAX_TILE_COUNT = 250
REQUEST_RETRY_COUNT = 3
REQUEST_DELAY_SEC = 0.08


class VWorldManager(object):
    def __init__(self, iface, log):
        self.iface = iface
        self.log = log
        self.store = SettingsStore()

    def _metric_extent_and_transform(self):
        """활성/선택 레이어 범위를 EPSG:5179로 변환하고 요청용 버퍼를 적용."""
        extent, crs = active_or_selected_extent(self.iface)
        metric = QgsCoordinateReferenceSystem('EPSG:5179')
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        to_metric = QgsCoordinateTransform(crs, metric, QgsProject.instance())
        to_wgs84 = QgsCoordinateTransform(metric, wgs84, QgsProject.instance())
        mrect = to_metric.transformBoundingBox(extent)
        mrect.grow(DEFAULT_BUFFER_M)
        return mrect, to_wgs84

    def _make_tiles(self, rect):
        """EPSG:5179 사각형을 2km² 미만의 격자로 분할."""
        width = max(rect.width(), 1.0)
        height = max(rect.height(), 1.0)
        cols = max(1, int(math.ceil(width / TILE_SIDE_M)))
        rows = max(1, int(math.ceil(height / TILE_SIDE_M)))
        count = cols * rows
        if count > MAX_TILE_COUNT:
            raise ValueError(
                '조회 범위가 너무 넓어 %s개 격자가 필요합니다(최대 %s개). '
                '사업지역 일부를 선택하거나 범위를 나누어 실행하세요.' % (count, MAX_TILE_COUNT)
            )

        tile_w = width / float(cols)
        tile_h = height / float(rows)
        tiles = []
        for row in range(rows):
            ymin = rect.yMinimum() + row * tile_h
            ymax = rect.yMaximum() if row == rows - 1 else ymin + tile_h
            for col in range(cols):
                xmin = rect.xMinimum() + col * tile_w
                xmax = rect.xMaximum() if col == cols - 1 else xmin + tile_w
                tiles.append(QgsRectangle(xmin, ymin, xmax, ymax))
        return tiles, rows, cols

    def _extract_geojson(self, payload):
        """VWorld JSON 응답에서 FeatureCollection 추출."""
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

    def _request_page(self, key, bbox, page):
        params = {
            'service': 'data',
            'version': '2.0',
            'request': 'GetFeature',
            'key': key,
            'format': 'json',
            'errorFormat': 'json',
            'size': PAGE_SIZE,
            'page': page,
            'data': CADASTRAL_DATA_NAME,
            'geomFilter': 'BOX(%s)' % bbox,
            'columns': CADASTRAL_COLUMNS,
            'geometry': 'true',
            'attribute': 'true',
            'crs': 'EPSG:4326',
        }
        url = VWORLD_DATA_URL + '?' + urllib.parse.urlencode(params)
        last_error = None
        for attempt in range(1, REQUEST_RETRY_COUNT + 1):
            try:
                req = urllib.request.Request(
                    url,
                    headers={'User-Agent': 'QGIS-EIA-AI-Assistant/0.3'}
                )
                with urllib.request.urlopen(req, timeout=40) as res:
                    raw = res.read().decode('utf-8')
                payload = json.loads(raw)
                root = payload.get('response') if isinstance(payload, dict) and isinstance(payload.get('response'), dict) else payload
                status = root.get('status') if isinstance(root, dict) else None
                if status and status != 'OK':
                    message = ''
                    if isinstance(root.get('error'), dict):
                        message = root['error'].get('text') or root['error'].get('code') or ''
                    raise RuntimeError('VWorld status=%s %s' % (status, message))
                return self._extract_geojson(payload)
            except Exception as exc:
                last_error = exc
                if attempt < REQUEST_RETRY_COUNT:
                    time.sleep(0.4 * attempt)
        raise RuntimeError(str(last_error))

    @staticmethod
    def _feature_key(feature):
        """타일 경계에서 중복 수신된 필지를 제거하기 위한 키."""
        props = feature.get('properties') or {}
        pnu = props.get('pnu') or props.get('PNU')
        if pnu:
            return 'pnu:' + str(pnu)
        fid = feature.get('id')
        if fid is not None:
            return 'id:' + str(fid)
        # PNU/ID가 없는 예외 응답은 형상+속성을 안정적으로 직렬화해 비교합니다.
        return 'raw:' + json.dumps(
            {'geometry': feature.get('geometry'), 'properties': props},
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':')
        )

    def load_cadastral_by_active_layer(self):
        key = self.store.vworld_key
        if not key:
            self.log('오류: VWorld API Key가 없습니다. [API Key 설정]에서 VWorld API Key를 저장하세요.')
            return None

        try:
            metric_extent, to_wgs84 = self._metric_extent_and_transform()
            tiles, rows, cols = self._make_tiles(metric_extent)
        except Exception as exc:
            self.log('오류: 지적도 조회 범위를 만들지 못했습니다: %s' % exc)
            return None

        area_km2 = metric_extent.width() * metric_extent.height() / 1000000.0
        self.log(
            'VWorld 연속지적도 분할조회 시작: 범위 약 %.2fkm², %s행 × %s열 = %s개 격자' %
            (area_km2, rows, cols, len(tiles))
        )

        merged = []
        seen = set()
        failed_tiles = []
        total_pages = 0

        for tile_index, metric_tile in enumerate(tiles, 1):
            QCoreApplication.processEvents()
            e4326 = to_wgs84.transformBoundingBox(metric_tile)
            bbox = '%.8f,%.8f,%.8f,%.8f' % (
                e4326.xMinimum(), e4326.yMinimum(),
                e4326.xMaximum(), e4326.yMaximum()
            )
            tile_feature_count = 0
            try:
                for page in range(1, MAX_PAGES_PER_TILE + 1):
                    fc = self._request_page(key, bbox, page)
                    features = fc.get('features', []) if fc else []
                    total_pages += 1
                    for feature in features:
                        feature_key = self._feature_key(feature)
                        if feature_key not in seen:
                            seen.add(feature_key)
                            merged.append(feature)
                            tile_feature_count += 1
                    if len(features) < PAGE_SIZE:
                        break
                    if page == MAX_PAGES_PER_TILE:
                        self.log(
                            '경고: 격자 %s의 조회 결과가 %s페이지를 초과하여 일부가 누락될 수 있습니다.' %
                            (tile_index, MAX_PAGES_PER_TILE)
                        )
                    time.sleep(REQUEST_DELAY_SEC)
                self.log(
                    '격자 %s/%s 완료: 신규 필지 %s, 누적 %s' %
                    (tile_index, len(tiles), tile_feature_count, len(merged))
                )
            except Exception as exc:
                failed_tiles.append((tile_index, bbox, str(exc)))
                self.log('경고: 격자 %s/%s 조회 실패: %s' % (tile_index, len(tiles), exc))

        if not merged:
            self.log('조회 결과가 없습니다. 선택 SHP 위치와 VWorld 인증키 서비스 URL을 확인하세요.')
            return None

        feature_collection = {
            'type': 'FeatureCollection',
            'name': 'VWorld_연속지적도_선택SHP인근',
            'crs': {
                'type': 'name',
                'properties': {'name': 'urn:ogc:def:crs:OGC:1.3:CRS84'}
            },
            'features': merged,
        }

        out_dir = os.path.join(tempfile.gettempdir(), 'qgis_eia_ai_assistant')
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        out_path = os.path.join(out_dir, 'vworld_cadastral_merged_%s.geojson' % int(time.time()))
        with open(out_path, 'w', encoding='utf-8') as output:
            json.dump(feature_collection, output, ensure_ascii=False)

        layer = QgsVectorLayer(out_path, 'VWorld_연속지적도_선택SHP인근_분할조회', 'ogr')
        if not layer.isValid():
            self.log('오류: 병합 GeoJSON을 만들었지만 QGIS 레이어로 열지 못했습니다.')
            self.log('임시 GeoJSON: %s' % out_path)
            return None

        QgsProject.instance().addMapLayer(layer)
        self.log(
            'VWorld 연속지적도 분할조회 완료: 격자 %s개, API 페이지 %s회, 중복제거 후 필지 %s개' %
            (len(tiles), total_pages, layer.featureCount())
        )
        if failed_tiles:
            self.log(
                '주의: 전체 %s개 격자 중 %s개가 실패했습니다. 같은 명령을 다시 실행하거나 범위를 나누어 확인하세요.' %
                (len(tiles), len(failed_tiles))
            )
        self.log('임시 GeoJSON: %s' % out_path)
        return layer
