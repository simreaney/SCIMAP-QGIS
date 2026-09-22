"""Assemble a self-contained SCIMAP web dashboard.

Takes a :class:`DashboardSpec` describing a finished run and writes a folder
that opens by double-clicking ``index.html`` — no server, no internet, no GIS.

This module orchestrates; ``core/webexport.py`` does the GDAL and NumPy work and
``core/ramps.py`` supplies the colours. It imports neither QGIS nor GDAL
directly, so the assembly logic can be exercised on its own.
"""

import datetime
import hashlib
import json
import logging
import os
import shutil
import zipfile

from . import ramps, webexport

logger = logging.getLogger(__name__)

SCHEMA = 1

WEB_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web')

CITATION = {
    'text': ('Reaney, S.M., Lane, S.N., Heathwaite, A.L. and Dugdale, L.J. (2011) '
             'Risk-based modelling of diffuse land use impacts from rural landscapes '
             'upon salmonid fry abundance. Ecological Modelling 222(4), 1016-1029.'),
    'doi': '10.1016/j.ecolmodel.2010.08.022',
}

DISCLAIMER = (
    'SCIMAP maps the relative risk of diffuse pollution across a catchment: it '
    'shows where risk is concentrated, not absolute pollutant loads. Results '
    'depend on the input terrain, land cover and rainfall data and on the risk '
    'weights recorded on this page. Use them to prioritise where to look, '
    'alongside local knowledge and field survey.'
)


def folder_name(title='', when=None):
    """A distinctive folder name for one dashboard, e.g.
    ``SCIMAP_Dashboard_River_Skell_2026-09-16``.

    Used where the caller chooses a parent directory rather than naming the
    output itself: the dashboard is ~600 files plus a sibling zip, so writing
    it loose into somewhere like Documents would be a mess.
    """
    when = when or datetime.date.today()
    slug = ''.join(char if char.isalnum() else '_' for char in str(title)).strip('_')
    while '__' in slug:
        slug = slug.replace('__', '_')
    slug = slug[:48].strip('_')
    parts = ['SCIMAP_Dashboard'] + ([slug] if slug else []) + [when.isoformat()]
    return '_'.join(parts)


def unique_folder(parent, title='', when=None):
    """:func:`folder_name` under *parent*, suffixed if that name is taken."""
    base = folder_name(title, when)
    candidate = os.path.join(parent, base)
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(parent, f"{base}_{counter}")
        counter += 1
    return candidate


class LayerSpec(object):
    """One raster layer to publish."""

    __slots__ = ('key', 'label', 'path', 'ramp', 'units', 'description',
                 'default_visible', 'drape3d', 'resample', 'downloads')

    def __init__(self, key, label, path, ramp='viridis', units='', description='',
                 default_visible=False, drape3d=True, resample='bilinear',
                 downloads=()):
        self.key = key
        self.label = label
        self.path = path
        self.ramp = ramps.normalise_name(ramp)
        self.units = units
        self.description = description
        self.default_visible = default_visible
        self.drape3d = drape3d
        self.resample = resample
        self.downloads = tuple(downloads)


class VectorSpec(object):
    """One vector layer to publish."""

    __slots__ = ('key', 'label', 'path', 'kind', 'field', 'max_features', 'crs_wkt')

    def __init__(self, key, label, path, kind='line', field=None,
                 max_features=None, crs_wkt=None):
        self.key = key
        self.label = label
        self.path = path
        self.kind = kind
        self.field = field
        self.max_features = max_features
        self.crs_wkt = crs_wkt


class DashboardSpec(object):
    """Everything the exporter needs to build one dashboard."""

    def __init__(self, title='SCIMAP results', subtitle='', organisation='',
                 dem_path='', layers=(), vectors=(), scatter_csv='',
                 grid_max_px=1024, mesh_cap=512, max_zoom=None,
                 vertical_exaggeration=1.5, include_3d=True, include_tiles=True,
                 include_downloads=True, online_basemaps=True, write_zip=True,
                 metadata=None):
        self.title = title or 'SCIMAP results'
        self.subtitle = subtitle
        self.organisation = organisation
        self.dem_path = dem_path
        self.layers = list(layers)
        self.vectors = list(vectors)
        self.scatter_csv = scatter_csv
        self.grid_max_px = int(grid_max_px)
        self.mesh_cap = int(mesh_cap)
        self.max_zoom = max_zoom
        self.vertical_exaggeration = float(vertical_exaggeration)
        self.include_3d = bool(include_3d)
        self.include_tiles = bool(include_tiles)
        self.include_downloads = bool(include_downloads)
        self.online_basemaps = bool(online_basemaps)
        self.write_zip = bool(write_zip)
        self.metadata = dict(metadata or {})


class _NullFeedback(object):
    def pushInfo(self, message): logger.info('%s', message)
    def pushWarning(self, message): logger.warning('%s', message)
    def reportError(self, message, fatalError=False): logger.error('%s', message)
    def setProgress(self, value): pass
    def isCanceled(self): return False


def build_dashboard(spec, out_dir, feedback=None):
    """Write the dashboard into *out_dir*, returning the path of ``index.html``."""
    feedback = feedback or _NullFeedback()

    if not spec.layers and not spec.dem_path:
        raise RuntimeError("Choose at least one result layer or a DEM to build a dashboard.")

    _warn_if_occupied(out_dir, feedback)

    data_dir = os.path.join(out_dir, 'data')
    os.makedirs(data_dir, exist_ok=True)

    grid = _build_grid(spec, feedback)
    _copy_assets(out_dir, spec, feedback)

    feedback.setProgress(8)
    published = _write_rasters(spec, grid, out_dir, data_dir, feedback)
    if feedback.isCanceled():
        return ''

    feedback.setProgress(72)
    vectors, charts = _write_vectors_and_charts(spec, grid, data_dir, feedback)

    feedback.setProgress(84)
    downloads = _write_downloads(spec, out_dir, feedback) if spec.include_downloads else []

    feedback.setProgress(92)
    _write_grid(spec, grid, data_dir, feedback)
    # The relief ramp is not offered in the UI but the printable map composites
    # the hillshade with it, so it has to travel too.
    webexport.write_js_global(
        os.path.join(data_dir, 'ramps.js'), 'window.SCIMAP_RAMPS',
        ramps.ramps_payload(list(ramps.RAMP_NAMES) + [ramps.RELIEF_RAMP]))
    _write_layer_index(published, data_dir)
    webexport.write_js_global(os.path.join(data_dir, 'vectors.js'),
                              'window.SCIMAP_VECTORS', vectors)
    webexport.write_js_global(os.path.join(data_dir, 'charts.js'),
                              'window.SCIMAP_CHARTS', charts)
    _write_meta(spec, grid, published, downloads, data_dir)

    index_path = _render_index(spec, published, out_dir)
    _write_readme(spec, out_dir)

    feedback.setProgress(96)
    if spec.write_zip:
        _write_zip(out_dir, feedback)

    feedback.setProgress(100)
    feedback.pushInfo(f"Dashboard written to {out_dir} ({webexport_size(out_dir)}).")
    return index_path


# ── grid and assets ────────────────────────────────────────────────────────

def _warn_if_occupied(out_dir, feedback):
    """Flag a target folder that already holds something other than a dashboard."""
    if not os.path.isdir(out_dir):
        return
    ours = {'index.html', 'README.txt', 'assets', 'vendor', 'data', 'tiles', 'downloads'}
    strangers = [name for name in os.listdir(out_dir)
                 if not name.startswith('.') and name not in ours]
    if strangers:
        sample = ', '.join(sorted(strangers)[:4])
        more = '' if len(strangers) <= 4 else f" and {len(strangers) - 4} more"
        feedback.pushWarning(
            f"{out_dir} already contains {sample}{more}. The dashboard will be "
            "written alongside them; an empty folder of its own is usually tidier."
        )


def _build_grid(spec, feedback):
    sources = [layer.path for layer in spec.layers]
    if spec.dem_path:
        sources.insert(0, spec.dem_path)
    grid = webexport.build_common_grid(sources, max_px=spec.grid_max_px)

    feedback.pushInfo(
        f"Dashboard grid: {grid.width} x {grid.height} px in EPSG:3857, "
        f"about {grid.pixel_size[0] * grid.mercator_scale:.1f} m on the ground."
    )
    if grid.latitude_span() > 1.0:
        feedback.pushWarning(
            "This extent spans more than a degree of latitude. The 3D view "
            "corrects Web Mercator distortion using a single value for the "
            "centre, so relief near the edges is slightly off."
        )
    return grid


def _copy_assets(out_dir, spec, feedback):
    template = os.path.join(WEB_ROOT, 'template')
    vendor = os.path.join(WEB_ROOT, 'vendor')
    if not os.path.isdir(template) or not os.path.isdir(vendor):
        raise RuntimeError(
            "The dashboard's web assets are missing from the plugin. Reinstall "
            "the SCIMAP plugin, or run qgis_plugin/web/fetch_vendor.sh."
        )

    shutil.copytree(os.path.join(template, 'assets'), os.path.join(out_dir, 'assets'),
                    dirs_exist_ok=True)
    shutil.copytree(vendor, os.path.join(out_dir, 'vendor'), dirs_exist_ok=True)

    if not spec.include_3d:
        # Three.js is most of the vendored weight; drop it when 3D is off.
        shutil.rmtree(os.path.join(out_dir, 'vendor', 'three-r147'), ignore_errors=True)


def webexport_size(path):
    total = sum(os.path.getsize(os.path.join(root, name))
                for root, _, names in os.walk(path) for name in names)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if total < 1024 or unit == 'GB':
            return f"{total:.0f} {unit}" if unit == 'B' else f"{total:.1f} {unit}"
        total /= 1024.0
    return f"{total:.1f} GB"


# ── rasters ────────────────────────────────────────────────────────────────

def _write_rasters(spec, grid, out_dir, data_dir, feedback):
    """Tile and encode every raster layer, plus the hillshade base."""
    published = []
    tiles_dir = os.path.join(out_dir, 'tiles')
    jobs = len(spec.layers) + (1 if spec.dem_path and spec.include_tiles else 0)
    done = 0
    span = 62.0                       # progress budget for this stage

    def step_progress(fraction):
        feedback.setProgress(8 + span * ((done + fraction) / max(jobs, 1)))

    if spec.dem_path and spec.include_tiles:
        _write_hillshade_tiles(spec, grid, tiles_dir, feedback, step_progress)
        done += 1

    for layer in spec.layers:
        if feedback.isCanceled():
            return published
        feedback.pushInfo(f"Preparing {layer.label}...")

        vmin, vmax = webexport.cumulative_cut(layer.path)
        descriptor = {
            'key': layer.key,
            'label': layer.label,
            'kind': 'raster',
            'ramp': layer.ramp,
            'units': layer.units,
            'description': layer.description,
            'vmin': vmin,
            'vmax': vmax,
            'stretch': {'pLow': 5, 'pHigh': 95, 'low': vmin, 'high': vmax},
            'stats': webexport.raster_stats(layer.path),
            'defaultVisible': bool(layer.default_visible),
            'drape3d': bool(layer.drape3d),
            'tiles': None, 'minzoom': 0, 'maxzoom': 0,
        }

        # The quantised grid drives the 3D drape, the value probe and the
        # charts; the tiles drive the 2D map. Same source, same stretch.
        dataset = webexport.warp_to_grid(layer.path, grid, resample=layer.resample)
        codes = webexport.quantise(webexport.read_grid_array(dataset), vmin, vmax)
        dataset = None
        webexport.write_js_global(
            os.path.join(data_dir, f'layer_{layer.key}.js'),
            f'window.SCIMAP_PAYLOAD[{json.dumps(layer.key)}]',
            webexport.array_payload(codes),
        )

        if spec.include_tiles:
            low, high = webexport.zoom_range(layer.path, grid, spec.max_zoom)
            expected = webexport.count_tiles(grid, low, high)
            if expected > 20000:
                feedback.pushWarning(
                    f"{layer.label} needs up to {expected:,} tiles at zoom {low}-{high}. "
                    "Lower the maximum zoom if the export is slow or the folder too large."
                )
            info = webexport.build_tile_pyramid(
                layer.path, os.path.join(tiles_dir, layer.key), layer.ramp,
                vmin, vmax, grid, low, high, feedback=feedback, progress=step_progress,
                resample=layer.resample,
            )
            if info.get('canceled'):
                return published
            descriptor.update({
                'tiles': f'tiles/{layer.key}/{{z}}/{{x}}/{{y}}.png',
                'minzoom': info['minzoom'],
                'maxzoom': info['maxzoom'],
            })
            feedback.pushInfo(
                f"  {info['tiles']} tiles at zoom {info['minzoom']}-{info['maxzoom']}."
            )

        published.append(descriptor)
        done += 1
        step_progress(0.0)

    return published


def _write_hillshade_tiles(spec, grid, tiles_dir, feedback, step_progress):
    """Shade the DEM and tile it as the offline base layer."""
    import tempfile

    feedback.pushInfo("Rendering hillshade from the DEM...")
    workspace = tempfile.mkdtemp(prefix='scimap_dash_')
    try:
        shaded = webexport.hillshade_source(
            spec.dem_path, os.path.join(workspace, 'hillshade.tif'))
        low, high = webexport.zoom_range(spec.dem_path, grid, spec.max_zoom)
        info = webexport.build_tile_pyramid(
            shaded, os.path.join(tiles_dir, 'hillshade'), ramps.RELIEF_RAMP,
            1.0, 255.0, grid, low, high, feedback=feedback, progress=step_progress,
        )
        feedback.pushInfo(f"  {info['tiles']} hillshade tiles at zoom {low}-{high}.")
        return info
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _write_grid(spec, grid, data_dir, feedback):
    """Write the shared grid plus the DEM payload the 3D view is built from."""
    payload = grid.to_dict()

    if spec.dem_path:
        dataset = webexport.warp_to_grid(spec.dem_path, grid, resample='bilinear')
        elevation = webexport.read_grid_array(dataset)
        dataset = None
        payload['dem'] = webexport.elevation_payload(elevation)
        payload['hasTerrain'] = True
    else:
        feedback.pushWarning(
            "No DEM supplied, so the dashboard has no hillshade base layer and "
            "no 3D terrain view."
        )
        payload['hasTerrain'] = False

    webexport.write_js_global(os.path.join(data_dir, 'grid.js'),
                              'window.SCIMAP_GRID', payload)


def _write_layer_index(published, data_dir):
    """``layers.js`` also declares SCIMAP_PAYLOAD, which the per-layer files fill."""
    body = json.dumps(published, separators=(',', ':'), allow_nan=False)
    with open(os.path.join(data_dir, 'layers.js'), 'w') as handle:
        handle.write('window.SCIMAP_LAYERS = ' + body + ';\n')
        handle.write('window.SCIMAP_PAYLOAD = window.SCIMAP_PAYLOAD || {};\n')


# ── vectors and charts ─────────────────────────────────────────────────────

def _write_vectors_and_charts(spec, grid, data_dir, feedback):
    vectors = {}
    charts = {'histograms': {}, 'scatter': None, 'concentration': {}, 'topReaches': []}
    cell_size = grid.pixel_size[0] * grid.mercator_scale

    for vector in spec.vectors:
        if feedback.isCanceled():
            break
        try:
            if vector.kind == 'line':
                geojson = webexport.stream_network_geojson(
                    vector.path, value_field=vector.field, source_wkt=vector.crs_wkt,
                    cell_size=cell_size, max_features=vector.max_features or 4000,
                    feedback=feedback,
                )
                vectors[vector.key] = geojson
                if vector.key == 'streams':
                    charts['topReaches'] = _top_reaches(geojson)
            else:
                vectors[vector.key] = webexport.vector_to_geojson(
                    vector.path, source_wkt=vector.crs_wkt,
                    max_features=vector.max_features, sort_field=vector.field,
                )
        except Exception as error:      # never lose a whole export over one layer
            feedback.pushWarning(f"Could not add {vector.label}: {error}")

    for layer in spec.layers:
        try:
            charts['histograms'][layer.key] = webexport.raster_histogram(layer.path)
        except Exception as error:
            feedback.pushWarning(f"Could not build a histogram for {layer.label}: {error}")
        try:
            curve = webexport.risk_concentration(layer.path)
            if curve is not None:
                charts['concentration'][layer.key] = curve
        except Exception as error:
            feedback.pushWarning(
                f"Could not build a concentration curve for {layer.label}: {error}")

    if spec.scatter_csv and os.path.exists(spec.scatter_csv):
        try:
            scatter = webexport.read_scatter_csv(spec.scatter_csv)
            scatter['x'] = 'Topographic wetness index'
            scatter['y'] = 'Connectivity score'
            charts['scatter'] = scatter
        except Exception as error:
            feedback.pushWarning(f"Could not read the wetness-connectivity curve: {error}")

    return vectors, charts


def _top_reaches(geojson, count=25):
    """The highest-risk reaches, each with a point to fly the map to."""
    rows = []
    for feature in geojson.get('features', []):
        properties = feature['properties']
        if properties.get('Risk') is None:
            continue
        coords = feature['geometry']['coordinates']
        middle = coords[len(coords) // 2]
        rows.append({
            'rank': properties.get('rank'),
            'risk': properties['Risk'],
            'lengthM': properties.get('lengthM', 0.0),
            'lon': middle[0],
            'lat': middle[1],
        })
    rows.sort(key=lambda row: row['risk'], reverse=True)
    return rows[:count]


# ── downloads ──────────────────────────────────────────────────────────────

_FORMAT_LABELS = {
    '.tif': 'GeoTIFF', '.tiff': 'GeoTIFF', '.gpkg': 'GeoPackage',
    '.shp': 'Shapefile', '.geojson': 'GeoJSON', '.csv': 'CSV',
    '.kml': 'KML', '.xml': 'XML', '.json': 'JSON', '.png': 'PNG',
}


def _write_downloads(spec, out_dir, feedback):
    """Copy the source data in beside the dashboard, with checksums."""
    downloads_dir = os.path.join(out_dir, 'downloads')
    os.makedirs(downloads_dir, exist_ok=True)
    entries = []

    sources = []
    if spec.dem_path:
        sources.append((spec.dem_path, 'Digital elevation model (input terrain)'))
    for layer in spec.layers:
        sources.append((layer.path, layer.description or layer.label))
    for vector in spec.vectors:
        sources.append((vector.path, vector.label))
    if spec.scatter_csv:
        sources.append((spec.scatter_csv, 'Wetness-connectivity curve dataset'))

    for path, label in sources:
        if not path or not os.path.exists(path):
            continue
        try:
            entries.extend(_copy_download(path, downloads_dir, label))
        except Exception as error:
            feedback.pushWarning(f"Could not include {os.path.basename(path)}: {error}")

    provenance = os.path.join(downloads_dir, 'provenance.json')
    with open(provenance, 'w') as handle:
        json.dump(_provenance(spec), handle, indent=2, sort_keys=True)
    entries.append(_describe(provenance, out_dir, 'Full run parameters and software versions'))

    if entries:
        checksums = os.path.join(downloads_dir, 'checksums.txt')
        with open(checksums, 'w') as handle:
            for entry in entries:
                handle.write(f"{entry['sha256']}  {os.path.basename(entry['file'])}\n")
        entries.append(_describe(checksums, out_dir, 'SHA-256 checksums for the files above'))

    feedback.pushInfo(f"Bundled {len(entries)} files for download.")
    return entries


# Sidecars that must travel with their main file or it stops being readable.
_SIDECARS = {
    '.shp': ('.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx'),
    '.tif': ('.tfw', '.aux.xml', '.ovr'),
    '.png': ('.pgw', '.prj'),
}


def _copy_download(path, downloads_dir, label):
    base, extension = os.path.splitext(path)
    destination = os.path.join(downloads_dir, os.path.basename(path))
    shutil.copy2(path, destination)
    entries = [_describe(destination, os.path.dirname(downloads_dir), label)]

    for suffix in _SIDECARS.get(extension.lower(), ()):
        sidecar = base + suffix
        if os.path.exists(sidecar):
            shutil.copy2(sidecar, os.path.join(downloads_dir, os.path.basename(sidecar)))
    return entries


def _describe(path, out_dir, label):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    extension = os.path.splitext(path)[1].lower()
    return {
        'file': os.path.relpath(path, out_dir).replace(os.sep, '/'),
        'label': label,
        'format': _FORMAT_LABELS.get(extension, extension.lstrip('.').upper() or 'File'),
        'bytes': os.path.getsize(path),
        'sha256': digest.hexdigest(),
    }


# ── metadata, index, readme, zip ───────────────────────────────────────────

def _provenance(spec):
    record = dict(spec.metadata)
    record.setdefault('generated', _timestamp())
    record['title'] = spec.title
    record['subtitle'] = spec.subtitle
    record['organisation'] = spec.organisation
    record['layers'] = [
        {'key': layer.key, 'label': layer.label,
         'source': os.path.basename(layer.path), 'ramp': layer.ramp}
        for layer in spec.layers
    ]
    record['vectors'] = [
        {'key': vector.key, 'label': vector.label,
         'source': os.path.basename(vector.path)}
        for vector in spec.vectors
    ]
    record['dashboard'] = {
        'gridMaxPx': spec.grid_max_px,
        'meshCap': spec.mesh_cap,
        'maxZoom': spec.max_zoom,
        'verticalExaggeration': spec.vertical_exaggeration,
        'schema': SCHEMA,
    }
    return record


def _timestamp():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _write_meta(spec, grid, published, downloads, data_dir):
    metadata = spec.metadata
    payload = {
        'schema': SCHEMA,
        'title': spec.title,
        'subtitle': spec.subtitle,
        'organisation': spec.organisation,
        'generated': metadata.get('generated') or _timestamp(),
        'plugin': metadata.get('plugin', {}),
        'run': metadata.get('run', {}),
        'weights': metadata.get('weights', {}),
        'classNames': metadata.get('classNames', {}),
        'inputs': metadata.get('inputs', []),
        'citation': metadata.get('citation', CITATION),
        'disclaimer': metadata.get('disclaimer', DISCLAIMER),
        'downloads': downloads,
        'onlineBasemaps': spec.online_basemaps,
        'mesh': {'cap': spec.mesh_cap},
        'tiles': {'hillshadeMaxZoom': metadata.get('hillshadeMaxZoom', 16)},
        'layerCount': len(published),
    }
    webexport.write_js_global(os.path.join(data_dir, 'meta.js'),
                              'window.SCIMAP_META', payload)


def _render_index(spec, published, out_dir):
    template_path = os.path.join(WEB_ROOT, 'template', 'index.html')
    with open(template_path, 'r') as handle:
        html = handle.read()

    scripts = '\n'.join(
        f'<script src="data/layer_{layer["key"]}.js"></script>' for layer in published
    )
    note = ('Full-resolution results in their original projection, plus this '
            "dashboard's own derived files. Everything here is yours to reuse."
            if spec.include_downloads else
            'This dashboard was exported without the data bundle.')

    replacements = {
        '{{TITLE}}': _escape(spec.title),
        '{{SUBTITLE}}': _escape(spec.subtitle),
        '{{EXAGGERATION}}': f"{spec.vertical_exaggeration:g}",
        '{{DOWNLOAD_NOTE}}': _escape(note),
        '{{LAYER_SCRIPTS}}': scripts,
    }
    for token, value in replacements.items():
        html = html.replace(token, value)

    index_path = os.path.join(out_dir, 'index.html')
    with open(index_path, 'w') as handle:
        handle.write(html)
    return index_path


def _escape(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _write_readme(spec, out_dir):
    lines = [
        spec.title,
        '=' * len(spec.title),
        '',
        'Open index.html in a web browser (double-click it). Everything the page',
        'needs is inside this folder, so it works with no internet connection and',
        'no GIS software.',
        '',
        'Keep the folder together: index.html on its own will not work.',
        '',
        'What is here',
        '------------',
        '  index.html   the dashboard',
        '  tiles/       map images for the 2D view',
        '  data/        the values behind the map, charts and 3D terrain',
        '  assets/      stylesheets and scripts',
        '  vendor/      Leaflet and three.js (see vendor/VENDOR.md for licences)',
    ]
    if spec.include_downloads:
        lines += [
            '  downloads/   the result data itself, plus provenance.json and checksums',
        ]
    lines += [
        '',
        'Browsers: Chrome, Edge, Firefox and Safari are all supported. The 3D view',
        'needs WebGL; the rest of the dashboard works without it.',
        '',
        f'Generated {_timestamp()} by the SCIMAP QGIS plugin.',
        '',
    ]
    with open(os.path.join(out_dir, 'README.txt'), 'w') as handle:
        handle.write('\n'.join(lines))


def _write_zip(out_dir, feedback):
    """Zip the folder next to itself, so the whole thing can be handed over as one file."""
    archive = os.path.normpath(out_dir) + '.zip'
    root = os.path.dirname(os.path.normpath(out_dir))
    base = os.path.basename(os.path.normpath(out_dir))

    feedback.pushInfo("Packing the dashboard into a zip...")
    # ZIP_DEFLATED on already-compressed PNGs buys almost nothing and costs a
    # lot of time on a large pyramid, so store those and deflate the rest.
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for folder, _, names in os.walk(out_dir):
            if feedback.isCanceled():
                break
            for name in names:
                path = os.path.join(folder, name)
                arcname = os.path.join(base, os.path.relpath(path, out_dir))
                compression = (zipfile.ZIP_STORED if name.lower().endswith('.png')
                               else zipfile.ZIP_DEFLATED)
                bundle.write(path, arcname, compress_type=compression)

    feedback.pushInfo(f"Wrote {archive} ({os.path.getsize(archive) / 1e6:.1f} MB).")
    return archive
