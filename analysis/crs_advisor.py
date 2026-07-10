# -*- coding: utf-8 -*-

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)


class CRSAdvisor:
    """좌표계가 없는 국내 벡터 레이어의 후보 CRS를 평가합니다."""

    CANDIDATES = [4326, 5174, 5179, 5181, 5186, 5187, 5188]

    KOREA_BOUNDS = {
        "xmin": 124.0,
        "xmax": 132.5,
        "ymin": 32.0,
        "ymax": 39.8,
    }

    def __init__(self, iface):
        self.iface = iface

    def recommend(self, layer):
        """가장 가능성이 높은 좌표계를 반환합니다.

        후보 간 점수 차이가 작으면 잘못된 자동 지정을 막기 위해 None을 반환합니다.
        """
        if not layer or not layer.isValid():
            return None

        extent = layer.extent()
        if extent.isNull() or extent.isEmpty():
            return None

        xmin = extent.xMinimum()
        xmax = extent.xMaximum()
        ymin = extent.yMinimum()
        ymax = extent.yMaximum()

        if self._looks_like_lonlat(xmin, xmax, ymin, ymax):
            return {
                "epsg": 4326,
                "score": 99,
                "reason": "X·Y 좌표값이 대한민국 경위도 범위와 일치합니다.",
                "alternatives": [],
            }

        ranked = []
        for epsg in self.CANDIDATES:
            if epsg == 4326:
                continue
            result = self._evaluate_candidate(extent, epsg)
            if result:
                ranked.append(result)

        if not ranked:
            return None

        ranked.sort(key=lambda item: item["score"], reverse=True)
        best = ranked[0]
        second_score = ranked[1]["score"] if len(ranked) > 1 else 0
        gap = best["score"] - second_score

        if best["score"] < 72 or gap < 12:
            return None

        return {
            "epsg": best["epsg"],
            "score": min(99, int(round(best["score"]))),
            "reason": best["reason"],
            "alternatives": ranked[1:4],
        }

    def _looks_like_lonlat(self, xmin, xmax, ymin, ymax):
        return (
            120.0 < xmin < 140.0
            and 120.0 < xmax < 140.0
            and 30.0 < ymin < 45.0
            and 30.0 < ymax < 45.0
        )

    def _evaluate_candidate(self, extent, epsg):
        source = QgsCoordinateReferenceSystem.fromEpsgId(epsg)
        target = QgsCoordinateReferenceSystem.fromEpsgId(4326)
        if not source.isValid() or not target.isValid():
            return None

        transform = QgsCoordinateTransform(
            source,
            target,
            QgsProject.instance().transformContext(),
        )

        center = QgsPointXY(extent.center())
        try:
            center4326 = transform.transform(center)
        except Exception:
            return None

        lon = center4326.x()
        lat = center4326.y()
        if not self._inside_korea(lon, lat):
            return None

        score = 55.0
        reasons = ["변환된 중심점이 대한민국 영역에 포함됩니다"]

        x = center.x()
        y = center.y()
        signature_score, signature_reason = self._coordinate_signature(epsg, x, y)
        score += signature_score
        if signature_reason:
            reasons.append(signature_reason)

        score += self._korea_center_score(lon, lat)

        return {
            "epsg": epsg,
            "score": score,
            "reason": "; ".join(reasons),
            "longitude": lon,
            "latitude": lat,
        }

    def _inside_korea(self, lon, lat):
        bounds = self.KOREA_BOUNDS
        return (
            bounds["xmin"] <= lon <= bounds["xmax"]
            and bounds["ymin"] <= lat <= bounds["ymax"]
        )

    def _coordinate_signature(self, epsg, x, y):
        if epsg == 5179:
            if 700000 <= x <= 1400000 and 1200000 <= y <= 2300000:
                return 38.0, "좌표값 규모가 Korea 2000 통합좌표계 형식과 일치합니다"
            return -12.0, "통합좌표계의 일반적인 좌표값 규모와 차이가 있습니다"

        if epsg == 5174:
            if 50000 <= x <= 450000 and 50000 <= y <= 700000:
                return 24.0, "좌표값 규모가 Korean 1985 중부원점 계열과 유사합니다"
            return -8.0, "Korean 1985 계열의 일반적인 좌표값 규모와 차이가 있습니다"

        if epsg in (5181, 5186):
            if 50000 <= x <= 450000 and 300000 <= y <= 750000:
                bonus = 30.0 if epsg == 5186 else 24.0
                return bonus, "좌표값 규모가 Korea 2000 중부원점 계열과 유사합니다"
            return -8.0, "중부원점 계열의 일반적인 좌표값 규모와 차이가 있습니다"

        if epsg == 5187:
            if 50000 <= x <= 450000 and 300000 <= y <= 750000:
                return 28.0, "좌표값 규모가 Korea 2000 동부원점 계열과 유사합니다"
            return -8.0, "동부원점 계열의 일반적인 좌표값 규모와 차이가 있습니다"

        if epsg == 5188:
            if 50000 <= x <= 450000 and 300000 <= y <= 750000:
                return 27.0, "좌표값 규모가 Korea 2000 동해원점 계열과 유사합니다"
            return -8.0, "동해원점 계열의 일반적인 좌표값 규모와 차이가 있습니다"

        return 0.0, ""

    def _korea_center_score(self, lon, lat):
        dx = abs(lon - 127.8)
        dy = abs(lat - 36.3)
        distance = dx + dy

        if distance <= 1.0:
            return 8.0
        if distance <= 2.5:
            return 5.0
        if distance <= 4.5:
            return 2.0
        return 0.0