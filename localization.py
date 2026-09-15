"""Lightweight translation lookup.

QGIS's own ``.ts``/``.qm`` machinery needs a compile step; this plugin ships a
plain dict instead so translations can be edited in place. Strings without an
entry for the active language fall through to English, so partial coverage is
fine.
"""

from qgis.PyQt.QtCore import QLocale, QSettings


_TRANSLATIONS = {
    # ── Provider, tools and groups ──────────────────────────────────────
    "SCIMAP": {"de": "SCIMAP", "es": "SCIMAP"},
    "SCIMAP Panel": {
        "de": "SCIMAP-Bedienfeld",
        "es": "Panel SCIMAP",
    },
    "Risk Mapping": {
        "de": "Risikokartierung",
        "es": "Cartografía de riesgo",
    },
    "Catchment": {
        "de": "Einzugsgebiet",
        "es": "Cuenca",
    },
    "Preparation": {
        "de": "Vorbereitung",
        "es": "Preparación",
    },
    "Export": {
        "de": "Export",
        "es": "Exportar",
    },
    "SCIMAP Sediment": {
        "de": "SCIMAP Sediment",
        "es": "SCIMAP Sedimento",
    },
    "SCIMAP FIO": {
        "de": "SCIMAP FIO",
        "es": "SCIMAP FIO",
    },
    "SCIMAP Flood": {
        "de": "SCIMAP Hochwasser",
        "es": "SCIMAP Inundación",
    },
    "Network Index": {
        "de": "Netzwerkindex",
        "es": "Índice de red",
    },
    "Flood": {
        "de": "Hochwasser",
        "es": "Inundación",
    },
    "Delineate Catchment": {
        "de": "Einzugsgebiet abgrenzen",
        "es": "Delimitar cuenca",
    },
    "Apply Land Cover Risk Weights": {
        "de": "Risikogewichte der Landbedeckung anwenden",
        "es": "Aplicar ponderaciones de riesgo de cobertura del suelo",
    },
    "Export SCIMAP Results": {
        "de": "SCIMAP-Ergebnisse exportieren",
        "es": "Exportar resultados de SCIMAP",
    },
    "Overland Flow Distance to Point": {
        "de": "Oberflächenabflussdistanz zum Punkt",
        "es": "Distancia de flujo superficial al punto",
    },
    "SCIMAP Sediment Diffuse Pollution Risk": {
        "de": "SCIMAP-Sedimentrisiko für diffuse Verschmutzung",
        "es": "Riesgo SCIMAP de contaminación difusa por sedimentos",
    },
    "SCIMAP Network Index from DEM": {
        "de": "SCIMAP-Netzwerkindex aus DGM",
        "es": "Índice de red SCIMAP desde MDE",
    },

    # ── Menu actions ────────────────────────────────────────────────────
    "Run SCIMAP Sediment": {
        "de": "SCIMAP Sediment ausführen",
        "es": "Ejecutar SCIMAP Sedimento",
    },
    "Run SCIMAP FIO": {
        "de": "SCIMAP FIO ausführen",
        "es": "Ejecutar SCIMAP FIO",
    },
    "Run SCIMAP Network Index": {
        "de": "SCIMAP-Netzwerkindex ausführen",
        "es": "Ejecutar índice de red SCIMAP",
    },
    "Run SCIMAP Flood": {
        "de": "SCIMAP Hochwasser ausführen",
        "es": "Ejecutar SCIMAP Inundación",
    },
    "Run Overland Flow Distance to Point": {
        "de": "Oberflächenabflussdistanz zum Punkt ausführen",
        "es": "Ejecutar distancia de flujo superficial al punto",
    },

    # ── Inputs ──────────────────────────────────────────────────────────
    "Digital Elevation Model (DEM)": {
        "de": "Digitales Geländemodell (DGM)",
        "es": "Modelo digital de elevación (MDE)",
    },
    "DEM": {"de": "DGM", "es": "MDE"},
    "Land Cover Map / Risk Weighting": {
        "de": "Landbedeckungskarte / Risikogewichtung",
        "es": "Mapa de cobertura del suelo / ponderación de riesgo",
    },
    "Land Cover Map": {
        "de": "Landbedeckungskarte",
        "es": "Mapa de cobertura del suelo",
    },
    "Land cover": {
        "de": "Landbedeckung",
        "es": "Cobertura del suelo",
    },
    "Rainfall Map": {
        "de": "Niederschlagskarte",
        "es": "Mapa de precipitación",
    },
    "Rainfall": {
        "de": "Niederschlag",
        "es": "Precipitación",
    },
    "Rainfall Map for Connectivity": {
        "de": "Niederschlagskarte für Konnektivität",
        "es": "Mapa de precipitación para conectividad",
    },
    "Rainfall Pattern Rasters": {
        "de": "Raster für Niederschlagsmuster",
        "es": "Rásteres de patrones de precipitación",
    },
    "Land Cover Pattern Rasters": {
        "de": "Raster für Landbedeckungsmuster",
        "es": "Rásteres de patrones de cobertura del suelo",
    },
    "FIO Concentration Raster (CFU)": {
        "de": "FIO-Konzentrationsraster (KBE)",
        "es": "Ráster de concentración de FIO (UFC)",
    },
    "FIO concentration": {
        "de": "FIO-Konzentration",
        "es": "Concentración de FIO",
    },
    "FIO normalisation constant (CFU)": {
        "de": "FIO-Normalisierungskonstante (KBE)",
        "es": "Constante de normalización de FIO (UFC)",
    },
    "Connectivity": {
        "de": "Konnektivität",
        "es": "Conectividad",
    },
    "Connectivity Raster (pre-computed)": {
        "de": "Konnektivitätsraster (vorberechnet)",
        "es": "Ráster de conectividad (precalculado)",
    },
    "Runoff / Land Cover Weights Raster (pre-computed)": {
        "de": "Abfluss-/Landbedeckungsgewichtsraster (vorberechnet)",
        "es": "Ráster de escorrentía / ponderaciones de cobertura (precalculado)",
    },
    "Runoff / weights": {
        "de": "Abfluss / Gewichte",
        "es": "Escorrentía / ponderaciones",
    },
    "Overland Flow Distance Rasters (pre-computed)": {
        "de": "Raster der Oberflächenabflussdistanz (vorberechnet)",
        "es": "Rásteres de distancia de flujo superficial (precalculados)",
    },
    "Impact Point Layer": {
        "de": "Ebene der Wirkungspunkte",
        "es": "Capa de puntos de impacto",
    },
    "Impact points": {
        "de": "Wirkungspunkte",
        "es": "Puntos de impacto",
    },
    "Target Point(s)": {
        "de": "Zielpunkt(e)",
        "es": "Punto(s) objetivo",
    },
    "Pour point": {
        "de": "Auslasspunkt",
        "es": "Punto de desagüe",
    },
    "Pour point (catchment outlet)": {
        "de": "Auslasspunkt (Gebietsauslass)",
        "es": "Punto de desagüe (salida de la cuenca)",
    },
    "SCIMAP Result Raster": {
        "de": "SCIMAP-Ergebnisraster",
        "es": "Ráster de resultado SCIMAP",
    },
    "SCIMAP Result Vector": {
        "de": "SCIMAP-Ergebnisvektor",
        "es": "Vector de resultado SCIMAP",
    },

    # ── Settings ────────────────────────────────────────────────────────
    "Stream Initiation Threshold (m²)": {
        "de": "Schwellenwert für Gewässerbeginn (m²)",
        "es": "Umbral de inicio de cauce (m²)",
    },
    "Stream initiation threshold": {
        "de": "Schwellenwert für Gewässerbeginn",
        "es": "Umbral de inicio de cauce",
    },
    "Use stream power in erosion calculation": {
        "de": "Stream Power in der Erosionsberechnung verwenden",
        "es": "Usar potencia del flujo en el cálculo de erosión",
    },
    "Connectivity algorithm": {
        "de": "Konnektivitätsalgorithmus",
        "es": "Algoritmo de conectividad",
    },
    "Network Index (flow-path trace)": {
        "de": "Netzwerkindex (Fließpfadverfolgung)",
        "es": "Índice de red (trazado de ruta de flujo)",
    },
    "Percentage Downslope Saturated Length (PDSL)": {
        "de": "Prozentualer gesättigter Hangabwärtslänge (PDSL)",
        "es": "Porcentaje de longitud saturada ladera abajo (PDSL)",
    },
    "PDSL": {
        "de": "PDSL",
        "es": "PDSL",
    },
    "Colour ramp for output styling": {
        "de": "Farbverlauf für die Ausgabedarstellung",
        "es": "Rampa de color para el estilo de salida",
    },
    "Colour ramp": {
        "de": "Farbverlauf",
        "es": "Rampa de color",
    },
    "SCIMAP defaults (per layer)": {
        "de": "SCIMAP-Standard (pro Layer)",
        "es": "Predeterminados de SCIMAP (por capa)",
    },
    "WhiteboxTools executable; leave blank to auto-detect": {
        "de": "WhiteboxTools-Programmdatei; leer lassen für automatische Erkennung",
        "es": "Ejecutable de WhiteboxTools; dejar en blanco para detección automática",
    },
    "WhiteboxTools": {"de": "WhiteboxTools", "es": "WhiteboxTools"},
    "Land cover already uses SCIMAP classes (1-7)": {
        "de": "Landbedeckung verwendet bereits SCIMAP-Klassen (1-7)",
        "es": "La cobertura del suelo ya usa clases SCIMAP (1-7)",
    },
    "Land cover is already a risk weighting (use values as-is)": {
        "de": "Landbedeckung ist bereits eine Risikogewichtung (Werte unverändert verwenden)",
        "es": "La cobertura del suelo ya es una ponderación de riesgo (usar los valores tal cual)",
    },
    "Land cover class -> SCIMAP class": {
        "de": "Landbedeckungsklasse -> SCIMAP-Klasse",
        "es": "Clase de cobertura del suelo -> clase SCIMAP",
    },
    "SCIMAP class -> risk weight": {
        "de": "SCIMAP-Klasse -> Risikogewicht",
        "es": "Clase SCIMAP -> ponderación de riesgo",
    },
    "SCIMAP class used for unmapped land cover values": {
        "de": "SCIMAP-Klasse für nicht zugeordnete Landbedeckungswerte",
        "es": "Clase SCIMAP para valores de cobertura no asignados",
    },
    "Parameter set XML (overrides the risk weight table)": {
        "de": "Parametersatz-XML (überschreibt die Risikogewichtstabelle)",
        "es": "XML del conjunto de parámetros (anula la tabla de ponderaciones)",
    },
    "Land cover ID": {
        "de": "Landbedeckungs-ID",
        "es": "ID de cobertura del suelo",
    },
    "SCIMAP class": {
        "de": "SCIMAP-Klasse",
        "es": "Clase SCIMAP",
    },
    "Risk weight": {
        "de": "Risikogewicht",
        "es": "Ponderación de riesgo",
    },
    "Class": {"de": "Klasse", "es": "Clase"},
    "Snap search radius": {
        "de": "Fangradius",
        "es": "Radio de búsqueda de ajuste",
    },
    "Snap search radius (map units)": {
        "de": "Fangradius (Karteneinheiten)",
        "es": "Radio de búsqueda de ajuste (unidades de mapa)",
    },
    "Minimum contributing area": {
        "de": "Minimales Einzugsgebiet",
        "es": "Área contribuyente mínima",
    },
    "Minimum contributing area to snap onto (m²)": {
        "de": "Minimales Einzugsgebiet zum Fangen (m²)",
        "es": "Área contribuyente mínima para el ajuste (m²)",
    },
    "Minimum accumulation ratio vs the clicked cell": {
        "de": "Mindestverhältnis der Akkumulation zur angeklickten Zelle",
        "es": "Relación mínima de acumulación frente a la celda seleccionada",
    },
    "Drop catchment parts smaller than (m²)": {
        "de": "Einzugsgebietsteile kleiner als (m²) verwerfen",
        "es": "Descartar partes de cuenca menores que (m²)",
    },
    "Breach depressions before routing": {
        "de": "Senken vor der Abflussberechnung durchbrechen",
        "es": "Romper depresiones antes del enrutamiento",
    },
    "Mapping type": {
        "de": "Kartierungstyp",
        "es": "Tipo de cartografía",
    },
    "Field name for exported point values": {
        "de": "Feldname für exportierte Punktwerte",
        "es": "Nombre del campo para los valores de punto exportados",
    },
    "Export only cells with values greater than zero": {
        "de": "Nur Zellen mit Werten größer als null exportieren",
        "es": "Exportar solo celdas con valores mayores que cero",
    },

    # ── Outputs ─────────────────────────────────────────────────────────
    "Network Connectivity Risk": {
        "de": "Risiko der Netzwerkkonnektivität",
        "es": "Riesgo de conectividad de la red",
    },
    "Erosion Risk": {
        "de": "Erosionsrisiko",
        "es": "Riesgo de erosión",
    },
    "FIO Delivery Risk": {
        "de": "FIO-Eintragsrisiko",
        "es": "Riesgo de aporte de FIO",
    },
    "Instream Risk Concentration": {
        "de": "Risikokonzentration im Gewässer",
        "es": "Concentración de riesgo en el cauce",
    },
    "Stream Risk Points": {
        "de": "Gewässerrisiko-Punkte",
        "es": "Puntos de riesgo de cauce",
    },
    "Stream Network (KML)": {
        "de": "Gewässernetz (KML)",
        "es": "Red de cauces (KML)",
    },
    "SCIMAP Land Cover Classes": {
        "de": "SCIMAP-Landbedeckungsklassen",
        "es": "Clases de cobertura del suelo SCIMAP",
    },
    "Land Cover Risk Weighting": {
        "de": "Risikogewichtung der Landbedeckung",
        "es": "Ponderación de riesgo de cobertura del suelo",
    },
    "Catchment Boundary": {
        "de": "Einzugsgebietsgrenze",
        "es": "Límite de la cuenca",
    },
    "Snapped Pour Point": {
        "de": "Gefangener Auslasspunkt",
        "es": "Punto de desagüe ajustado",
    },
    "Basin Raster": {
        "de": "Einzugsgebietsraster",
        "es": "Ráster de la cuenca",
    },
    "Overland Flow Travel Distance": {
        "de": "Fließweglänge des Oberflächenabflusses",
        "es": "Distancia de recorrido del flujo superficial",
    },
    "Flood Mean": {
        "de": "Hochwasser-Mittelwert",
        "es": "Media de inundación",
    },
    "Flood Standard Deviation": {
        "de": "Hochwasser-Standardabweichung",
        "es": "Desviación estándar de inundación",
    },
    "SCIMAP-Flood Mean": {
        "de": "SCIMAP-Hochwasser-Mittelwert",
        "es": "Media de SCIMAP-Inundación",
    },
    "SCIMAP-Flood Standard Deviation": {
        "de": "SCIMAP-Hochwasser-Standardabweichung",
        "es": "Desviación estándar de SCIMAP-Inundación",
    },
    "Result as Points": {
        "de": "Ergebnis als Punkte",
        "es": "Resultado como puntos",
    },
    "Result as GeoPackage": {
        "de": "Ergebnis als GeoPackage",
        "es": "Resultado como GeoPackage",
    },
    "Result as KML": {
        "de": "Ergebnis als KML",
        "es": "Resultado como KML",
    },
    "Result as Cloud-Optimised GeoTIFF": {
        "de": "Ergebnis als Cloud-optimiertes GeoTIFF",
        "es": "Resultado como GeoTIFF optimizado para la nube",
    },

    # ── Panel ───────────────────────────────────────────────────────────
    "Parameters": {"de": "Parameter", "es": "Parámetros"},
    "Run": {"de": "Ausführen", "es": "Ejecutar"},
    "Results": {"de": "Ergebnisse", "es": "Resultados"},
    "Progress": {"de": "Fortschritt", "es": "Progreso"},
    "Settings": {"de": "Einstellungen", "es": "Configuración"},
    "Cancel": {"de": "Abbrechen", "es": "Cancelar"},
    "Cancelling…": {"de": "Wird abgebrochen…", "es": "Cancelando…"},
    "Clear": {"de": "Leeren", "es": "Limpiar"},
    "Pick on map": {"de": "Auf Karte wählen", "es": "Elegir en el mapa"},
    "No pour point selected": {
        "de": "Kein Auslasspunkt gewählt",
        "es": "Ningún punto de desagüe seleccionado",
    },
    "Delineate catchment": {
        "de": "Einzugsgebiet abgrenzen",
        "es": "Delimitar cuenca",
    },
    "Run risk mapping": {
        "de": "Risikokartierung ausführen",
        "es": "Ejecutar cartografía de riesgo",
    },
    "Open in Processing dialog…": {
        "de": "Im Verarbeitungsdialog öffnen…",
        "es": "Abrir en el diálogo de procesamiento…",
    },
    "Reset to defaults": {
        "de": "Auf Standard zurücksetzen",
        "es": "Restablecer valores predeterminados",
    },
    "Import XML…": {"de": "XML importieren…", "es": "Importar XML…"},
    "Export XML…": {"de": "XML exportieren…", "es": "Exportar XML…"},
    "Export…": {"de": "Exportieren…", "es": "Exportar…"},
    "Browse…": {"de": "Durchsuchen…", "es": "Examinar…"},
    "Apply ramp": {"de": "Farbverlauf anwenden", "es": "Aplicar rampa"},
    "Zoom to layer": {"de": "Auf Layer zoomen", "es": "Acercar a la capa"},
    "Refresh raster lists": {
        "de": "Rasterlisten aktualisieren",
        "es": "Actualizar listas de rásteres",
    },
    "Calc. OFD": {},
    "Rainfall pattern rasters (select one or more)": {
        "de": "Niederschlagsmuster-Raster (eines oder mehrere wählen)",
        "es": "Rásteres de patrones de precipitación (elija uno o varios)",
    },
    "Overland flow distance rasters (select one or more)": {
        "de": "Oberflächenabflussdistanz-Raster (eines oder mehrere wählen)",
        "es": "Rásteres de distancia de flujo superficial (elija uno o varios)",
    },
    "Layers produced in this session": {
        "de": "In dieser Sitzung erzeugte Layer",
        "es": "Capas producidas en esta sesión",
    },
    "SCIMAP run log": {
        "de": "SCIMAP-Ausführungsprotokoll",
        "es": "Registro de ejecución de SCIMAP",
    },
    "map units": {"de": "Karteneinheiten", "es": "unidades de mapa"},
    "XML files (*.xml)": {
        "de": "XML-Dateien (*.xml)",
        "es": "Archivos XML (*.xml)",
    },
    "Import parameter set": {
        "de": "Parametersatz importieren",
        "es": "Importar conjunto de parámetros",
    },
    "Export parameter set": {
        "de": "Parametersatz exportieren",
        "es": "Exportar conjunto de parámetros",
    },
    "Locate the WhiteboxTools executable": {
        "de": "WhiteboxTools-Programmdatei suchen",
        "es": "Localizar el ejecutable de WhiteboxTools",
    },
    "Not found — set it here or install WhiteboxTools": {
        "de": "Nicht gefunden — hier festlegen oder WhiteboxTools installieren",
        "es": "No encontrado — indíquelo aquí o instale WhiteboxTools",
    },

    # ── Messages ────────────────────────────────────────────────────────
    "Choose a DEM.": {
        "de": "Wählen Sie ein DGM.",
        "es": "Elija un MDE.",
    },
    "Choose a DEM before picking a pour point.": {
        "de": "Wählen Sie ein DGM, bevor Sie einen Auslasspunkt setzen.",
        "es": "Elija un MDE antes de seleccionar un punto de desagüe.",
    },
    "Pick a pour point on the map first.": {
        "de": "Setzen Sie zuerst einen Auslasspunkt auf der Karte.",
        "es": "Seleccione primero un punto de desagüe en el mapa.",
    },
    "Choose a DEM, a weighting raster and a rainfall raster.": {
        "de": "Wählen Sie ein DGM, ein Gewichtungsraster und ein Niederschlagsraster.",
        "es": "Elija un MDE, un ráster de ponderación y un ráster de precipitación.",
    },
    "Choose a connectivity raster and a runoff raster.": {
        "de": "Wählen Sie ein Konnektivitäts- und ein Abflussraster.",
        "es": "Elija un ráster de conectividad y uno de escorrentía.",
    },
    "Select at least one rainfall pattern raster.": {
        "de": "Wählen Sie mindestens ein Niederschlagsmuster-Raster.",
        "es": "Seleccione al menos un ráster de patrón de precipitación.",
    },
    "Pick at least one impact point on the map.": {
        "de": "Setzen Sie mindestens einen Wirkungspunkt auf der Karte.",
        "es": "Seleccione al menos un punto de impacto en el mapa.",
    },
    "Select a result layer first.": {
        "de": "Wählen Sie zuerst einen Ergebnislayer.",
        "es": "Seleccione primero una capa de resultado.",
    },
    "A SCIMAP run is already in progress.": {
        "de": "Eine SCIMAP-Berechnung läuft bereits.",
        "es": "Ya hay una ejecución de SCIMAP en curso.",
    },
    "Run produced no outputs.": {
        "de": "Die Berechnung erzeugte keine Ausgaben.",
        "es": "La ejecución no produjo resultados.",
    },
    "Delineating catchment": {
        "de": "Einzugsgebiet wird abgegrenzt",
        "es": "Delimitando la cuenca",
    },
    "Running SCIMAP Sediment": {
        "de": "SCIMAP Sediment wird ausgeführt",
        "es": "Ejecutando SCIMAP Sedimento",
    },
    "Running SCIMAP FIO": {
        "de": "SCIMAP FIO wird ausgeführt",
        "es": "Ejecutando SCIMAP FIO",
    },
    "Running SCIMAP Flood": {
        "de": "SCIMAP Hochwasser wird ausgeführt",
        "es": "Ejecutando SCIMAP Inundación",
    },
    "Risk weights reset to SCIMAP defaults.": {
        "de": "Risikogewichte auf SCIMAP-Standard zurückgesetzt.",
        "es": "Ponderaciones de riesgo restablecidas a los valores de SCIMAP.",
    },
}


def current_language():
    locale_name = QSettings().value("locale/userLocale", QLocale.system().name(), type=str) or "en"
    return locale_name.split("_")[0].split("-")[0].lower()


def tr(text):
    language = current_language()
    return _TRANSLATIONS.get(text, {}).get(language, text)
