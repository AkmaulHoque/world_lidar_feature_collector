# Copyright (C) 2026 Akmaul Hoque and Manish Kumar Naskar
# SPDX-License-Identifier: GPL-2.0-or-later

def classFactory(iface):
    from .lidar_geoextract_plugin import LidarAutoVectorStudio
    return LidarAutoVectorStudio(iface)
