# -*- coding: utf-8 -*-

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsRectangle
)


class CRSAdvisor:

    CANDIDATES = [
        4326,
        5174,
        5179,
        5181,
        5186,
        5187,
        5188
    ]

    def __init__(self, iface):
        self.iface = iface

    def recommend(self, layer):
        """
        좌표계 추천
        반환:
            {
                "epsg":5179,
                "score":95,
                "reason":"좌표범위가 Korea2000 Unified와 일치"
            }
        """

        extent = layer.extent()

        xmin = extent.xMinimum()
        xmax = extent.xMaximum()
        ymin = extent.yMinimum()
        ymax = extent.yMaximum()

        # 1차 판정
        if (
            120 < xmin < 140 and
            120 < xmax < 140 and
             30 < ymin <  45 and
             30 < ymax <  45
        ):
            return {
                "epsg":4326,
                "score":99,
                "reason":"경위도 좌표"
            }

        # TODO
        # 이후 한국 TM계열 자동판단

        return None