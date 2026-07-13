# -*- coding: utf-8 -*-

import urllib.parse

from qgis.core import (
    QgsProject,
    QgsRasterLayer,
)

from ..settings import SettingsStore


class NgiiManager:
    """
    국토지리정보원 국토정보플랫폼 OnMap 타일 레이어 로더.

    현재는 국토정보플랫폼의 Gettile.do 형식 WMTS 타일 URL을
    QGIS XYZ 레이어로 등록합니다.
    """

    DEFAULT_LAYER_NAME = "국토지리정보원_온맵"

    def __init__(self, iface, log):
        self.iface = iface
        self.log = log
        self.store = SettingsStore()

    def load_onmap(self):
        api_key = (self.store.ngii_key or "").strip()

        if not api_key:
            self.log(
                "오류: 국토지리정보원 API Key가 없습니다. "
                "API Key 설정에서 먼저 저장하세요."
            )
            return None

        # 국토지리정보원 타일 호출 형식.
        # API 승인 문서에서 URL이 다르게 안내될 경우
        # BASE_URL과 layer/style/tilematrixset 값만 조정하면 됩니다.
        base_url = "https://map.ngii.go.kr/openapi/Gettile.do"

        query = {
            "apikey": api_key,
            "layer": "korean_map",
            "style": "korean",
            "tilematrixset": "korean",
            "Service": "WMTS",
            "Request": "GetTile",
            "Version": "1.0.0",
            "Format": "image/png",
            "TileMatrix": "{z}",
            "TileCol": "{x}",
            "TileRow": "{y}",
        }

        tile_url = (
            base_url
            + "?"
            + urllib.parse.urlencode(
                query,
                safe="{}",
            )
        )

        # QGIS XYZ provider URI
        uri = (
            "type=xyz"
            "&url=%s"
            "&zmin=0"
            "&zmax=19"
            "&crs=EPSG:3857"
        ) % urllib.parse.quote(
            tile_url,
            safe=":/?&=%{}",
        )

        layer = QgsRasterLayer(
            uri,
            self.DEFAULT_LAYER_NAME,
            "wms",
        )

        if not layer.isValid():
            self.log(
                "오류: 온맵 레이어를 생성하지 못했습니다. "
                "API 승인 상태와 국토정보플랫폼의 타일 URL을 확인하세요."
            )
            self.log(
                "참고: 승인 문서의 OnMap WMTS 호출 URL이 "
                "현재 Gettile.do 형식과 다른 경우 URL 수정이 필요합니다."
            )
            return None

        QgsProject.instance().addMapLayer(
            layer,
            False,
        )

        root = QgsProject.instance().layerTreeRoot()
        root.insertLayer(
            len(root.children()),
            layer,
        )

        self.log(
            "국토지리정보원 온맵 레이어를 불러왔습니다."
        )

        active = self.iface.activeLayer()
        if active and active.isValid():
            try:
                self.iface.mapCanvas().setExtent(
                    active.extent()
                )
                self.iface.mapCanvas().refresh()
            except Exception:
                pass

        return layer
