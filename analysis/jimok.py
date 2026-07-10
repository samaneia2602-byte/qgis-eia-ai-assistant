# -*- coding: utf-8 -*-
import re

from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsField


JIMOK_MAP = {
    '전': '전',
    '답': '답',
    '과': '과수원',
    '과수원': '과수원',
    '목': '목장용지',
    '목장용지': '목장용지',
    '임': '임야',
    '임야': '임야',
    '광': '광천지',
    '광천지': '광천지',
    '염': '염전',
    '염전': '염전',
    '대': '대',
    '대지': '대',
    '장': '공장용지',
    '공장용지': '공장용지',
    '학': '학교용지',
    '학교용지': '학교용지',
    '차': '주차장',
    '주차장': '주차장',
    '주': '주유소용지',
    '주유소용지': '주유소용지',
    '창': '창고용지',
    '창고용지': '창고용지',
    '도': '도로',
    '도로': '도로',
    '철': '철도용지',
    '철도용지': '철도용지',
    '제': '제방',
    '제방': '제방',
    '천': '하천',
    '하천': '하천',
    '구': '구거',
    '구거': '구거',
    '유': '유지',
    '유지': '유지',
    '양': '양어장',
    '양어장': '양어장',
    '수': '수도용지',
    '수도용지': '수도용지',
    '공': '공원',
    '공원': '공원',
    '체': '체육용지',
    '체육용지': '체육용지',
    '원': '유원지',
    '유원지': '유원지',
    '종': '종교용지',
    '종교용지': '종교용지',
    '사': '사적지',
    '사적지': '사적지',
    '묘': '묘지',
    '묘지': '묘지',
    '잡': '잡종지',
    '잡종지': '잡종지',
}

# 긴 명칭부터 검사해야 '도로' 안의 '도'를 먼저 잡는 오류를 막을 수 있습니다.
JIMOK_TOKENS = sorted(JIMOK_MAP.keys(), key=len, reverse=True)


def extract_jimok(value):
    """필드 값에서 지목 약어 또는 정식 명칭을 추출합니다."""
    if value is None:
        return ''

    text = str(value).strip()
    if not text:
        return ''

    # 값 자체가 지목이면 즉시 반환합니다.
    if text in JIMOK_MAP:
        return JIMOK_MAP[text]

    # 지번 끝에 붙은 지목: 221전, 산460-27임, 15-2도로 등
    for token in JIMOK_TOKENS:
        if text.endswith(token):
            return JIMOK_MAP[token]

    # 구분자가 섞인 값: "221-3 / 전", "지목:답" 등
    pattern = r'(?:^|[\s,;/|:\[\](){}_-])(' + '|'.join(
        re.escape(token) for token in JIMOK_TOKENS
    ) + r')(?:$|[\s,;/|:\[\](){}_-])'
    match = re.search(pattern, text)
    if match:
        return JIMOK_MAP.get(match.group(1), '')

    return ''


def cleanup_jimok(layer, field_name='지목'):
    """
    활성 벡터 레이어에 '지목' 필드를 만들고 값을 정리합니다.

    우선순위 필드: jibun → bonbun → bubun → addr
    기존 '지목' 필드가 있으면 값을 갱신합니다.
    """
    if not layer or not layer.isValid():
        return 0

    provider = layer.dataProvider()
    fields = layer.fields()

    if fields.indexFromName(field_name) < 0:
        ok = provider.addAttributes([
            QgsField(field_name, QVariant.String, len=20)
        ])
        if not ok:
            return 0
        layer.updateFields()

    target_idx = layer.fields().indexFromName(field_name)
    if target_idx < 0:
        return 0

    source_names = ['jibun', 'bonbun', 'bubun', 'addr']
    source_indexes = []
    for name in source_names:
        idx = layer.fields().indexFromName(name)
        if idx >= 0:
            source_indexes.append(idx)

    if not source_indexes:
        return 0

    started_here = not layer.isEditable()
    if started_here and not layer.startEditing():
        return 0

    changed = 0
    for feature in layer.getFeatures():
        jimok = ''
        for idx in source_indexes:
            jimok = extract_jimok(feature[idx])
            if jimok:
                break

        if not jimok:
            continue

        current = feature[target_idx]
        if str(current or '').strip() == jimok:
            continue

        if layer.changeAttributeValue(feature.id(), target_idx, jimok):
            changed += 1

    if started_here:
        if not layer.commitChanges():
            layer.rollBack()
            return 0

    layer.updateFields()
    layer.triggerRepaint()
    return changed
