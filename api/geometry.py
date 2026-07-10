# -*- coding: utf-8 -*-
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

def active_or_selected_extent(iface):
    layer = iface.activeLayer()
    if layer and hasattr(layer, 'selectedFeatureCount') and layer.selectedFeatureCount() > 0:
        feats = list(layer.selectedFeatures())
        if feats:
            bbox = feats[0].geometry().boundingBox()
            for f in feats[1:]:
                bbox.combineExtentWith(f.geometry().boundingBox())
            return bbox, layer.crs()
    if layer and layer.isValid():
        return layer.extent(), layer.crs()
    canvas = iface.mapCanvas()
    return canvas.extent(), canvas.mapSettings().destinationCrs()

def extent_to_epsg4326(extent, src_crs):
    dst = QgsCoordinateReferenceSystem('EPSG:4326')
    tr = QgsCoordinateTransform(src_crs, dst, QgsProject.instance())
    return tr.transformBoundingBox(extent)

def center_to_epsg4326(extent, src_crs):
    dst = QgsCoordinateReferenceSystem('EPSG:4326')
    tr = QgsCoordinateTransform(src_crs, dst, QgsProject.instance())
    c = extent.center()
    return tr.transform(c)
