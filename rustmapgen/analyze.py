"""What is on a map: monuments, roads and rails, scenery, the shape of the land and the coast.

`describe(map, names)` returns one JSON-ready dict. The same function reads any map file, so two
maps can be compared number by number.
"""
import math
import re

import numpy as np

MONUMENT_FOLDER = 'assets/bundled/prefabs/autospawn/monument/'
AUTOSPAWN = 'assets/bundled/prefabs/autospawn/'

# Monument file name -> the name players know. Anything not listed is its file name, tidied.
LABELS = {
    'compound': 'Outpost', 'bandit_town': 'Bandit Camp', 'radtown_small_3': 'Sewer Branch', 'radtown_1': 'Radtown',
    'harbor_1': 'Large Harbor', 'harbor_2': 'Small Harbor', 'ferry_terminal_1': 'Ferry Terminal',
    'oilrig_1': 'Large Oil Rig', 'oilrig_2': 'Oil Rig', 'gas_station_1': 'Gas Station',
    'supermarket_1': 'Supermarket', 'warehouse': 'Mining Outpost', 'stables_a': 'Ranch', 'stables_b': 'Large Barn',
    'mining_quarry_a': 'Sulfur Quarry', 'mining_quarry_b': 'Stone Quarry', 'mining_quarry_c': 'HQM Quarry',
    'sphere_tank': 'The Dome', 'satellite_dish': 'Satellite Dish', 'water_treatment_plant_1': 'Water Treatment',
    'military_tunnel_1': 'Military Tunnels', 'powerplant_1': 'Power Plant', 'trainyard_1': 'Train Yard',
    'airfield_1': 'Airfield', 'excavator_1': 'Giant Excavator', 'launch_site_1': 'Launch Site',
    'junkyard_1': 'Junkyard', 'nuclear_missile_silo': 'Missile Silo', 'apartments_complex_1': 'Apartments',
    'arctic_research_base_a': 'Arctic Research Base', 'jungle_ziggurat_a': 'Ziggurat', 'lighthouse': 'Lighthouse',
}

# Counted on every map: what a recipe switches on or off besides monuments. Name -> path fragment.
FEATURES = {
    'road car wrecks': 'autospawn/decor/v3_road_vehicles',
    'road power poles': 'autospawn/decor/powerline-small/',
    'climbable power poles': 'autospawn/decor/powerline-pole-',
    'power line towers': 'autospawn/decor/powerline/',
    'electrical boxes': 'autospawn/decor/roadside/electrical_box',
    'road signs': 'autospawn/decor/roadside/roadsign',
    'icebergs': 'autospawn/decor/iceberg/',
    'mountains': 'autospawn/mountain/',
    'canyons': 'autospawn/unique_environment/canyon/',
    'oases': 'autospawn/unique_environment/oasis/',
    'lakes': 'autospawn/unique_environment/lake/',
    'jungle swamps': 'autospawn/unique_environment/jungle/',
    'power substations': 'autospawn/power substations/',
    'tunnel entrances': 'autospawn/tunnel-entrance/',
    'train tunnel pieces': 'autospawn/tunnel/',
    'underwater lab modules': 'autospawn/underwater-lab-',
}

# Prefabs whose place is measured, not only counted: where a tower stands over the ground, and
# whether every way down to the train tunnels has its entrance. List -> path fragment.
STANDING = {'towers': 'autospawn/decor/powerline/', 'stairs': 'autospawn/tunnel-link/link-stairwell',
            'bunkers': 'autospawn/tunnel-entrance/', 'stations': 'autospawn/tunnel-station/'}
# How far over the ground the game stands the pivot of a power line tower: 2.00 m is the middle value
# of each of the seven tower prefabs over 171 maps measured.
TOWER_PIVOT_M = 2.0
# A tower this far above that is kept in the facts with its place: the limit of the check
# (library.STRUCTURE) can then be moved without measuring the maps again.
LISTED_AIR_M = 1.0
# Two prefabs this close, seen from above, are one over the other: a stairwell's pieces, and the
# entrance bunker on top of them, share their place to the centimetre.
SAME_PLACE_M = 2.0

TOPOLOGY = {'ocean': 128, 'river': 16384, 'lake': 65536, 'mountain': 8388608, 'cliff': 2, 'beach': 16,
            'tier0': 67108864, 'tier1': 134217728, 'tier2': 268435456, 'mainland': 536870912, 'road': 2048}
# Monuments that stand in or on the water, or underground: their own height says nothing about the ground.
OFF_GROUND = ('offshore', 'underwater_lab', 'harbor', 'fishing_village', 'cave', 'maintainables', 'ice_lakes', 'swamp')
BIOMES = ('arid', 'temperate', 'tundra', 'arctic', 'jungle')


def label(short):
    if short in LABELS:
        return LABELS[short]
    return ' '.join(w.upper() if len(w) == 1 else w.capitalize() for w in short.split('_'))


def _length(nodes):
    total = 0.0
    for a, b in zip(nodes, nodes[1:]):
        total += math.hypot(b[0] - a[0], b[2] - a[2])
    return total


def _thin(nodes, step):
    """Nodes at least `step` metres apart, as flat [x, z, x, z, ...] rounded to a metre."""
    out, last = [], None
    for index, node in enumerate(nodes):
        if last is None or index == len(nodes) - 1 or math.hypot(node[0] - last[0], node[2] - last[2]) >= step:
            out += [round(node[0]), round(node[2])]
            last = node
    return out


def _terrain(world):
    """Shares of the map square, from a 1 in 4 sample of the layers."""
    out = {}
    height = world.metres('height')[::4, ::4]
    land = height > 0.0
    out['land'] = round(float(land.mean()), 4)
    if land.any():
        on_land = height[land]
        out['highest_m'] = round(float(on_land.max()), 1)
        out['mean_land_m'] = round(float(on_land.mean()), 1)
        # Share of the land at each level: where a base is easy (low), hills, and mountains.
        for name, low, high in (('land_below_25m', 0, 25), ('land_25_to_75m', 25, 75), ('land_above_75m', 75, 1e9)):
            out[name] = round(float(((on_land > low) & (on_land <= high)).mean()), 4)
        step = float(world.size) / (height.shape[0] - 1)
        dz, dx = np.gradient(height, step)
        slope = np.degrees(np.arctan(np.hypot(dx, dz)))[land]
        out['land_steeper_30deg'] = round(float((slope > 30.0).mean()), 4)
    topology = world.layer('topology')
    if topology is not None:
        sample = topology[::2, ::2]
        for name in ('tier0', 'tier1', 'tier2', 'river', 'lake', 'mountain'):
            out['topology_' + name] = round(float(((sample & TOPOLOGY[name]) != 0).mean()), 4)
        # Every cell, not a sample: "none" has to mean none (nothing can be built on a cell tagged as road).
        roads = int(np.count_nonzero(topology & TOPOLOGY['road']))
        out['topology_road'] = max(round(roads / topology.size, 5), 0.00001) if roads else 0.0
    biome = world.layer('biome')
    if biome is not None and topology is not None and biome.shape[1:] == topology.shape:
        ground = (topology[::2, ::2] & TOPOLOGY['ocean']) == 0
        weights = biome[:, ::2, ::2].astype(np.float32)
        total = float(weights[:, ground].sum()) or 1.0
        for index in range(weights.shape[0]):
            out['biome_' + BIOMES[index]] = round(float(weights[index][ground].sum()) / total, 4)
    return out


# The game joins two tracks where the end of one meets the other within 0.1 m and the two run within 10°
# of each other there (CollateTrainTracks, MAX_NODE_DIST and MAX_NODE_ANGLE): a train crosses nowhere else.
# An end that misses by up to NEAR_MISS_M, or meets at a wider angle, is a cut worth naming.
JOIN_M, JOIN_DEG, NEAR_MISS_M = 0.1, 10.0, 3.0


def _tangents(path):
    """Which way a track runs at each node, as the game takes it (PathInterpolator.RecalculateTangents): from
    the node before to the node after, an end from itself, and across the seam of a closed path."""
    closed = np.linalg.norm(path[0] - path[-1]) < JOIN_M
    before = np.concatenate(([path[-2] if closed else path[0]], path[:-1]))
    after = np.concatenate((path[1:], [path[1] if closed else path[-1]]))
    step = after - before
    length = np.linalg.norm(step, axis=1, keepdims=True)
    return step / np.where(length > 0, length, 1.0)


def _rails(world):
    """The above-ground rail network as a train meets it: one closed ring, everything else joined to it.

    The game stores the ring as one path whose two ends meet, sidings as paths with both ends on
    another path's node, and spurs into monuments as paths with one free end. A stretch the ring
    does not reach is described where it is (loose_at, loose_lines), and an end that misses another
    track by a little is told with its gap (near_misses).
    """
    paths = [np.array(item['nodes'], dtype=np.float64) for item in world.paths if item['name'].startswith('Rail') and len(item['nodes']) > 1]
    out = {'paths': len(paths), 'rings': 0, 'ring_m': 0, 'sidings': 0, 'spurs': 0, 'loose': 0, 'loose_at': [], 'loose_lines': [], 'near_misses': [],
           'max_grade': 0.0, 'max_turn_deg': 0.0, 'lowest_m': None,
           # The ring and the tracks off it apart: a siding may climb to a tunnel mouth as steeply as the
           # game's own ramps (42 %), the line a train laps should not.
           'ring_grade': 0.0, 'ring_turn_deg': 0.0, 'side_grade': 0.0, 'side_turn_deg': 0.0}
    if not paths:
        return out
    nodes = np.concatenate(paths)
    owner = np.concatenate([np.full(len(path), index) for index, path in enumerate(paths)])
    ways = np.concatenate([_tangents(path) for path in paths])
    # The game splits a track at a node in its middle only when the track has more than three; it links ends always.
    splits = np.concatenate([[True] + [len(path) > 3] * (len(path) - 2) + [True] for path in paths])
    out['lowest_m'] = round(float(nodes[:, 1].min()), 1)
    rings, graph, joins = set(), [set() for _ in paths], [0] * len(paths)
    for index, path in enumerate(paths):
        steps = np.hypot(np.diff(path[:, 0]), np.diff(path[:, 2]))
        part = 'ring' if len(path) > 20 and np.linalg.norm(path[0] - path[-1]) < 1.0 else 'side'
        # The last ring node repeats the first (within the game's closure tolerance).
        # Wrap four nodes so grades and turns include the closing seam, without a tiny extra segment.
        measured = np.concatenate((path[:-1], path[:5])) if part == 'ring' else path
        if len(measured) > 4:               # the climb over four nodes (about 30 m), not node to node
            run = np.hypot(measured[4:, 0] - measured[:-4, 0], measured[4:, 2] - measured[:-4, 2])
            grade = float((np.abs(measured[4:, 1] - measured[:-4, 1]) / np.maximum(run, 1.0)).max())
            out['max_grade'], out[part + '_grade'] = max(out['max_grade'], grade), max(out[part + '_grade'], grade)
        heading = np.arctan2(np.diff(measured[:, 2]), np.diff(measured[:, 0]))
        if len(heading) > 1:
            turn = float(np.degrees(np.abs((np.diff(heading) + np.pi) % (2 * np.pi) - np.pi).max()))
            out['max_turn_deg'], out[part + '_turn_deg'] = max(out['max_turn_deg'], turn), max(out[part + '_turn_deg'], turn)
        if part == 'ring':
            rings.add(index)
            out['rings'] += 1
            out['ring_m'] += float(steps.sum())
        ends = _tangents(path)
        for end, way in ((path[0], ends[0]), (path[-1], ends[-1])):
            apart = np.linalg.norm(nodes - end, axis=1)
            # Either way along the other track: the game tries the end's direction and its reverse.
            angle = np.degrees(np.arccos(np.clip(np.abs(ways @ way), 0.0, 1.0)))
            close = (owner != index) & (apart <= JOIN_M) & splits
            touching = set(owner[close & (angle < JOIN_DEG)].tolist())
            if not touching and part != 'ring':          # a closed ring's two ends meet each other
                if close.any():                          # it meets another track, at too wide an angle
                    nearest = np.where(close)[0][int(angle[close].argmin())]
                    out['near_misses'].append({'x': round(float(end[0])), 'z': round(float(end[2])), 'gap_m': round(float(apart[nearest]), 3),
                                               'higher_m': round(float(nodes[nearest][1] - end[1]), 2), 'angle_deg': round(float(angle[nearest]), 1)})
                    continue
                others = np.where(owner != index)[0]
                if len(others):
                    nearest = others[int(apart[others].argmin())]
                    if apart[nearest] <= NEAR_MISS_M:
                        out['near_misses'].append({'x': round(float(end[0])), 'z': round(float(end[2])), 'gap_m': round(float(apart[nearest]), 3),
                                                   'higher_m': round(float(nodes[nearest][1] - end[1]), 2)})
            joins[index] += bool(touching)
            graph[index].update(touching)
            for other in touching:
                graph[other].add(index)
    connected, pending = set(rings), list(rings)
    while pending:
        for other in graph[pending.pop()] - connected:
            connected.add(other)
            pending.append(other)
    for index in range(len(paths)):
        if index not in rings:
            out['loose' if index not in connected else 'sidings' if joins[index] == 2 else 'spurs'] += 1
        if index not in connected:
            middle = paths[index][len(paths[index]) // 2]
            out['loose_at'].append([round(float(middle[0])), round(float(middle[2]))])
            out['loose_lines'].append(_thin(paths[index], 12.0))
    out['ring_m'] = round(out['ring_m'])
    for key in ('max_grade', 'ring_grade', 'side_grade'):
        out[key] = round(out[key], 3)
    for key in ('max_turn_deg', 'ring_turn_deg', 'side_turn_deg'):
        out[key] = round(out[key], 1)
    return out


def _coast(world):
    """Where sea and land meet: what the game tagged against what the heights say, and the beaches."""
    topology = world.layer('topology')
    if topology is None:
        return {}
    height = world.metres('height')
    cells = topology.shape[0]
    index = np.minimum((np.arange(cells) + 0.5) * height.shape[0] / cells, height.shape[0] - 1).astype(np.intp)
    height = height[np.ix_(index, index)]
    under = height < 0.0
    land = ~under
    ocean = (topology & TOPOLOGY['ocean']) != 0
    beach = (topology & TOPOLOGY['beach']) != 0
    on_land = height[land]
    out = {'sea_tag_agrees': round(float((ocean == under).mean()), 4),
           'land_tagged_sea': round(float((ocean & land).sum() / max(1, land.sum())), 4),
           'beach': round(float(beach.mean()), 4),
           'low_coast': round(float((land & (height < 3.0)).mean()), 4)}
    if len(on_land):
        for share in (10, 25, 50, 75, 90, 99):
            out[f'land_p{share}_m'] = round(float(np.percentile(on_land, share)), 1)
    # The shallows and the shelf: how much of the square is sea no deeper than a given depth.
    for depth in (1, 3, 6, 12, 20, 30, 40):
        out[f'sea_to_{depth}m'] = round(float((under & (height > -depth)).mean()), 4)
    # The sea floor 12 to 30 m down, and how much of it is gentle: where things dropped to the
    # bottom of the sea find a level patch.
    shelf = (height < -12.0) & (height > -30.0)
    step = float(world.size) / height.shape[0]
    dz, dx = np.gradient(height, step)
    gentle = np.degrees(np.arctan(np.hypot(dx, dz))) < 18.0
    out['shelf'] = round(float(shelf.mean()), 4)
    out['shelf_gentle'] = round(float((shelf & gentle).sum() / max(1, shelf.sum())), 3)
    return out


def _grounding(world, monuments):
    """Give each monument `ground_m`: its own height above the ground under its pivot.

    The number means little alone (a prefab's pivot may sit metres under its floor), but it is
    the same on every map for one prefab: a monument that floats or is sunk stands out against
    the reference value of that prefab (reference.json, measured on many maps).
    """
    height = world.metres('height')
    scale = (height.shape[0] - 1) / float(world.size)
    half = world.size * 0.5
    for item in monuments:
        if item['folder'] in OFF_GROUND:
            continue
        i = int(round((item['x'] + half) * scale))
        j = int(round((item['z'] + half) * scale))
        if 0 <= i < height.shape[0] and 0 <= j < height.shape[0]:
            item['ground_m'] = round(float(item['y'] - height[j, i]), 1)


# The ground a monument levels for itself reaches well beyond the box the game places it by (the
# Launch Site's box is 400 x 200 m, its ground runs about 280 m from its middle), so a neighbour
# placed after it can cut that ground down or raise it: on a map in play on 2026-10-25 the end of a
# Launch Site that faced the Power Plant stood up to 6 m over the ground. Under its pivot all was as
# usual, so `ground_m` cannot see it; the whole ground is compared with the usual one instead
# (ground.npz, checks.load_ground_shapes), on a grid in the monument's own frame.
GROUND_STEP = 4.0


def ground_grid(height, size, item, radius, step=GROUND_STEP):
    """The terrain minus a monument's own height, on a square grid of `step` metres in its frame (x along its turn)."""
    last = height.shape[0] - 1
    scale, half = last / float(size), size * 0.5
    axis = np.arange(-radius, radius + step / 2, step)
    u, v = np.meshgrid(axis, axis)
    turn = math.radians(item['turn'])
    x = item['x'] + u * math.cos(turn) + v * math.sin(turn)
    z = item['z'] - u * math.sin(turn) + v * math.cos(turn)
    i = np.clip(np.rint((x + half) * scale).astype(int), 0, last)
    j = np.clip(np.rint((z + half) * scale).astype(int), 0, last)
    return (height[j, i] - item['y']).astype(np.float32)


def ground_patches(world, monuments, shapes, off_m):
    """Give each monument whose usual ground is in `shapes` how much of that ground is more than `off_m` lower
    here (`ground_low_m2`: what stands there floats) or higher (`ground_high_m2`: it is buried), and its size."""
    height = world.metres('height')
    for item in monuments:
        shape = shapes.get(item['prefab'])
        if not shape or item['folder'] in OFF_GROUND:
            continue
        dev = ground_grid(height, world.size, item, shape['radius']) - shape['usual']
        own, cell = shape['own'], GROUND_STEP ** 2
        item['ground_low_m2'] = int((own & (dev < -off_m)).sum() * cell)
        item['ground_high_m2'] = int((own & (dev > off_m)).sum() * cell)
        item['ground_own_m2'] = int(own.sum() * cell)


def _surface(world):
    """The ground under a point, in metres: the terrain with the cliff models on it, between its four samples."""
    height = world.metres('height')
    last = height.shape[0] - 1
    scale, half = last / float(world.size), world.size * 0.5

    def at(x, z):
        fx = min(max((x + half) * scale, 0.0), last - 0.001)
        fz = min(max((z + half) * scale, 0.0), last - 0.001)
        i, j = int(fx), int(fz)
        u, v = fx - i, fz - j
        return float(height[j, i] * (1 - u) * (1 - v) + height[j, i + 1] * u * (1 - v)
                     + height[j + 1, i] * (1 - u) * v + height[j + 1, i + 1] * u * v)
    return at


def _towers(towers, monuments, surface):
    """The power line towers against the ground under them.

    The game stands every tower's pivot the same height over the ground (99 in 100 within 0.9 m of
    it) and lays the line before the small caves cut their hollows: a tower over such a hollow keeps
    its height, and its legs hang in the air. `air_m` is how far a tower stands above where it should.
    The ground is the surface with the cliff models on it: a tower under a cliff is not in the air.
    """
    over = [(y - surface(x, z) - TOWER_PIVOT_M, name, x, z) for name, x, y, z in towers]
    out = {'count': len(over), 'highest_air_m': None, 'in_the_air': []}
    if not over:
        return out
    out['highest_air_m'] = round(max(row[0] for row in over), 1)
    for air, name, x, z in sorted(over, reverse=True):
        if air <= LISTED_AIR_M:
            break
        item = {'prefab': name, 'x': round(x, 1), 'z': round(z, 1), 'air_m': round(air, 1)}
        if monuments:
            near = min(monuments, key=lambda m: math.hypot(m['x'] - x, m['z'] - z))
            item.update(near=near['label'], near_m=round(math.hypot(near['x'] - x, near['z'] - z)))
        out['in_the_air'].append(item)
    return out


def _piece_m(name):
    """How tall a stairwell piece is: its name ends in its height in centimetres; the landing at the bottom is 3 m."""
    end = name.rsplit('-', 1)[-1]
    if end.isdigit():
        return int(end) / 100.0
    return 3.0 if '-bottom-' in name else None


def _tunnels(stairs, bunkers, stations, surface):
    """The ways down to the train tunnels: each a column of stairwell pieces from a station's level
    up to an entrance. The entrance is a bunker of its own, or part of a monument.

    `depth_m` is how far under the ground a column ends: 15.0 m under a bunker, whose shaft does the
    rest, and 5.6 to 6.0 m under a monument's own entrance (2,638 columns measured).
    None when the top piece is of a kind whose height its name does not say.
    """
    columns = []
    for piece in sorted(stairs, key=lambda p: -p[2]):       # from the top down: a column starts at its highest piece
        for column in columns:
            if math.hypot(column[0][1] - piece[1], column[0][3] - piece[3]) < SAME_PLACE_M:
                column.append(piece)
                break
        else:
            columns.append([piece])
    ways = []
    for column in columns:
        name, x, y, z = column[0]
        tall = _piece_m(name)
        ways.append({'x': round(x, 1), 'z': round(z, 1),
                     'bunker': any(math.hypot(b[1] - x, b[3] - z) < SAME_PLACE_M for b in bunkers),
                     'depth_m': None if tall is None else round(surface(x, z) - (y + tall), 1)})
    alone = [[round(x, 1), round(z, 1)] for _, x, _, z in bunkers
             if not any(math.hypot(column[0][1] - x, column[0][3] - z) < SAME_PLACE_M for column in columns)]
    return {'stations': len(stations), 'bunkers': len(bunkers), 'ways_down': ways, 'bunkers_without_stairs': alone}


def describe(world, names, lines_step=12.0):
    paths_of = {}
    for item in world.paths:
        kind = re.sub(r'\s*\d+$', '', item['name']) or 'Path'
        entry = paths_of.setdefault(kind, {'count': 0, 'length_m': 0.0, 'lines': []})
        entry['count'] += 1
        entry['length_m'] += _length(item['nodes'])
        entry['lines'].append(_thin(item['nodes'], lines_step))
    for entry in paths_of.values():
        entry['length_m'] = round(entry['length_m'])

    monuments, features = [], {name: 0 for name in FEATURES}
    standing = {kind: [] for kind in STANDING}
    unknown = 0
    for index, ident in enumerate(world.id):
        path = names.get(int(ident))
        if path is None:
            unknown += 1
            continue
        if path.startswith(MONUMENT_FOLDER):
            short = path.rsplit('/', 1)[1][:-len('.prefab')]
            x, y, z = world.position[index]
            monuments.append({'prefab': short, 'folder': path[len(MONUMENT_FOLDER):].split('/')[0],
                              'label': label(short), 'x': round(float(x), 1), 'y': round(float(y), 1), 'z': round(float(z), 1),
                              'turn': round(float(world.rotation[index][1]), 1), 'category': world.category[index]})
        elif path.startswith(AUTOSPAWN):
            for name, fragment in FEATURES.items():
                if fragment in path:
                    features[name] += 1
                    break
            for kind, fragment in STANDING.items():
                if fragment in path:
                    standing[kind].append((path.rsplit('/', 1)[1][:-len('.prefab')],) + tuple(float(v) for v in world.position[index]))
                    break

    counts = {}
    for item in monuments:
        counts[item['prefab']] = counts.get(item['prefab'], 0) + 1
    outposts = [m for m in monuments if m['prefab'] == 'compound']
    rails = paths_of.get('Rail', {'count': 0, 'length_m': 0})
    _grounding(world, monuments)
    surface = _surface(world)
    out = {
        'size': int(world.size), 'version': int(world.version), 'saved_ms': int(world.timestamp),
        'prefabs': int(len(world.id)), 'unknown_prefabs': unknown,
        'monuments': sorted(monuments, key=lambda m: (m['folder'], m['prefab'], m['x'])),
        'monument_counts': dict(sorted(counts.items())),
        'features': features,
        'paths': {kind: {'count': e['count'], 'length_m': e['length_m']} for kind, e in sorted(paths_of.items())},
        'lines': {kind: e['lines'] for kind, e in sorted(paths_of.items())},
        'rail_m': rails['length_m'], 'rail_paths': rails['count'],
        'outpost_from_centre_m': round(math.hypot(outposts[0]['x'], outposts[0]['z'])) if outposts else None,
        'terrain': _terrain(world),
        'rails': _rails(world), 'coast': _coast(world),
        'towers': _towers(standing['towers'], monuments, surface),
        'tunnels': _tunnels(standing['stairs'], standing['bunkers'], standing['stations'], surface),
    }
    return out
