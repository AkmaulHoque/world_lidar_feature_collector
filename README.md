# LiDAR AutoVector Studio

QGIS plugin for deriving candidate vector features from LAS/LAZ/COPC/EPT point clouds.

## Recommended workflow
For already classified products such as USGS 3DEP, leave **Reclassify ground with SMRF** unchecked. The plugin preserves standard Classification 2 ground points and avoids damaging authoritative classifications. Enable SMRF only for genuinely unclassified data.

## Outputs
DTM, DSM, CHM, density, roughness, slope, and separate GeoPackage layers for buildings, settlements, vegetation, low vegetation, cropland, bare land, roads, water, and elevated linear candidates.

## Requirements
- QGIS build with GDAL Python utilities
- PDAL command line available within QGIS/OSGeo4W

Run the plugin dependency check before processing. LiDAR-only land-cover classes are candidate outputs and require validation with imagery.


## Security design (v0.6.1)
This version does not use `subprocess`, shell commands, `os.system`, or arbitrary executable paths. Point-cloud operations are executed only through registered QGIS Processing PDAL algorithms.

## Authors
- Mr. Akmaul Hoque
- Dr. Manish Kumar Naskar
