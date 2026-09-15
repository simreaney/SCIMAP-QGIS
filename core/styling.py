"""Automatic styling for SCIMAP result layers.

The web application renders every raster through MapServer using a matplotlib
colormap stretched between the band's 5th and 95th percentiles
(``processing/ows_utils.py``). These post-processors reproduce that look in
QGIS so results are readable the moment they land on the canvas.
"""

import logging

from qgis.core import (
    QgsColorRampShader,
    QgsGradientColorRamp,
    QgsGradientStop,
    QgsGraduatedSymbolRenderer,
    QgsProcessingLayerPostProcessorInterface,
    QgsRasterBandStats,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsStyle,
)
from qgis.PyQt.QtGui import QColor

logger = logging.getLogger(__name__)

# QGIS ships these ramps in its default style under fixed (non-reversed)
# names. ``invert`` reproduces a matplotlib "_r" variant by sampling the
# QGIS ramp back-to-front, matching the web app's "Spectral" option (which is
# really matplotlib's Spectral_r) in layouts/results_layout.py.
_RAMP_SPECS = {
    'magma': ('Magma', False),
    'viridis': ('Viridis', False),
    'plasma': ('Plasma', False),
    'inferno': ('Inferno', False),
    'cividis': ('Cividis', False),
    'spectral': ('Spectral', True),
    'turbo': ('Turbo', False),
    # ColorBrewer's RdBu runs red (low) to blue (high); invert for blue-to-red.
    'blue-red': ('RdBu', True),
}

# Fallback two-stop gradients (low -> high) if a ramp is missing from the
# user's style library, already expressed in the requested direction.
_FALLBACK_ENDPOINTS = {
    'magma': ("#000004", "#fcfdbf"),
    'viridis': ("#440154", "#fde725"),
    'plasma': ("#0d0887", "#f0f921"),
    'inferno': ("#000004", "#fcffa4"),
    'cividis': ("#00224e", "#fee838"),
    'spectral': ("#5e4fa2", "#9e0142"),
    'turbo': ("#30123b", "#7a0403"),
    'blue-red': ("#053061", "#67001f"),
}


def _reversed_ramp(ramp, samples=32):
    """Sample *ramp* back-to-front into a fresh gradient ramp (a "_r" variant)."""
    stops = [
        QgsGradientStop(i / float(samples), ramp.color(1.0 - i / float(samples)))
        for i in range(1, samples)
    ]
    return QgsGradientColorRamp(ramp.color(1.0), ramp.color(0.0), False, stops)


def resolve_ramp(name):
    """Return a QgsColorRamp for *name*, falling back to a two-stop gradient."""
    key = str(name).lower()
    canonical, invert = _RAMP_SPECS.get(key, (str(name), False))

    ramp = QgsStyle.defaultStyle().colorRamp(canonical)
    if ramp is not None:
        return _reversed_ramp(ramp) if invert else ramp

    low, high = _FALLBACK_ENDPOINTS.get(key, ("#000000", "#ffffff"))
    logger.info("Colour ramp %r not in the style library; using a gradient fallback", canonical)
    return QgsGradientColorRamp(QColor(low), QColor(high))


def percentile_range(layer, band=1, low=5.0, high=95.0):
    """Approximate the band's *low*/*high* percentile values.

    QgsRasterDataProvider exposes cumulative-cut limits directly, which is both
    faster and more memory-frugal than reading the band into NumPy.
    """
    provider = layer.dataProvider()
    try:
        vmin, vmax = provider.cumulativeCut(band, low / 100.0, high / 100.0)
        if vmin is not None and vmax is not None and vmax > vmin:
            return float(vmin), float(vmax)
    except Exception:  # pragma: no cover - provider-dependent
        logger.debug("cumulativeCut unavailable; falling back to band statistics", exc_info=True)

    stats = provider.bandStatistics(band, QgsRasterBandStats.Min | QgsRasterBandStats.Max)
    vmin, vmax = float(stats.minimumValue), float(stats.maximumValue)
    if vmax <= vmin:
        vmax = vmin + 1.0
    return vmin, vmax


def apply_ramp(layer, ramp_name, band=1, low=5.0, high=95.0, classes=12):
    """Render *layer* with a discrete colour ramp over its percentile range."""
    vmin, vmax = percentile_range(layer, band, low, high)
    ramp = resolve_ramp(ramp_name)

    items = []
    for idx in range(classes):
        fraction = idx / float(classes - 1) if classes > 1 else 0.0
        value = vmin + fraction * (vmax - vmin)
        items.append(QgsColorRampShader.ColorRampItem(
            value, ramp.color(fraction), f"{value:.4g}",
        ))

    shader_fn = QgsColorRampShader(vmin, vmax)
    shader_fn.setColorRampType(QgsColorRampShader.Interpolated)
    shader_fn.setColorRampItemList(items)
    shader_fn.setClassificationMode(QgsColorRampShader.Continuous)

    shader = QgsRasterShader()
    shader.setRasterShaderFunction(shader_fn)

    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), band, shader)
    renderer.setClassificationMin(vmin)
    renderer.setClassificationMax(vmax)
    layer.setRenderer(renderer)
    layer.triggerRepaint()


def apply_graduated(layer, field_name, ramp_name, classes=7):
    """Render a vector layer graduated on *field_name*."""
    if layer.fields().indexFromName(field_name) < 0:
        return
    renderer = QgsGraduatedSymbolRenderer.createRenderer(
        layer, field_name, classes,
        QgsGraduatedSymbolRenderer.Quantile,
        layer.renderer().symbol().clone() if layer.renderer() else None,
        resolve_ramp(ramp_name),
    )
    if renderer is not None:
        layer.setRenderer(renderer)
        layer.triggerRepaint()


class RampPostProcessor(QgsProcessingLayerPostProcessorInterface):
    """Applies a colour ramp to a raster output once Processing loads it.

    Processing takes ownership of post-processors, so each instance must be kept
    alive by the algorithm until the layer is loaded — hence :meth:`create`
    stashing instances in a module-level registry.
    """

    _instances = []

    def __init__(self, ramp_name, layer_name=None):
        super().__init__()
        self.ramp_name = ramp_name
        self.layer_name = layer_name

    def postProcessLayer(self, layer, context, feedback):
        try:
            if self.layer_name:
                layer.setName(self.layer_name)
            apply_ramp(layer, self.ramp_name)
        except Exception:  # pragma: no cover - never fail a run over styling
            logger.warning("Could not style %s with ramp %s", layer.name(), self.ramp_name,
                           exc_info=True)

    @classmethod
    def create(cls, ramp_name, layer_name=None):
        instance = cls(ramp_name, layer_name)
        cls._instances.append(instance)
        return instance


class GraduatedPostProcessor(QgsProcessingLayerPostProcessorInterface):
    """Applies a graduated renderer to a vector output (e.g. stream Risk)."""

    _instances = []

    def __init__(self, field_name, ramp_name, layer_name=None):
        super().__init__()
        self.field_name = field_name
        self.ramp_name = ramp_name
        self.layer_name = layer_name

    def postProcessLayer(self, layer, context, feedback):
        try:
            if self.layer_name:
                layer.setName(self.layer_name)
            apply_graduated(layer, self.field_name, self.ramp_name)
        except Exception:  # pragma: no cover
            logger.warning("Could not style %s on field %s", layer.name(), self.field_name,
                           exc_info=True)

    @classmethod
    def create(cls, field_name, ramp_name, layer_name=None):
        instance = cls(field_name, ramp_name, layer_name)
        cls._instances.append(instance)
        return instance


def style_output(context, dest_id, ramp_name, layer_name=None):
    """Attach a raster ramp post-processor to a Processing destination."""
    if not dest_id:
        return
    try:
        details = context.layerToLoadOnCompletionDetails(dest_id)
    except Exception:
        return
    if details is None:
        return
    details.setPostProcessor(RampPostProcessor.create(ramp_name, layer_name))


def style_vector_output(context, dest_id, field_name, ramp_name, layer_name=None):
    """Attach a graduated post-processor to a Processing vector destination."""
    if not dest_id:
        return
    try:
        details = context.layerToLoadOnCompletionDetails(dest_id)
    except Exception:
        return
    if details is None:
        return
    details.setPostProcessor(
        GraduatedPostProcessor.create(field_name, ramp_name, layer_name)
    )
