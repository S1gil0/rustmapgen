"""What the 3D view of one map downloads, made from its final.map the first time it is asked for.

Beside the map's other files in data/maps/<name>/ (the panel serves them under /3d/data/):

    3d_meta.json      size, samples, monuments with their names, roads, rails, rivers and power lines, where
                      each monument model stands (`placed`) and the kinds of scenery
    3d_heights.png    the ground (cliff tops included) at up to 2049 x 2049 samples, one corner each, row z
                      then x from the south-west: the game's int16 + 32768, high byte in red, low byte in green
    3d_rivers.png     the same grid: depth of a river or lake over the ground in cm + 32768 (red high, green
                      low), negative where its surface runs on under a bank, 0 where there is none
    3d_ground.webp    colour of the ground from the eight ground layers, cell-centred, as heights rows
    3d_mix.webp       per cell: plants (red), rock (green), smooth snow or sand (blue), for the shader
    3d_trees.bin      8 bytes a tree: x, z (uint16 over the square), kind, turn, size in %, 0
    3d_scenery.bin    44 bytes a piece of scenery (models3d.SCENERY_FOLDERS): uint16 kind (its number in
                      `scenery_kinds`), uint16 0, float32 x, y, z, float32 quaternion x, y, z, w, float32
                      scale x, y, z, in viewer space (game x, y, -z)

The map file holds no trees (the game grows them when a server first starts), so the trees are scattered
here from the forest layer and the biomes, the same every time for a map: where they stand, and their
shapes, are ours. The models (models3d.py) are not named here: the panel gives the page the version made
for the installed game, so a map baked once keeps its view across game updates.
"""
import json
import math
import os
import struct

import numpy as np
from PIL import Image

from . import analyze, library, models3d, paths, render
from .mapfile import SHORT_TO_FLOAT, TERRAIN_BASE, TERRAIN_HEIGHT, MapFile, load_names

FILES = ('3d_meta.json', '3d_heights.png', '3d_rivers.png', '3d_ground.webp', '3d_mix.webp', '3d_trees.bin', '3d_scenery.bin')
SAMPLES = 2049
TO_METRES = SHORT_TO_FLOAT * TERRAIN_HEIGHT
# The page's ground layers and what each is made of (plants, rock, smooth), in the game's order: dirt, snow,
# sand, rock, grass, forest, stones, gravel.
LAYERS = ['#75604a', '#d3dade', '#cdb785', '#827e76', '#6d7742', '#4c5c32', '#8a8579', '#908a7b']
VEGETATION = [0, 0, 0, 0, 1, 1, 0, 0]
ROCKY = [0.2, 0, 0, 1, 0, 0, 0.8, 0.6]
SOFT = [0, 1, 0.8, 0, 0, 0, 0, 0]
# Grass and forest as each biome colours them (arid, temperate, tundra, arctic, jungle).
GREENS = {'grass': ['#8f8a4f', '#6d7742', '#6f6e48', '#7d806c', '#557a35'],
          'forest': ['#6b6a3d', '#4c5c32', '#4d5534', '#59604d', '#35562a']}
# Trees by biome: (kind, share). Kinds are the page's (viewer/nature.js).
TREES = {0: (('palm', 0.18), ('cactus', 0.47), ('bush', 0.35)), 1: (('oak', 0.45), ('birch', 0.25), ('pine', 0.3)),
         2: (('pine', 0.6), ('birch', 0.4)), 3: (('pine', 1.0),), 4: (('jungle', 0.6), ('palm', 0.4))}
KINDS = ['pine', 'oak', 'birch', 'palm', 'cactus', 'jungle', 'bush']
TREE_CELL_M = 7.0
# Topology (TerrainTopology): no tree on these.
NO_TREE = 2 | 16 | 128 | 1024 | 2048 | 4096 | 16384 | 65536 | 524288 | 1048576 | 2097152


def linear(hex_colour):
    rgb = np.array([int(hex_colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5)])
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def srgb(values):
    values = np.clip(values, 0.0, 1.0)
    return np.where(values <= 0.0031308, values * 12.92, 1.055 * values ** (1 / 2.4) - 0.055)


def png(path, rgb):
    Image.fromarray(np.ascontiguousarray(rgb), 'RGB').save(path, format='PNG', optimize=True)


def pack16(values):
    """uint16 grid -> RGB bytes (red high, green low)."""
    out = np.zeros(values.shape + (3,), dtype=np.uint8)
    out[..., 0] = values >> 8
    out[..., 1] = values & 255
    return out


def surface(world, xs, zs):
    """Surface of the river or lake at arrays of game positions, in metres; NaN where the map has none.

    The map marks "no water" with 0, which is 500 m under the sea and must never be blended with a real
    surface: the answer is the mean of the samples around that hold water, weighted as in a bilinear lookup."""
    grid = world.layer('water')
    if grid is None:
        return np.full(np.broadcast(np.asarray(xs), np.asarray(zs)).shape, np.nan)
    last = grid.shape[0] - 1
    scale, half = last / float(world.size), world.size / 2.0
    fx = np.clip((np.asarray(xs, dtype=np.float64) + half) * scale, 0.0, last)
    fz = np.clip((np.asarray(zs, dtype=np.float64) + half) * scale, 0.0, last)
    ix = np.minimum(fx.astype(np.intp), last - 1)
    iz = np.minimum(fz.astype(np.intp), last - 1)
    tx, tz = fx - ix, fz - iz
    total = np.zeros(np.broadcast(fx, fz).shape)
    weight = np.zeros(total.shape)
    for dz, dx, share in ((0, 0, (1.0 - tx) * (1.0 - tz)), (0, 1, tx * (1.0 - tz)), (1, 0, (1.0 - tx) * tz), (1, 1, tx * tz)):
        raw = grid[iz + dz, ix + dx].astype(np.float64)
        held = np.where(raw != 0, share, 0.0)
        total += raw * held
        weight += held
    out = np.full(total.shape, np.nan)
    np.divide(total, weight, out=out, where=weight > 1e-9)
    return TERRAIN_BASE + out * TO_METRES


def rivers(world, heights, xs, zs):
    """Depth of rivers and lakes over every sample."""
    level_of = surface(world, xs, zs)
    with np.errstate(invalid='ignore'):
        level_of[level_of <= 0.05] = np.nan               # a river mouth: the sea covers it
        wet = level_of > heights
    n = heights.shape[0]
    level = np.pad(np.where(wet, level_of, 0.0), 1)
    count = np.pad(wet.astype(np.float32), 1)
    around = [(dj, di) for dj in (0, 1, 2) for di in (0, 1, 2) if (dj, di) != (1, 1)]
    near = sum(count[dj:dj + n, di:di + n] for dj, di in around)
    mean = sum(level[dj:dj + n, di:di + n] for dj, di in around) / np.maximum(near, 1.0)
    bank = np.isnan(level_of) & (near > 0) & (heights >= mean - 0.3)
    level_of[bank] = mean[bank]
    depth = level_of - heights
    keep = wet | (~np.isnan(depth) & (near > 0))
    out = np.zeros(heights.shape, dtype=np.uint16)
    out[keep] = (np.clip(np.round(depth[keep] * 100), -32767, 32767) + 32768).astype(np.uint16)
    return out, int(keep.sum())


def ground(world):
    """Colour and makeup of the ground, cell by cell, from the eight layer weights."""
    weights = world.layer('splat').astype(np.float32)                 # 8, r, r
    r = weights.shape[1]
    share = weights / np.maximum(weights.sum(axis=0), 1.0)
    biome = world.layer('biome')
    pick = np.linspace(0, biome.shape[1] - 1, r).round().astype(np.intp)
    biome = biome[:, pick][:, :, pick].astype(np.float32)
    biome /= np.maximum(biome.sum(axis=0), 1.0)
    kinds = biome.shape[0]
    colour = np.zeros((r, r, 3), dtype=np.float32)
    for index, hex_colour in enumerate(LAYERS[:share.shape[0]]):
        if index in (4, 5):
            name = 'grass' if index == 4 else 'forest'
            colour += share[index][..., None] * sum(biome[b][..., None] * linear(GREENS[name][b]) for b in range(min(kinds, 5)))
        else:
            colour += share[index][..., None] * linear(hex_colour)
    mix = np.stack([sum(share[i] * table[i] for i in range(share.shape[0])) for table in (VEGETATION, ROCKY, SOFT)], axis=-1)
    return (srgb(colour) * 255).round().astype(np.uint8), (np.clip(mix, 0, 1) * 255).round().astype(np.uint8), share, biome


def trees(world, heights, share, biome, size, seed):
    """Trees where the forest layer is: one chance a 7 m cell, kind by biome."""
    cells = int(size // TREE_CELL_M)
    random = np.random.default_rng(seed)
    gx, gz = np.meshgrid(np.arange(cells), np.arange(cells))
    x = (gx + random.random(gx.shape)) * TREE_CELL_M - size / 2
    z = (gz + random.random(gz.shape)) * TREE_CELL_M - size / 2
    r = share.shape[1]
    si = np.clip(((x + size / 2) / size * r).astype(np.intp), 0, r - 1)
    sj = np.clip(((z + size / 2) / size * r).astype(np.intp), 0, r - 1)
    forest = share[5][sj, si]
    grass = share[4][sj, si]
    chance = np.clip((forest - 0.12) / 0.55, 0, 1) * 0.85 + grass * 0.012
    topology = world.layer('topology')
    if topology is not None:
        tr = topology.shape[0]
        ti = np.clip(((x + size / 2) / size * tr).astype(np.intp), 0, tr - 1)
        tj = np.clip(((z + size / 2) / size * tr).astype(np.intp), 0, tr - 1)
        blocked = (topology[tj, ti] & NO_TREE) != 0
    else:
        blocked = np.zeros(chance.shape, dtype=bool)
    n = heights.shape[0]
    hi = np.clip(((x + size / 2) / size * (n - 1)).round().astype(np.intp), 1, n - 2)
    hj = np.clip(((z + size / 2) / size * (n - 1)).round().astype(np.intp), 1, n - 2)
    step = size / (n - 1)
    slope = np.hypot(heights[hj, hi + 1] - heights[hj, hi - 1], heights[hj + 1, hi] - heights[hj - 1, hi]) / (2 * step)
    land = heights[hj, hi] > 1.0
    grow = (random.random(chance.shape) < chance) & ~blocked & land & (slope < 0.75)
    b = np.argmax(biome[:, sj, si], axis=0)
    roll = random.random(chance.shape)
    kind = np.full(chance.shape, KINDS.index('bush'), dtype=np.uint8)
    for code, choices in TREES.items():
        start = 0.0
        for name, part in choices:
            pick = (b == code) & (roll >= start) & (roll < start + part)
            kind[pick] = KINDS.index(name)
            start += part
    # Under the trees of a forest, a bush in about one cell in four.
    understory = grow & (random.random(chance.shape) < 0.22) & (forest > 0.35)
    kind[understory] = KINDS.index('bush')
    keep = grow.ravel()
    out = np.zeros((int(keep.sum()), 8), dtype=np.uint8)
    xs = np.clip((x.ravel()[keep] + size / 2) / size * 65535, 0, 65535).astype('<u2')
    zs = np.clip((z.ravel()[keep] + size / 2) / size * 65535, 0, 65535).astype('<u2')
    out[:, 0:2] = xs.view(np.uint8).reshape(-1, 2)
    out[:, 2:4] = zs.view(np.uint8).reshape(-1, 2)
    out[:, 4] = kind.ravel()[keep]
    out[:, 5] = (random.random(out.shape[0]) * 255).astype(np.uint8)
    out[:, 6] = (72 + random.random(out.shape[0]) * 46).astype(np.uint8)
    return out.tobytes(), {name: int((out[:, 4] == index).sum()) for index, name in enumerate(KINDS)}


def quaternion(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz)


def pose(world, index):
    """Viewer-space position, rotation and scale of one prefab of the map file."""
    x, y, z = (float(v) for v in world.position[index])
    # Unity's Euler angles turn about z, then x, then y; the mirror of z turns the quaternion to (-x, -y, z, w).
    half = [math.radians(float(v)) / 2 for v in world.rotation[index]]
    qx = (math.sin(half[0]), 0.0, 0.0, math.cos(half[0]))
    qy = (0.0, math.sin(half[1]), 0.0, math.cos(half[1]))
    qz = (0.0, 0.0, math.sin(half[2]), math.cos(half[2]))
    q = quaternion(quaternion(qy, qx), qz)
    return (x, y, -z), (-q[0], -q[1], q[2], q[3]), tuple(float(v) for v in world.scale[index])


def catalog_rows():
    with open(paths.package('catalog.json'), encoding='utf-8') as handle:
        return {models3d.PREFABS + row['path']: row for row in json.load(handle)['monuments']}


def placed(world, names, rows):
    """Every monument of the catalog on the map: its prefab's short name, place, turn and size in viewer space."""
    out = []
    for index, ident in enumerate(world.id):
        row = rows.get(names.get(int(ident), ''))
        if row is None:
            continue
        p, q, size = pose(world, index)
        out.append({'short': row['prefab'], 'p': [round(v, 2) for v in p], 'q': [round(v, 6) for v in q], 's': [round(v, 4) for v in size]})
    return out


def scenery(world, names):
    """(kinds, bytes of 3d_scenery.bin) for the scenery the map file stands."""
    kinds, rows = [], []
    for index, ident in enumerate(world.id):
        path = names.get(int(ident), '')
        if not path.startswith(models3d.AUTOSPAWN) or not path.endswith('.prefab'):
            continue
        key = path[len(models3d.AUTOSPAWN):-len('.prefab')]
        if not any((key.rsplit('/', 1)[0] + '/').startswith(top + '/') for top in models3d.SCENERY_FOLDERS):
            continue
        if key not in kinds:
            kinds.append(key)
        p, q, size = pose(world, index)
        rows.append(struct.pack('<HH3f4f3f', kinds.index(key), 0, *p, *q, *size))
    return kinds, b''.join(rows)


def monuments(world, names, rows):
    """The catalog's monuments on the map, with their names, for the labels."""
    out = []
    for index, ident in enumerate(world.id):
        row = rows.get(names.get(int(ident), ''))
        if row is None or row['folder'] in render.HIDDEN:
            continue
        x, y, z = (round(float(v), 1) for v in world.position[index])
        out.append({'label': row['label'], 'prefab': row['prefab'], 'folder': row['folder'], 'x': x, 'y': y, 'z': z,
                    'icon': None, 'minor': row['folder'] in render.MINOR,
                    'rank': render.RANK.index(row['folder']) if row['folder'] in render.RANK else len(render.RANK)})
    return out


def bake(map_path, out, seed=0, names=None):
    """The 3D files of one map file into the folder `out`; returns its 3d_meta.json. 3d_meta.json is written
    last, so a folder with it holds the others."""
    world = MapFile(map_path)
    if names is None:
        names = load_names(library.prefab_table())
    size = float(world.size)
    raw = world.layer('height')
    if raw is None:
        raw = world.layer('terrain')
    every = max(1, (raw.shape[0] - 1) // (SAMPLES - 1))
    raw = raw[::every, ::every]
    n = raw.shape[0]
    heights = raw.astype(np.float32) * TO_METRES + TERRAIN_BASE
    os.makedirs(out, exist_ok=True)
    meta_file = os.path.join(out, '3d_meta.json')
    if os.path.exists(meta_file):
        os.unlink(meta_file)
    png(os.path.join(out, '3d_heights.png'), pack16((raw.astype(np.int32) + 32768).astype(np.uint16)))

    axis = np.linspace(-size / 2, size / 2, n)
    depth, wet = rivers(world, heights, axis[None, :], axis[:, None])
    png(os.path.join(out, '3d_rivers.png'), pack16(depth))

    colour, mix, share, biome = ground(world)
    Image.fromarray(colour, 'RGB').save(os.path.join(out, '3d_ground.webp'), format='WEBP', quality=86, method=4)
    Image.fromarray(mix, 'RGB').save(os.path.join(out, '3d_mix.webp'), format='WEBP', quality=80, method=4)

    tree_bytes, counts = trees(world, heights, share, biome, size, int(seed or 1))
    with open(os.path.join(out, '3d_trees.bin'), 'wb') as handle:
        handle.write(tree_bytes)

    kinds, placed_scenery = scenery(world, names)
    with open(os.path.join(out, '3d_scenery.bin'), 'wb') as handle:
        handle.write(placed_scenery)

    rows = catalog_rows()
    # Roads, rails, rivers and power lines as the map's own pages draw them.
    lines = analyze.describe(world, names)['lines']
    meta = {'size': size, 'samples': n, 'height_low': float(heights.min()), 'height_high': float(heights.max()),
            'layers': int(colour.shape[0]), 'river_samples': wet, 'trees': counts, 'tree_kinds': KINDS,
            'monuments': monuments(world, names, rows), 'placed': placed(world, names, rows), 'scenery_kinds': kinds, 'lines': lines}
    with open(meta_file + '.partial', 'w') as handle:
        json.dump(meta, handle, separators=(',', ':'))
    os.replace(meta_file + '.partial', meta_file)
    return meta
