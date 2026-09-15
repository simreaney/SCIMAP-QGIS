"""Canvas map tools for picking pour points and flood impact points.

Typing British National Grid coordinates by hand is the awkward part of the
desktop workflow, so the dock panel lets users click points straight off the map
the way the web application does.
"""

from qgis.core import QgsCoordinateTransform, QgsProject, QgsWkbTypes
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor


class PointPickerMapTool(QgsMapToolEmitPoint):
    """Emits a single clicked point, transformed into a target CRS."""

    pointPicked = pyqtSignal(object)

    def __init__(self, canvas, target_crs=None, marker_colour="#B91C1C"):
        super().__init__(canvas)
        self.canvas = canvas
        self.target_crs = target_crs
        self._previous_tool = None
        self.marker = QgsRubberBand(canvas, QgsWkbTypes.PointGeometry)
        self.marker.setColor(QColor(marker_colour))
        self.marker.setWidth(3)
        self.marker.setIconSize(12)

    def set_target_crs(self, crs):
        self.target_crs = crs

    def activate(self):
        self._previous_tool = self.canvas.mapTool()
        super().activate()
        self.canvas.setCursor(Qt.CursorShape.CrossCursor)

    def restore_previous_tool(self):
        """Hand the canvas back to whatever tool was active before."""
        if self._previous_tool is not None and self.canvas.mapTool() is self:
            self.canvas.setMapTool(self._previous_tool)
        self._previous_tool = None

    def clear(self):
        self.marker.reset(QgsWkbTypes.PointGeometry)

    def _to_target(self, point):
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        if (self.target_crs is None or not self.target_crs.isValid()
                or not canvas_crs.isValid() or canvas_crs == self.target_crs):
            return point
        transform = QgsCoordinateTransform(
            canvas_crs, self.target_crs, QgsProject.instance(),
        )
        return transform.transform(point)

    def canvasReleaseEvent(self, event):
        canvas_point = self.toMapCoordinates(event.pos())
        self.clear()
        self.marker.addPoint(canvas_point, True)
        self.pointPicked.emit(self._to_target(canvas_point))


class MultiPointPickerMapTool(PointPickerMapTool):
    """Accumulates clicked points, keeping a marker for each."""

    pointsChanged = pyqtSignal(list)

    def __init__(self, canvas, target_crs=None, marker_colour="#1D4ED8"):
        super().__init__(canvas, target_crs, marker_colour)
        self.points = []

    def clear(self):
        self.points = []
        self.marker.reset(QgsWkbTypes.PointGeometry)
        self.pointsChanged.emit(self.points)

    def remove_at(self, index):
        if 0 <= index < len(self.points):
            del self.points[index]
            self._redraw()
            self.pointsChanged.emit(self.points)

    def _redraw(self):
        self.marker.reset(QgsWkbTypes.PointGeometry)
        canvas_crs = self.canvas.mapSettings().destinationCrs()
        transform = None
        if (self.target_crs is not None and self.target_crs.isValid()
                and canvas_crs.isValid() and canvas_crs != self.target_crs):
            transform = QgsCoordinateTransform(
                self.target_crs, canvas_crs, QgsProject.instance(),
            )
        for point in self.points:
            self.marker.addPoint(transform.transform(point) if transform else point, True)
        self.marker.show()

    def canvasReleaseEvent(self, event):
        canvas_point = self.toMapCoordinates(event.pos())
        self.points.append(self._to_target(canvas_point))
        self._redraw()
        self.pointsChanged.emit(self.points)
        self.pointPicked.emit(self.points[-1])
