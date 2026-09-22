import logging
import os

from qgis.core import Qgis, QgsApplication, QgsMessageLog, QgsProcessingProvider
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

try:  # QGIS ships the sip shim under qgis.PyQt on both Qt5 and Qt6 builds.
    from qgis.PyQt import sip
except ImportError:  # pragma: no cover - very old QGIS
    sip = None

from .algorithms.catchment import ScimapCatchmentAlgorithm
from .algorithms.dashboard import ScimapWebDashboardAlgorithm
from .algorithms.export import ScimapExportResultsAlgorithm
from .algorithms.fio import ScimapFioAlgorithm
from .algorithms.fitted_calibrate import ScimapFittedCalibrateAlgorithm
from .algorithms.fitted_maps import ScimapFittedMapsAlgorithm
from .algorithms.fitted_stats import ScimapFittedStatsAlgorithm
from .algorithms.flood import ScimapFloodAlgorithm
from .algorithms.network_index import ScimapNetworkIndexAlgorithm
from .algorithms.overland_flow import ScimapOverlandFlowDistanceAlgorithm
from .algorithms.reclass import ScimapLandcoverWeightsAlgorithm
from .algorithms.sediment import ScimapStandardAlgorithm
from .localization import tr

logger = logging.getLogger(__name__)

ICONS_DIR = os.path.join(os.path.dirname(__file__), 'icons')

#: Processing provider id. Only one provider may claim it per QGIS session.
PROVIDER_ID = 'scimap'

#: Log Messages panel tab used for plugin-level problems.
LOG_TAG = 'SCIMAP'


def _icon(name):
    return QIcon(os.path.join(ICONS_DIR, name))


class ScimapProcessingProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(ScimapStandardAlgorithm())
        self.addAlgorithm(ScimapFioAlgorithm())
        self.addAlgorithm(ScimapNetworkIndexAlgorithm())
        self.addAlgorithm(ScimapFloodAlgorithm())
        self.addAlgorithm(ScimapOverlandFlowDistanceAlgorithm())
        self.addAlgorithm(ScimapCatchmentAlgorithm())
        self.addAlgorithm(ScimapLandcoverWeightsAlgorithm())
        self.addAlgorithm(ScimapFittedStatsAlgorithm())
        self.addAlgorithm(ScimapFittedCalibrateAlgorithm())
        self.addAlgorithm(ScimapFittedMapsAlgorithm())
        self.addAlgorithm(ScimapExportResultsAlgorithm())
        self.addAlgorithm(ScimapWebDashboardAlgorithm())

    def id(self):
        return PROVIDER_ID

    def name(self):
        return 'SCIMAP'

    def longName(self):
        return 'SCIMAP Toolkit'

    def icon(self):
        return _icon('icon.svg')


class ScimapProviderPlugin:
    #: (algorithm id, icon file, menu label) for each toolbar/menu shortcut.
    TOOLS = (
        ('scimap:scimapstandard', 'icon_sediment.svg', 'Run SCIMAP Sediment'),
        ('scimap:scimapfio', 'icon_fio.svg', 'Run SCIMAP FIO'),
        ('scimap:scimapcatchment', 'icon_catchment.svg', 'Delineate Catchment'),
        ('scimap:landcoverweights', 'icon_landcover.svg', 'Apply Land Cover Risk Weights'),
        ('scimap:fittedstats', 'icon_fitted.svg', 'SCIMAP Fitted 1: Catchment Statistics'),
        ('scimap:fittedcalibrate', 'icon_fitted.svg', 'SCIMAP Fitted 2: Calibrate Weights'),
        ('scimap:fittedmaps', 'icon_fitted.svg', 'SCIMAP Fitted 3: Ensemble Risk Maps'),
        ('scimap:scimapnetworkindex', 'icon_network.svg', 'Run SCIMAP Network Index'),
        ('scimap:scimapflood', 'icon_flood.svg', 'Run SCIMAP Flood'),
        ('scimap:scimapoverlandflowdistance', 'icon_ofd.svg',
         'Run Overland Flow Distance to Point'),
        ('scimap:webdashboard', 'icon_dashboard.svg', 'Create Web Dashboard'),
    )

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []
        self.panel_action = None
        self.dock = None

    def initProcessing(self):
        """Register the Processing provider, unless one is already registered.

        ``QgsProcessingRegistry.addProvider`` takes ownership of the provider
        and **deletes it** when it refuses one whose id is already taken, so a
        second copy of this plugin would leave ``self.provider`` wrapping a
        dead C++ object and :meth:`unload` would raise. Two copies installed at
        once is an easy state to reach — an unzipped development folder
        alongside the packaged ``SCIMAPQGIS`` — so detect it and say so plainly
        rather than half-registering.
        """
        self.provider = None
        registry = QgsApplication.processingRegistry()

        if registry.providerById(PROVIDER_ID) is not None:
            self._warn_duplicate()
            return

        provider = ScimapProcessingProvider()
        if registry.addProvider(provider):
            self.provider = provider
        else:
            # Refused: `provider` has already been deleted, so do not touch it.
            self._warn_duplicate()

    def _warn_duplicate(self):
        message = tr(
            'Another copy of the SCIMAP Toolkit is already loaded, so this one '
            'has not registered its Processing algorithms. Open '
            'Plugins > Manage and Install Plugins and disable or uninstall the '
            'duplicate, then restart QGIS.'
        )
        logger.warning(message)
        QgsMessageLog.logMessage(message, LOG_TAG, Qgis.MessageLevel.Warning)

    def initGui(self):
        self.initProcessing()
        if self.provider is None:
            # A duplicate install owns the provider; adding menu entries and
            # toolbar icons here would simply show everything twice.
            return

        self.panel_action = QAction(
            _icon('icon.svg'), tr('SCIMAP Panel'), self.iface.mainWindow())
        self.panel_action.setCheckable(True)
        self.panel_action.triggered.connect(self.toggle_panel)
        self.iface.addPluginToMenu("&SCIMAP", self.panel_action)
        self.iface.addToolBarIcon(self.panel_action)

        for algorithm_id, icon_name, label in self.TOOLS:
            action = QAction(_icon(icon_name), tr(label), self.iface.mainWindow())
            action.triggered.connect(
                lambda _checked=False, alg=algorithm_id: self.run_algorithm(alg))
            self.iface.addPluginToMenu("&SCIMAP", action)
            self.actions.append(action)

    def run_algorithm(self, algorithm_id):
        import processing
        processing.execAlgorithmDialog(algorithm_id)

    def toggle_panel(self, checked):
        if checked:
            if self.dock is None:
                from .gui.dock import ScimapDockWidget
                self.dock = ScimapDockWidget(self.iface, self.iface.mainWindow())
                self.dock.visibilityChanged.connect(self._on_panel_visibility_changed)
                self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.show()
        elif self.dock is not None:
            self.dock.hide()

    def _on_panel_visibility_changed(self, visible):
        if self.panel_action is not None:
            self.panel_action.setChecked(visible)

    def unload(self):
        # QGIS may already have deleted the provider (see initProcessing), and
        # a failure here would abandon the rest of the teardown, leaving stale
        # menu entries and toolbar icons behind.
        if self.provider is not None:
            if sip is None or not sip.isdeleted(self.provider):
                try:
                    QgsApplication.processingRegistry().removeProvider(self.provider)
                except RuntimeError:  # pragma: no cover - already deleted
                    logger.debug('Processing provider was already deleted', exc_info=True)
            self.provider = None

        for action in self.actions:
            self.iface.removePluginMenu("&SCIMAP", action)
        self.actions = []

        if self.panel_action is not None:
            self.iface.removePluginMenu("&SCIMAP", self.panel_action)
            self.iface.removeToolBarIcon(self.panel_action)
            self.panel_action = None

        if self.dock is not None:
            self.dock.deactivate_map_tools()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
