"""Colour ramps as plain NumPy lookup tables.

``core/styling.py`` renders these ramps inside QGIS by asking ``QgsStyle`` for
them. The web dashboard has no QGIS at hand — it colours pixels in Python when
writing map tiles, and again in JavaScript when draping the 3D terrain — so the
ramps are reproduced here as data.

Each ramp is 33 anchor colours sampled evenly from the matching matplotlib
colormap (the same colormaps the web application feeds to MapServer in
``processing/ows_utils.py``), linearly interpolated up to 256 entries. That is
within 5 of 255 levels of the real colormap everywhere, which is imperceptible
in a continuous ramp and finer than the 12 discrete classes ``core/styling.py``
renders on the QGIS canvas.

Deliberately imports nothing but NumPy, so tile rendering stays testable
outside QGIS and the dashboard's colours provably match the canvas.
"""

import base64

import numpy as np

# Direction already baked in: every tuple runs low -> high as SCIMAP displays it.
# 'spectral' and 'blue-red' are reversed relative to their matplotlib names,
# matching the ``invert`` flags in core/styling.py ``_RAMP_SPECS``.
_ANCHORS = {
    'magma': (
        "000004", "030312", "0a0822", "130d34", "1d1147", "29115a",
        "36106b", "440f76", "51127c", "5d177f", "6a1c81", "762181",
        "832681", "902a81", "9c2e7f", "aa337d", "b73779", "c43c75",
        "d0416f", "dc4869", "e75263", "ef5d5e", "f56b5c", "f9795d",
        "fc8961", "fd9869", "fea772", "feb67c", "fec488", "fed395",
        "fde2a3", "fcf0b2", "fcfdbf",
    ),
    'viridis': (
        "440154", "470d60", "48186a", "482374", "472d7b", "453781",
        "424086", "3e4989", "3b528b", "375b8d", "33638d", "2f6b8e",
        "2c728e", "297a8e", "26828e", "23898e", "21918c", "1f988b",
        "1fa088", "22a785", "28ae80", "32b67a", "3fbc73", "4ec36b",
        "5ec962", "70cf57", "84d44b", "98d83e", "addc30", "c2df23",
        "d8e219", "ece51b", "fde725",
    ),
    'plasma': (
        "0d0887", "220690", "310597", "3f049c", "4c02a1", "5901a5",
        "6600a7", "7201a8", "7e03a8", "8a09a5", "9511a1", "a01a9c",
        "aa2395", "b32c8e", "bc3587", "c43e7f", "cc4778", "d35171",
        "da5a6a", "e06363", "e66c5c", "eb7655", "f0804e", "f58b47",
        "f89540", "fba139", "fdac33", "feb82c", "fdc527", "fcd225",
        "f8df25", "f4ed27", "f0f921",
    ),
    'inferno': (
        "000004", "040312", "0b0724", "150b37", "210c4a", "2f0a5b",
        "3d0965", "4a0c6b", "57106e", "64156e", "71196e", "7d1e6d",
        "8a226a", "972766", "a32c61", "b0315b", "bc3754", "c73e4c",
        "d24644", "db503b", "e45a31", "eb6628", "f1731d", "f68013",
        "f98e09", "fb9d07", "fcac11", "fbbc21", "f9cb35", "f5db4c",
        "f2ea69", "f3f68a", "fcffa4",
    ),
    'cividis': (
        "00224e", "00285b", "002e6a", "053371", "1a386f", "273e6e",
        "32436d", "3b496c", "434e6c", "4b546c", "535a6d", "5a5f6e",
        "61656f", "686a71", "6f7073", "767676", "7d7c78", "848279",
        "8c8878", "938e78", "9b9476", "a39a74", "aba072", "b4a76f",
        "bcae6c", "c4b468", "cdbb63", "d5c25e", "dec958", "e7d150",
        "f0d846", "f9e03a", "fee838",
    ),
    'spectral': (
        "5e4fa2", "525fa9", "4471b2", "3682ba", "3d95b8", "4ea7b0",
        "5eb9a9", "71c6a5", "86cfa5", "9cd7a4", "b1dfa3", "c3e79f",
        "d6ee9b", "e7f59a", "eff9a6", "f7fcb2", "ffffbe", "fff6b0",
        "feec9f", "fee28f", "fed481", "fdc574", "fdb567", "fba35c",
        "f98e52", "f67a49", "f06744", "e75948", "dd4a4c", "d23a4e",
        "c1274a", "af1446", "9e0142",
    ),
    'turbo': (
        "30123b", "392a73", "4040a2", "4456c7", "466be3", "4680f6",
        "4294ff", "37a8fa", "28bceb", "1ccdd8", "18ddc2", "1fe9af",
        "32f298", "4ef97d", "6dfe62", "8bff4b", "a4fc3c", "b9f635",
        "cdec34", "dfdf37", "eecf3a", "f8be39", "fdac34", "fe962b",
        "fb7e21", "f46617", "eb500e", "df3f08", "d02f05", "be2102",
        "a91601", "920b01", "7a0403",
    ),
    'blue-red': (
        "053061", "0d3f76", "15508d", "1e61a5", "2870b1", "337eb8",
        "3e8cbf", "4f9bc7", "68abd0", "81bad8", "98c8e0", "acd2e5",
        "c0dceb", "d2e6f0", "deebf2", "eaf1f5", "f6f7f7", "f9efe9",
        "fbe6da", "fdddcb", "fbceb7", "f8bda1", "f5ac8b", "ef9979",
        "e58368", "dc6e57", "d25849", "c6413e", "bb2a34", "ae172a",
        "960f27", "7f0823", "67001f",
    ),
    # Not a SCIMAP result ramp: the shaded-relief base layer. Warm neutral
    # greys rather than pure grey, so result ramps sit on it without clashing.
    'relief': (
        "2b2823", "393530", "46423c", "525049", "5f5c55", "6b6962",
        "78766e", "84827a", "908f86", "9c9b92", "a8a79e", "b3b3aa",
        "bfbfb6", "cacac1", "d5d5cd", "e0e0d8", "eaeae3",
    ),
}

# The ramps offered for results, matching data/defaults.py COLOUR_RAMPS.
RAMP_NAMES = ('magma', 'viridis', 'plasma', 'inferno', 'cividis',
              'spectral', 'turbo', 'blue-red')
RELIEF_RAMP = 'relief'
DEFAULT_RAMP = 'viridis'

_LUT_CACHE = {}


def _hex_to_rgb(value):
    value = value.lstrip('#')
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def normalise_name(name):
    """Map a display name ("Blue-Red", "Magma") onto a ramp key."""
    key = str(name or '').strip().lower()
    return key if key in _ANCHORS else DEFAULT_RAMP


def ramp_lut(name, samples=256):
    """Return an ``(samples, 3)`` uint8 lookup table running low -> high."""
    key = normalise_name(name)
    if samples == 256:
        cached = _LUT_CACHE.get(key)
        if cached is not None:
            return cached

    anchors = np.array([_hex_to_rgb(h) for h in _ANCHORS[key]], dtype=np.float64)
    positions = np.linspace(0.0, 1.0, len(anchors))
    wanted = np.linspace(0.0, 1.0, samples)
    lut = np.empty((samples, 3), dtype=np.uint8)
    for channel in range(3):
        lut[:, channel] = np.rint(
            np.interp(wanted, positions, anchors[:, channel])
        ).astype(np.uint8)

    if samples == 256:
        lut.flags.writeable = False
        _LUT_CACHE[key] = lut
    return lut


def ramp_hex(name, n=9):
    """Return *n* evenly spaced ``#rrggbb`` swatches for a legend."""
    lut = ramp_lut(name, max(int(n), 2))
    return ['#%02x%02x%02x' % tuple(int(c) for c in row) for row in lut]


def ramps_payload(names=None):
    """Return ``{name: base64(768 bytes RGB)}`` for the dashboard's JS side.

    Plain base64, *not* the deflate-raw encoding ``core/webexport`` uses for
    pixel arrays: a 768-byte lookup table does not compress usefully, and
    keeping it raw means the JS can slice colours straight out of it.
    """
    return {
        name: base64.b64encode(ramp_lut(name).tobytes()).decode('ascii')
        for name in (names or RAMP_NAMES)
    }


def colourise(values, vmin, vmax, name, valid=None):
    """Colour a float array through a ramp, returning ``(h, w, 4)`` uint8 RGBA.

    Values are clipped to ``[vmin, vmax]``; anything outside *valid* (or NaN
    where no mask is supplied) becomes fully transparent.
    """
    lut = ramp_lut(name)
    values = np.asarray(values, dtype=np.float32)

    if valid is None:
        valid = np.isfinite(values)
    else:
        valid = np.asarray(valid, dtype=bool) & np.isfinite(values)

    span = float(vmax) - float(vmin)
    if span <= 0.0:
        span = 1.0
    scaled = (values - float(vmin)) / span
    indices = np.clip(np.rint(np.nan_to_num(scaled, nan=0.0) * 255.0), 0, 255).astype(np.uint8)

    rgba = np.empty(values.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = lut[indices]
    rgba[..., 3] = np.where(valid, 255, 0).astype(np.uint8)
    rgba[~valid, :3] = 0
    return rgba
