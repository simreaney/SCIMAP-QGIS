"""Shared parameters and helpers for the SCIMAP-Fitted tools.

SCIMAP Standard takes land-cover risk weights as an assumption. SCIMAP-Fitted
infers them from observed water quality instead, in three steps that are
separate algorithms because their costs differ by orders of magnitude:

``scimap:fittedstats``
    Delineate the catchment upstream of every observation site and summarise
    modelled risk per land-cover class inside it. Minutes to hours — it runs
    the full hydrology preamble.
``scimap:fittedcalibrate``
    Search weight space against the observations, optionally cross-validating.
    Seconds. This is the step users re-run with different determinands, sample
    counts and bounds.
``scimap:fittedmaps``
    Propagate the calibrated weight sets back onto the raster grid as an
    ensemble. Hours — one mass-flux routing run per land-cover class.

Keeping them apart means changing ``top_n`` from 30 to 50 costs seconds rather
than re-running the hydrology.
"""

import os

from qgis.core import (
    QgsCoordinateTransform,
    QgsProcessingParameterEnum,
    QgsProcessingParameterMatrix,
)

from ..core import landcover
from ..data.defaults import (
    CEH21_TO_SCIMAP,
    CUSTOM_LANDCOVER_SCHEME,
    LANDCOVER_SCHEMES,
    SCIMAP_CLASS_NAMES,
    UNMAPPED_FALLBACK_CLASS,
    default_remap_matrix_for,
)
from ..localization import tr
from .base import ScimapAlgorithmBase


class ScimapFittedAlgorithmBase(ScimapAlgorithmBase):
    """Group, land-cover scheme and observation-site plumbing."""

    LC_SCHEME = 'LC_SCHEME'
    REMAP_TABLE = 'REMAP_TABLE'

    _ICON = 'icon_landcover.svg'

    def group(self):
        return tr('Calibration')

    def groupId(self):
        return 'scimap_calibration'

    # ── Land-cover scheme ───────────────────────────────────────────────

    def add_landcover_scheme_parameters(self):
        """Ask which class scheme the land-cover raster actually uses.

        This is not a nicety. ``CEH_TO_SCIMAP`` in ``data/defaults.py`` is the
        LCM2007 23-class scheme, while every UKCEH product since LCM2015 uses a
        21-class scheme in which classes 20 and 21 are Urban and Suburban
        rather than Littoral sediment and Saltmarsh. Push a modern raster
        through the older table and every urban cell silently becomes SCIMAP
        class 7 (Other) — nothing errors, and the unmapped-value warning never
        fires because those IDs exist in both tables. A calibration run on that
        is fitting weights to a land-cover map that does not exist.
        """
        self.addParameter(QgsProcessingParameterEnum(
            self.LC_SCHEME,
            tr('Land cover classification scheme'),
            options=[tr(label) for label, _table in LANDCOVER_SCHEMES],
            defaultValue=0,
        ))

        self.addParameter(QgsProcessingParameterMatrix(
            self.REMAP_TABLE,
            tr('Land cover class -> SCIMAP class (used by "Custom" only)'),
            headers=[tr('Land cover ID'), tr('SCIMAP class')],
            numberRows=len(CEH21_TO_SCIMAP),
            defaultValue=default_remap_matrix_for(CEH21_TO_SCIMAP),
            optional=True,
        ))

    def resolve_landcover_remap(self, parameters, context, feedback):
        """Return ``(remap, already_scimap)`` for the chosen scheme."""
        index = self.parameterAsEnum(parameters, self.LC_SCHEME, context)
        try:
            label, table = LANDCOVER_SCHEMES[index]
        except IndexError:
            label, table = LANDCOVER_SCHEMES[0]

        if table is None:
            feedback.pushInfo(
                "Land cover: treating raster values as SCIMAP classes 1-7.")
            return {}, True

        if table == CUSTOM_LANDCOVER_SCHEME:
            remap = landcover.matrix_to_lookup(
                self.parameterAsMatrix(parameters, self.REMAP_TABLE, context), int)
            if not remap:
                raise RuntimeError(
                    "The 'Custom' land cover scheme was selected but the remap "
                    "table is empty."
                )
            feedback.pushInfo(
                f"Land cover: custom remap of {len(remap)} classes to SCIMAP 1-7.")
            return remap, False

        feedback.pushInfo(f"Land cover: {label}.")
        return dict(table), False

    def scimap_class_labels(self, classes):
        """Human-readable names for the SCIMAP classes actually present."""
        return [SCIMAP_CLASS_NAMES.get(int(c), f"Class {int(c)}") for c in classes]

    def remap_to_scimap_classes(self, lc_array, remap, already_scimap, feedback=None):
        """Apply the chosen scheme, folding unmapped values into the fallback."""
        import numpy as np

        if already_scimap:
            classes = np.where(np.isfinite(lc_array), lc_array, np.nan)
        else:
            classes = landcover.remap_landcover_ids(lc_array, remap)
            mapped = set(remap.values())
            unmapped = landcover.find_unmapped_landcover_ids(
                classes, {value: 0 for value in mapped})
            if unmapped:
                if feedback is not None:
                    feedback.pushWarning(
                        "Land cover values with no SCIMAP class "
                        f"({len(unmapped)}): {', '.join(str(v) for v in unmapped[:20])}"
                        f" — assigning them to class {UNMAPPED_FALLBACK_CLASS}."
                    )
                for value in unmapped:
                    classes = np.where(classes == value, UNMAPPED_FALLBACK_CLASS, classes)
        return classes

    # ── Observation sites ───────────────────────────────────────────────

    def read_observation_sites(self, points_layer, target_crs, context,
                               id_field=None, value_fields=(), feedback=None):
        """Read an observation point layer into plain dicts.

        Returns ``[{'site_id', 'x', 'y', 'values': {field: float}}, ...]`` in
        the target CRS. Multipart geometries contribute their first point only:
        an observation site is one location, and silently fanning a multipoint
        into several sites would put several rows in the design matrix for one
        measurement.
        """
        transform = None
        layer_crs = points_layer.crs()
        if layer_crs.isValid() and target_crs.isValid() and layer_crs != target_crs:
            transform = QgsCoordinateTransform(layer_crs, target_crs,
                                               context.transformContext())
            if feedback is not None:
                feedback.pushInfo(
                    f"Reprojecting observation sites from {layer_crs.authid()} "
                    f"to {target_crs.authid()}."
                )

        sites = []
        for feature in points_layer.getFeatures():
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            if transform is not None:
                geometry = geometry.clone()
                geometry.transform(transform)
            point = (geometry.asMultiPoint()[0] if geometry.isMultipart()
                     else geometry.asPoint())

            site_id = feature[id_field] if id_field else feature.id()
            if site_id is None:
                site_id = feature.id()

            values = {}
            for field in value_fields:
                try:
                    values[field] = float(feature[field])
                except (TypeError, ValueError):
                    values[field] = float('nan')

            sites.append({
                'site_id': site_id,
                'x': float(point.x()),
                'y': float(point.y()),
                'values': values,
            })

        if not sites:
            raise RuntimeError(
                "The observation layer contains no usable point features.")

        duplicates = len(sites) - len({str(site['site_id']) for site in sites})
        if duplicates and feedback is not None:
            feedback.pushWarning(
                f"{duplicates} observation site(s) share an identifier. Site IDs "
                "are what join catchment statistics to observations, so "
                "duplicates will be merged."
            )
        return sites

    # ── Misc ────────────────────────────────────────────────────────────

    @staticmethod
    def mark_advanced(parameter_definition):
        """Flag a parameter as advanced, as ``algorithms/dashboard.py`` does."""
        parameter_definition.setFlags(
            parameter_definition.flags()
            | parameter_definition.FlagAdvanced
        )
        return parameter_definition

    def add_advanced_parameter(self, parameter):
        self.addParameter(self.mark_advanced(parameter))
        return parameter

    @staticmethod
    def output_sibling(path, filename):
        """A path beside *path*, for extra files a destination cannot declare."""
        return os.path.join(os.path.dirname(os.path.abspath(path)), filename)


class CollectingFeedback:
    """A feedback stand-in that a worker thread may safely write to.

    ``core/wbt.py::run_wbt`` reports the command it is about to run through
    ``feedback.pushInfo``. ``QgsProcessingFeedback`` is wired to dialog widgets
    and is not documented thread-safe, so a WhiteboxTools call made from a
    worker thread must not be handed the real one. This collects messages
    instead (``list.append`` is atomic under the GIL) for the calling thread to
    replay with :meth:`replay_into`.
    """

    __slots__ = ('messages', 'warnings', '_canceled')

    def __init__(self, canceled=False):
        self.messages = []
        self.warnings = []
        self._canceled = canceled

    def pushInfo(self, message):
        self.messages.append(str(message))

    def pushWarning(self, message):
        self.warnings.append(str(message))

    def pushDebugInfo(self, message):
        self.messages.append(str(message))

    def reportError(self, message, fatalError=False):
        self.warnings.append(str(message))

    def setProgress(self, value):
        return None

    def isCanceled(self):
        return self._canceled

    def replay_into(self, feedback, verbose=False):
        """Forward collected messages on the calling thread."""
        if verbose:
            for message in self.messages:
                feedback.pushInfo(message)
        for message in self.warnings:
            feedback.pushWarning(message)
        self.messages.clear()
        self.warnings.clear()
