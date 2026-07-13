# -*- coding: utf-8 -*-

from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)


class CadastralOptionsDialog(QDialog):
    """사업지역 지목별 면적 분석 옵션."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("사업지역 지목별 면적 산출")
        self.resize(430, 300)

        layout = QVBoxLayout(self)

        guide = QLabel(
            "분석에 사용할 옵션을 선택하세요.\n"
            "사업지역 레이어에 선택 객체가 있으면 선택 객체만 분석할 수 있습니다."
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)

        self.selected_only = QCheckBox("사업지역의 선택 객체만 분석")
        self.selected_only.setChecked(True)

        self.add_clip_layer = QCheckBox("Clip 결과를 QGIS에 추가")
        self.add_clip_layer.setChecked(True)

        self.add_summary_layer = QCheckBox("지목별 집계표를 QGIS에 추가")
        self.add_summary_layer.setChecked(True)

        self.save_excel = QCheckBox("Excel 결과표 저장")
        self.save_excel.setChecked(True)

        self.report_style = QCheckBox("환경영향평가 보고서용 Excel 서식 적용")
        self.report_style.setChecked(True)

        self.show_chat_table = QCheckBox("플러그인 대화창에 결과표 표시")
        self.show_chat_table.setChecked(True)

        for widget in (
            self.selected_only,
            self.add_clip_layer,
            self.add_summary_layer,
            self.save_excel,
            self.report_style,
            self.show_chat_table,
        ):
            layout.addWidget(widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText("실행")
        buttons.button(QDialogButtonBox.Cancel).setText("취소")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self):
        return {
            "selected_only": self.selected_only.isChecked(),
            "add_clip_layer": self.add_clip_layer.isChecked(),
            "add_summary_layer": self.add_summary_layer.isChecked(),
            "save_excel": self.save_excel.isChecked(),
            "report_style": self.report_style.isChecked(),
            "show_chat_table": self.show_chat_table.isChecked(),
        }
