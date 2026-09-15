"""Overland Flow Distance to Point.

Computes the directional downslope distance from every cell to the nearest
target point, using WhiteboxTools DownslopeDistanceToStream over a rasterised
point set. Cells that do not drain to a target are left as NoData.
"""

import math
import os
import tempfile

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import (
    QgsCoordinateTransform,
    QgsProcessing,
    QgsPointXY,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterVectorLayer,
    QgsProject,
    QgsSpatialIndex,
)

from ..core import styling, vectors
from ..data.defaults import RAMP_OFD
from ..localization import tr
from .base import ScimapAlgorithmBase, ScimapCanceled


class ScimapOverlandFlowDistanceAlgorithm(ScimapAlgorithmBase):
    """Overland Flow Distance to Point.

    Computes the downslope flow-path distance across the land surface from
    each cell to the nearest of one or more target points, using
    WhiteboxTools' DownslopeDistanceToStream tool exclusively (no GRASS or
    SAGA dependency).

    Unlike a cost-distance surface, this follows the D8 downslope flow
    direction derived from the DEM: water only travels downhill, so cells
    that do not drain to a target point receive no distance (NoData)
    instead of an omnidirectional straight-line-like distance.
    """

    INPUT_DEM = 'INPUT_DEM'
    INPUT_POINTS = 'INPUT_POINTS'
    INPUT_CHANNELS = 'INPUT_CHANNELS'
    SNAP_DISTANCE = 'SNAP_DISTANCE'
    SNAP_TO_CHANNEL = 'SNAP_TO_CHANNEL'
    SNAP_RADIUS = 'SNAP_RADIUS'
    SNAP_AREA_THRESHOLD = 'SNAP_AREA_THRESHOLD'
    SNAP_MIN_RATIO = 'SNAP_MIN_RATIO'
    FILL_DEPRESSIONS = 'FILL_DEPRESSIONS'

    OUT_DISTANCE = 'OUT_DISTANCE'

    def createInstance(self):
        return ScimapOverlandFlowDistanceAlgorithm()

    def name(self):
        return 'scimapoverlandflowdistance'

    def displayName(self):
        return tr('Overland Flow Distance to Point')

    _ICON = 'icon_ofd.svg'

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('Digital Elevation Model (DEM)')))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_POINTS,
            tr('Target Point(s)'),
            types=[QgsProcessing.TypeVectorPoint],
        ))

        self.addParameter(QgsProcessingParameterVectorLayer(
            self.INPUT_CHANNELS,
            tr('Channel Network (optional; snaps target point(s) to the nearest channel)'),
            types=[QgsProcessing.TypeVectorLine],
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP_DISTANCE,
            tr('Maximum snap distance to channel (map units; ignored if no channel network supplied)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=100.0,
            minValue=0.0,
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.SNAP_TO_CHANNEL,
            tr('Snap target point(s) to the channel network by flow accumulation, as in Delineate Catchment'),
            defaultValue=False,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP_RADIUS,
            tr('Snap search radius (map units; ignored unless snapping to the channel network)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=250.0,
            minValue=0.0,
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP_AREA_THRESHOLD,
            tr('Minimum contributing area to snap onto (m²; ignored unless snapping to the channel network)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=800000.0,
            minValue=0.0,
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterNumber(
            self.SNAP_MIN_RATIO,
            tr('Minimum accumulation ratio vs the clicked cell (ignored unless snapping to the channel network)'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=100.0,
            minValue=1.0,
            optional=True,
        ))

        self.addParameter(QgsProcessingParameterBoolean(
            self.FILL_DEPRESSIONS,
            tr('Breach DEM depressions before computing flow direction (recommended, avoids broken flow paths at sinks)'),
            defaultValue=True,
        ))

        self.add_colour_ramp_parameter()
        self.add_wbt_parameter()

        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_DISTANCE, tr('Overland Flow Travel Distance')))

    def _snap_points_to_channels(self, points, channels_layer, dem_crs, context, max_distance, feedback=None):
        """Snap each (x, y) target point onto the nearest line in channels_layer.

        Points further than max_distance from any channel (when max_distance > 0)
        are left unsnapped, since a distant "nearest" match is more likely a data
        error than an intended snap target.
        """
        to_channel_crs = None
        to_dem_crs = None
        if channels_layer.crs().isValid() and dem_crs.isValid() and channels_layer.crs() != dem_crs:
            to_channel_crs = QgsCoordinateTransform(dem_crs, channels_layer.crs(), context.transformContext())
            to_dem_crs = QgsCoordinateTransform(channels_layer.crs(), dem_crs, context.transformContext())

        index = QgsSpatialIndex(channels_layer.getFeatures())
        geometries = {f.id(): f.geometry() for f in channels_layer.getFeatures()}

        snapped_points = []
        snapped_count = 0
        for x, y in points:
            query_point = QgsPointXY(x, y)
            if to_channel_crs is not None:
                query_point = to_channel_crs.transform(query_point)

            best_point = None
            best_dist_sq = None
            for fid in index.nearestNeighbor(query_point, 5):
                geometry = geometries.get(fid)
                if geometry is None or geometry.isEmpty():
                    continue
                dist_sq, closest_point, _, _ = geometry.closestSegmentWithContext(query_point)
                if best_dist_sq is None or dist_sq < best_dist_sq:
                    best_dist_sq = dist_sq
                    best_point = closest_point

            if best_point is None:
                snapped_points.append((x, y))
                continue

            distance = math.sqrt(best_dist_sq)
            if max_distance and max_distance > 0 and distance > max_distance:
                snapped_points.append((x, y))
                continue

            if to_dem_crs is not None:
                best_point = to_dem_crs.transform(best_point)

            snapped_points.append((float(best_point.x()), float(best_point.y())))
            snapped_count += 1

        if feedback is not None:
            feedback.pushInfo(
                f'   Snapped {snapped_count} of {len(points)} target point(s) to the channel network.'
            )

        return snapped_points

    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        points_layer = self.parameterAsVectorLayer(parameters, self.INPUT_POINTS, context)
        channels_layer = self.parameterAsVectorLayer(parameters, self.INPUT_CHANNELS, context)
        snap_distance = self.parameterAsDouble(parameters, self.SNAP_DISTANCE, context)
        snap_to_channel = self.parameterAsBoolean(parameters, self.SNAP_TO_CHANNEL, context)
        snap_radius = self.parameterAsDouble(parameters, self.SNAP_RADIUS, context)
        snap_area_threshold = self.parameterAsDouble(parameters, self.SNAP_AREA_THRESHOLD, context)
        snap_min_ratio = self.parameterAsDouble(parameters, self.SNAP_MIN_RATIO, context)
        fill_depressions = self.parameterAsBoolean(parameters, self.FILL_DEPRESSIONS, context)
        self.resolve_wbt_executable(parameters, context, feedback)

        out_distance_path = self.parameterAsOutputLayer(parameters, self.OUT_DISTANCE, context)

        dem_ds = gdal.Open(dem_layer.source())
        if dem_ds is None:
            raise RuntimeError(f'Could not open DEM: {dem_layer.source()}')
        dem_band = dem_ds.GetRasterBand(1)
        dem_arr = dem_band.ReadAsArray().astype(np.float32, copy=False)
        dem_nodata = dem_band.GetNoDataValue()
        if dem_nodata is not None:
            if np.isfinite(dem_nodata):
                dem_mask = (~np.isclose(dem_arr, dem_nodata)) & np.isfinite(dem_arr)
            else:
                dem_mask = (dem_arr != dem_nodata) & np.isfinite(dem_arr)
        else:
            dem_mask = np.isfinite(dem_arr)

        if not np.any(dem_mask):
            raise RuntimeError('DEM contains no valid cells.')

        reference_gt = dem_ds.GetGeoTransform()
        reference_size = (dem_ds.RasterXSize, dem_ds.RasterYSize)

        def _read_aligned_raster(layer_or_path, label):
            source = layer_or_path.source() if hasattr(layer_or_path, 'source') else str(layer_or_path)
            ds = gdal.Open(source)
            if ds is None:
                raise RuntimeError(f'Could not open {label}: {source}')
            if ds.RasterXSize != reference_size[0] or ds.RasterYSize != reference_size[1]:
                raise RuntimeError(f'{label} does not match the DEM grid size.')
            gt = ds.GetGeoTransform()
            if any(abs(float(gt[i]) - float(reference_gt[i])) > 1e-9 for i in range(6)):
                raise RuntimeError(f'{label} does not match the DEM grid alignment.')
            band = ds.GetRasterBand(1)
            arr = band.ReadAsArray().astype(np.float32, copy=False)
            nodata = band.GetNoDataValue()
            if nodata is not None:
                if np.isfinite(nodata):
                    arr[np.isclose(arr, nodata)] = np.nan
                else:
                    arr[arr == nodata] = np.nan
            arr[~np.isfinite(arr)] = np.nan
            return ds, arr

        with tempfile.TemporaryDirectory() as tmpdir:
            feedback.setProgress(5)

            if fill_depressions:
                feedback.pushInfo('1. Breaching DEM depressions before computing flow direction...')
                dem_fill_path = os.path.join(tmpdir, 'dem_fill.tif')
                self.run_wbt('BreachDepressions', {
                    'dem': dem_layer.source(),
                    'output': dem_fill_path,
                }, feedback)
                flow_dem_path = dem_fill_path
            else:
                feedback.pushInfo('1. Using DEM as supplied for flow direction (depression filling disabled)...')
                flow_dem_path = dem_layer.source()

            feedback.setProgress(30)
            feedback.pushInfo('2. Rasterising target point(s)...')

            target_points = []
            transform = None
            if points_layer.crs().isValid() and dem_layer.crs().isValid() and points_layer.crs() != dem_layer.crs():
                transform = QgsCoordinateTransform(points_layer.crs(), dem_layer.crs(), context.transformContext())

            for feature in points_layer.getFeatures():
                geometry = feature.geometry()
                if geometry is None or geometry.isEmpty():
                    continue
                if transform is not None:
                    geometry = geometry.clone()
                    geometry.transform(transform)
                points = geometry.asMultiPoint() if geometry.isMultipart() else [geometry.asPoint()]
                for point in points:
                    target_points.append((float(point.x()), float(point.y())))

            if not target_points:
                raise RuntimeError('Target point layer does not contain any valid points.')

            if channels_layer is not None:
                feedback.pushInfo(
                    f'   Snapping {len(target_points)} target point(s) to the nearest channel...'
                )
                target_points = self._snap_points_to_channels(
                    target_points, channels_layer, dem_layer.crs(), context, snap_distance, feedback
                )

            if snap_to_channel:
                feedback.pushInfo(
                    '   Computing flow accumulation to find the channel network...'
                )
                accum_path = os.path.join(tmpdir, 'accum.tif')
                self.run_wbt('D8FlowAccumulation', {
                    'i': flow_dem_path,
                    'out_type': 'cells',
                    'output': accum_path,
                }, feedback)

                accum_ds = gdal.Open(accum_path)
                accum_gt = accum_ds.GetGeoTransform()
                cell_area = abs(accum_gt[1] * accum_gt[5])
                accum_ds = None
                snap_cells = snap_area_threshold / cell_area if cell_area else snap_area_threshold

                snapped_points = []
                moved_count = 0
                for x, y in target_points:
                    snap_x, snap_y, _ = vectors.snap_pour_point_to_max_accumulation(
                        accum_path, x, y, snap_radius, snap_cells, snap_min_ratio,
                    )
                    if (snap_x, snap_y) != (x, y):
                        moved_count += 1
                    snapped_points.append((snap_x, snap_y))
                target_points = snapped_points
                feedback.pushInfo(
                    f'   Snapped {moved_count} of {len(target_points)} target point(s) '
                    f'onto the channel network.'
                )

            pt_path = os.path.join(tmpdir, 'points.tif')
            self.create_points_raster_safe(target_points, dem_layer.source(), pt_path, feedback)

            feedback.setProgress(50)
            feedback.pushInfo(
                f'3. Computing WhiteboxTools downslope flow-path distance to nearest of '
                f'{len(target_points)} target point(s)...'
            )
            dist_path = os.path.join(tmpdir, 'distance.tif')
            self.run_wbt('DownslopeDistanceToStream', {
                'dem': flow_dem_path,
                'streams': pt_path,
                'output': dist_path,
            }, feedback)

            _, dist_arr = _read_aligned_raster(dist_path, 'overland flow distance raster')
            # Cells whose downslope flow path never reaches a target point (e.g. they
            # drain off the grid or into a different catchment) get no distance, rather
            # than being coerced to 0 and looking like they're at the target.
            valid_output_mask = dem_mask & np.isfinite(dist_arr)
            dist_arr = np.where(valid_output_mask, np.maximum(dist_arr, 0.0), 0.0)

            feedback.setProgress(90)
            feedback.pushInfo('4. Writing output raster...')
            self.save_raster(dist_arr, out_distance_path, dem_ds, gdal.GDT_Float32, valid_output_mask)

            feedback.setProgress(100)
            feedback.pushInfo('Overland flow distance complete.')
            feedback.pushInfo(f'- distance: {out_distance_path}')

        styling.style_output(
            context, out_distance_path,
            self.colour_ramp_name(parameters, context, RAMP_OFD))

        return {
            self.OUT_DISTANCE: out_distance_path,
        }
