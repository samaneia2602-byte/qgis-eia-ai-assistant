# -*- coding: utf-8 -*-
import re
from qgis.PyQt.QtCore import QVariant
from qgis.core import QgsField

JIMOK_MAP = {
    '전':'전', '답':'답', '과':'과수원', '목':'목장용지', '임':'임야', '광':'광천지', '염':'염전',
    '대':'대지', '장':'공장용지', '학':'학교용지', '차':'주차장', '주':'주유소용지', '창':'창고용지',
    '도':'도로', '철':'철도용지', '제':'제방', '천':'하천', '구':'구거', '유':'유지', '양':'양어장',
    '수':'수도용지', '공':'공원', '체':'체육용지', '원':'유원지', '종':'종교용지', '사':'사적지',
    '묘':'묘지', '잡':'잡종지'
}

def extract_jimok(text):
    if text is None:
        return ''
    s = str(text).strip()
    # 지번 문자열 끝의 지목 약어 추출: 산460-27임, 221전, 0-15가 등
    for k in sorted(JIMOK_MAP.keys(), key=len, reverse=True):
        if s.endswith(k):
            return JIMOK_MAP[k]
    m = re.search(r'(전|답|과|목|임|광|염|대|장|학|차|주|창|도|철|제|천|구|유|양|수|공|체|원|종|사|묘|잡)\s*$', s)
    return JIMOK_MAP.get(m.group(1), '') if m else ''

def cleanup_jimok(layer, field_name='jimok_cls'):
    if not layer or not layer.isValid():
        return 0
    provider = layer.dataProvider()
    fields = layer.fields()
    if fields.indexFromName(field_name) < 0:
        provider.addAttributes([QgsField(field_name, QVariant.String, len=20)])
        layer.updateFields()
    target_idx = layer.fields().indexFromName(field_name)
    src_names = ['jibun', 'bonbun', 'bubun', 'addr']
    src_idxs = [layer.fields().indexFromName(n) for n in src_names if layer.fields().indexFromName(n) >= 0]
    count = 0
    layer.startEditing()
    for f in layer.getFeatures():
        val = ''
        for idx in src_idxs:
            val = extract_jimok(f[idx])
            if val:
                break
        if val:
            layer.changeAttributeValue(f.id(), target_idx, val)
            count += 1
    layer.commitChanges()
    return count
