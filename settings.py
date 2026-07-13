# -*- coding: utf-8 -*-

from qgis.core import QgsSettings


ORG = "qgis_eia_ai_assistant"


class SettingsStore:
    def __init__(self):
        self.s = QgsSettings()

    def get(self, key, default=""):
        return self.s.value(
            "%s/%s" % (ORG, key),
            default,
        )

    def set(self, key, value):
        self.s.setValue(
            "%s/%s" % (ORG, key),
            value or "",
        )

    @property
    def vworld_key(self):
        return self.get("vworld_key")

    @property
    def eia_key(self):
        return self.get("eia_key")

    @property
    def ecology_key(self):
        return self.get("ecology_key")

    @property
    def ngii_key(self):
        """
        국토지리정보원 국토정보플랫폼 OpenAPI 인증키.

        QGIS 사용자 설정에 저장되며 플러그인 소스나 ZIP에는
        직접 기록되지 않습니다.
        """
        return self.get("ngii_key")
