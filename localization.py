from qgis.PyQt.QtCore import QLocale, QSettings


_TRANSLATIONS = {
    "Run SCIMAP Sediment": {
        "de": "SCIMAP Sediment ausfuhren",
        "es": "Ejecutar SCIMAP Sedimento",
    },
    "Run SCIMAP Network Index": {
        "de": "SCIMAP Netzwerkindex ausfuhren",
        "es": "Ejecutar indice de red SCIMAP",
    },
    "Run SCIMAP Flood": {
        "de": "SCIMAP Hochwasser ausfuhren",
        "es": "Ejecutar SCIMAP Inundacion",
    },
    "SCIMAP Sediment Diffuse Pollution Risk": {
        "de": "SCIMAP Sedimentrisiko fur diffuse Verschmutzung",
        "es": "Riesgo SCIMAP de contaminacion difusa por sedimentos",
    },
    "Risk Mapping": {
        "de": "Risikokartierung",
        "es": "Cartografia de riesgo",
    },
    "Digital Elevation Model (DEM)": {
        "de": "Digitales Gelandemodell (DEM)",
        "es": "Modelo digital de elevacion (DEM)",
    },
    "Land Cover Map / Risk Weighting": {
        "de": "Landbedeckungskarte / Risikogewichtung",
        "es": "Mapa de cobertura del suelo / ponderacion de riesgo",
    },
    "Rainfall Map": {
        "de": "Niederschlagskarte",
        "es": "Mapa de precipitacion",
    },
    "Stream Initiation Threshold (m²)": {
        "de": "Schwellenwert fur Gewasserbeginn (m2)",
        "es": "Umbral de inicio de cauce (m2)",
    },
    "Use stream power in erosion calculation": {
        "de": "Stream Power in der Erosionsberechnung verwenden",
        "es": "Usar potencia del flujo en el calculo de erosion",
    },
    "WhiteboxTools executable; leave blank to auto-detect": {
        "de": "WhiteboxTools-Programmdatei; leer lassen fur automatische Erkennung",
        "es": "Ejecutable de WhiteboxTools; dejar en blanco para deteccion automatica",
    },
    "Network Connectivity Risk": {
        "de": "Risiko der Netzwerkkonnektivitat",
        "es": "Riesgo de conectividad de la red",
    },
    "Erosion Risk": {
        "de": "Erosionsrisiko",
        "es": "Riesgo de erosion",
    },
    "SCIMAP Output Risk": {
        "de": "SCIMAP-Ausgaberisiko",
        "es": "Riesgo de salida de SCIMAP",
    },
    "Vector Stream Network": {
        "de": "Vektor-Gewassernetz",
        "es": "Red vectorial de cauces",
    },
    "SCIMAP Network Index from DEM": {
        "de": "SCIMAP Netzwerkindex aus DEM",
        "es": "Indice de red SCIMAP desde DEM",
    },
    "SCIMAP Flood": {
        "de": "SCIMAP Hochwasser",
        "es": "SCIMAP Inundacion",
    },
    "Network Index": {
        "de": "Netzwerkindex",
        "es": "Indice de red",
    },
    "Flood": {
        "de": "Hochwasser",
        "es": "Inundacion",
    },
    "Land Cover Pattern Rasters": {
        "de": "Raster fur Landbedeckungsmuster",
        "es": "Rasters de patrones de cobertura del suelo",
    },
    "Rainfall Map for Connectivity": {
        "de": "Niederschlagskarte fur Konnektivitat",
        "es": "Mapa de precipitacion para conectividad",
    },
    "Rainfall Pattern Rasters": {
        "de": "Raster fur Niederschlagsmuster",
        "es": "Rasters de patrones de precipitacion",
    },
    "Impact Point Layer": {
        "de": "Ebene der Wirkungspunkte",
        "es": "Capa de puntos de impacto",
    },
    "Flood Mean": {
        "de": "Hochwasser-Mittelwert",
        "es": "Media de inundacion",
    },
    "Flood Standard Deviation": {
        "de": "Hochwasser-Standardabweichung",
        "es": "Desviacion estandar de inundacion",
    },
}


def current_language():
    locale_name = QSettings().value("locale/userLocale", QLocale.system().name(), type=str) or "en"
    return locale_name.split("_")[0].split("-")[0].lower()


def tr(text):
    language = current_language()
    return _TRANSLATIONS.get(text, {}).get(language, text)