#!/bin/sh
# Re-download the vendored browser libraries. See vendor/VENDOR.md for why these
# exact versions are pinned -- three.js r147 in particular is the last release
# that ships the classic UMD build a file:// dashboard can load.
set -e
cd "$(dirname "$0")/vendor"

L=https://unpkg.com/leaflet@1.9.4/dist
curl -sSfL -o leaflet-1.9.4/leaflet.js  "$L/leaflet.js"
curl -sSfL -o leaflet-1.9.4/leaflet.css "$L/leaflet.css"
for img in marker-icon.png marker-icon-2x.png marker-shadow.png layers.png layers-2x.png; do
    curl -sSfL -o "leaflet-1.9.4/images/$img" "$L/images/$img"
done

T=https://unpkg.com/three@0.147.0
curl -sSfL -o three-r147/three.min.js    "$T/build/three.min.js"
curl -sSfL -o three-r147/OrbitControls.js "$T/examples/js/controls/OrbitControls.js"
for f in LineSegmentsGeometry LineSegments2 LineGeometry Line2 LineMaterial; do
    curl -sSfL -o "three-r147/lines/$f.js" "$T/examples/js/lines/$f.js"
done
for f in GLTFExporter STLExporter OBJExporter; do
    curl -sSfL -o "three-r147/exporters/$f.js" "$T/examples/js/exporters/$f.js"
done

# Upstream tiny-inflate is CommonJS; re-apply the browser-global wrapper.
curl -sSfL -o tiny-inflate/tiny-inflate.js https://unpkg.com/tiny-inflate@1.0.3/index.js
python3 - <<'PY'
p = 'tiny-inflate/tiny-inflate.js'
src = open(p).read()
open(p, 'w').write(
    "/* tiny-inflate 1.0.3 (MIT, Devon Govett) - wrapped as a browser global.\n"
    "   Consumes RAW deflate, matching Python zlib.compressobj(-MAX_WBITS) and\n"
    "   DecompressionStream('deflate-raw'). */\n"
    "(function (root) {\n  var module = { exports: {} };\n\n"
    + src +
    "\n\n  root.tinyInflate = module.exports;\n})(typeof window !== 'undefined' ? window : this);\n"
)
PY
echo "Vendor files refreshed. Verify against the checksums in vendor/VENDOR.md."
