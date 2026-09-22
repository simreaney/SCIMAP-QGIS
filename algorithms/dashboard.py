"""Build a self-contained web dashboard from SCIMAP results.

Writes a folder that opens by double-clicking ``index.html`` — a zoomable 2D
map, a 3D terrain view with the results draped over it, interactive charts, the
run's provenance and a download bundle. It needs no web server, no internet and
no GIS software, so results can be handed to anyone.

Follows the shape of ``algorithms/export.py``: point it at whichever outputs you
have and leave the rest blank.
"""

import json
import os

from qgis.core import (
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFile,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterString,
    QgsProcessingParameterVectorLayer,
    QgsProcessing,
)

from ..core import dashboard
from ..core._numba import describe_backend
from ..data.defaults import RAMP_CONNECTIVITY, RAMP_EROSION, RAMP_FLOOD, RAMP_LANDCOVER, RAMP_SCIMAP
from ..localization import tr
from .base import ScimapAlgorithmBase

# Named slots for the outputs SCIMAP produces, so each arrives with the right
# label, colour ramp and plain-English description rather than a layer name.
RASTER_SLOTS = (
    # (parameter, key, label, ramp, description, visible by default)
    ('INPUT_SCIMAP', 'scimap', 'In-channel risk', RAMP_SCIMAP,
     'Diffuse pollution risk concentration in the channel network — the headline '
     'SCIMAP result. Higher values mean a greater share of the catchment\'s '
     'risk arrives at that point in the river.', True),
    ('INPUT_EROSION', 'erosion', 'Erosion risk', RAMP_EROSION,
     'Where sediment and associated pollutants are most likely to be mobilised, '
     'combining slope, flow accumulation and land-cover risk weighting.', False),
    ('INPUT_CONNECTIVITY', 'connectivity', 'Network connectivity', RAMP_CONNECTIVITY,
     'How readily runoff from each cell reaches the stream network. High risk '
     'only matters where connectivity is also high.', False),
    ('INPUT_NETWORK_INDEX', 'network_index', 'Network index', RAMP_CONNECTIVITY,
     'Hydrological connectivity from terrain alone, without land cover or rainfall.', False),
    ('INPUT_FLOOD_MEAN', 'flood_mean', 'Flood risk (mean)', RAMP_FLOOD,
     'Mean SCIMAP-Flood response across the rainfall patterns tested.', False),
    ('INPUT_FLOOD_STDEV', 'flood_stdev', 'Flood risk (variability)', RAMP_FLOOD,
     'How much the flood response varies between rainfall patterns — high values '
     'mark places whose behaviour depends strongly on where rain falls.', False),
    ('INPUT_LANDCOVER', 'landcover', 'Land cover risk weighting', RAMP_LANDCOVER,
     'The per-cell risk weight derived from land cover.', False),
)

VECTOR_SLOTS = (
    # (parameter, key, dashboard label, parameter prompt, kind, field, max features)
    ('INPUT_STREAMS', 'streams', 'Stream network (in-channel risk)',
     'Stream network (with a Risk field)', 'line', 'Risk', 4000),
    ('INPUT_CATCHMENT', 'catchment', 'Catchment boundary',
     'Catchment boundary', 'polygon', None, None),
    ('INPUT_POINTS', 'points', 'Stream risk points',
     'Stream risk points', 'point', 'scimap_ris', 5000),
)

DETAIL_CHOICES = (512, 1024, 2048)
MESH_CHOICES = (256, 512, 1024)


class ScimapWebDashboardAlgorithm(ScimapAlgorithmBase):
    """Export SCIMAP results as an offline, interactive web dashboard."""

    INPUT_DEM = 'INPUT_DEM'
    EXTRA_RASTERS = 'EXTRA_RASTERS'
    INPUT_CURVE_CSV = 'INPUT_CURVE_CSV'

    TITLE = 'TITLE'
    SUBTITLE = 'SUBTITLE'
    ORGANISATION = 'ORGANISATION'

    DETAIL = 'DETAIL'
    DETAIL_3D = 'DETAIL_3D'
    MAX_ZOOM = 'MAX_ZOOM'
    VERTICAL_EXAGGERATION = 'VERTICAL_EXAGGERATION'

    INCLUDE_3D = 'INCLUDE_3D'
    INCLUDE_TILES = 'INCLUDE_TILES'
    INCLUDE_DOWNLOADS = 'INCLUDE_DOWNLOADS'
    ONLINE_BASEMAPS = 'ONLINE_BASEMAPS'
    WRITE_ZIP = 'WRITE_ZIP'
    RUN_METADATA = 'RUN_METADATA'

    OUTPUT_FOLDER = 'OUTPUT_FOLDER'
    INDEX_HTML = 'INDEX_HTML'

    _ICON = 'icon_dashboard.svg'

    def createInstance(self):
        return ScimapWebDashboardAlgorithm()

    def name(self):
        return 'webdashboard'

    def displayName(self):
        return tr('Create Web Dashboard')

    def group(self):
        return tr('Export')

    def groupId(self):
        return 'scimap_export'

    def shortHelpString(self):
        return tr(
            'Builds a self-contained web dashboard from SCIMAP results: a zoomable '
            '2D map, a 3D terrain view with the results draped over it, interactive '
            'charts and a download bundle.\n\n'
            'The output folder opens by double-clicking index.html. It needs no web '
            'server, no internet connection and no GIS software, so it can be zipped '
            'and sent to anyone.\n\n'
            'Supply the DEM used for the run — it provides the shaded-relief base '
            'map and the 3D terrain. Every other input is optional; leave blank what '
            'you do not have.'
        )

    # ── parameters ──────────────────────────────────────────────────────

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT_DEM, tr('Digital elevation model (for relief and 3D terrain)'),
            optional=True))

        for parameter, _key, label, _ramp, _description, _visible in RASTER_SLOTS:
            self.addParameter(QgsProcessingParameterRasterLayer(
                parameter, tr(label), optional=True))

        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.EXTRA_RASTERS, tr('Additional raster layers'),
            layerType=QgsProcessing.TypeRaster, optional=True))

        for parameter, _key, _label, prompt, _kind, _field, _cap in VECTOR_SLOTS:
            self.addParameter(QgsProcessingParameterVectorLayer(
                parameter, tr(prompt), optional=True))

        self.addParameter(QgsProcessingParameterFile(
            self.INPUT_CURVE_CSV, tr('Wetness-connectivity curve dataset (CSV)'),
            behavior=QgsProcessingParameterFile.File,
            fileFilter='CSV files (*.csv)', optional=True))

        self.addParameter(QgsProcessingParameterString(
            self.TITLE, tr('Dashboard title'), defaultValue='SCIMAP results'))
        self.addParameter(QgsProcessingParameterString(
            self.SUBTITLE, tr('Subtitle'), defaultValue='', optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.ORGANISATION, tr('Organisation'), defaultValue='', optional=True))

        self.addParameter(QgsProcessingParameterEnum(
            self.DETAIL, tr('Detail of the embedded data'),
            options=[tr('Standard (512 px)'), tr('High (1024 px)'), tr('Maximum (2048 px)')],
            defaultValue=1))
        self.addParameter(QgsProcessingParameterEnum(
            self.DETAIL_3D, tr('Detail of the 3D terrain mesh'),
            options=[tr('Low (256)'), tr('Standard (512)'), tr('High (1024)')],
            defaultValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_ZOOM, tr('Maximum map zoom level; 0 matches the data resolution'),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=0, minValue=0, maxValue=19))
        self.addParameter(QgsProcessingParameterNumber(
            self.VERTICAL_EXAGGERATION, tr('Initial vertical exaggeration in the 3D view'),
            type=QgsProcessingParameterNumber.Double,
            defaultValue=1.5, minValue=1.0, maxValue=6.0))

        self.addParameter(QgsProcessingParameterBoolean(
            self.INCLUDE_3D, tr('Include the 3D terrain view'), defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.INCLUDE_TILES, tr('Include map tiles (needed for the 2D map)'),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.INCLUDE_DOWNLOADS, tr('Include the source data for download'),
            defaultValue=True))
        # On by default: the shaded relief stays the selected base layer, so a
        # viewer with no connection sees exactly what they saw before and the
        # basemaps are simply an option they never pick.
        self.addParameter(QgsProcessingParameterBoolean(
            self.ONLINE_BASEMAPS,
            tr('Offer online basemaps when the viewer has an internet connection'),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.WRITE_ZIP, tr('Also write a zip of the whole dashboard'),
            defaultValue=True))

        metadata = QgsProcessingParameterString(
            self.RUN_METADATA, tr('Run metadata (JSON)'), defaultValue='', optional=True)
        metadata.setFlags(metadata.flags() | metadata.FlagHidden)
        self.addParameter(metadata)

        self.addParameter(QgsProcessingParameterFolderDestination(
            self.OUTPUT_FOLDER, tr('Dashboard folder')))
        self.addOutput(QgsProcessingOutputFile(
            self.INDEX_HTML, tr('Dashboard home page')))

    # ── run ─────────────────────────────────────────────────────────────

    def processAlgorithm(self, parameters, context, feedback):
        out_dir = self.parameterAsString(parameters, self.OUTPUT_FOLDER, context)
        if not out_dir:
            raise QgsProcessingException(tr(
                'Set the "Dashboard folder" parameter at the bottom of this dialog: '
                'click the … button and choose Save to Directory, or type a path. '
                'The dashboard is written into that folder.'))

        dem_layer = self.parameterAsRasterLayer(parameters, self.INPUT_DEM, context)
        layers = self._collect_rasters(parameters, context, feedback)
        vectors = self._collect_vectors(parameters, context, feedback)

        if not layers and dem_layer is None:
            raise QgsProcessingException(
                tr('Choose at least one result layer, or a DEM, to put on the dashboard.'))
        if dem_layer is None:
            feedback.pushWarning(tr(
                'No DEM supplied: the dashboard will have no shaded-relief base map '
                'and no 3D terrain view.'))

        spec = dashboard.DashboardSpec(
            title=self.parameterAsString(parameters, self.TITLE, context) or 'SCIMAP results',
            subtitle=self.parameterAsString(parameters, self.SUBTITLE, context),
            organisation=self.parameterAsString(parameters, self.ORGANISATION, context),
            dem_path=_source(dem_layer),
            layers=layers,
            vectors=vectors,
            scatter_csv=self.parameterAsFile(parameters, self.INPUT_CURVE_CSV, context),
            grid_max_px=DETAIL_CHOICES[self.parameterAsEnum(parameters, self.DETAIL, context)],
            mesh_cap=MESH_CHOICES[self.parameterAsEnum(parameters, self.DETAIL_3D, context)],
            max_zoom=(self.parameterAsInt(parameters, self.MAX_ZOOM, context) or None),
            vertical_exaggeration=self.parameterAsDouble(
                parameters, self.VERTICAL_EXAGGERATION, context),
            include_3d=self.parameterAsBool(parameters, self.INCLUDE_3D, context),
            include_tiles=self.parameterAsBool(parameters, self.INCLUDE_TILES, context),
            include_downloads=self.parameterAsBool(parameters, self.INCLUDE_DOWNLOADS, context),
            online_basemaps=self.parameterAsBool(parameters, self.ONLINE_BASEMAPS, context),
            write_zip=self.parameterAsBool(parameters, self.WRITE_ZIP, context),
            metadata=self._metadata(parameters, context, dem_layer, layers),
        )

        os.makedirs(out_dir, exist_ok=True)
        index_path = dashboard.build_dashboard(spec, out_dir, feedback)
        if feedback.isCanceled():
            return {}

        feedback.pushInfo(tr('Open index.html in the dashboard folder to view the results.'))
        return {self.OUTPUT_FOLDER: out_dir, self.INDEX_HTML: index_path}

    # ── gathering inputs ────────────────────────────────────────────────

    def _collect_rasters(self, parameters, context, feedback):
        layers = []
        seen = set()

        for parameter, key, label, ramp, description, visible in RASTER_SLOTS:
            layer = self.parameterAsRasterLayer(parameters, parameter, context)
            source = _source(layer)
            if not source:
                continue
            seen.add(source)
            layers.append(dashboard.LayerSpec(
                key, tr(label), source, ramp=ramp, description=tr(description),
                default_visible=visible,
            ))

        # Whatever the named slots did not cover, labelled by its layer name.
        for layer in self.parameterAsLayerList(parameters, self.EXTRA_RASTERS, context) or []:
            source = _source(layer)
            if not source or source in seen:
                continue
            seen.add(source)
            layers.append(dashboard.LayerSpec(
                _slug(layer.name()), layer.name(), source, ramp=RAMP_CONNECTIVITY,
            ))

        # Nothing else is visible by default, so make sure something is.
        if layers and not any(layer.default_visible for layer in layers):
            layers[0].default_visible = True

        feedback.pushInfo(tr('Publishing {n} raster layers.').format(n=len(layers)))
        return layers

    def _collect_vectors(self, parameters, context, feedback):
        vectors = []
        for parameter, key, label, _prompt, kind, field, cap in VECTOR_SLOTS:
            layer = self.parameterAsVectorLayer(parameters, parameter, context)
            source = _source(layer)
            if not source:
                continue
            if key == 'points':
                feedback.pushInfo(tr(
                    'Stream risk points are capped at {n} features on the map; the '
                    'full layer is in the download bundle.').format(n=cap))
            vectors.append(dashboard.VectorSpec(
                key, tr(label), source, kind=kind, field=field, max_features=cap,
                # Take the CRS from the layer, as export.py does for KML: a
                # shapefile written without a .prj still has one in QGIS.
                crs_wkt=layer.crs().toWkt() if layer.crs().isValid() else None,
            ))
        return vectors

    def _metadata(self, parameters, context, dem_layer, layers):
        """Provenance: whatever the panel recorded, plus what we can see here."""
        raw = self.parameterAsString(parameters, self.RUN_METADATA, context)
        metadata = {}
        if raw:
            try:
                metadata = json.loads(raw)
            except ValueError:
                metadata = {}

        plugin = metadata.setdefault('plugin', {})
        plugin.setdefault('name', 'SCIMAP Toolkit')
        plugin.setdefault('version', _plugin_version())
        plugin.setdefault('qgis', _qgis_version())
        plugin.setdefault('gdal', _gdal_version())

        run = metadata.setdefault('run', {})
        run.setdefault('connectivityBackend', describe_backend())

        inputs = metadata.setdefault('inputs', [])
        if dem_layer is not None:
            inputs.append(_describe_layer(dem_layer, 'Digital elevation model'))
        for layer_spec in layers:
            inputs.append({'role': layer_spec.label,
                           'name': os.path.basename(layer_spec.path),
                           'source': layer_spec.path})
        return metadata


def _source(layer):
    """A plain file path for a layer, or '' when it has none we can read."""
    if layer is None:
        return ''
    source = layer.source()
    if not source:
        return ''
    # QGIS decorates some sources ("file.gpkg|layername=x"); GDAL wants the file.
    path = source.split('|', 1)[0]
    return path if os.path.exists(path) else source


def _describe_layer(layer, role):
    provider = layer.dataProvider()
    extent = layer.extent()
    described = {
        'role': role,
        'name': layer.name(),
        'source': _source(layer),
        'crs': layer.crs().authid() or layer.crs().description(),
        'extent': [extent.xMinimum(), extent.yMinimum(),
                   extent.xMaximum(), extent.yMaximum()],
    }
    if provider is not None and hasattr(layer, 'width'):
        described['size'] = [layer.width(), layer.height()]
        described['pixel'] = [layer.rasterUnitsPerPixelX(), layer.rasterUnitsPerPixelY()]
    return described


def _slug(name):
    cleaned = ''.join(char.lower() if char.isalnum() else '_' for char in str(name))
    return cleaned.strip('_') or 'layer'


def _plugin_version():
    metadata_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'metadata.txt')
    try:
        with open(metadata_path, 'r') as handle:
            for line in handle:
                if line.startswith('version='):
                    return line.split('=', 1)[1].strip()
    except OSError:
        pass
    return 'unknown'


def _qgis_version():
    try:
        from qgis.core import Qgis
        return Qgis.QGIS_VERSION
    except Exception:
        return 'unknown'


def _gdal_version():
    try:
        from osgeo import gdal
        return gdal.__version__
    except Exception:
        return 'unknown'
