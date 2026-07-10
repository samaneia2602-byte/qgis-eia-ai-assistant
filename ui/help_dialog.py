# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QMessageBox

HELP_TEXT = '''사용 가능한 주요 명령어

[파일/화면]
- 파일열기 실행해줘
- qgis 종료해줘
- 현재 선택된 shp를 중심으로 10km 범위까지 줌아웃해줘

[지적도/VWorld]
- 지적도 불러와줘
- 현재 화면에 지적도 불러와줘
- 지목별 면적 집계해줘
- 현재 선택된 레이어에 지목 속성값 정리해줘

[표고·경사]
- 현재 선택한 사업구역에 대해 표고경사 분석해서 엑셀로 출력해줘
- 등고선 분석해서 엑셀 저장해줘

[환경영향평가/생태자연도]
- 환경영향평가 사업구역 불러와줘
- 생태자연도 불러와줘
- API Key 설정해줘
'''

def show_help(parent):
    QMessageBox.information(parent, '도움말', HELP_TEXT)
