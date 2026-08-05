# Copyright (C) 2026 Akmaul Hoque and Manish Kumar Naskar
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import math
import os
import traceback
from pathlib import Path

from osgeo import gdal, ogr
from osgeo_utils.gdal_calc import Calc as GdalCalc

from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QTabWidget,
    QVBoxLayout, QWidget
)
from qgis.core import (QgsProject, QgsRasterLayer, QgsVectorLayer, QgsApplication,
                       QgsCoordinateReferenceSystem, QgsProcessingFeedback)
import processing


class PipelineWorker(QThread):
    message = pyqtSignal(str)
    failed = pyqtSignal(str)
    completed = pyqtSignal(dict)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.cancelled = False
        self.feedback = QgsProcessingFeedback()

    def cancel(self):
        self.cancelled = True
        self.feedback.cancel()

    def _check_cancelled(self):
        if self.cancelled or self.feedback.isCanceled():
            raise RuntimeError('Processing cancelled by user.')

    def run_processing(self, algorithm_id, parameters):
        """Run only a registered QGIS Processing algorithm.

        No shell, subprocess, command string, or arbitrary executable is used.
        This prevents command injection and satisfies QGIS repository security rules.
        """
        self._check_cancelled()
        self.message.emit('QGIS Processing: ' + algorithm_id)
        result = processing.run(algorithm_id, parameters, feedback=self.feedback)
        self._check_cancelled()
        return result

    def run_calc(self, calc, outfile, inputs, data_type='Float32', nodata=-9999):
        self.message.emit('GDAL Calc: {0}'.format(Path(outfile).name))
        Path(outfile).parent.mkdir(parents=True, exist_ok=True)
        if Path(outfile).exists():
            Path(outfile).unlink()
        GdalCalc(calc=calc, outfile=outfile, NoDataValue=nodata, type=data_type,
                 overwrite=True, quiet=True, **dict(inputs))
        ds = gdal.Open(outfile)
        if ds is None or ds.RasterXSize <= 0 or ds.RasterYSize <= 0:
            raise RuntimeError('Raster calculation failed: ' + outfile)
        ds = None

    def run_sieve(self, source, destination, threshold):
        self.message.emit('GDAL Sieve: ' + Path(destination).name)
        if Path(destination).exists():
            Path(destination).unlink()
        src_ds = gdal.Open(source, gdal.GA_ReadOnly)
        if src_ds is None:
            raise RuntimeError('Cannot open mask raster: ' + source)
        src_band = src_ds.GetRasterBand(1)
        driver = gdal.GetDriverByName('GTiff')
        dst_ds = driver.Create(destination, src_ds.RasterXSize, src_ds.RasterYSize,
                               1, gdal.GDT_Byte,
                               options=['COMPRESS=DEFLATE', 'TILED=YES'])
        if dst_ds is None:
            raise RuntimeError('Cannot create sieved raster: ' + destination)
        dst_ds.SetGeoTransform(src_ds.GetGeoTransform())
        dst_ds.SetProjection(src_ds.GetProjection())
        dst_band = dst_ds.GetRasterBand(1)
        dst_band.SetNoDataValue(0)
        dst_band.Fill(0)
        err = gdal.SieveFilter(src_band, None, dst_band, int(threshold), 8)
        dst_band.FlushCache(); dst_ds.FlushCache()
        dst_ds = None; src_ds = None
        if err != 0:
            raise RuntimeError('Sieve operation failed: ' + destination)

    def run_polygonize(self, source, gpkg, layer_name):
        self.message.emit('GDAL Polygonize: ' + layer_name)
        if Path(gpkg).exists():
            Path(gpkg).unlink()
        src_ds = gdal.Open(source, gdal.GA_ReadOnly)
        if src_ds is None:
            raise RuntimeError('Cannot open raster for polygonizing: ' + source)
        band = src_ds.GetRasterBand(1)
        driver = ogr.GetDriverByName('GPKG')
        out_ds = driver.CreateDataSource(gpkg)
        if out_ds is None:
            raise RuntimeError('Cannot create GeoPackage: ' + gpkg)
        from osgeo import osr
        srs = None
        projection = src_ds.GetProjection()
        if projection:
            srs = osr.SpatialReference(); srs.ImportFromWkt(projection)
        layer = out_ds.CreateLayer(layer_name, srs=srs, geom_type=ogr.wkbPolygon)
        layer.CreateField(ogr.FieldDefn('class', ogr.OFTInteger))
        err = gdal.Polygonize(band, band, layer, 0, [], callback=None)
        layer.SyncToDisk(); out_ds = None; src_ds = None
        if err != 0 or not Path(gpkg).exists():
            raise RuntimeError('Polygonization failed: ' + gpkg)

    @staticmethod
    def _and_expression(terms):
        if not terms:
            return '1'
        expr = terms[0]
        for term in terms[1:]:
            expr = 'logical_and({0},{1})'.format(expr, term)
        return expr

    def run(self):
        try:
            c = self.config
            out = Path(c['output_dir'])
            out.mkdir(parents=True, exist_ok=True)
            source = c['input']

            if c['reclassify_ground']:
                raise RuntimeError(
                    'Ground reclassification was disabled in the repository-safe build. '
                    'Use a classified LAS/LAZ file or classify it first with a trusted QGIS Processing tool.'
                )

            # QGIS Processing/PDAL is the only point-cloud execution route.
            prepared = source
            if c['source_crs']:
                assigned = str(out / 'assigned_crs.copc.laz')
                self.run_processing('pdal:assignprojection', {
                    'INPUT': prepared,
                    'CRS': QgsCoordinateReferenceSystem(c['source_crs']),
                    'OUTPUT': assigned
                })
                prepared = assigned
            if c['target_crs']:
                reprojected = str(out / 'prepared.copc.laz')
                self.run_processing('pdal:reproject', {
                    'INPUT': prepared,
                    'CRS': QgsCoordinateReferenceSystem(c['target_crs']),
                    'OUTPUT': reprojected
                })
                prepared = reprojected

            resolution = c['resolution']
            products = {}

            def export_raster(filename, filter_expression):
                path = str(out / filename)
                if Path(path).exists(): Path(path).unlink()
                self.run_processing('pdal:exportraster', {
                    'INPUT': prepared,
                    'ATTRIBUTE': 'Z',
                    'RESOLUTION': resolution,
                    'FILTER_EXPRESSION': filter_expression,
                    'OUTPUT': path
                })
                ds = gdal.Open(path)
                if ds is None or ds.RasterXSize <= 0 or ds.RasterYSize <= 0:
                    raise RuntimeError('QGIS PDAL created an invalid raster: ' + path)
                ds = None; products[filename] = path

            export_raster('DTM.tif', 'Classification == 2')
            export_raster('DSM.tif', 'Classification != 7 && Classification != 18')

            chm = str(out / 'CHM.tif')
            self.run_calc('where(logical_or(A==-9999,B==-9999),-9999,maximum(A-B,0))',
                          chm, {'A': str(out/'DSM.tif'), 'B': str(out/'DTM.tif')})
            products['CHM.tif'] = chm

            density = str(out / 'Point_Density.tif')
            if Path(density).exists(): Path(density).unlink()
            self.run_processing('pdal:density', {
                'INPUT': prepared, 'RESOLUTION': resolution, 'TILE_SIZE': 1000,
                'FILTER_EXPRESSION': '', 'OUTPUT': density
            })
            products['Point_Density.tif'] = density

            roughness = str(out / 'Surface_Roughness.tif')
            if Path(roughness).exists(): Path(roughness).unlink()
            rough_ds = gdal.DEMProcessing(roughness, str(out/'DSM.tif'), 'roughness',
                                          format='GTiff', computeEdges=True)
            if rough_ds is None:
                raise RuntimeError('GDAL could not generate surface roughness.')
            rough_ds = None; products['Surface_Roughness.tif'] = roughness

            slope = str(out / 'DTM_Slope_Degrees.tif')
            if Path(slope).exists(): Path(slope).unlink()
            slope_ds = gdal.DEMProcessing(slope, str(out/'DTM.tif'), 'slope',
                                          format='GTiff', slopeFormat='degree', computeEdges=True)
            if slope_ds is None:
                raise RuntimeError('GDAL could not generate terrain slope.')
            slope_ds = None; products['DTM_Slope_Degrees.tif'] = slope

            def mask_to_vector(name, calc, inputs, min_pixels=None):
                raw = str(out/(name+'_raw.tif')); clean = str(out/(name+'.tif'))
                gpkg = str(out/(name+'.gpkg'))
                for old in (raw, clean, gpkg):
                    if Path(old).exists(): Path(old).unlink()
                valid_terms = ['{0}!=-9999'.format(key) for key, _ in inputs]
                valid_expr = valid_terms[0] if len(valid_terms)==1 else self._and_expression(valid_terms)
                self.run_calc('logical_and({0},{1})'.format(valid_expr, calc), raw,
                              {key:value for key,value in inputs}, data_type='Byte', nodata=0)
                self.run_sieve(raw, clean, c['min_pixels'] if min_pixels is None else min_pixels)
                self.run_polygonize(clean, gpkg, name)
                products[name+'.gpkg'] = gpkg

            if c['extract_settlement']:
                mask_to_vector('Settlement_Candidates',
                    'logical_and(logical_and(A>={0},A<={1}),B<={2})'.format(c['settlement_min'],c['settlement_max'],c['settlement_roughness']),
                    [('A',chm),('B',roughness)], c['settlement_min_pixels'])
            if c['extract_buildings']:
                mask_to_vector('Building_Candidates',
                    'logical_and(logical_and(A>={0},A<={1}),B<={2})'.format(c['building_min'],c['building_max'],c['building_roughness']),
                    [('A',chm),('B',roughness)])
            if c['extract_vegetation']:
                mask_to_vector('Vegetation_Candidates',
                    'logical_and(A>={0},B>={1})'.format(c['tree_min'],c['vegetation_roughness']),
                    [('A',chm),('B',roughness)])
            if c['extract_lowveg']:
                mask_to_vector('Low_Vegetation_Candidates',
                    'logical_and(logical_and(logical_and(A>={0},A<={1}),B<={2}),C<={3})'.format(c['lowveg_min'],c['lowveg_max'],c['lowveg_roughness'],c['lowveg_slope_max']),
                    [('A',chm),('B',roughness),('C',slope)])
            if c['extract_cropland']:
                mask_to_vector('Cropland_Candidates',
                    'logical_and(logical_and(logical_and(A>={0},A<={1}),B<={2}),logical_and(C<={3},D>={4}))'.format(c['crop_min'],c['crop_max'],c['crop_roughness'],c['crop_slope_max'],c['crop_density_min']),
                    [('A',chm),('B',roughness),('C',slope),('D',density)], c['crop_min_pixels'])
            if c['extract_bareland']:
                mask_to_vector('Bare_Land_Candidates',
                    'logical_and(logical_and(logical_and(A<={0},B<={1}),C<={2}),D>={3})'.format(c['bare_height_max'],c['bare_slope_max'],c['bare_roughness_max'],c['bare_density_min']),
                    [('A',chm),('B',slope),('C',roughness),('D',density)])
            if c['extract_roads']:
                mask_to_vector('Road_Open_Surface_Candidates',
                    'logical_and(logical_and(A>={0},A<={1}),B<={2})'.format(c['road_min'],c['road_max'],c['road_slope_max']),
                    [('A',chm),('B',slope)])
            if c['extract_water']:
                mask_to_vector('Water_Low_Return_Candidates',
                    'logical_and(A<={0},B<={1})'.format(c['water_density_max'],c['water_slope_max']),
                    [('A',density),('B',slope)])
            if c['extract_linear']:
                # Secure approximation: high CHM with sparse total-return density.
                mask_to_vector('Elevated_Linear_Candidates',
                    'logical_and(logical_and(A>={0},A<={1}),logical_and(B>={2},B<={3}))'.format(c['linear_min'],c['linear_max'],c['linear_density_min'],c['linear_density_max']),
                    [('A',chm),('B',density)], c['linear_min_pixels'])

            manifest = out/'processing_manifest.json'
            manifest.write_text(json.dumps({'configuration':c,'outputs':products}, indent=2), encoding='utf-8')
            self.completed.emit(products)
        except Exception:
            self.failed.emit(traceback.format_exc())


class LidarGeoExtractDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.worker = None
        self.setWindowTitle('LiDAR AutoVector Studio – Individual Feature Automation')
        self.resize(840, 760)
        self.build_ui()

    def file_row(self, edit, button_text, callback):
        box = QHBoxLayout()
        box.addWidget(edit, 1)
        b = QPushButton(button_text)
        b.clicked.connect(callback)
        box.addWidget(b)
        w = QWidget(); w.setLayout(box)
        return w

    def build_ui(self):
        root = QVBoxLayout(self)
        tabs = QTabWidget(); root.addWidget(tabs, 1)

        input_tab = QWidget(); form = QFormLayout(input_tab)
        self.input_edit = QLineEdit(); self.input_edit.setPlaceholderText('LAS/LAZ/COPC file or https://.../ept.json')
        form.addRow('LiDAR source:', self.file_row(self.input_edit, 'Browse…', self.pick_input))
        self.output_edit = QLineEdit()
        form.addRow('Output folder:', self.file_row(self.output_edit, 'Browse…', self.pick_output))
        self.source_crs = QLineEdit(); self.source_crs.setPlaceholderText('Optional, e.g. EPSG:4326')
        form.addRow('Override source CRS:', self.source_crs)
        self.target_crs = QLineEdit(); self.target_crs.setPlaceholderText('Recommended projected CRS, e.g. EPSG:32646')
        form.addRow('Target CRS:', self.target_crs)
        note = QLabel('For worldwide use, select a suitable local projected CRS. A global geographic CRS is unsuitable for metre-based raster resolution.')
        note.setWordWrap(True); form.addRow(note)
        tabs.addTab(input_tab, 'Input & CRS')

        terrain_tab = QWidget(); f = QFormLayout(terrain_tab)
        self.resolution = QDoubleSpinBox(); self.resolution.setRange(0.05, 100); self.resolution.setValue(1.0); self.resolution.setSuffix(' m'); self.resolution.setDecimals(2)
        f.addRow('Raster resolution:', self.resolution)
        self.smrf_slope = self.spin(0.01, 5, 0.2, 2); f.addRow('SMRF slope:', self.smrf_slope)
        self.smrf_window = self.spin(1, 1000, 16, 1); f.addRow('SMRF window:', self.smrf_window)
        self.smrf_threshold = self.spin(0.01, 20, 0.45, 2); f.addRow('SMRF threshold:', self.smrf_threshold)
        self.smrf_scalar = self.spin(0.1, 10, 1.2, 2); f.addRow('SMRF scalar:', self.smrf_scalar)
        self.reclassify_ground = QCheckBox('Reclassify ground with SMRF (only for unclassified LiDAR)')
        self.reclassify_ground.setChecked(False)
        f.addRow(self.reclassify_ground)
        preset_note = QLabel('Recommended for USGS/3DEP: leave reclassification OFF because Classification 2 ground points are already supplied.')
        preset_note.setWordWrap(True); f.addRow(preset_note)
        tabs.addTab(terrain_tab, 'Terrain')

        feat_tab = QWidget(); f2 = QVBoxLayout(feat_tab)
        top_actions = QHBoxLayout()
        select_all_btn = QPushButton('Select all features')
        clear_all_btn = QPushButton('Clear all')
        select_all_btn.clicked.connect(self.select_all_features)
        clear_all_btn.clicked.connect(self.clear_all_features)
        top_actions.addWidget(select_all_btn); top_actions.addWidget(clear_all_btn); top_actions.addStretch()
        f2.addLayout(top_actions)

        selector = QGroupBox('Individual feature automation')
        sg = QGridLayout(selector)
        self.extract_water = QCheckBox('Water bodies'); self.extract_water.setChecked(False)
        self.extract_vegetation = QCheckBox('Vegetation / tree cover'); self.extract_vegetation.setChecked(True)
        self.extract_settlement = QCheckBox('Settlement areas'); self.extract_settlement.setChecked(True)
        self.extract_buildings = QCheckBox('Individual buildings'); self.extract_buildings.setChecked(True)
        self.extract_cropland = QCheckBox('Cropland / agricultural fields'); self.extract_cropland.setChecked(True)
        self.extract_bareland = QCheckBox('Bare land / exposed soil'); self.extract_bareland.setChecked(False)
        self.extract_lowveg = QCheckBox('Low vegetation / grass'); self.extract_lowveg.setChecked(False)
        self.extract_roads = QCheckBox('Roads / open surfaces'); self.extract_roads.setChecked(True)
        self.extract_linear = QCheckBox('Power lines / elevated linear features'); self.extract_linear.setChecked(False)
        checks=[self.extract_water,self.extract_vegetation,self.extract_lowveg,self.extract_settlement,self.extract_buildings,self.extract_cropland,self.extract_bareland,self.extract_roads,self.extract_linear]
        for i,cb in enumerate(checks): sg.addWidget(cb, i//2, i%2)
        f2.addWidget(selector)

        params = QTabWidget(); f2.addWidget(params,1)

        settlement_tab=QWidget(); sf=QFormLayout(settlement_tab)
        self.settlement_min=self.spin(0,100,2.0,1); self.settlement_max=self.spin(1,300,60,1)
        self.settlement_roughness=self.spin(0.05,20,1.8,2); self.settlement_min_pixels=QSpinBox(); self.settlement_min_pixels.setRange(1,100000); self.settlement_min_pixels.setValue(100)
        sf.addRow('Minimum object height:',self.settlement_min); sf.addRow('Maximum object height:',self.settlement_max)
        sf.addRow('Maximum surface roughness:',self.settlement_roughness); sf.addRow('Minimum connected pixels:',self.settlement_min_pixels)
        params.addTab(settlement_tab,'Settlement')

        building_tab=QWidget(); bf=QFormLayout(building_tab)
        self.building_min=self.spin(0,100,2.5,1); self.building_max=self.spin(1,300,60,1); self.building_roughness=self.spin(0.05,20,1.5,2)
        bf.addRow('Building height minimum:',self.building_min); bf.addRow('Building height maximum:',self.building_max); bf.addRow('Maximum roof roughness:',self.building_roughness)
        params.addTab(building_tab,'Buildings')

        veg_tab=QWidget(); vf=QFormLayout(veg_tab)
        self.tree_min=self.spin(0,100,3.0,1); self.vegetation_roughness=self.spin(0.05,30,1.5,2)
        vf.addRow('Vegetation height minimum:',self.tree_min); vf.addRow('Minimum canopy roughness:',self.vegetation_roughness)
        params.addTab(veg_tab,'Vegetation')

        lowveg_tab=QWidget(); lvf=QFormLayout(lowveg_tab)
        self.lowveg_min=self.spin(0,10,0.10,2); self.lowveg_max=self.spin(0,20,2.0,2)
        self.lowveg_roughness=self.spin(0.01,20,1.5,2); self.lowveg_slope_max=self.spin(0,90,25,1)
        lvf.addRow('Minimum vegetation height:',self.lowveg_min); lvf.addRow('Maximum vegetation height:',self.lowveg_max)
        lvf.addRow('Maximum roughness:',self.lowveg_roughness); lvf.addRow('Maximum slope:',self.lowveg_slope_max)
        params.addTab(lowveg_tab,'Low vegetation')

        crop_tab=QWidget(); cf=QFormLayout(crop_tab)
        self.crop_min=self.spin(0,20,0.05,2); self.crop_max=self.spin(0,30,2.5,2); self.crop_roughness=self.spin(0.01,20,1.2,2)
        self.crop_slope_max=self.spin(0,90,15,1); self.crop_density_min=self.spin(0,1000,2,1); self.crop_min_pixels=QSpinBox(); self.crop_min_pixels.setRange(1,1000000); self.crop_min_pixels.setValue(250)
        cf.addRow('Crop height minimum:',self.crop_min); cf.addRow('Crop height maximum:',self.crop_max); cf.addRow('Maximum surface roughness:',self.crop_roughness)
        cf.addRow('Maximum terrain slope:',self.crop_slope_max); cf.addRow('Minimum point density:',self.crop_density_min); cf.addRow('Minimum connected pixels:',self.crop_min_pixels)
        params.addTab(crop_tab,'Cropland')

        bare_tab=QWidget(); baf=QFormLayout(bare_tab)
        self.bare_height_max=self.spin(0,10,0.20,2); self.bare_slope_max=self.spin(0,90,30,1)
        self.bare_roughness_max=self.spin(0.01,20,0.60,2); self.bare_density_min=self.spin(0,1000,2,1)
        baf.addRow('Maximum normalized height:',self.bare_height_max); baf.addRow('Maximum slope:',self.bare_slope_max)
        baf.addRow('Maximum surface roughness:',self.bare_roughness_max); baf.addRow('Minimum point density:',self.bare_density_min)
        params.addTab(bare_tab,'Bare land')

        water_tab=QWidget(); wf=QFormLayout(water_tab)
        self.water_density_max=self.spin(0,1000,1.0,1); self.water_slope_max=self.spin(0,90,3.0,1)
        wf.addRow('Maximum return density:',self.water_density_max); wf.addRow('Maximum water-surface slope:',self.water_slope_max)
        params.addTab(water_tab,'Water')

        road_tab=QWidget(); rf=QFormLayout(road_tab)
        self.road_min=self.spin(0,20,0.0,2); self.road_max=self.spin(0,20,0.35,2); self.road_slope_max=self.spin(0,90,12,1)
        rf.addRow('Surface height minimum:',self.road_min); rf.addRow('Surface height maximum:',self.road_max); rf.addRow('Maximum slope:',self.road_slope_max)
        params.addTab(road_tab,'Roads')

        linear_tab=QWidget(); lf=QFormLayout(linear_tab)
        self.linear_min=self.spin(0,200,4,1); self.linear_max=self.spin(1,500,80,1); self.linear_density_min=self.spin(0,1000,1,1); self.linear_density_max=self.spin(0,1000,8,1)
        self.linear_min_pixels=QSpinBox(); self.linear_min_pixels.setRange(1,10000); self.linear_min_pixels.setValue(3)
        lf.addRow('Height minimum:',self.linear_min); lf.addRow('Height maximum:',self.linear_max); lf.addRow('Return density minimum:',self.linear_density_min); lf.addRow('Return density maximum:',self.linear_density_max); lf.addRow('Minimum connected pixels:',self.linear_min_pixels)
        params.addTab(linear_tab,'Linear features')

        common=QGroupBox('Common vector cleaning'); cform=QFormLayout(common)
        self.min_pixels=QSpinBox(); self.min_pixels.setRange(1,100000); self.min_pixels.setValue(25); cform.addRow('Default minimum polygon pixels:',self.min_pixels)
        f2.addWidget(common)
        warning=QLabel('Each selected class is processed independently and exported as a separate GeoPackage. LiDAR-only land-cover interpretation is candidate mapping; validate cropland, water, settlement and road outputs against imagery or field data.')
        warning.setWordWrap(True); f2.addWidget(warning)
        tabs.addTab(feat_tab, 'Feature Studio')

        log_tab = QWidget(); lv = QVBoxLayout(log_tab); self.log = QPlainTextEdit(); self.log.setReadOnly(True); lv.addWidget(self.log)
        tabs.addTab(log_tab, 'Log')

        self.progress = QProgressBar(); self.progress.setRange(0, 1); self.progress.setValue(0); root.addWidget(self.progress)
        buttons = QHBoxLayout(); self.run_btn = QPushButton('Extract Selected Features'); self.cancel_btn = QPushButton('Cancel'); self.cancel_btn.setEnabled(False); close_btn = QPushButton('Close')
        self.run_btn.clicked.connect(self.start); self.cancel_btn.clicked.connect(self.cancel); close_btn.clicked.connect(self.close)
        buttons.addStretch(); buttons.addWidget(self.run_btn); buttons.addWidget(self.cancel_btn); buttons.addWidget(close_btn); root.addLayout(buttons)

    def feature_checkboxes(self):
        return [self.extract_water, self.extract_vegetation, self.extract_lowveg, self.extract_settlement,
                self.extract_buildings, self.extract_cropland, self.extract_bareland, self.extract_roads,
                self.extract_linear]

    def select_all_features(self):
        for checkbox in self.feature_checkboxes():
            checkbox.setChecked(True)

    def clear_all_features(self):
        for checkbox in self.feature_checkboxes():
            checkbox.setChecked(False)

    def spin(self, mn, mx, val, decimals):
        s = QDoubleSpinBox(); s.setRange(mn, mx); s.setValue(val); s.setDecimals(decimals); return s

    def pick_input(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select LiDAR', '', 'Point clouds (*.las *.laz *.copc.laz);;All files (*)')
        if path: self.input_edit.setText(path)

    def pick_output(self):
        path = QFileDialog.getExistingDirectory(self, 'Select output folder')
        if path: self.output_edit.setText(path)

    def config(self):
        return {
            'input': self.input_edit.text().strip(), 'output_dir': self.output_edit.text().strip(),
            'source_crs': self.source_crs.text().strip(), 'target_crs': self.target_crs.text().strip(),
            'resolution': self.resolution.value(), 'smrf_slope': self.smrf_slope.value(),
            'smrf_window': self.smrf_window.value(), 'smrf_threshold': self.smrf_threshold.value(),
            'smrf_scalar': self.smrf_scalar.value(), 'reclassify_ground': self.reclassify_ground.isChecked(),
            'extract_settlement': self.extract_settlement.isChecked(),
            'settlement_min': self.settlement_min.value(), 'settlement_max': self.settlement_max.value(),
            'settlement_roughness': self.settlement_roughness.value(),
            'settlement_min_pixels': self.settlement_min_pixels.value(),
            'extract_buildings': self.extract_buildings.isChecked(),
            'building_min': self.building_min.value(), 'building_max': self.building_max.value(),
            'building_roughness': self.building_roughness.value(),
            'extract_vegetation': self.extract_vegetation.isChecked(),
            'tree_min': self.tree_min.value(), 'vegetation_roughness': self.vegetation_roughness.value(),
            'extract_lowveg': self.extract_lowveg.isChecked(), 'lowveg_min': self.lowveg_min.value(),
            'lowveg_max': self.lowveg_max.value(), 'lowveg_roughness': self.lowveg_roughness.value(),
            'lowveg_slope_max': self.lowveg_slope_max.value(),
            'extract_cropland': self.extract_cropland.isChecked(),
            'crop_min': self.crop_min.value(), 'crop_max': self.crop_max.value(),
            'crop_roughness': self.crop_roughness.value(), 'crop_slope_max': self.crop_slope_max.value(),
            'crop_density_min': self.crop_density_min.value(), 'crop_min_pixels': self.crop_min_pixels.value(),
            'extract_bareland': self.extract_bareland.isChecked(), 'bare_height_max': self.bare_height_max.value(),
            'bare_slope_max': self.bare_slope_max.value(), 'bare_roughness_max': self.bare_roughness_max.value(),
            'bare_density_min': self.bare_density_min.value(),
            'extract_roads': self.extract_roads.isChecked(),
            'road_min': self.road_min.value(), 'road_max': self.road_max.value(),
            'road_slope_max': self.road_slope_max.value(),
            'extract_water': self.extract_water.isChecked(),
            'water_density_max': self.water_density_max.value(), 'water_slope_max': self.water_slope_max.value(),
            'min_pixels': self.min_pixels.value(), 'extract_linear': self.extract_linear.isChecked(),
            'linear_min': self.linear_min.value(), 'linear_max': self.linear_max.value(),
            'linear_density_min': self.linear_density_min.value(), 'linear_density_max': self.linear_density_max.value(),
            'linear_min_pixels': self.linear_min_pixels.value()
        }

    def preflight(self):
        problems = []
        required_algorithms = ('pdal:assignprojection', 'pdal:reproject', 'pdal:exportraster', 'pdal:density')
        registry = QgsApplication.processingRegistry()
        for algorithm_id in required_algorithms:
            if registry.algorithmById(algorithm_id) is None:
                problems.append('Required QGIS Processing algorithm is unavailable: ' + algorithm_id)
        try:
            if not callable(GdalCalc):
                problems.append('GDAL raster calculator API is unavailable.')
            if gdal.GetDriverByName('GTiff') is None:
                problems.append('GDAL GeoTIFF driver is unavailable.')
            if ogr.GetDriverByName('GPKG') is None:
                problems.append('OGR GeoPackage driver is unavailable.')
        except Exception as exc:
            problems.append('GDAL/OGR dependency check failed: {0}'.format(exc))
        return problems

    def start(self):
        c = self.config()
        if not c['input'] or not c['output_dir']:
            QMessageBox.warning(self, 'Missing input', 'Select a LiDAR source and output folder.')
            return
        if not c['input'].lower().startswith(('http://', 'https://')) and not os.path.exists(c['input']):
            QMessageBox.warning(self, 'Invalid input', 'The selected LiDAR file does not exist.')
            return
        if not any([c['extract_water'], c['extract_vegetation'], c['extract_settlement'],
                    c['extract_buildings'], c['extract_cropland'], c['extract_bareland'], c['extract_lowveg'], c['extract_roads'], c['extract_linear']]):
            QMessageBox.warning(self, 'No feature selected', 'Select at least one feature class to extract.')
            return
        problems = self.preflight()
        if problems:
            QMessageBox.critical(self, 'Dependency check failed', '\n\n'.join(problems) +
                                 '\n\nInstall a QGIS build with the PDAL Processing provider enabled, then restart QGIS.')
            return
        self.log.clear(); self.progress.setRange(0, 0); self.run_btn.setEnabled(False); self.cancel_btn.setEnabled(True)
        self.worker = PipelineWorker(c); self.worker.message.connect(self.log.appendPlainText); self.worker.failed.connect(self.failed); self.worker.completed.connect(self.completed); self.worker.start()

    def cancel(self):
        if self.worker: self.worker.cancel()

    def failed(self, text):
        self.progress.setRange(0, 1); self.progress.setValue(0); self.run_btn.setEnabled(True); self.cancel_btn.setEnabled(False)
        self.log.appendPlainText(text); QMessageBox.critical(self, 'Processing failed', 'See the Log tab for details.')

    def completed(self, products):
        self.progress.setRange(0, 1); self.progress.setValue(1); self.run_btn.setEnabled(True); self.cancel_btn.setEnabled(False)
        for name, path in products.items():
            if path.lower().endswith(('.tif', '.tiff')):
                layer = QgsRasterLayer(path, Path(path).stem)
            elif path.lower().endswith('.gpkg'):
                layer_name = Path(path).stem
                layer = QgsVectorLayer(path + '|layername=' + layer_name, layer_name, 'ogr')
            else:
                continue
            if layer.isValid(): QgsProject.instance().addMapLayer(layer)
        QMessageBox.information(self, 'Completed', 'Selected LiDAR features were extracted as separate vector layers and added to QGIS. Review candidate layers against orthophotos before final mapping.')
