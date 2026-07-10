# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QFileDialog
from qgis.core import QgsProject, QgsVectorLayer, QgsRectangle

from .parser import parse_command
from ..analysis.jimok import cleanup_jimok
from ..api.api_manager import ApiManager
from ..ui.api_settings import ApiSettingsDialog


class CommandExecutor:
    def __init__(self, iface, log):
        self.iface = iface
        self.log = log
        self.api = ApiManager(iface, log)

    def execute(self, text):
        cmd = parse_command(text)
        if cmd == 'api_settings':
            return ApiSettingsDialog(self.iface.mainWindow()).exec_()
        if cmd == 'quit':
            self.iface.mainWindow().close()
            return
        if cmd == 'open_file':
            return self.open_file()
        if cmd == 'jimok_cleanup':
            return self.jimok_cleanup()
        if cmd == 'eia_wfs':
            return self.api.load_eia_bsnsarea_wfs()
        if cmd == 'ecology_wfs':
            return self.api.load_ecology_wfs()
        if cmd == 'terrain_excel':
            self.log('표고·경사 분석은 analysis/terrain.py 확장 모듈에서 실행하도록 구조화되어 있습니다. 현재 버전은 분석 엔진 연결 전 단계입니다.')
            return
        if cmd == 'cadastral_load':
            return self.api.load_vworld_cadastral()
        if cmd == 'cadastral_stats':
            self.log('지목별 면적 집계는 지목 필드 정리 후 면적 집계 모듈과 연결할 예정입니다.')
            return
        if cmd == 'zoomout_10km':
            return self.zoomout_10km()
        self.log('알 수 없는 명령입니다. [? 도움말]을 확인하세요.')

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self.iface.mainWindow(),
            '벡터 파일 열기',
            '',
            'Vector files (*.shp *.gpkg *.geojson *.dxf);;All files (*.*)',
        )
        if not path:
            return

        layer = QgsVectorLayer(path, path.split('/')[-1], 'ogr')
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self.log('파일을 열었습니다: %s' % path)
        else:
            self.log('오류: 파일을 열 수 없습니다.')

    def jimok_cleanup(self):
        layer = self.iface.activeLayer()
        if not layer:
            self.log('오류: 현재 선택된 레이어가 없습니다.')
            return

        if not isinstance(layer, QgsVectorLayer):
            self.log('오류: 현재 선택된 레이어가 벡터 레이어가 아닙니다.')
            return

        source_fields = {'jibun', 'bonbun', 'bubun', 'addr'}
        available = {field.name() for field in layer.fields()}
        if not source_fields.intersection(available):
            self.log(
                '오류: jibun, bonbun, bubun, addr 필드 중 하나 이상이 필요합니다.'
            )
            return

        count = cleanup_jimok(layer, field_name='지목')
        self.log(
            "'지목' 필드를 생성/갱신했습니다. 변경된 객체 수: %s" % count
        )

    def zoomout_10km(self):
        layer = self.iface.activeLayer()
        if not layer:
            self.log('오류: 현재 선택된 레이어가 없습니다.')
            return

        ex = layer.extent()
        rect = QgsRectangle(ex)
        rect.grow(10000)
        self.iface.mapCanvas().setExtent(rect)
        self.iface.mapCanvas().refresh()
        self.log('선택 레이어 범위 기준 10km 줌아웃했습니다.')
