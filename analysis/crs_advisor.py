# -*- coding: utf-8 -*-

import json
import math
import urllib.parse
import urllib.request

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)

from ..settings import SettingsStore


class CRSAdvisor:
    CANDIDATES = [4326, 5174, 5179, 5181, 5186, 5187, 5188]
    KOREA_BOUNDS = (124.0, 132.5, 32.0, 39.8)

    def __init__(self, iface):
        self.iface = iface
        self.settings = SettingsStore()

    def recommend(self, layer):
        if not layer or not layer.isValid():
            return None
        extent = layer.extent()
        if extent.isNull() or extent.isEmpty():
            return None

        xmin, xmax = extent.xMinimum(), extent.xMaximum()
        ymin, ymax = extent.yMinimum(), extent.yMaximum()
        if 120 < xmin < 140 and 120 < xmax < 140 and 30 < ymin < 45 and 30 < ymax < 45:
            return {
                "epsg": 4326,
                "score": 99,
                "reason": "X·Y 좌표값이 대한민국 경위도 범위와 일치합니다.",
            }

        ranked = []
        for epsg in self.CANDIDATES:
            if epsg == 4326:
                continue
            item = self._evaluate_candidate(extent, epsg)
            if item:
                ranked.append(item)

        if not ranked:
            return None
        ranked.sort(key=lambda item: item["score"], reverse=True)
        best = ranked[0]
        second = ranked[1]["score"] if len(ranked) > 1 else 0
        if best["score"] < 72 or best["score"] - second < 12:
            return None
        return {
            "epsg": best["epsg"],
            "score": min(99, int(round(best["score"]))),
            "reason": best["reason"],
        }

    def recommend_by_address(self, layer, address):
        found = self._search_vworld(address)
        if not found:
            return None

        center = QgsPointXY(layer.extent().center())
        candidates = []
        for epsg in self.CANDIDATES:
            point = self._transform(center, epsg, 4326)
            if not point or not self._inside_korea(point.x(), point.y()):
                continue
            distance = self._haversine_m(
                point.x(), point.y(), found["longitude"], found["latitude"]
            )
            candidates.append({
                "epsg": epsg,
                "distance_m": distance,
                "longitude": point.x(),
                "latitude": point.y(),
            })

        if not candidates:
            return None
        candidates.sort(key=lambda item: item["distance_m"])
        best = candidates[0]
        return {
            "query": address,
            "matched_address": found.get("matched_address", address),
            "longitude": found["longitude"],
            "latitude": found["latitude"],
            "epsg": best["epsg"],
            "distance_m": best["distance_m"],
            "candidates": candidates[:5],
        }

    def _search_vworld(self, query):
        key = self.settings.vworld_key
        if not key:
            raise RuntimeError(
                "VWorld API Key가 없습니다. API Key 설정에서 먼저 저장하세요."
            )

        for search_type in ("address", "place"):
            params = {
                "service": "search",
                "request": "search",
                "version": "2.0",
                "crs": "EPSG:4326",
                "size": 10,
                "page": 1,
                "query": query,
                "type": search_type,
                "format": "json",
                "errorformat": "json",
                "key": key,
            }
            url = "https://api.vworld.kr/req/search?" + urllib.parse.urlencode(params)
            request = urllib.request.Request(
                url, headers={"User-Agent": "QGIS-EIA-AI-Assistant/0.4"}
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))

            response_data = payload.get("response", {})
            if response_data.get("status") != "OK":
                continue
            items = response_data.get("result", {}).get("items", [])
            if not items:
                continue

            item = items[0]
            point = item.get("point", {})
            try:
                lon = float(point["x"])
                lat = float(point["y"])
            except (KeyError, TypeError, ValueError):
                continue

            address_data = item.get("address", {})
            matched = (
                address_data.get("parcel")
                or address_data.get("road")
                or item.get("title", "")
                or query
            )
            return {
                "longitude": lon,
                "latitude": lat,
                "matched_address": matched,
            }
        return None

    def _evaluate_candidate(self, extent, epsg):
        center = QgsPointXY(extent.center())
        p = self._transform(center, epsg, 4326)
        if not p or not self._inside_korea(p.x(), p.y()):
            return None

        score = 55.0
        reason = ["변환된 중심점이 대한민국 영역에 포함됩니다"]
        x, y = center.x(), center.y()

        if epsg == 5179:
            if 700000 <= x <= 1400000 and 1200000 <= y <= 2300000:
                score += 38
                reason.append("통합좌표계 좌표값 규모와 일치합니다")
            else:
                score -= 12
        elif epsg == 5174:
            if 50000 <= x <= 450000 and 50000 <= y <= 700000:
                score += 24
                reason.append("Korean 1985 중부원점 계열과 유사합니다")
            else:
                score -= 8
        elif epsg in (5181, 5186, 5187, 5188):
            if 50000 <= x <= 450000 and 300000 <= y <= 750000:
                score += {5181: 24, 5186: 30, 5187: 28, 5188: 27}[epsg]
                reason.append("Korea 2000 TM 계열 좌표값 규모와 유사합니다")
            else:
                score -= 8

        center_distance = abs(p.x() - 127.8) + abs(p.y() - 36.3)
        if center_distance <= 1.0:
            score += 8
        elif center_distance <= 2.5:
            score += 5
        elif center_distance <= 4.5:
            score += 2

        return {"epsg": epsg, "score": score, "reason": "; ".join(reason)}

    def _transform(self, point, source_epsg, target_epsg):
        source = QgsCoordinateReferenceSystem.fromEpsgId(source_epsg)
        target = QgsCoordinateReferenceSystem.fromEpsgId(target_epsg)
        if not source.isValid() or not target.isValid():
            return None
        transform = QgsCoordinateTransform(
            source, target, QgsProject.instance().transformContext()
        )
        try:
            return transform.transform(point)
        except Exception:
            return None

    def _inside_korea(self, lon, lat):
        xmin, xmax, ymin, ymax = self.KOREA_BOUNDS
        return xmin <= lon <= xmax and ymin <= lat <= ymax

    def _haversine_m(self, lon1, lat1, lon2, lat2):
        radius = 6371008.8
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        value = (
            math.sin(dphi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        )
        return 2 * radius * math.atan2(math.sqrt(value), math.sqrt(1 - value))
