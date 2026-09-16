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

import xml.etree.ElementTree as ET  # nosec B405 - only used to build/serialise trusted output XML; untrusted input goes through _fromstring_safe below
import xml.parsers.expat as expat

from .defaults import CEH_TO_SCIMAP, DEFAULT_WEIGHTS, SCIMAP_CLASSES

try:
    # Prefer defusedxml when it happens to be present in the QGIS Python
    # environment, since it is more thoroughly audited than the fallback
    # parser below. The plugin ships as a self-contained zip with no
    # dependency installer, so it cannot be a hard requirement.
    import defusedxml.ElementTree as _safe_ET
except ImportError:
    _safe_ET = None


class XMLSecurityError(ValueError):
    """Raised when parameter-set XML contains a disallowed DOCTYPE/entity."""


def _reject_doctype(*_args, **_kwargs):
    raise XMLSecurityError("DOCTYPE declarations are not allowed in parameter set XML")


def _fromstring_safe(xml_text):
    """Parse untrusted XML, guarding against XXE and entity-expansion attacks.

    Falls back to a hardened stdlib parser (DOCTYPE/entity declarations
    rejected outright) when defusedxml is unavailable, since neither this
    format nor its use case needs a DOCTYPE. Built directly on
    ``xml.parsers.expat`` rather than ``ET.XMLParser``, because the
    C-accelerated ``ET.XMLParser`` does not expose the underlying expat
    parser needed to install these handlers.
    """
    if _safe_ET is not None:
        return _safe_ET.fromstring(xml_text)

    builder = ET.TreeBuilder()
    parser = expat.ParserCreate()
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = _reject_doctype
    parser.EntityDeclHandler = _reject_doctype
    parser.UnparsedEntityDeclHandler = _reject_doctype
    parser.ExternalEntityRefHandler = _reject_doctype

    if isinstance(xml_text, str):
        xml_text = xml_text.encode("utf-8")
    parser.Parse(xml_text, True)
    return builder.close()


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
    root = _fromstring_safe(xml_text)
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
