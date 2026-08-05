# Copyright (C) 2026 Akmaul Hoque and Manish Kumar Naskar
# SPDX-License-Identifier: GPL-2.0-or-later

import os
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction
from qgis.core import QgsApplication, QgsMessageLog, Qgis

from .lidar_geoextract_dialog import LidarGeoExtractDialog


class LidarAutoVectorStudio:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None
        self.plugin_dir = os.path.dirname(__file__)

    def tr(self, message):
        return QCoreApplication.translate('LidarAutoVectorStudio', message)

    def initGui(self):
        icon = QIcon(os.path.join(self.plugin_dir, 'icon.svg'))
        self.action = QAction(icon, self.tr('LiDAR AutoVector Studio'), self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToVectorMenu(self.tr('&LiDAR AutoVector Studio'), self.action)

    def unload(self):
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginVectorMenu(self.tr('&LiDAR AutoVector Studio'), self.action)

    def run(self):
        if self.dialog is None:
            self.dialog = LidarGeoExtractDialog(self.iface)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
