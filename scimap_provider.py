import os

from qgis.core import QgsApplication, QgsProcessingProvider
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .algorithms.catchment import ScimapCatchmentAlgorithm
from .algorithms.export import ScimapExportResultsAlgorithm
from .algorithms.fio import ScimapFioAlgorithm
from .algorithms.flood import ScimapFloodAlgorithm
from .algorithms.network_index import ScimapNetworkIndexAlgorithm
from .algorithms.overland_flow import ScimapOverlandFlowDistanceAlgorithm
from .algorithms.reclass import ScimapLandcoverWeightsAlgorithm
from .algorithms.sediment import ScimapStandardAlgorithm
from .localization import tr

ICONS_DIR = os.path.join(os.path.dirname(__file__), 'icons')


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
        self.addAlgorithm(ScimapExportResultsAlgorithm())

    def id(self):
        return 'scimap'

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
        ('scimap:scimapnetworkindex', 'icon_network.svg', 'Run SCIMAP Network Index'),
        ('scimap:scimapflood', 'icon_flood.svg', 'Run SCIMAP Flood'),
        ('scimap:scimapoverlandflowdistance', 'icon_ofd.svg',
         'Run Overland Flow Distance to Point'),
    )

    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []
        self.panel_action = None
        self.dock = None

    def initProcessing(self):
        self.provider = ScimapProcessingProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()

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
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
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
