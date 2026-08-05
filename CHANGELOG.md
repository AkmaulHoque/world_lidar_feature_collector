# Changelog

## 0.6.1
- Removed all `__pycache__`, `.pyc`, and `.pyo` files from the distribution.
- Updated plugin authors to Mr. Akmaul Hoque and Dr. Manish Kumar Naskar.
- Standardized copyright headers and repository documentation.
- Prepared a clean QGIS Plugin Repository and GitHub release package.

## 0.6.0
- Removed all subprocess and external executable invocation.
- Routed point-cloud operations through registered QGIS Processing PDAL algorithms.
- Disabled in-plugin SMRF reclassification in the repository-safe build.
- Retained in-process GDAL raster calculation, terrain analysis, sieving, and polygonization.

## 0.5.1
- Fixed QGIS Windows invalid-data-source errors caused by invoking GDAL modules through qgis-bin.exe.
- GDAL raster calculation, sieving and polygonization now run directly through Python APIs.
- Added output validation and Windows paths-with-spaces safety.

# Changelog

## 0.5.0
- Preserves existing classified LiDAR by default; optional SMRF reclassification.
- Added PDAL/GDAL preflight dependency checks.
- Fixed NoData propagation that caused false feature polygons.
- Added raster validity checks and clearer command errors.
- Added overwrite-safe GeoPackage and raster mask generation.
- Improved loading of named GeoPackage layers.
- Tested package structure, syntax, pipeline JSON generation, and uploaded LAZ header.

## 0.4.0
- Individual feature extraction workflow.
