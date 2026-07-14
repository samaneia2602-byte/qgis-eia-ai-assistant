# -*- coding: utf-8 -*-

from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)


class AermodAreaDialog(QDialog):
    SETTINGS_PREFIX = "qgis_eia_ai_assistant/aermod_area"

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle(
            "AERMOD AREA SOURCE 생성 설정"
        )
        self.resize(620, 440)

        settings = QSettings()

        layout = QVBoxLayout(self)

        guide = QLabel(
            "활성화한 사업지역 폴리곤을 AERMOD의 "
            "SO LOCATION, SO SRCPARAM, SO AREAVERT 형식으로 변환합니다.\n"
            "선택된 객체가 있으면 선택 객체만 사용하고, "
            "없으면 레이어의 모든 객체를 사용합니다."
        )
        guide.setWordWrap(True)
        layout.addWidget(guide)

        form = QFormLayout()

        self.prefix = QLineEdit(
            settings.value(
                self.SETTINGS_PREFIX + "/prefix",
                "A",
            )
        )
        self.prefix.setMaxLength(4)

        self.start_number = QSpinBox()
        self.start_number.setRange(1, 9999)
        self.start_number.setValue(
            int(
                settings.value(
                    self.SETTINGS_PREFIX
                    + "/start_number",
                    1,
                )
            )
        )

        self.emission_rate = QLineEdit(
            settings.value(
                self.SETTINGS_PREFIX
                + "/emission_rate",
                "6.122E-05",
            )
        )
        self.release_height = QLineEdit(
            settings.value(
                self.SETTINGS_PREFIX
                + "/release_height",
                "0.5",
            )
        )
        self.initial_sigma = QLineEdit(
            settings.value(
                self.SETTINGS_PREFIX
                + "/initial_sigma",
                "5",
            )
        )
        self.base_elevation = QLineEdit(
            settings.value(
                self.SETTINGS_PREFIX
                + "/base_elevation",
                "25",
            )
        )
        self.emisfact = QLineEdit(
            settings.value(
                self.SETTINGS_PREFIX
                + "/emisfact",
                "HROFDY  9*0  3*1  1*0  5*1  6*0",
            )
        )

        self.coordinate_decimals = QSpinBox()
        self.coordinate_decimals.setRange(0, 8)
        self.coordinate_decimals.setValue(
            int(
                settings.value(
                    self.SETTINGS_PREFIX
                    + "/coordinate_decimals",
                    4,
                )
            )
        )

        self.use_attribute_id = QCheckBox(
            "SRC_ID·SOURCE_ID·NAME 등의 속성값을 Source ID로 우선 사용"
        )
        self.use_attribute_id.setChecked(
            str(
                settings.value(
                    self.SETTINGS_PREFIX
                    + "/use_attribute_id",
                    "true",
                )
            ).lower()
            == "true"
        )

        self.include_emisfact = QCheckBox(
            "SO EMISFACT 행 생성"
        )
        self.include_emisfact.setChecked(
            str(
                settings.value(
                    self.SETTINGS_PREFIX
                    + "/include_emisfact",
                    "true",
                )
            ).lower()
            == "true"
        )

        self.include_wrapper = QCheckBox(
            "SO STARTING·SO SRCGROUP·SO FINISHED 포함"
        )
        self.include_wrapper.setChecked(
            str(
                settings.value(
                    self.SETTINGS_PREFIX
                    + "/include_wrapper",
                    "true",
                )
            ).lower()
            == "true"
        )

        form.addRow(
            "Source 접두어",
            self.prefix,
        )
        form.addRow(
            "시작번호",
            self.start_number,
        )
        form.addRow(
            "배출량",
            self.emission_rate,
        )
        form.addRow(
            "배출높이",
            self.release_height,
        )
        form.addRow(
            "초기 Sigma",
            self.initial_sigma,
        )
        form.addRow(
            "SO LOCATION 표고",
            self.base_elevation,
        )
        form.addRow(
            "EMISFACT",
            self.emisfact,
        )
        form.addRow(
            "좌표 소수점 자리",
            self.coordinate_decimals,
        )

        layout.addLayout(form)
        layout.addWidget(
            self.use_attribute_id
        )
        layout.addWidget(
            self.include_emisfact
        )
        layout.addWidget(
            self.include_wrapper
        )

        note = QLabel(
            "SO LOCATION의 X·Y는 각 폴리곤 첫 번째 꼭지점이며, "
            "SO SRCPARAM의 꼭지점 개수는 폐합 중복점을 제외해 자동 계산합니다. "
            "SO AREAVERT는 한 줄당 최대 3개 점으로 자동 줄바꿈합니다."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok
            | QDialogButtonBox.Cancel
        )
        buttons.button(
            QDialogButtonBox.Ok
        ).setText("파일 생성")
        buttons.button(
            QDialogButtonBox.Cancel
        ).setText("취소")
        buttons.accepted.connect(
            self.accept
        )
        buttons.rejected.connect(
            self.reject
        )
        layout.addWidget(buttons)

    def values(self):
        values = {
            "prefix": (
                self.prefix.text().strip()
                or "A"
            ),
            "start_number": (
                self.start_number.value()
            ),
            "emission_rate": (
                self.emission_rate.text().strip()
                or "6.122E-05"
            ),
            "release_height": (
                self.release_height.text().strip()
                or "0.5"
            ),
            "initial_sigma": (
                self.initial_sigma.text().strip()
                or "5"
            ),
            "base_elevation": (
                self.base_elevation.text().strip()
                or "25"
            ),
            "emisfact": (
                self.emisfact.text().strip()
                or "HROFDY  9*0  3*1  1*0  5*1  6*0"
            ),
            "coordinate_decimals": (
                self.coordinate_decimals.value()
            ),
            "use_attribute_id": (
                self.use_attribute_id.isChecked()
            ),
            "include_emisfact": (
                self.include_emisfact.isChecked()
            ),
            "include_wrapper": (
                self.include_wrapper.isChecked()
            ),
        }

        settings = QSettings()

        for key, value in values.items():
            settings.setValue(
                self.SETTINGS_PREFIX + "/" + key,
                value,
            )

        return values
