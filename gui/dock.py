"""Guided SCIMAP panel.

Mirrors the SCIMAP web application's workflow — Catchment, Parameters, Run,
Flood, Results — inside a QGIS dock so the whole analysis can be driven without
assembling Processing dialogs by hand.

Every tab delegates to the Processing algorithms in :mod:`..algorithms`; none of
the science lives here. The algorithms carry ``FlagNoThreading`` because they
drive GDAL and WhiteboxTools directly, so runs happen on the main thread with
``processEvents`` pumped from the feedback object to keep the panel responsive
and the Cancel button live.
"""

import os

import processing
from qgis.core import (
    QgsApplication,
    QgsMapLayerProxyModel,
    QgsProcessingFeedback,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import styling, wbt
from ..data import params_xml
from ..data.defaults import (
    COLOUR_RAMPS,
    DEFAULT_WEIGHTS,
    SCIMAP_CLASSES,
    default_remap_matrix,
)
from ..localization import tr
from .map_tools import MultiPointPickerMapTool, PointPickerMapTool

ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'icons')

#: Keeps the docked panel from stretching to fit its widest combo box; the
#: individual widgets are still capped narrower in ``_build_ui``.
PANEL_MAX_WIDTH = 340
COMBO_MAX_WIDTH = 220


def _icon(name):
    return QIcon(os.path.join(ICONS_DIR, name))


class PanelFeedback(QgsProcessingFeedback):
    """Routes algorithm progress into the dock's progress bar and log."""

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self._canceled = False

    def setProgress(self, progress):
        super().setProgress(progress)
        self.panel.progress.setValue(int(progress))
        QCoreApplication.processEvents()

    def pushInfo(self, info):
        self.panel.log_message(info)

    def pushCommandInfo(self, info):
        self.panel.log_message(info)

    def pushDebugInfo(self, info):
        pass

    def pushWarning(self, warning):
        self.panel.log_message(f"⚠ {warning}")

    def reportError(self, error, fatalError=False):
        self.panel.log_message(f"✖ {error}")

    def cancel(self):
        self._canceled = True
        super().cancel()

    def isCanceled(self):
        return self._canceled or super().isCanceled()


class ScimapDockWidget(QDockWidget):
    """The SCIMAP guided panel."""

    def __init__(self, iface, parent=None):
        super().__init__(tr('SCIMAP'), parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.setObjectName('ScimapDockWidget')

        self.feedback = None
        self.results = []
        self.last_result_layers = {}

        self.pour_point = None
        self.pour_point_tool = PointPickerMapTool(self.canvas)
        self.pour_point_tool.pointPicked.connect(self._on_pour_point_picked)

        self.impact_tool = MultiPointPickerMapTool(self.canvas)
        self.impact_tool.pointsChanged.connect(self._on_impact_points_changed)

        self.setWidget(self._build_ui())
        self._load_settings()

    # ── Layout ──────────────────────────────────────────────────────────

    def _build_ui(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_catchment_tab(), _icon('icon_catchment.svg'), tr('Catchment'))
        self.tabs.addTab(self._build_parameters_tab(), _icon('icon_landcover.svg'), tr('Parameters'))
        self.tabs.addTab(self._build_run_tab(), _icon('icon_sediment.svg'), tr('Run'))
        self.tabs.addTab(self._build_flood_tab(), _icon('icon_flood.svg'), tr('Flood'))
        self.tabs.addTab(self._build_results_tab(), _icon('icon.svg'), tr('Results'))
        layout.addWidget(self.tabs)

        layout.addWidget(self._build_progress_group())

        # Cap the container and every combo box so the dock doesn't stretch
        # itself to fit the widest item across all five tabs.
        container.setMaximumWidth(PANEL_MAX_WIDTH)
        for combo in container.findChildren(QComboBox):
            combo.setMaximumWidth(COMBO_MAX_WIDTH)
        return container

    def _build_progress_group(self):
        group = QGroupBox(tr('Progress'))
        box = QVBoxLayout(group)

        row = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        row.addWidget(self.progress)
        self.cancel_button = QPushButton(tr('Cancel'))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_run)
        row.addWidget(self.cancel_button)
        box.addLayout(row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(90)
        self.log.setAccessibleName(tr('SCIMAP run log'))
        box.addWidget(self.log)
        return group

    # ── Catchment tab ───────────────────────────────────────────────────

    def _build_catchment_tab(self):
        widget = QWidget()
        form = QFormLayout(widget)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.catchment_dem = QgsMapLayerComboBox()
        self.catchment_dem.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow(tr('DEM'), self.catchment_dem)

        pick_row = QHBoxLayout()
        self.pour_point_button = QToolButton()
        self.pour_point_button.setText(tr('Pick on map'))
        self.pour_point_button.setCheckable(True)
        self.pour_point_button.toggled.connect(self._toggle_pour_point_tool)
        pick_row.addWidget(self.pour_point_button)
        self.pour_point_label = QLabel(tr('No pour point selected'))
        self.pour_point_label.setWordWrap(True)
        pick_row.addWidget(self.pour_point_label, 1)
        form.addRow(tr('Pour point'), self._wrap(pick_row))

        self.snap_radius = self._spin(250.0, 0.0, 1e7, tr('map units'))
        form.addRow(tr('Snap search radius'), self.snap_radius)

        self.snap_area = self._spin(800000.0, 0.0, 1e12, tr('m²'))
        form.addRow(tr('Minimum contributing area'), self.snap_area)

        self.catchment_breach = QCheckBox(tr('Breach depressions before routing'))
        self.catchment_breach.setChecked(True)
        form.addRow('', self.catchment_breach)

        run_button = QPushButton(_icon('icon_catchment.svg'), tr('Delineate catchment'))
        run_button.clicked.connect(self._run_catchment)
        form.addRow('', run_button)
        return widget

    # ── Parameters tab ──────────────────────────────────────────────────

    def _build_parameters_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel(tr(
            'Risk weight per SCIMAP land cover class. These feed the Run tab and '
            'can be shared with the SCIMAP web application as XML.'
        )))

        self.weights_table = QTableWidget(len(SCIMAP_CLASSES), 3)
        self.weights_table.setHorizontalHeaderLabels(
            [tr('Class'), tr('Land cover'), tr('Risk weight')])
        self.weights_table.verticalHeader().setVisible(False)
        self.weights_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, (class_id, label) in enumerate(SCIMAP_CLASSES):
            self.weights_table.setItem(row, 0, QTableWidgetItem(str(class_id)))
            self.weights_table.setItem(row, 1, QTableWidgetItem(label))
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 1000.0)
            spin.setDecimals(3)
            spin.setSingleStep(0.05)
            spin.setValue(DEFAULT_WEIGHTS.get(class_id, 0.5))
            self.weights_table.setCellWidget(row, 2, spin)
        self.weights_table.resizeColumnsToContents()
        layout.addWidget(self.weights_table)

        self.lc_is_scimap = QCheckBox(tr('Land cover already uses SCIMAP classes (1-7)'))
        layout.addWidget(self.lc_is_scimap)

        buttons = QHBoxLayout()
        reset = QPushButton(tr('Reset to defaults'))
        reset.clicked.connect(self._reset_weights)
        buttons.addWidget(reset)
        import_button = QPushButton(tr('Import XML…'))
        import_button.clicked.connect(self._import_weights)
        buttons.addWidget(import_button)
        export_button = QPushButton(tr('Export XML…'))
        export_button.clicked.connect(self._export_weights)
        buttons.addWidget(export_button)
        layout.addLayout(buttons)

        layout.addStretch(1)
        return widget

    # ── Run tab ─────────────────────────────────────────────────────────

    def _build_run_tab(self):
        widget = QWidget()
        form = QFormLayout(widget)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.mapping_type = QComboBox()
        self.mapping_type.addItem(tr('SCIMAP Sediment'), 'scimap:scimapstandard')
        self.mapping_type.addItem(tr('SCIMAP FIO'), 'scimap:scimapfio')
        self.mapping_type.currentIndexChanged.connect(self._update_run_labels)
        form.addRow(tr('Mapping type'), self.mapping_type)

        self.run_catchment = QgsMapLayerComboBox()
        self.run_catchment.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self.run_catchment.setAllowEmptyLayer(True)
        self.run_catchment.setLayer(None)
        form.addRow(tr('Catchment area (optional)'), self.run_catchment)

        self.run_dem = QgsMapLayerComboBox()
        self.run_dem.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow(tr('DEM'), self.run_dem)

        self.run_weight_layer = QgsMapLayerComboBox()
        self.run_weight_layer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.run_weight_label = QLabel(tr('Land cover'))
        form.addRow(self.run_weight_label, self.run_weight_layer)

        self.run_rainfall = QgsMapLayerComboBox()
        self.run_rainfall.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow(tr('Rainfall'), self.run_rainfall)

        self.run_stream_threshold = self._spin(800000.0, 0.0, 1e12, tr('m²'))
        form.addRow(tr('Stream initiation threshold'), self.run_stream_threshold)

        self.run_stream_power = QCheckBox(tr('Use stream power in erosion calculation'))
        self.run_stream_power.setChecked(True)
        form.addRow('', self.run_stream_power)

        self.run_connectivity_method = QComboBox()
        self.run_connectivity_method.addItem(tr('Network Index (flow-path trace)'), 'flow_path_trace')
        self.run_connectivity_method.addItem(tr('Percentage Downslope Saturated Length (PDSL)'), 'pdsl')
        form.addRow(tr('Connectivity algorithm'), self.run_connectivity_method)

        self.run_ramp = QComboBox()
        self.run_ramp.addItem(tr('SCIMAP defaults (per layer)'), 0)
        for index, ramp in enumerate(COLOUR_RAMPS, start=1):
            self.run_ramp.addItem(ramp, index)
        form.addRow(tr('Colour ramp'), self.run_ramp)

        run_button = QPushButton(_icon('icon_sediment.svg'), tr('Run risk mapping'))
        run_button.clicked.connect(self._run_risk_mapping)
        form.addRow('', run_button)

        advanced = QPushButton(tr('Open in Processing dialog…'))
        advanced.clicked.connect(
            lambda: processing.execAlgorithmDialog(self.mapping_type.currentData()))
        form.addRow('', advanced)
        return widget

    def _update_run_labels(self):
        is_fio = self.mapping_type.currentData() == 'scimap:scimapfio'
        self.run_weight_label.setText(tr('FIO concentration') if is_fio else tr('Land cover'))

    # ── Flood tab ───────────────────────────────────────────────────────

    def _build_flood_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.flood_connectivity = QgsMapLayerComboBox()
        self.flood_connectivity.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow(tr('Connectivity'), self.flood_connectivity)

        self.flood_runoff = QgsMapLayerComboBox()
        self.flood_runoff.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow(tr('Runoff / weights'), self.flood_runoff)
        layout.addLayout(form)

        layout.addWidget(QLabel(tr('Rainfall pattern rasters (select one or more)')))
        self.flood_rainfall_list = QListWidget()
        self.flood_rainfall_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.flood_rainfall_list.setMaximumHeight(90)
        layout.addWidget(self.flood_rainfall_list)

        impact_group = QGroupBox(tr('Impact points'))
        impact_layout = QVBoxLayout(impact_group)
        impact_buttons = QHBoxLayout()
        self.impact_button = QToolButton()
        self.impact_button.setText(tr('Pick on map'))
        self.impact_button.setCheckable(True)
        self.impact_button.toggled.connect(self._toggle_impact_tool)
        impact_buttons.addWidget(self.impact_button)
        clear_impacts = QPushButton(tr('Clear'))
        clear_impacts.clicked.connect(self.impact_tool.clear)
        impact_buttons.addWidget(clear_impacts)
        compute_ofd = QPushButton(tr('Calc. OFD'))
        compute_ofd.clicked.connect(self._compute_ofd_from_impacts)
        impact_buttons.addWidget(compute_ofd)
        impact_layout.addLayout(impact_buttons)
        self.impact_list = QListWidget()
        self.impact_list.setMaximumHeight(70)
        impact_layout.addWidget(self.impact_list)
        layout.addWidget(impact_group)

        layout.addWidget(QLabel(tr('Overland flow distance rasters (select one or more)')))
        self.flood_ofd_list = QListWidget()
        self.flood_ofd_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.flood_ofd_list.setMaximumHeight(90)
        layout.addWidget(self.flood_ofd_list)

        refresh = QPushButton(tr('Refresh raster lists'))
        refresh.clicked.connect(self._refresh_raster_lists)
        layout.addWidget(refresh)

        run_button = QPushButton(_icon('icon_flood.svg'), tr('Run SCIMAP Flood'))
        run_button.clicked.connect(self._run_flood)
        layout.addWidget(run_button)
        layout.addStretch(1)

        self._refresh_raster_lists()
        return widget

    # ── Results tab ─────────────────────────────────────────────────────

    def _build_results_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel(tr('Layers produced in this session')))
        self.results_list = QListWidget()
        layout.addWidget(self.results_list)

        ramp_row = QHBoxLayout()
        self.results_ramp = QComboBox()
        self.results_ramp.addItems(COLOUR_RAMPS)
        ramp_row.addWidget(self.results_ramp, 1)
        apply_ramp = QPushButton(tr('Apply ramp'))
        apply_ramp.clicked.connect(self._apply_ramp_to_selection)
        ramp_row.addWidget(apply_ramp)
        layout.addLayout(ramp_row)

        buttons = QHBoxLayout()
        zoom = QPushButton(tr('Zoom to layer'))
        zoom.clicked.connect(self._zoom_to_selection)
        buttons.addWidget(zoom)
        export = QPushButton(tr('Export…'))
        export.clicked.connect(lambda: processing.execAlgorithmDialog('scimap:exportresults'))
        buttons.addWidget(export)
        layout.addLayout(buttons)

        settings_group = QGroupBox(tr('Settings'))
        settings_form = QFormLayout(settings_group)
        wbt_row = QHBoxLayout()
        self.wbt_label = QLabel()
        self.wbt_label.setWordWrap(True)
        wbt_row.addWidget(self.wbt_label, 1)
        browse = QPushButton(tr('Browse…'))
        browse.clicked.connect(self._choose_wbt)
        wbt_row.addWidget(browse)
        settings_form.addRow(tr('WhiteboxTools'), self._wrap(wbt_row))
        layout.addWidget(settings_group)

        layout.addStretch(1)
        return widget

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _wrap(layout):
        holder = QWidget()
        holder.setLayout(layout)
        return holder

    @staticmethod
    def _spin(value, minimum, maximum, suffix=None):
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(2)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(f" {suffix}")
        return spin

    def log_message(self, message):
        self.log.append(str(message))
        self.log.ensureCursorVisible()
        QCoreApplication.processEvents()

    def _load_settings(self):
        self._update_wbt_label()
        self._update_run_labels()

    def _update_wbt_label(self):
        found = wbt.find_executable()
        self.wbt_label.setText(found or tr('Not found — set it here or install WhiteboxTools'))

    def _choose_wbt(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('Locate the WhiteboxTools executable'))
        if path:
            wbt.store_executable(path)
            self._update_wbt_label()

    def _refresh_raster_lists(self):
        rasters = [
            layer for layer in QgsProject.instance().mapLayers().values()
            if isinstance(layer, QgsRasterLayer)
        ]
        for widget in (self.flood_rainfall_list, self.flood_ofd_list):
            selected = {item.text() for item in widget.selectedItems()}
            widget.clear()
            for layer in rasters:
                widget.addItem(layer.name())
            for row in range(widget.count()):
                if widget.item(row).text() in selected:
                    widget.item(row).setSelected(True)

    @staticmethod
    def _layers_named(names):
        project = QgsProject.instance()
        layers = []
        for name in names:
            matches = project.mapLayersByName(name)
            if matches:
                layers.append(matches[0])
        return layers

    # ── Map tools ───────────────────────────────────────────────────────

    def _toggle_pour_point_tool(self, checked):
        if checked:
            layer = self.catchment_dem.currentLayer()
            if layer is None:
                self.pour_point_button.setChecked(False)
                self._warn(tr('Choose a DEM before picking a pour point.'))
                return
            self.pour_point_tool.set_target_crs(layer.crs())
            self.canvas.setMapTool(self.pour_point_tool)
        else:
            self.pour_point_tool.restore_previous_tool()

    def _on_pour_point_picked(self, point):
        self.pour_point = point
        self.pour_point_label.setText(f"{point.x():.2f}, {point.y():.2f}")
        self.pour_point_button.setChecked(False)

    def _toggle_impact_tool(self, checked):
        if checked:
            layer = self.flood_connectivity.currentLayer()
            if layer is not None:
                self.impact_tool.set_target_crs(layer.crs())
            self.canvas.setMapTool(self.impact_tool)
        else:
            self.impact_tool.restore_previous_tool()

    def _on_impact_points_changed(self, points):
        self.impact_list.clear()
        for index, point in enumerate(points, start=1):
            self.impact_list.addItem(f"{index}. {point.x():.2f}, {point.y():.2f}")

    # ── Parameter set handling ──────────────────────────────────────────

    def current_weights(self):
        weights = {}
        for row, (class_id, _label) in enumerate(SCIMAP_CLASSES):
            spin = self.weights_table.cellWidget(row, 2)
            weights[class_id] = float(spin.value())
        return weights

    def _set_weights(self, weights):
        for row, (class_id, _label) in enumerate(SCIMAP_CLASSES):
            spin = self.weights_table.cellWidget(row, 2)
            spin.setValue(float(weights.get(class_id, DEFAULT_WEIGHTS.get(class_id, 0.5))))

    def _reset_weights(self):
        self._set_weights(DEFAULT_WEIGHTS)
        self.log_message(tr('Risk weights reset to SCIMAP defaults.'))

    def _import_weights(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr('Import parameter set'), '', tr('XML files (*.xml)'))
        if not path:
            return
        try:
            name, weights = params_xml.read_weights(path)
        except Exception as exc:
            self._warn(tr('Could not read that parameter set: ') + str(exc))
            return
        self._set_weights(weights)
        self.log_message(tr("Imported parameter set '{}'.").format(name))

    def _export_weights(self):
        path, _ = QFileDialog.getSaveFileName(
            self, tr('Export parameter set'), 'scimap_parameters.xml',
            tr('XML files (*.xml)'))
        if not path:
            return
        try:
            params_xml.write_weights(path, self.current_weights(), os.path.basename(path))
        except Exception as exc:
            self._warn(tr('Could not write that parameter set: ') + str(exc))
            return
        self.log_message(tr('Exported parameter set to {}.').format(path))

    def weights_matrix(self):
        matrix = []
        for class_id, weight in sorted(self.current_weights().items()):
            matrix.extend([class_id, weight])
        return matrix

    # ── Running algorithms ──────────────────────────────────────────────

    def _cancel_run(self):
        if self.feedback is not None:
            self.feedback.cancel()
            self.log_message(tr('Cancelling…'))

    def run_algorithm(self, algorithm_id, params, description):
        """Run a Processing algorithm on the main thread with live feedback."""
        if self.feedback is not None:
            self._warn(tr('A SCIMAP run is already in progress.'))
            return None

        self.log.clear()
        self.progress.setValue(0)
        self.cancel_button.setEnabled(True)
        self.log_message(f"▶ {description}")
        self.feedback = PanelFeedback(self)

        try:
            results = processing.run(
                algorithm_id, params, feedback=self.feedback, is_child_algorithm=False,
            )
        except Exception as exc:
            self.log_message(f"✖ {exc}")
            self._warn(str(exc))
            return None
        finally:
            self.feedback = None
            self.cancel_button.setEnabled(False)

        if not results:
            self.log_message(tr('Run produced no outputs.'))
            return None

        self.progress.setValue(100)
        self.log_message(tr('✔ Complete.'))
        self.last_result_layers = self._register_results(results)
        return results

    def _register_results(self, results):
        """Add produced layers to the canvas and the Results tab.

        Returns the layers just created, keyed by their output parameter name,
        so callers that need a specific one (e.g. the delineated catchment) don't
        have to re-derive it from the generic results list.
        """
        layers = {}
        for key, value in results.items():
            if not isinstance(value, str) or not value:
                continue
            name = f"{key.replace('OUT_', '').replace('_', ' ').title()}"
            layer = QgsRasterLayer(value, name)
            if not layer.isValid():
                layer = QgsVectorLayer(value, name, 'ogr')
            if not layer.isValid():
                continue
            QgsProject.instance().addMapLayer(layer)
            self.results.append(layer.id())
            self.results_list.addItem(f"{name} — {os.path.basename(value)}")
            layers[key] = layer
        self._refresh_raster_lists()
        return layers

    def _run_catchment(self):
        dem = self.catchment_dem.currentLayer()
        if dem is None:
            return self._warn(tr('Choose a DEM.'))
        if self.pour_point is None:
            return self._warn(tr('Pick a pour point on the map first.'))

        results = self.run_algorithm('scimap:scimapcatchment', {
            'INPUT_DEM': dem,
            'POUR_POINT': f"{self.pour_point.x()},{self.pour_point.y()} [{dem.crs().authid()}]",
            'SNAP_RADIUS': self.snap_radius.value(),
            'SNAP_AREA_THRESHOLD': self.snap_area.value(),
            'SNAP_MIN_RATIO': 100.0,
            'MIN_PART_AREA': 0.0,
            'BREACH_DEPRESSIONS': self.catchment_breach.isChecked(),
            'OUT_CATCHMENT': 'TEMPORARY_OUTPUT',
            'OUT_POUR_POINT': 'TEMPORARY_OUTPUT',
        }, tr('Delineating catchment'))
        if not results:
            return None

        catchment_layer = self.last_result_layers.get('OUT_CATCHMENT')
        if catchment_layer is not None:
            # Wire the freshly delineated catchment straight into the Run tab's
            # clip option so Run just works without hunting through every
            # polygon layer in the project.
            self.run_catchment.setLayer(catchment_layer)
            self.log_message(tr(
                "Set '{}' as the Run tab's clip catchment.").format(catchment_layer.name()))

    def _clip_rasters_to_catchment(self, catchment, layers):
        """Clip each (layer, label) pair to *catchment*'s outline.

        Returns the clipped :class:`QgsRasterLayer` objects in the same order
        as *layers*, or ``None`` if a clip fails (a warning is shown either way).
        """
        clipped = []
        for layer, label in layers:
            self.log_message(tr('Clipping {} to the catchment area…').format(label))
            try:
                result = processing.run('gdal:cliprasterbymasklayer', {
                    'INPUT': layer,
                    'MASK': catchment,
                    'SOURCE_CRS': None,
                    'TARGET_CRS': None,
                    'TARGET_EXTENT': None,
                    'NODATA': None,
                    'ALPHA_BAND': False,
                    'CROP_TO_CUTLINE': True,
                    'KEEP_RESOLUTION': True,
                    'SET_RESOLUTION': False,
                    'X_RESOLUTION': None,
                    'Y_RESOLUTION': None,
                    'MULTITHREADING': False,
                    'OPTIONS': '',
                    'DATA_TYPE': 0,
                    'EXTRA': '',
                    'OUTPUT': 'TEMPORARY_OUTPUT',
                }, is_child_algorithm=False)
            except Exception as exc:
                self._warn(tr('Could not clip {} to the catchment area: {}').format(label, exc))
                return None
            clipped_layer = QgsRasterLayer(result['OUTPUT'], f"{layer.name()} ({tr('clipped')})")
            if not clipped_layer.isValid():
                self._warn(tr(
                    'Clipping {} to the catchment area produced an invalid raster. '
                    'Check that the catchment overlaps the layer.').format(label))
                return None
            clipped.append(clipped_layer)
        return clipped

    def _run_risk_mapping(self):
        dem = self.run_dem.currentLayer()
        weight_layer = self.run_weight_layer.currentLayer()
        rainfall = self.run_rainfall.currentLayer()
        if dem is None or weight_layer is None or rainfall is None:
            return self._warn(tr('Choose a DEM, a weighting raster and a rainfall raster.'))

        catchment = self.run_catchment.currentLayer()
        if catchment is not None:
            clipped = self._clip_rasters_to_catchment(catchment, [
                (dem, tr('DEM')),
                (weight_layer, tr('land cover / FIO raster')),
                (rainfall, tr('rainfall raster')),
            ])
            if clipped is None:
                return None
            dem, weight_layer, rainfall = clipped

        algorithm_id = self.mapping_type.currentData()
        params = {
            'INPUT_DEM': dem,
            'INPUT_RAIN': rainfall,
            'STREAM_THRESHOLD': self.run_stream_threshold.value(),
            'USE_STREAM_POWER': self.run_stream_power.isChecked(),
            'CONNECTIVITY_METHOD': self.run_connectivity_method.currentIndex(),
            'COLOUR_RAMP': self.run_ramp.currentData(),
            'OUT_CONNECTIVITY': 'TEMPORARY_OUTPUT',
            'OUT_EROSION': 'TEMPORARY_OUTPUT',
            'OUT_VECTOR_STREAM': 'TEMPORARY_OUTPUT',
        }

        if algorithm_id == 'scimap:scimapfio':
            params['INPUT_FIO'] = weight_layer
            description = tr('Running SCIMAP FIO')
        else:
            params.update({
                'INPUT_LC': weight_layer,
                'LC_IS_PREWEIGHTED': False,
                'LC_IS_SCIMAP_CLASSES': self.lc_is_scimap.isChecked(),
                'REMAP_TABLE': default_remap_matrix(),
                'WEIGHTS_TABLE': self.weights_matrix(),
            })
            description = tr('Running SCIMAP Sediment')

        self.run_algorithm(algorithm_id, params, description)

    def _compute_ofd_from_impacts(self):
        """Turn the clicked impact points into overland flow distance rasters.

        The Flood tool consumes pre-computed distance rasters, so this bridges
        the web application's click-to-place impact points to that input by
        running the Overland Flow Distance tool once per point.
        """
        points = list(self.impact_tool.points)
        if not points:
            return self._warn(tr('Pick at least one impact point on the map.'))

        dem = self.run_dem.currentLayer() or self.catchment_dem.currentLayer()
        if dem is None:
            return self._warn(tr('Choose a DEM on the Catchment or Run tab first.'))

        for index, point in enumerate(points, start=1):
            layer = QgsVectorLayer(
                f"Point?crs={dem.crs().authid()}", f"impact_point_{index}", "memory")
            provider = layer.dataProvider()
            from qgis.core import QgsFeature, QgsGeometry
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPointXY(point))
            provider.addFeatures([feature])
            layer.updateExtents()

            self.run_algorithm('scimap:scimapoverlandflowdistance', {
                'INPUT_DEM': dem,
                'INPUT_POINTS': layer,
                'SNAP_DISTANCE': 0.0,
                'SNAP_TO_CHANNEL': True,
                'SNAP_RADIUS': self.snap_radius.value(),
                'SNAP_AREA_THRESHOLD': self.snap_area.value(),
                'SNAP_MIN_RATIO': 100.0,
                'FILL_DEPRESSIONS': True,
                'OUT_DISTANCE': 'TEMPORARY_OUTPUT',
            }, tr('Computing overland flow distance for point {} of {}').format(
                index, len(points)))

    def _run_flood(self):
        connectivity = self.flood_connectivity.currentLayer()
        runoff = self.flood_runoff.currentLayer()
        rainfall = self._layers_named(
            [item.text() for item in self.flood_rainfall_list.selectedItems()])
        ofd = self._layers_named(
            [item.text() for item in self.flood_ofd_list.selectedItems()])

        if connectivity is None or runoff is None:
            return self._warn(tr('Choose a connectivity raster and a runoff raster.'))
        if not rainfall:
            return self._warn(tr('Select at least one rainfall pattern raster.'))
        if not ofd:
            return self._warn(tr(
                'Select at least one overland flow distance raster, or compute '
                'them from your impact points.'))

        self.run_algorithm('scimap:scimapflood', {
            'INPUT_CONNECTIVITY': connectivity,
            'INPUT_RUNOFF': runoff,
            'INPUT_RAINFALL_MAPS': rainfall,
            'INPUT_OFD_MAPS': ofd,
            'OUT_MEAN': 'TEMPORARY_OUTPUT',
            'OUT_STDEV': 'TEMPORARY_OUTPUT',
        }, tr('Running SCIMAP Flood'))

    # ── Results tab actions ─────────────────────────────────────────────

    def _selected_result_layer(self):
        row = self.results_list.currentRow()
        if row < 0 or row >= len(self.results):
            return None
        return QgsProject.instance().mapLayer(self.results[row])

    def _apply_ramp_to_selection(self):
        layer = self._selected_result_layer()
        if layer is None:
            return self._warn(tr('Select a result layer first.'))
        ramp = self.results_ramp.currentText()
        if isinstance(layer, QgsRasterLayer):
            styling.apply_ramp(layer, ramp)
        else:
            styling.apply_graduated(layer, 'Risk', ramp)
        self.canvas.refresh()
        self.log_message(tr('Applied {} to {}.').format(ramp, layer.name()))

    def _zoom_to_selection(self):
        layer = self._selected_result_layer()
        if layer is None:
            return self._warn(tr('Select a result layer first.'))
        self.canvas.setExtent(
            self.canvas.mapSettings().layerExtentToOutputExtent(layer, layer.extent()))
        self.canvas.refresh()

    # ── Misc ────────────────────────────────────────────────────────────

    def _warn(self, message):
        QMessageBox.warning(self, tr('SCIMAP'), message)
        return None

    def deactivate_map_tools(self):
        """Release both map tools; called when the plugin unloads."""
        for tool, button in (
            (self.pour_point_tool, self.pour_point_button),
            (self.impact_tool, self.impact_button),
        ):
            button.setChecked(False)
            tool.clear()
            tool.restore_previous_tool()

    def closeEvent(self, event):
        self.deactivate_map_tools()
        super().closeEvent(event)
