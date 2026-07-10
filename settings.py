# -*- coding: utf-8 -*-
from qgis.core import QgsSettings

ORG = 'qgis_eia_ai_assistant'

class SettingsStore:
    def __init__(self):
        self.s = QgsSettings()

    def get(self, key, default=''):
        return self.s.value('%s/%s' % (ORG, key), default)

    def set(self, key, value):
        self.s.setValue('%s/%s' % (ORG, key), value or '')

    @property
    def vworld_key(self): return self.get('vworld_key')
    @property
    def eia_key(self): return self.get('eia_key')
    @property
    def ecology_key(self): return self.get('ecology_key')
