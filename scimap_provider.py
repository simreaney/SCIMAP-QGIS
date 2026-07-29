import os
from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
from .scimap_algorithm import (
    ScimapStandardAlgorithm,
    ScimapNetworkIndexAlgorithm,
    ScimapFloodAlgorithm,
    ScimapOverlandFlowDistanceAlgorithm,
)
from .localization import tr

class ScimapProcessingProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        self.addAlgorithm(ScimapStandardAlgorithm())
        self.addAlgorithm(ScimapNetworkIndexAlgorithm())
        self.addAlgorithm(ScimapFloodAlgorithm())
        self.addAlgorithm(ScimapOverlandFlowDistanceAlgorithm())

    def id(self):
        return 'scimap'

    def name(self):
        return 'SCIMAP'

    def icon(self):
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.svg')
        return QIcon(icon_path)


class ScimapProviderPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.action = None
        self.network_index_action = None
        self.flood_action = None
        self.ofd_action = None

    def initProcessing(self):
        self.provider = ScimapProcessingProvider()
        from qgis.core import QgsApplication
        QgsApplication.processingRegistry().addProvider(self.provider)

    def initGui(self):
        self.initProcessing()
        
        from qgis.PyQt.QtWidgets import QAction
        from qgis.PyQt.QtGui import QIcon
        import os
        
        icon_path = os.path.join(os.path.dirname(__file__), 'icon_sediment.svg')
        network_icon_path = os.path.join(os.path.dirname(__file__), 'icon_network.svg')
        flood_icon_path = os.path.join(os.path.dirname(__file__), 'icon_flood.svg')
        ofd_icon_path = os.path.join(os.path.dirname(__file__), 'icon_ofd.svg')
        self.action = QAction(QIcon(icon_path), tr('Run SCIMAP Sediment'), self.iface.mainWindow())
        self.action.triggered.connect(self.run_algorithm)

        self.network_index_action = QAction(
            QIcon(network_icon_path),
            tr('Run SCIMAP Network Index'),
            self.iface.mainWindow(),
        )
        self.network_index_action.triggered.connect(self.run_network_index_algorithm)

        self.flood_action = QAction(QIcon(flood_icon_path), tr('Run SCIMAP Flood'), self.iface.mainWindow())
        self.flood_action.triggered.connect(self.run_flood_algorithm)

        # TODO: re-enable Overland Flow Distance to Point tool (temporarily disabled)
        # self.ofd_action = QAction(QIcon(ofd_icon_path), tr('Run Overland Flow Distance to Point'), self.iface.mainWindow())
        # self.ofd_action.triggered.connect(self.run_overland_flow_distance_algorithm)

        self.iface.addPluginToMenu("&SCIMAP", self.action)
        self.iface.addPluginToMenu("&SCIMAP", self.network_index_action)
        self.iface.addPluginToMenu("&SCIMAP", self.flood_action)
        # self.iface.addPluginToMenu("&SCIMAP", self.ofd_action)
        self.iface.addToolBarIcon(self.action)
        self.iface.addToolBarIcon(self.network_index_action)
        self.iface.addToolBarIcon(self.flood_action)
        # self.iface.addToolBarIcon(self.ofd_action)

    def run_algorithm(self):
        import processing
        # scimap is the provider id, scimapstandard is the algorithm id
        processing.execAlgorithmDialog('scimap:scimapstandard')

    def run_network_index_algorithm(self):
        import processing
        # scimap is the provider id, scimapnetworkindex is the algorithm id
        processing.execAlgorithmDialog('scimap:scimapnetworkindex')

    def run_flood_algorithm(self):
        import processing
        # scimap is the provider id, scimapflood is the algorithm id
        processing.execAlgorithmDialog('scimap:scimapflood')

    def run_overland_flow_distance_algorithm(self):
        import processing
        # scimap is the provider id, scimapoverlandflowdistance is the algorithm id
        processing.execAlgorithmDialog('scimap:scimapoverlandflowdistance')

    def unload(self):
        from qgis.core import QgsApplication
        QgsApplication.processingRegistry().removeProvider(self.provider)
        
        if self.action:
            self.iface.removePluginMenu("&SCIMAP", self.action)
            self.iface.removeToolBarIcon(self.action)

        if self.network_index_action:
            self.iface.removePluginMenu("&SCIMAP", self.network_index_action)
            self.iface.removeToolBarIcon(self.network_index_action)

        if self.flood_action:
            self.iface.removePluginMenu("&SCIMAP", self.flood_action)
            self.iface.removeToolBarIcon(self.flood_action)

        if self.ofd_action:
            self.iface.removePluginMenu("&SCIMAP", self.ofd_action)
            self.iface.removeToolBarIcon(self.ofd_action)