# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QUrl
from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer, QgsCoordinateReferenceSystem
from .geometry import active_or_selected_extent, extent_to_epsg4326, center_to_epsg4326
from ..settings import SettingsStore
import urllib.parse
from .vworld import VWorldManager

class ApiManager:
    def __init__(self, iface, log):
        self.iface = iface
        self.log = log
        self.store = SettingsStore()
        self.vworld = VWorldManager(iface, log)

    def _add_wms(self, name, base_url, layers, crs='EPSG:3857', styles='', fmt='image/png', extra=None):
        params = {
            'url': base_url,
            'layers': layers,
            'styles': styles,
            'format': fmt,
            'crs': crs,
        }
        if extra:
            params.update(extra)
        uri = '&'.join('%s=%s' % (k, urllib.parse.quote(str(v), safe=':/?&=,%')) for k, v in params.items())
        layer = QgsRasterLayer(uri, name, 'wms')
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log('레이어 추가: %s' % name)
            return layer
        self.log('오류: WMS 레이어를 추가하지 못했습니다. API URL/파라미터를 확인하세요.')
        return None

    def load_eia_bsnsarea_wfs(self):
        key = self.store.eia_key
        if not key:
            self.log('오류: 환경영향평가 API Key가 없습니다. API Key 설정에서 저장하세요.')
            return None
        extent, crs = active_or_selected_extent(self.iface)
        c = center_to_epsg4326(extent, crs)
        base = 'https://apis.data.go.kr/1480523/BsnsAreaService/getInfoWFS'
        url = base + '?' + urllib.parse.urlencode({'serviceKey': key, 'centerX': c.x(), 'centerY': c.y()})
        self.log('환경영향평가 사업구역 WFS 조회 중심좌표 EPSG:4326 = %.6f, %.6f' % (c.x(), c.y()))
        layer = QgsVectorLayer(url, '환경영향평가_사업구역_WFS', 'ogr')
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            return layer
        self.log('오류: WFS를 직접 레이어로 추가하지 못했습니다. API 응답 형식/XML 필드 확인이 필요합니다.')
        self.log('호출 URL: %s' % url)
        return None

    def load_eia_bsnsarea_wms(self):
        key = self.store.eia_key
        if not key:
            self.log('오류: 환경영향평가 API Key가 없습니다. API Key 설정에서 저장하세요.')
            return None
        extent, crs = active_or_selected_extent(self.iface)
        c = center_to_epsg4326(extent, crs)
        base = 'https://apis.data.go.kr/1480523/BsnsAreaService/getInfoWMS'
        # 공공데이터 API 문서에서 실제 명칭이 다를 수 있어, 누락되기 쉬운 이미지 파라미터를 함께 전달합니다.
        query = {
            'serviceKey': key,
            'centerX': c.x(),
            'centerY': c.y(),
            'coordType': 'EPSG:4326',
            'width': 1024,
            'height': 1024,
            'imgType': 'png',
            'background': 'transparent',
        }
        url = base + '?' + urllib.parse.urlencode(query)
        # REST 이미지 반환이면 WMS provider가 아닌 raster url provider가 필요할 수 있음. 먼저 직접 URL 레이어 시도.
        layer = QgsRasterLayer(url, '환경영향평가_사업구역_WMS')
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log('환경영향평가 사업구역 WMS 이미지를 추가했습니다.')
            return layer
        self.log('오류: WMS 이미지를 레이어로 추가하지 못했습니다. 브라우저에서 호출 URL을 확인하세요.')
        self.log('호출 URL: %s' % url)
        return None

    def load_ecology_wfs(self):
        key = self.store.ecology_key
        if not key:
            self.log('오류: 생태자연도 API Key가 없습니다. 생태자연도 API Key 설정에서 저장하세요.')
            return None
        extent, crs = active_or_selected_extent(self.iface)
        e4326 = extent_to_epsg4326(extent, crs)
        base = 'https://apis.data.go.kr/B553084/ecopias/ecoapi/EcologyzmpService/getEcologyzmpWFS'
        bbox = '%.8f,%.8f,%.8f,%.8f' % (e4326.xMinimum(), e4326.yMinimum(), e4326.xMaximum(), e4326.yMaximum())
        query = {'serviceKey': key, 'bbox': bbox, 'coordType': 'EPSG:4326'}
        url = base + '?' + urllib.parse.urlencode(query)
        self.log('생태자연도 WFS BBOX EPSG:4326 = %s' % bbox)
        layer = QgsVectorLayer(url, '생태자연도_WFS', 'ogr')
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            return layer
        self.log('오류: 생태자연도 WFS를 추가하지 못했습니다. API 문서의 필수 파라미터명을 확인하세요.')
        self.log('호출 URL: %s' % url)
        return None

    def load_ecology_wms(self):
        key = self.store.ecology_key
        if not key:
            self.log('오류: 생태자연도 API Key가 없습니다. 생태자연도 API Key 설정에서 저장하세요.')
            return None
        extent, crs = active_or_selected_extent(self.iface)
        e4326 = extent_to_epsg4326(extent, crs)
        base = 'https://apis.data.go.kr/B553084/ecopias/ecoapi/EcologyzmpService/getEcologyzmpWMS'
        query = {
            'serviceKey': key,
            'minx': e4326.xMinimum(), 'miny': e4326.yMinimum(),
            'maxx': e4326.xMaximum(), 'maxy': e4326.yMaximum(),
            'coordType': 'EPSG:4326', 'width': 1024, 'height': 1024,
            'imgType': 'png', 'background': 'transparent'
        }
        url = base + '?' + urllib.parse.urlencode(query)
        layer = QgsRasterLayer(url, '생태자연도_WMS')
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log('생태자연도 WMS 이미지를 추가했습니다.')
            return layer
        self.log('오류: 생태자연도 WMS를 추가하지 못했습니다. API 문서의 필수 파라미터명을 확인하세요.')
        self.log('호출 URL: %s' % url)
        return None

    def load_vworld_cadastral(self):
        return self.vworld.load_cadastral_by_active_layer()
