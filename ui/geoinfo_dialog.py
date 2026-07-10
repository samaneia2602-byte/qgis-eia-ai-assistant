# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QDialog, QVBoxLayout, QListWidget, QHBoxLayout, QPushButton, QLabel

class GeoInfoDialog(QDialog):
    def __init__(self, parent, api_manager):
        super().__init__(parent)
        self.api = api_manager
        self.setWindowTitle('지리정보 불러오기')
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('불러올 지리정보를 선택하세요. 토지피복지도 항목은 제외했습니다.'))
        self.list = QListWidget()
        items = [
            ('환경영향평가 / 사업구역 WFS 조회', 'eia_wfs'),
            ('환경영향평가 / 사업구역 WMS 조회', 'eia_wms'),
            ('생태자연도 / 생태자연도 WFS 조회', 'eco_wfs'),
            ('생태자연도 / 생태자연도 WMS 조회', 'eco_wms'),
        ]
        for label, key in items:
            self.list.addItem(label)
            self.list.item(self.list.count()-1).setData(32, key)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        self.load_btn = QPushButton('선택 지리정보 불러오기')
        self.close_btn = QPushButton('닫기')
        row.addWidget(self.load_btn); row.addWidget(self.close_btn)
        layout.addLayout(row)
        self.load_btn.clicked.connect(self.load_selected)
        self.close_btn.clicked.connect(self.close)

    def load_selected(self):
        item = self.list.currentItem()
        if not item: return
        key = item.data(32)
        if key == 'eia_wfs': self.api.load_eia_bsnsarea_wfs()
        elif key == 'eia_wms': self.api.load_eia_bsnsarea_wms()
        elif key == 'eco_wfs': self.api.load_ecology_wfs()
        elif key == 'eco_wms': self.api.load_ecology_wms()
