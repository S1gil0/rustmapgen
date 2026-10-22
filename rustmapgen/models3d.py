"""The models of the 3D view: monuments and scenery, made on this computer from the game RustMapGen installed.

    python -m rustmapgen models-3d

A dedicated server keeps few of the parts the game draws, but every solid collider: the walls, floors,
towers, rocks and cliffs players stand on and bump into. So each model here is a prefab's solid colliders,
coloured by the surface the game gives each one (Metal, Concrete, Wood, ...) with colours of our own. No
texture or drawn model of the game is read. A monument keeps its objects at least MIN_OBJECT_M across
(buildings, masts, walls, cranes), without rubble and litter, within a budget of triangles that grows
with its footprint; each model also has a far version for when the camera is far.

The models are made once for a build of the game (about two minutes and 2.5 GB of memory on a fast
computer) into data/3d-models/<build>-<code>/: by themselves after the game is installed or updated,
and when the panel starts without them (a new RustMapGen, a game updated elsewhere). A try that fails
is kept in failed.json and not made again by itself for that game; Prepare 3D in the panel tries again.
Reading the game's files needs UnityPy, which is installed into RustMapGen's Python the first time
(without the texture and sound decoders it would otherwise bring: nothing here needs them).

Files, as the page reads them: <kind>/<key>.<level>.bin.gz, one a level (near, far), gzipped and
little-endian: uint32 length of a JSON header, the header, padding to 4 bytes, then the level's
positions (int16 x, y, z over the header's box), colours (uint8 r, g, b) and triangles (uint16 or uint32
x 3, as the level's `index` says), each padded to 4 bytes. Positions are in viewer space (game x, y, -z)
around the prefab's own origin. <kind>/index.json lists what was written; <kind>/far.bin.gz holds every
far model of the folder in one file; <kind>/nobuild.json.gz the volumes where the game refuses a base.
"""
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from collections import Counter
from functools import lru_cache

import numpy as np

from . import paths

PREFABS = 'assets/bundled/prefabs/'
AUTOSPAWN = PREFABS + 'autospawn/'
# The scenery drawn, by folder under autospawn/.
SCENERY_FOLDERS = ('decor/cliff_coastal', 'decor/cliff_hills', 'decor/cliff_jutting_arctic', 'decor/coastal_rocks',
                   'decor/coastal_rocks_large', 'decor/coastal_rocks_large_arctic', 'decor/hill_cliffs_small',
                   'decor/v3_arid_rock_formations', 'decor/v3_ice_sheets', 'decor/v3_rock_formations',
                   'decor/v3_rock_formations_large', 'decor/v3_rock_formations_river', 'decor/v3_rock_formations_small',
                   'decor/v3_shore_ice', 'decor/powerline', 'decor/powerline-small', 'decor/busstop', 'power substations',
                   'tunnel-entrance', 'unique_environment')
CONTAINER_M = 40.0          # a node wider than this on the ground is a group of objects, not one
MIN_OBJECT_M = 6.0          # smaller objects of a monument (props) are left out
MIN_PART_M = 0.5
PER_SQUARE_METRE, LEAST, MOST = 2.0, 8000, 150000           # a monument's budget of triangles
PER_METRE, SCENERY_LEAST, SCENERY_MOST = 250, 400, 12000    # a piece of scenery's
FAR_SHARE, FAR_LEAST = 0.15, 60
# Rubble and litter, which thinning turns to shards.
LEFT_OUT = re.compile(r'debris|junk_?pile|rubble|trash|litter|garbage|cracks|biome_visuals|shrub|monumentblocker', re.I)
NOT_SOLID = re.compile(r'prevent|trigger|soft_collider|vehicle_movement', re.I)
# Our colours (sRGB 0..1) for the surface names the game gives its colliders.
SURFACE = {
    'Metal': (0.50, 0.53, 0.56), 'Concrete': (0.68, 0.67, 0.63), 'Rock': (0.50, 0.48, 0.44),
    'Wood': (0.55, 0.42, 0.29), 'Dirt': (0.47, 0.40, 0.31), 'Gravel': (0.56, 0.54, 0.50),
    'SandBag': (0.64, 0.58, 0.44), 'Glass': (0.62, 0.72, 0.78), 'Glass_Small': (0.62, 0.72, 0.78),
    'Cloth': (0.42, 0.47, 0.37),
}
NEUTRAL, ROCK, ICE = (0.62, 0.62, 0.60), (0.52, 0.50, 0.46), (0.84, 0.90, 0.95)
# What reading the game's files needs beyond RustMapGen's own packages, installed on first use without
# their dependencies (UnityPy would bring texture, sound and FMOD decoders; numpy, Pillow and lz4 are there).
# Exact where a package is compiled (wheels for Windows and Linux, Python 3.8 to 3.14), so pip never builds one.
PACKAGES = ('UnityPy==1.25.0', 'brotli==1.2.0', 'attrs>=22.2', 'fsspec>=2023.1')
MODULES = ('gamefiles.py', 'models3d.py', 'catalog.json')
VERSION = re.compile(r'^[0-9]{1,12}-[0-9a-f]{10}$')
FAILED = 'failed.json'


@lru_cache(maxsize=1)
def code():
    """What the models are made with: these files of RustMapGen. Another code makes them again."""
    digest = hashlib.sha256()
    for name in MODULES:
        with open(paths.package(name), 'rb') as handle:
            digest.update(handle.read())
    return digest.hexdigest()[:10]


def version():
    """The models for the installed game and this code, as a folder name; None without a game."""
    from . import runner
    build = runner.game_build()
    return f'{build}-{code()}' if build and str(build).isdigit() else None


def folder(name):
    return paths.data('3d-models', name)


def ready():
    """The version whose models are made, or None."""
    name = version()
    return name if name and os.path.isfile(os.path.join(folder(name), 'done.json')) else None


def failed():
    """Why the models of the installed game could not be made, when a try failed; None otherwise."""
    from . import runner
    record = runner.read_json(paths.data('3d-models', FAILED))
    return record.get('error') if isinstance(record, dict) and record.get('version') == version() else None


def ensure_packages(say=print):
    """UnityPy and the three packages it reads bundles with, into this Python, the first time."""
    try:
        import UnityPy  # noqa: F401
        return
    except ImportError:
        pass
    from . import release
    say('installing UnityPy to read the game files (once)…', flush=True)
    with release._install_lock():
        # No window of its own on Windows: the panel's jobs run without one.
        hidden = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '--no-deps',
                                 *PACKAGES], capture_output=True, text=True, errors='replace', timeout=1800, **hidden)
    if result.returncode:
        raise RuntimeError('the packages for the 3D view did not install, so its monuments and rocks cannot be made '
                           '(the view still shows the ground): ' + (result.stderr or result.stdout).strip()[-300:])
    import importlib
    importlib.invalidate_caches()


# ---- the shapes of a prefab ----

def solid_pieces(game, root):
    """A prefab's solid colliders as pieces, each with the name of its surface."""
    from . import gamefiles as gf
    out = []
    for node in game.walk(root):
        if (not node['active'] and not node['root']) or node['layer'] in (18, 28, 29) or NOT_SOLID.search(node['path']):
            continue
        matrix = node['matrix']
        for kind in ('MeshCollider', 'BoxCollider', 'CapsuleCollider', 'SphereCollider'):
            for component in node['parts'].get(kind, []):
                data = component.read_typetree()
                if data.get('m_IsTrigger') or not data.get('m_Enabled', True):
                    continue
                if kind == 'MeshCollider':
                    mesh = game.deref(component, data['m_Mesh'])
                    if mesh is None:
                        continue
                    piece = gf.piece(game, mesh, matrix)
                elif kind == 'BoxCollider':
                    piece = gf.made_piece(gf.box_shape(gf.vec(data['m_Center']), gf.vec(data['m_Size'])), matrix, 'box collider')
                elif kind == 'CapsuleCollider':
                    radius = data['m_Radius']
                    piece = gf.made_piece(gf.round_shape(gf.vec(data['m_Center']), radius, max(0.0, data['m_Height'] / 2 - radius),
                                                         data['m_Direction'], 4), matrix, 'capsule collider')
                else:
                    piece = gf.made_piece(gf.round_shape(gf.vec(data['m_Center']), data['m_Radius'], 0.0, 1, 6), matrix, 'sphere collider')
                material = game.deref(component, data.get('m_Material'))
                piece['surface'] = material.read_typetree()['m_Name'] if material is not None else ''
                out.append(piece)
    return out


def objects(game, root, pieces):
    """The object each piece belongs to: (number, name, box of the whole object).

    An object is a node of the prefab's tree with everything under it. A node wider than CONTAINER_M on the
    ground is a container (a quarter of the monument, a group of buildings): its children are the objects.
    A mast, however tall, stays one object."""
    from . import gamefiles as gf
    nodes, parent, stack = [], [], []
    at = {}
    for node in game.walk(root):
        depth = node['path'].count('/') if node['path'] else 0
        del stack[depth:]
        parent.append(stack[-1] if stack else -1)
        stack.append(len(nodes))
        nodes.append(node['path'].rsplit('/', 1)[-1] or node['name'])
        at.setdefault(tuple(round(v, 4) for v in node['matrix']), len(nodes) - 1)
    of = [at.get(tuple(round(v, 4) for v in piece['matrix']), 0) for piece in pieces]
    box = [None] * len(nodes)
    for piece, number in zip(pieces, of):
        if piece['box']:
            box[number] = gf.grow(box[number], [piece['box'][:3], piece['box'][3:]])
    children = [[] for _ in nodes]
    for number in range(len(nodes) - 1, 0, -1):
        children[parent[number]].append(number)
        if box[number]:
            up = parent[number]
            box[up] = gf.grow(box[up], [box[number][:3], box[number][3:]])
    owner = list(range(len(nodes)))

    def place(number, mine):
        for below in children[number]:
            owner[below] = mine
            place(below, mine)

    def split(number):
        for child in children[number]:
            b = box[child]
            if b and children[child] and max(b[3] - b[0], b[5] - b[2]) > CONTAINER_M:
                split(child)
            else:
                place(child, child)
    if nodes:
        split(0)
    return [(owner[n], re.sub(r' \(\d+\)$', '', nodes[owner[n]]), box[owner[n]]) for n in of]


def across(piece):
    box = piece['box']
    return max(box[3] - box[0], box[4] - box[1], box[5] - box[2]) if box else 0.0


def gather(game, pieces, colour_of, judge=None):
    """Positions (viewer space), colours and triangles of the pieces kept, and the triangles left out by reason."""
    from . import gamefiles as gf
    positions, colours, triangles = [], [], []
    count = 0
    left = Counter()
    for piece in pieces:
        reason = judge(piece) if judge else None
        if reason:
            left[reason] += piece['tris']
            continue
        if across(piece) < MIN_PART_M:
            left['small'] += piece['tris']
            continue
        data = gf.geometry(game, piece)
        subs = [np.array(s, dtype=np.int64).reshape(-1, 3) for s in data['subs'] if s]
        if not data['positions'] or not subs:
            continue
        m = np.array(piece['matrix'], dtype=np.float64).reshape(3, 4)
        p = np.array(data['positions'], dtype=np.float64) @ m[:, :3].T + m[:, 3]
        p[:, 2] *= -1.0                                     # viewer space
        t = np.concatenate(subs)
        if np.linalg.det(m[:, :3]) < 0:                     # a mirrored object: its faces are turned over already
            t = t[:, [0, 2, 1]]
        positions.append(p)
        colours.append(np.tile(np.array(colour_of(piece), dtype=np.float32), (len(p), 1)))
        triangles.append(t[:, [0, 2, 1]] + count)           # the mirror of z turns the faces over
        count += len(p)
    if not triangles:
        return None, dict(left)
    return (np.concatenate(positions), np.concatenate(colours), np.concatenate(triangles)), dict(left)


def clustered(positions, colours, triangles, cell):
    """Vertices merged on a grid of `cell` metres; triangles that collapse or repeat are dropped."""
    q = np.floor(positions / cell).astype(np.int64)
    _, inverse = np.unique(q, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    n = int(inverse.max()) + 1
    counts = np.bincount(inverse, minlength=n).astype(np.float64)
    merged_p = np.stack([np.bincount(inverse, positions[:, k], n) / counts for k in range(3)], axis=1)
    merged_c = np.stack([np.bincount(inverse, colours[:, k], n) / counts for k in range(3)], axis=1)
    t = inverse[triangles]
    t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]
    if len(t):
        _, first = np.unique(np.sort(t, axis=1), axis=0, return_index=True)
        t = t[np.sort(first)]
    used = np.unique(t)
    remap = np.full(n, -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return merged_p[used], merged_c[used], remap[t]


def thinned(model, limit):
    """The model within `limit` triangles: vertices merged on the finest grid that gets it there."""
    positions, colours, triangles = model
    if len(triangles) <= limit:
        return clustered(positions, colours, triangles, 0.002)
    low, high = 0.002, 12.0
    best = None
    for _ in range(16):
        cell = math.sqrt(low * high)
        result = clustered(positions, colours, triangles, cell)
        if len(result[2]) > limit:
            low = cell
        else:
            high, best = cell, result
    return best if best is not None else clustered(positions, colours, triangles, high)


def monument(game, root):
    pieces = solid_pieces(game, root)
    if not pieces:
        return None
    belongs = dict(zip(map(id, pieces), objects(game, root, pieces)))

    def judge(piece):
        _, name, box = belongs[id(piece)]
        if LEFT_OUT.search(name):
            return 'by_name'
        if not box or max(box[3] - box[0], box[4] - box[1], box[5] - box[2]) < MIN_OBJECT_M:
            return 'small_objects'
        return None

    model, left = gather(game, pieces, lambda p: SURFACE.get(p['surface'], NEUTRAL), judge)
    if model is None:
        return None
    lo, hi = model[0].min(axis=0), model[0].max(axis=0)
    area = max(1.0, (hi[0] - lo[0]) * (hi[2] - lo[2]))
    budget = int(min(MOST, max(LEAST, area * PER_SQUARE_METRE)))
    near = thinned(model, budget)
    far = thinned(near, max(400, int(budget * FAR_SHARE)))
    return [('near', near), ('far', far)], len(model[2]), left


def scenery(game, root, key):
    pieces = solid_pieces(game, root)
    if not pieces:
        return None
    base = ICE if 'ice' in key else ROCK if re.search(r'cliff|rock', key) else None
    model, left = gather(game, pieces, lambda p: base or SURFACE.get(p['surface'], NEUTRAL))
    if model is None:
        return None
    lo, hi = model[0].min(axis=0), model[0].max(axis=0)
    budget = int(min(SCENERY_MOST, max(SCENERY_LEAST, float(max(hi - lo)) * PER_METRE)))
    near = thinned(model, budget)
    far = thinned(near, max(FAR_LEAST, int(budget * FAR_SHARE)))
    return [('near', near), ('far', far)], len(model[2]), left


def no_build(game, root):
    """Where a prefab stops building: its colliders on layer 29 (Prevent Building), triggers too, each with its
    own shape, in viewer space around the prefab's origin. Shapes: {'s': [x, y, z, r]} a sphere; {'b': [x, y,
    z, then three half-axis vectors]} a box; {'c': [x0, y0, z0, x1, y1, z1, r]} a capsule; {'m': {'p': [x, y,
    z, ...], 't': [a, b, c, ...]}} a mesh."""
    from . import gamefiles as gf
    flip = np.array([1.0, 1.0, -1.0])
    out = []
    for node in game.walk(root):
        if node['layer'] != 29 or not (node['active'] or node['root']):
            continue
        m = np.array(node['matrix'], dtype=np.float64).reshape(3, 4)
        scale = np.linalg.norm(m[:, :3], axis=0)
        for kind in ('SphereCollider', 'BoxCollider', 'CapsuleCollider', 'MeshCollider'):
            for component in node['parts'].get(kind, []):
                data = component.read_typetree()
                if not data.get('m_Enabled', True):
                    continue
                centre = m[:, :3] @ np.array(gf.vec(data.get('m_Center', {'x': 0, 'y': 0, 'z': 0}))) + m[:, 3]
                if kind == 'SphereCollider':
                    out.append({'s': [round(float(v), 2) for v in list(centre * flip) + [data['m_Radius'] * scale.max()]]})
                elif kind == 'BoxCollider':
                    axes = [m[:, i] * gf.vec(data['m_Size'])[i] / 2 * flip for i in range(3)]
                    out.append({'b': [round(float(v), 2) for v in np.concatenate([centre * flip] + axes)]})
                elif kind == 'CapsuleCollider':
                    d = data['m_Direction']
                    radius = data['m_Radius'] * max(scale[i] for i in range(3) if i != d)
                    half = max(0.0, data['m_Height'] * scale[d] / 2 - radius)
                    unit = m[:, d] / max(scale[d], 1e-9)
                    ends = [(centre - unit * half) * flip, (centre + unit * half) * flip]
                    out.append({'c': [round(float(v), 2) for v in np.concatenate(ends + [[radius]])]})
                else:
                    mesh = game.deref(component, data['m_Mesh'])
                    if mesh is None:
                        continue
                    shape = gf.geometry(game, gf.piece(game, mesh, node['matrix']))
                    if not shape['positions']:
                        continue
                    p = np.array(shape['positions'], dtype=np.float64) @ m[:, :3].T + m[:, 3]
                    t = [index for subs in shape['subs'] for triangle in subs for index in triangle]
                    out.append({'m': {'p': [round(float(v), 1) for v in (p * flip).ravel()], 't': t}})
    return out


# ---- files ----

def write(target, base, levels):
    """One gzipped file a level; returns the index entry's `box` and `levels`."""
    allp = np.concatenate([p for _, (p, _, _) in levels])
    box = [round(float(v), 3) for v in list(allp.min(axis=0)) + list(allp.max(axis=0))]
    lo = np.array(box[:3], dtype=np.float64)
    span = np.maximum(np.array(box[3:], dtype=np.float64) - lo, 1e-3)
    listed = []
    for name, (p, c, t) in levels:
        quantised = np.clip(np.round((p - lo) / span * 65535 - 32768), -32768, 32767).astype('<i2').tobytes()
        quantised += b'\0' * (-len(quantised) % 4)
        rgb = np.clip(np.round(c * 255), 0, 255).astype(np.uint8).tobytes()
        rgb += b'\0' * (-len(rgb) % 4)
        wide = 2 if len(p) <= 65536 else 4
        index = t.astype('<u2' if wide == 2 else '<u4').tobytes()
        index += b'\0' * (-len(index) % 4)
        level = {'name': name, 'vertices': len(p), 'triangles': len(t), 'index': wide}
        text = json.dumps({'box': box, 'levels': [level]}, separators=(',', ':')).encode()
        text += b' ' * (-(len(text) + 4) % 4)
        file = f'{base}.{name}.bin.gz'
        with open(os.path.join(target, file), 'wb') as handle:
            handle.write(gzip.compress(struct.pack('<I', len(text)) + text + quantised + rgb + index, 9, mtime=0))
        listed.append(dict(level, file=file, bytes=os.path.getsize(os.path.join(target, file))))
    return box, listed


def bundle(target):
    """Every far model of a folder in one file, far.bin.gz, which the page asks for first: gzipped, uint32
    length of a JSON table {key: [offset, length]}, the table padded to 4 bytes, then each far model as its
    own file holds it, gunzipped, from 4-byte offsets."""
    with open(os.path.join(target, 'index.json')) as handle:
        index = json.load(handle)
    table, body = {}, b''
    for key in sorted(index):
        far = next((level for level in index[key]['levels'] if level['name'] == 'far'), None)
        if far is None:
            continue
        with open(os.path.join(target, far['file']), 'rb') as handle:
            model = gzip.decompress(handle.read())
        table[key] = [len(body), len(model)]
        body += model + b'\0' * (-len(model) % 4)
    text = json.dumps(table, separators=(',', ':')).encode()
    text += b' ' * (-(len(text) + 4) % 4)
    with open(os.path.join(target, 'far.bin.gz'), 'wb') as handle:
        handle.write(gzip.compress(struct.pack('<I', len(text)) + text + body, 9, mtime=0))
    return len(table), os.path.getsize(os.path.join(target, 'far.bin.gz'))


def jobs(game):
    """(kind, key, roots) of every monument of the catalog and every piece of scenery of SCENERY_FOLDERS."""
    with open(paths.package('catalog.json'), encoding='utf-8') as handle:
        catalog = json.load(handle)
    by_short = {}
    for name, roots in game.roots.items():
        by_short.setdefault(name.rsplit('/', 1)[-1].replace('.prefab', ''), roots)
    out = []
    for row in catalog['monuments']:
        out.append(('monuments', row['prefab'], game.roots.get(PREFABS + row['path']) or by_short.get(row['prefab'])))
    for name in sorted(game.roots):
        if not (name.startswith(AUTOSPAWN) and name.endswith('.prefab')):
            continue
        rest = name[len(AUTOSPAWN):-len('.prefab')]
        if any((rest.rsplit('/', 1)[0] + '/').startswith(top + '/') for top in SCENERY_FOLDERS):
            out.append(('scenery', rest, game.roots[name]))
    return out


def build(say=print):
    """The models of the installed game into data/3d-models/<version>/, unless they are there. Returns the version.
    A failure is kept in failed.json (a busy game is not one: another job holds it, and the models wait)."""
    from . import runner
    name = version()
    if not name:
        raise RuntimeError('install the game first: the models are made from its files')
    try:
        made = _build(name, say)
    except runner.Busy:
        raise
    except Exception as error:
        os.makedirs(paths.data('3d-models'), exist_ok=True)
        with open(paths.data('3d-models', FAILED), 'w') as handle:
            json.dump({'version': name, 'error': str(error)[:400],
                       'when': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, handle)
        raise
    if os.path.exists(paths.data('3d-models', FAILED)):
        os.unlink(paths.data('3d-models', FAILED))
    return made


def after_game_change(say=print):
    """The models, right after the game was installed or updated, so that the 3D view is whole without asking.
    A failure is said, and does not undo the install or the update."""
    try:
        build(say)
    except Exception as error:
        say(f'the 3D models were not made: {error} (the panel\'s Prepare 3D tries again)', flush=True)


def _build(name, say):
    from . import gamefiles, runner
    if ready() == name:
        say(f'the 3D models of game build {name.split("-")[0]} are made already', flush=True)
        return name
    ensure_packages(say)
    started = time.time()

    def step(text):
        say(f'{time.time() - started:6.0f}s  {text}', flush=True)

    with runner.lock():                 # never while the game is updated or run
        step('reading the game files')
        game = gamefiles.Game(paths.game('Bundles', 'shared'))
        todo = jobs(game)
        work = folder(f'{name}.partial')
        if os.path.isdir(work):
            shutil.rmtree(work)
        indexes, volumes, missing = {'monuments': {}, 'scenery': {}}, {'monuments': {}, 'scenery': {}}, []
        for number, (kind, key, roots) in enumerate(todo, 1):
            target = os.path.join(work, kind)
            os.makedirs(target, exist_ok=True)
            if not roots:
                missing.append(key)
                continue
            if number % 30 == 1:
                step(f'models {number} of {len(todo)}')
            made = monument(game, roots[0]) if kind == 'monuments' else scenery(game, roots[0], key)
            shapes = no_build(game, roots[0])
            if shapes:
                volumes[kind][key] = shapes
            if made is None:
                missing.append(key)
                continue
            levels, source, left = made
            box, listed = write(target, key.replace('/', '__'), levels)
            indexes[kind][key] = {'source_triangles': int(source), 'left_out': left, 'levels': listed, 'box': box}
        # A game whose files are laid out otherwise (an engine update) gives few models or none: that is a failure to say
        # and to try again (Prepare 3D), not a 3D view without monuments. A whole build makes every one of them.
        wanted = sum(1 for kind, _, _ in todo if kind == 'monuments')
        if len(indexes['monuments']) * 2 < wanted:
            raise RuntimeError(f'the game\'s files gave a model for {len(indexes["monuments"])} of {wanted} monuments; they may be '
                               'laid out otherwise since a game update (a newer RustMapGen may read them)')
        for kind in indexes:
            target = os.path.join(work, kind)
            os.makedirs(target, exist_ok=True)
            with open(os.path.join(target, 'index.json'), 'w') as handle:
                json.dump(indexes[kind], handle, indent=1)
            bundle(target)
            with open(os.path.join(target, 'nobuild.json.gz'), 'wb') as handle:
                handle.write(gzip.compress(json.dumps(volumes[kind], separators=(',', ':')).encode(), 9, mtime=0))
        with open(os.path.join(work, 'done.json'), 'w') as handle:
            json.dump({'version': name, 'monuments': len(indexes['monuments']), 'scenery': len(indexes['scenery']),
                       'without_model': missing, 'seconds': round(time.time() - started)}, handle, indent=1)
        final = folder(name)
        if os.path.isdir(final):
            shutil.rmtree(final)
        os.replace(work, final)
        # The models of other builds and code are made again when needed: they go.
        for other in os.listdir(paths.data('3d-models')):
            if other != name and (VERSION.match(other) or other.endswith('.partial')):
                shutil.rmtree(folder(other), ignore_errors=True)
    step(f'3D models made: {len(indexes["monuments"])} monuments, {len(indexes["scenery"])} kinds of scenery')
    return name
