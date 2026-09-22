"""Binary encoding for the web dashboard's data payloads.

Split out from ``core/webexport.py`` so it imports nothing but NumPy and the
standard library: these are the routines a browser's correctness depends on
most directly, and they can be tested without GDAL or QGIS installed.

Two conventions here are load-bearing, and both fail silently if broken:

* Arrays are compressed as **raw** deflate, matching both
  ``DecompressionStream('deflate-raw')`` and the vendored tiny-inflate. A zlib
  wrapper would make the dashboard fail to decode anything.
* Layer values are quantised to 0-254 with 255 reserved for "no data", which is
  also the transparent entry in the palette PNGs the map tiles use.
"""

import base64
import json
import struct
import zlib

import numpy as np

# uint8 sentinel for "no data" in quantised layer payloads. Values occupy
# 0..254 so 255 stays free; the JavaScript side applies the same rule.
NODATA_U8 = 255
QUANT_MAX = 254

# Int16 sentinel for missing elevation.
NODATA_I16 = -32768


_B64_CHUNK = 3 * 65536  # multiple of 3 so chunks concatenate without padding


# ── Binary payload encoding ────────────────────────────────────────────────

def _deflate_raw(data, level=9):
    """Compress to a *raw* deflate stream (no zlib or gzip wrapper).

    ``DecompressionStream('deflate-raw')`` and the vendored tiny-inflate both
    expect raw deflate; ``zlib.compress`` would add a 2-byte header that makes
    both of them fail.
    """
    compressor = zlib.compressobj(level, zlib.DEFLATED, -zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


_DTYPE_TAGS = {
    np.dtype(np.uint8): 'u8',
    np.dtype(np.int16): 'i16',
    np.dtype(np.uint16): 'u16',
    np.dtype(np.float32): 'f32',
}


class _Blob:
    """Raw bytes to be base64-streamed into a JS file rather than held as a str.

    A 2048x2048 layer is ~5.6 MB of base64 and an export writes several; keeping
    them out of Python strings keeps peak memory flat.
    """

    __slots__ = ('data',)

    def __init__(self, data):
        self.data = data


def _payload_header(array, extra):
    array = np.ascontiguousarray(array)
    tag = _DTYPE_TAGS.get(array.dtype)
    if tag is None:
        raise ValueError(f"Unsupported payload dtype: {array.dtype}")
    header = {
        'enc': 'deflate-raw-b64',
        'dtype': tag,
        'w': int(array.shape[1]),
        'h': int(array.shape[0]),
    }
    header.update(extra)
    return array, header


def array_payload(array, **extra):
    """Payload dict whose ``data`` is a :class:`_Blob`, for streamed writing."""
    array, header = _payload_header(array, extra)
    header['data'] = _Blob(_deflate_raw(array.tobytes()))
    return header


def encode_payload(array, **extra):
    """Payload dict with ``data`` as a base64 string, for nesting in a larger
    structure (``meta.js``, ``grid.js``) that is serialised in one go."""
    array, header = _payload_header(array, extra)
    header['data'] = base64.b64encode(_deflate_raw(array.tobytes())).decode('ascii')
    return header


def _dump_json(payload):
    return json.dumps(payload, separators=(',', ':'), allow_nan=False)


def write_js_global(path, expression, payload):
    """Write ``<expression> = <payload>;`` as a standalone classic script."""
    blob = None
    if isinstance(payload.get('data'), _Blob):
        blob = payload['data'].data
        payload = {key: value for key, value in payload.items() if key != 'data'}

    with open(path, 'wb') as handle:
        handle.write(f"{expression} = ".encode('utf-8'))
        if blob is None:
            handle.write(_dump_json(payload).encode('utf-8'))
        else:
            body = _dump_json(payload)[:-1]          # everything bar the closing brace
            handle.write(body.encode('utf-8'))
            if len(body) > 1:
                handle.write(b',')
            handle.write(b'"data":"')
            for start in range(0, len(blob), _B64_CHUNK):
                handle.write(base64.b64encode(blob[start:start + _B64_CHUNK]))
            handle.write(b'"}')
        handle.write(b";\n")


def quantise(array, vmin, vmax, valid=None):
    """Scale a float array to 0..254, with :data:`NODATA_U8` for invalid cells.

    254 steps across the 5-95 percentile stretch is finer than any colour ramp
    can show, and keeps a layer at one byte per pixel so the same array can
    serve the 3D drape, the value probe and the charts.
    """
    array = np.asarray(array, dtype=np.float32)
    if valid is None:
        valid = np.isfinite(array)
    else:
        valid = np.asarray(valid, dtype=bool) & np.isfinite(array)

    span = float(vmax) - float(vmin)
    if span <= 0.0:
        span = 1.0

    scaled = (np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0) - float(vmin)) / span
    out = np.clip(np.rint(scaled * QUANT_MAX), 0, QUANT_MAX).astype(np.uint8)
    out[~valid] = NODATA_U8
    return out


def dequantise(codes, vmin, vmax):
    """Inverse of :func:`quantise`; mirrors the JavaScript implementation."""
    codes = np.asarray(codes)
    values = float(vmin) + (codes.astype(np.float32) / QUANT_MAX) * (float(vmax) - float(vmin))
    return np.where(codes == NODATA_U8, np.nan, values)


def elevation_payload(array, valid=None):
    """Encode elevation as Int16 centimetres-or-better, plus its range.

    Int16 with an explicit scale keeps a 1024x1024 DEM at 2 MB before
    compression while holding better than 0.1 m precision for any real
    catchment, and avoids shipping Float32 the terrain mesh cannot use anyway.
    """
    array = np.asarray(array, dtype=np.float64)
    if valid is None:
        valid = np.isfinite(array)
    else:
        valid = np.asarray(valid, dtype=bool) & np.isfinite(array)

    if not valid.any():
        raise ValueError("The DEM has no valid cells over the dashboard extent.")

    zmin = float(array[valid].min())
    zmax = float(array[valid].max())
    span = max(zmax - zmin, 1e-6)

    # Int16 has 32767 usable positive steps; spread the real range across them.
    scale = span / 32000.0
    codes = np.full(array.shape, NODATA_I16, dtype=np.int16)
    codes[valid] = np.rint((array[valid] - zmin) / scale).astype(np.int16)

    return encode_payload(
        codes, scale=scale, offset=zmin, nodata=NODATA_I16,
        min=zmin, max=zmax,
    )


# ── PNG encoding ───────────────────────────────────────────────────────────

def _png_chunk(tag, data):
    return (struct.pack('>I', len(data)) + tag + data
            + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))


def encode_png_rgba(rgba, level=6):
    """Encode an ``(h, w, 4)`` uint8 array as PNG bytes.

    Hand-rolled rather than routed through GDAL's PNG driver: the driver is
    CreateCopy-only, its availability depends on how GDAL was built, and it
    cannot be tested without a GDAL install. This needs nothing but zlib, and a
    dashboard export writes thousands of tiles, so the encoder is on the hot
    path and worth keeping simple and predictable.

    Uses filter type 0 (None) on every row. Map tiles are mostly flat colour or
    smooth gradients, where filtering buys little, and skipping the per-row
    filter search keeps tile writing fast.
    """
    rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError(f"Expected an (h, w, 4) RGBA array, got {rgba.shape}")
    height, width = rgba.shape[:2]

    # Prefix every scanline with its filter byte in one allocation.
    raw = np.zeros((height, width * 4 + 1), dtype=np.uint8)
    raw[:, 1:] = rgba.reshape(height, width * 4)

    return b''.join((
        b'\x89PNG\r\n\x1a\n',
        _png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)),
        _png_chunk(b'IDAT', zlib.compress(raw.tobytes(), level)),
        _png_chunk(b'IEND', b''),
    ))


def encode_png_indexed(codes, lut, level=6):
    """Encode a uint8 index array as a *palette* PNG (colour type 3).

    A ramp-coloured tile is by construction 256 colours drawn from a lookup
    table, so an indexed PNG stores exactly the same image at one byte per pixel
    instead of four — roughly a 5x saving on a real pyramid, which is the
    difference between a dashboard you can email and one you cannot.

    :data:`NODATA_U8` (255) is the transparent palette entry, which is precisely
    what :func:`quantise` already emits, so no extra mask is needed.
    """
    codes = np.ascontiguousarray(codes, dtype=np.uint8)
    if codes.ndim != 2:
        raise ValueError(f"Expected a 2-D index array, got {codes.shape}")
    height, width = codes.shape

    palette = np.zeros((256, 3), dtype=np.uint8)
    palette[:len(lut)] = lut[:256]
    # Every index is opaque except the nodata sentinel.
    alpha = np.full(NODATA_U8 + 1, 255, dtype=np.uint8)
    alpha[NODATA_U8] = 0

    raw = np.zeros((height, width + 1), dtype=np.uint8)
    raw[:, 1:] = codes

    return b''.join((
        b'\x89PNG\r\n\x1a\n',
        _png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 3, 0, 0, 0)),
        _png_chunk(b'PLTE', palette.tobytes()),
        _png_chunk(b'tRNS', alpha.tobytes()),
        _png_chunk(b'IDAT', zlib.compress(raw.tobytes(), level)),
        _png_chunk(b'IEND', b''),
    ))


def write_png_indexed(path, codes, lut, level=6):
    """Write a uint8 index array to *path* as a palette PNG."""
    with open(path, 'wb') as handle:
        handle.write(encode_png_indexed(codes, lut, level))


def write_png_rgba(path, rgba, level=6):
    """Write an ``(h, w, 4)`` uint8 array to *path* as a PNG."""
    with open(path, 'wb') as handle:
        handle.write(encode_png_rgba(rgba, level))


def png_data_uri(rgba, level=6):
    """Encode RGBA as a ``data:`` URI, safe to use as a WebGL texture."""
    return 'data:image/png;base64,' + base64.b64encode(encode_png_rgba(rgba, level)).decode('ascii')


TRANSPARENT_PIXEL_URI = png_data_uri(np.zeros((1, 1, 4), dtype=np.uint8))
