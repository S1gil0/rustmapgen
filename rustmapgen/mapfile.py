"""A Rust map file held whole: every prefab, path and layer, and a writer that adds prefabs.

File layout (the game's `WorldSerialization`): uint32 version (9 or 10), for version 10 an int64
timestamp (unix milliseconds), then the stream of the game's LZ4 writer to the end of the file:
chunks of `varint flags, varint length, [varint packed length when flags & 1], bytes`, each an
independent LZ4 block of at most 1 MiB. The chunks joined are one protobuf `WorldData`:

    WorldData   1 size (varint)   2 maps (MapData, repeated)   3 prefabs (PrefabData, repeated)   4 paths (PathData, repeated)
    MapData     1 name (string)   2 data (bytes)
    PrefabData  1 category (string)   2 id (varint)   3 position   4 rotation   5 scale (VectorData)
    PathData    1 name (string)   5 width (float)   15 nodes (VectorData, repeated)   and more this reader skips
    VectorData  1 x   2 y   3 z (float; a component that is 0 is left out)

The layers (copied as they are, little-endian, row z then x):

    terrain   int16  res*res        the bare terrain
    height    int16  res*res        the same with the top of every cliff added: the ground one stands on
    water     int16  res*res        surface of rivers and lakes; 0 where there is none
    splat     uint8  8*res2*res2    weight of each ground layer
    biome     uint8  5*res2*res2    weight of each biome
    topology  int32  res2*res2      bit mask of what each cell is (ocean, road, beach, ...)
    alpha     uint8  res2*res2      0 where the terrain has a hole (cave and tunnel entrances)

A height sample v is `v / 32766` of the terrain's height; the terrain is 1000 m tall and starts at
y = -500, so sea level is v = 16383.

Writing never re-encodes what the game wrote. A saved file is the original `WorldData` bytes with
the new `PrefabData` messages appended: protobuf reads a repeated field wherever its entries
stand, so the layers, the paths and the prefabs already there stay byte for byte as they were.
"""
import hashlib
import json
import os
import struct
import time

import lz4.block
import numpy as np

TERRAIN_HEIGHT = 1000.0
TERRAIN_BASE = -500.0
SHORT_TO_FLOAT = 1.0 / 32766.0
VERSIONS = (9, 10)
CURRENT_VERSION = 10
CHUNK = 1 << 20          # the game's LZ4 writer makes blocks of at most 1 MiB
# What a file may say it holds, checked before anything is set aside for it. A 4250 map holds about
# 180 MB, 4 times its file; LZ4 cannot pack a block more than 255 times. The largest map the game
# makes (6000) holds well under 1 GB.
MAX_BLOCK = 16 * CHUNK
MAX_WORLD = 2 << 30
MAX_BLOCKS = 1 << 16    # a 6000 map has under 400
# name -> (numpy type, layers); the resolution follows from the length.
LAYERS = {'terrain': ('<i2', 1), 'height': ('<i2', 1), 'water': ('<i2', 1), 'splat': ('u1', 8),
          'biome': ('u1', 5), 'topology': ('<i4', 1), 'alpha': ('u1', 1)}
_FLOAT = struct.Struct('<f')


def prefab_id(path):
    """The number a map stores for a prefab path (the game's `StringEx.ManifestHash`)."""
    return struct.unpack('<I', hashlib.md5(path.lower().encode('utf-8')).digest()[:4])[0]


def varint(buf, pos):
    result = 0
    shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, pos
        shift += 7


def raw_fields(buf, pos, end):
    """Yield (field number, wire type, value); a length-delimited value is its (start, end)."""
    while pos < end:
        key, pos = varint(buf, pos)
        wire = key & 7
        if wire == 0:
            value, pos = varint(buf, pos)
        elif wire == 2:
            length, pos = varint(buf, pos)
            value = (pos, pos + length)
            pos += length
        elif wire == 5:
            value = buf[pos:pos + 4]
            pos += 4
        elif wire == 1:
            value = buf[pos:pos + 8]
            pos += 8
        else:
            raise ValueError(f'wire type {wire} at byte {pos}')
        yield key >> 3, wire, value


def _vector(buf, start, end):
    out = [0.0, 0.0, 0.0]
    for number, wire, value in raw_fields(buf, start, end):
        if wire == 5 and 1 <= number <= 3:
            out[number - 1] = _FLOAT.unpack(value)[0]
    return tuple(out)


def _text(buf, span):
    return bytes(buf[span[0]:span[1]]).decode('utf-8', 'replace')


def unpack(raw, path=''):
    """Header and LZ4 chunks of a map file -> (version, timestamp, the protobuf bytes)."""
    version = struct.unpack_from('<I', raw, 0)[0] if len(raw) >= 4 else None
    if version not in VERSIONS:
        raise ValueError(f'{path}: not a Rust map (version {version})')
    pos = 4
    timestamp = 0
    chunks = []
    total = 0
    end = len(raw)
    try:
        if version == 10:
            timestamp = struct.unpack_from('<q', raw, pos)[0]
            pos += 8
        while pos < end:
            flags, pos = varint(raw, pos)
            length, pos = varint(raw, pos)
            packed = length
            if flags & 1:                       # compressed
                packed, pos = varint(raw, pos)
            if flags >> 2:                      # several passes: the game refuses them too
                raise ValueError(f'{path}: LZ4 chunk with multiple passes')
            if packed > length or pos + packed > end:
                raise IndexError
            if length > MAX_BLOCK or flags & 1 and length > 255 * packed + 64 or total + length > MAX_WORLD:
                raise ValueError(f'{path}: a block says it holds {length} bytes, more than a Rust map can')
            if len(chunks) >= MAX_BLOCKS:
                raise ValueError(f'{path}: more than {MAX_BLOCKS} blocks, more than a Rust map has')
            chunks.append((flags & 1, pos, packed, total, length))
            pos += packed
            total += length
    except (IndexError, struct.error):
        raise ValueError(f'{path}: truncated (the file was still being written?)') from None
    out = bytearray(total)
    for compressed, start, packed, at, length in chunks:
        piece = raw[start:start + packed]
        if compressed:
            piece = lz4.block.decompress(piece, uncompressed_size=length)
            if len(piece) != length:
                raise ValueError(f'{path}: LZ4 chunk of {len(piece)} bytes, {length} expected')
        out[at:at + length] = piece
    return version, timestamp, out


def _varint_bytes(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _float_field(number, value):
    return _varint_bytes(number << 3 | 5) + struct.pack('<f', value)


def _vector_field(number, vec):
    # The game leaves out a component that is 0.
    body = b''.join(_float_field(index + 1, float(value)) for index, value in enumerate(vec) if float(value) != 0.0)
    return _varint_bytes(number << 3 | 2) + _varint_bytes(len(body)) + body


def encode_prefab(category, ident, position, rotation, scale):
    """One `PrefabData` as the `prefabs` field of a `WorldData`."""
    name = category.encode('utf-8')
    body = (_varint_bytes(1 << 3 | 2) + _varint_bytes(len(name)) + name + _varint_bytes(2 << 3) + _varint_bytes(int(ident))
            + _vector_field(3, position) + _vector_field(4, rotation) + _vector_field(5, scale))
    return _varint_bytes(3 << 3 | 2) + _varint_bytes(len(body)) + body


class MapFile:
    """`prefabs` are plain rows (category, id, position, rotation, scale); a path keeps its nodes."""

    def __init__(self, path, paths=True):
        self.path = path
        with open(path, 'rb') as handle:
            raw = handle.read()
        self.version, self.timestamp, self.data = unpack(raw, path)
        del raw
        buf = memoryview(self.data)
        self.size = 0
        self.spans = {}                 # layer name -> (start, end) in self.data
        self.category = []
        self.paths = []                 # dicts: name, width, nodes [(x, y, z), ...]
        ids, positions, rotations, scales = [], [], [], []
        for number, wire, value in raw_fields(buf, 0, len(buf)):
            if number == 1 and wire == 0:
                self.size = value
            elif number == 2 and wire == 2:
                name, span = '', (0, 0)
                for sub, sub_wire, sub_value in raw_fields(buf, *value):
                    if sub == 1 and sub_wire == 2:
                        name = _text(buf, sub_value)
                    elif sub == 2 and sub_wire == 2:
                        span = sub_value
                self.spans[name] = span
            elif number == 3 and wire == 2:
                category, ident = '', 0
                position = rotation = scale = (0.0, 0.0, 0.0)
                for sub, sub_wire, sub_value in raw_fields(buf, *value):
                    if sub == 1 and sub_wire == 2:
                        category = _text(buf, sub_value)
                    elif sub == 2 and sub_wire == 0:
                        ident = sub_value
                    elif sub == 3 and sub_wire == 2:
                        position = _vector(buf, *sub_value)
                    elif sub == 4 and sub_wire == 2:
                        rotation = _vector(buf, *sub_value)
                    elif sub == 5 and sub_wire == 2:
                        scale = _vector(buf, *sub_value)
                self.category.append(category)
                ids.append(ident)
                positions.append(position)
                rotations.append(rotation)
                scales.append(scale)
            elif number == 4 and wire == 2 and paths:
                name, width, nodes = '', 0.0, []
                for sub, sub_wire, sub_value in raw_fields(buf, *value):
                    if sub == 1 and sub_wire == 2:
                        name = _text(buf, sub_value)
                    elif sub == 5 and sub_wire == 5:
                        width = _FLOAT.unpack(sub_value)[0]
                    elif sub == 15 and sub_wire == 2:
                        nodes.append(_vector(buf, *sub_value))
                self.paths.append({'name': name, 'width': width, 'nodes': nodes})
        del buf
        if not self.size:
            raise ValueError(f'{path}: no world size')
        self.id = np.array(ids, dtype=np.uint32)
        self.position = np.array(positions, dtype=np.float64).reshape(-1, 3)
        self.rotation = np.array(rotations, dtype=np.float64).reshape(-1, 3)    # Unity euler angles, degrees
        self.scale = np.array(scales, dtype=np.float64).reshape(-1, 3)
        self.added = []                 # encoded PrefabData fields, written after the original bytes

    def layer(self, name):
        """One layer as an array [z, x] (or [layer, z, x]); None when the file has none.

        The number of layers of `splat` and `biome` follows from the bytes: a map made by an old
        editor has four biomes where the game writes five.
        """
        if name not in self.spans or name not in LAYERS:
            return None
        kind, count = LAYERS[name]
        start, end = self.spans[name]
        item = np.dtype(kind).itemsize
        cells = (end - start) // item
        if count > 1:
            count = next((c for c in (count, count - 1, count + 1) if c and cells % c == 0
                          and int(round((cells // c) ** 0.5)) ** 2 == cells // c), count)
        res = int(round((cells // count) ** 0.5))
        if res * res * count * item != end - start:
            raise ValueError(f'{self.path}: layer {name!r} of {end - start} bytes is not square')
        shape = (count, res, res) if count > 1 else (res, res)
        return np.frombuffer(self.data, dtype=kind, count=count * res * res, offset=start).reshape(shape)

    def metres(self, name='height'):
        """A height layer in metres above sea level."""
        return self.layer(name).astype(np.float32) * (SHORT_TO_FLOAT * TERRAIN_HEIGHT) + TERRAIN_BASE

    def clear_topology(self, bits):
        """Take topology bits off every cell, in place (the layer keeps its size). Returns the cells that had one."""
        if 'topology' not in self.spans:
            return 0
        if not isinstance(self.data, bytearray):
            self.data = bytearray(self.data)
        kind, _ = LAYERS['topology']
        start, end = self.spans['topology']
        cells = np.frombuffer(self.data, dtype=kind, count=(end - start) // np.dtype(kind).itemsize, offset=start)
        mask = np.array(bits, dtype=kind)
        had = int(np.count_nonzero(cells & mask))
        cells &= ~mask
        return had

    def add_prefab(self, category, ident, position, rotation, scale=(1.0, 1.0, 1.0)):
        self.added.append(encode_prefab(category, ident, position, rotation, scale))

    def save(self, path, timestamp=None):
        """Write the map with the added prefabs, as version 10. Returns the number of bytes."""
        world = bytes(self.data) + b''.join(self.added)
        out = [struct.pack('<I', CURRENT_VERSION),
               struct.pack('<q', int(time.time() * 1000) if timestamp is None else int(timestamp))]
        for start in range(0, len(world), CHUNK):
            piece = world[start:start + CHUNK]
            packed = lz4.block.compress(piece, store_size=False, mode='high_compression')
            if len(packed) >= len(piece):       # a block that does not shrink is stored as it is
                out += [_varint_bytes(2), _varint_bytes(len(piece)), piece]
            else:
                out += [_varint_bytes(3), _varint_bytes(len(piece)), _varint_bytes(len(packed)), packed]
        blob = b''.join(out)
        partial = path + '.partial'
        with open(partial, 'wb') as handle:
            handle.write(blob)
        os.replace(partial, path)
        return len(blob)


def load_names(path):
    """{prefab id: path} of every prefab of the game (the table the mod writes, prefabs.json)."""
    with open(path) as handle:
        return {int(key): value for key, value in json.load(handle).items()}


__all__ = ['MapFile', 'encode_prefab', 'load_names', 'prefab_id']
