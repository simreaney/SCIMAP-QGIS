"""Parameter-set XML import/export.

Matches the format used by the SCIMAP web application's Parameters workspace
(``callbacks/parameter_callbacks.py``), so parameter sets exported there load
directly into the plugin and vice versa::

    <parameterSet>
      <name>My weights</name>
      <values>
        <value lc_id="1">0.2</value>
        ...
      </values>
    </parameterSet>
"""

import xml.etree.ElementTree as ET

from .defaults import CEH_TO_SCIMAP, DEFAULT_WEIGHTS, SCIMAP_CLASSES


def export_weights(weights, name="SCIMAP parameter set"):
    """Serialise a ``{scimap_class_id: risk_value}`` dict to an XML string."""
    root = ET.Element("parameterSet")
    ET.SubElement(root, "name").text = str(name)
    values_el = ET.SubElement(root, "values")
    for class_id, _label in SCIMAP_CLASSES:
        value_el = ET.SubElement(values_el, "value")
        value_el.set("lc_id", str(class_id))
        value_el.text = str(float(weights.get(class_id, DEFAULT_WEIGHTS.get(class_id, 0.5))))
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def write_weights(path, weights, name="SCIMAP parameter set"):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(export_weights(weights, name))


def parse_weights(xml_text):
    """Parse parameter-set XML into ``(name, {scimap_class_id: risk_value})``.

    Accepts both SCIMAP-class XML (ids 1-7) and legacy CEH-class XML (ids 1-23),
    remapping the latter. Where several CEH classes collapse onto one SCIMAP
    class the last value wins, matching the web application. Classes absent from
    the file fall back to the SCIMAP defaults so the result is always complete.
    """
    root = ET.fromstring(xml_text)
    name = root.findtext("name") or "Imported parameter set"

    valid_ids = {class_id for class_id, _ in SCIMAP_CLASSES}
    merged = {}
    values_el = root.find("values")
    for value_el in (values_el.findall("value") if values_el is not None else []):
        try:
            lc_id = int(value_el.get("lc_id", 0))
            risk_value = float((value_el.text or "").strip())
        except (TypeError, ValueError):
            continue

        # Accept both SCIMAP-class XML and legacy CEH-class XML.
        mapped_id = lc_id if lc_id in valid_ids else CEH_TO_SCIMAP.get(lc_id)
        if mapped_id is None or mapped_id not in valid_ids:
            continue
        merged[mapped_id] = risk_value

    weights = {
        class_id: float(merged.get(class_id, DEFAULT_WEIGHTS.get(class_id, 0.5)))
        for class_id, _label in SCIMAP_CLASSES
    }
    return name.strip(), weights


def read_weights(path):
    with open(path, "r", encoding="utf-8") as handle:
        return parse_weights(handle.read())
