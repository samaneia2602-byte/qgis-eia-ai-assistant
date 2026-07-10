# -*- coding: utf-8 -*-

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)


class CrsAddressDialog(QDialog):
    def __init__(self, parent, layer, advisor):
        super().__init__(parent)
        self.layer = layer
        self.advisor = advisor
        self.result = None

        self.setWindowTitle("좌표계 주소 검색")
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        self.resize(560, 380)

        layout = QVBoxLayout(self)
        guide = QLabel(
            "좌표계를 검색합니다.\n"
            "대상지역의 주소를 읍·면·동 단위로 입력해 주세요.\n"
            "예: 충청북도 괴산군 연풍면"
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)

        form = QFormLayout()
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("예: 세종특별자치시 금남면")
        form.addRow("대상지역:", self.address_edit)
        layout.addLayout(form)

        self.search_button = QPushButton("주소 검색 및 좌표계 추천")
        self.search_button.clicked.connect(self.search)
        layout.addWidget(self.search_button)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("주소 검색 결과와 좌표계 후보가 여기에 표시됩니다.")
        layout.addWidget(self.output)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.apply_button = self.buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("추천 좌표계 적용")
        self.apply_button.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Cancel).setText("취소")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.address_edit.returnPressed.connect(self.search)

    def search(self):
        address = self.address_edit.text().strip()
        if not address:
            QMessageBox.information(self, "주소 입력", "대상지역의 읍·면·동 주소를 입력해 주세요.")
            return

        self.search_button.setEnabled(False)
        self.output.setPlainText("주소와 좌표계를 검색하는 중입니다...")
        try:
            result = self.advisor.recommend_by_address(self.layer, address)
        except Exception as exc:
            self.result = None
            self.apply_button.setEnabled(False)
            self.output.setPlainText("검색 중 오류가 발생했습니다.\n\n%s" % exc)
            return
        finally:
            self.search_button.setEnabled(True)

        if not result:
            self.result = None
            self.apply_button.setEnabled(False)
            self.output.setPlainText(
                "주소 검색 결과와 일치하는 좌표계 후보를 찾지 못했습니다.\n\n"
                "시·군·구를 포함하여 다시 입력해 보세요."
            )
            return

        self.result = result
        self.apply_button.setEnabled(True)
        lines = [
            "검색 주소: %s" % result.get("query", address),
            "확인된 위치: %s" % result.get("matched_address", ""),
            "주소 좌표: %.7f, %.7f" % (
                result.get("longitude", 0.0), result.get("latitude", 0.0)
            ),
            "",
            "추천 좌표계: EPSG:%s" % result["epsg"],
            "예상 오차: %.1f m" % result.get("distance_m", 0.0),
            "",
            "후보 순위:",
        ]
        for index, item in enumerate(result.get("candidates", []), start=1):
            lines.append("%d. EPSG:%s — %.1f m" % (
                index, item["epsg"], item["distance_m"]
            ))
        self.output.setPlainText("\n".join(lines))
