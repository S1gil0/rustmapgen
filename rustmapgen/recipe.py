"""A recipe: every decision behind a kind of map, and how each one reaches the game.

A recipe holds settings only. Three places read them:

  world       the game's own world config (`world.configfile`): switches, tier and biome shares
  monuments   one of "want", "block" or "any" per monument
                want  -> placed first, distance rules relaxed, allowed on any world size (the mod)
                block -> never placed (the game's own blocklist)
                any   -> the game decides
  scenery     the same per kind of scenery; "want" lifts the world size the game asks for
  steps       per placement step of the game: how many, from which world size, how far apart
  rail_ring, road_ring   "always", "never" or "game" (the game makes them from 4250 and 5000)
  centre      one monument searched outward from the middle of the map
  terrain     how much of the square is land, how high the land stands, how wide the shallows are
  underwater_labs, templates, template_skip, block_extra

`compile()` turns them into the two files a generation needs. catalog.json lists what there is to
decide (every monument, kind of scenery and placement step of the game) with the game's own
values, and is what the panel builds its controls from. A copy comes with the code; every map
made rebuilds it from the game that made it (<data>/tables/catalog.json), so it follows the game.
"""
import json
import math
import os
import re

from . import analyze, paths

CATALOG = paths.package('catalog.json')
MONUMENT_ROOT = 'assets/bundled/prefabs/autospawn/monument/'
STATES = ('want', 'block', 'any')
RINGS = ('always', 'never', 'game')

SWITCHES = (('MainRoads', 'Main roads'), ('SideRoads', 'Side roads'), ('Trails', 'Trails'), ('Rivers', 'Rivers'),
            ('Powerlines', 'Power lines'), ('AboveGroundRails', 'Above-ground rails'),
            ('BelowGroundRails', 'Underground rails'), ('UnderwaterLabs', 'Underwater labs'))
# Switches a map cannot be made without. Underground rails: a server playing the map reads this switch
# from its own world config (on, unless set there too), not from the map file, and only with it off
# does the game cover the tunnel openings at the monuments; a map made without the tunnels then has
# open holes there.
ALWAYS_ON = ('BelowGroundRails',)
# The most the land settings may ask. Land share: the shore moves 20 m at the most, which reached 68 to
# 71 % on large maps. Heights: twice the game's own highest point on a large map (about 100 m).
LAND_SHARE_MAX = 0.7
# What the shore's 20 m reach depends on is the size: on 23 maps (2026-10-17) it gave 45 to 60 % at 2,000,
# 60 to 65 % at 3,000, 68 % at 3,500 and 70 to 73 % from 4,000. The least at a size, give or take the land
# check's 5 points (LAND_SPARE, checks.LAND_SHARE_OFF), is what a map of that size may ask.
LAND_REACH = ((2000, 0.45), (4000, 0.70))
LAND_SPARE = 0.05


def land_share_max(size):
    """The most land a map of this size may ask: what its shore reaches on every seed seen, give or take 5 points."""
    (low_size, low), (high_size, high) = LAND_REACH
    reach = low + (high - low) * (size - low_size) / (high_size - low_size)
    return math.floor(min(LAND_SHARE_MAX, reach + LAND_SPARE) * 100 + 1e-9) / 100
# Monuments that cannot go to the middle of the map: roadside and offshore ones are placed by steps
# that search the roads or the edge of the sea, not land draws; the others stand on the coast or
# under the sea, and the middle of the map is inland.
NOT_IN_THE_MIDDLE = ('roadside', 'offshore', 'fishing_village', 'lighthouse', 'harbor', 'underwater_lab')
HEIGHT_MAX_M = 200.0
TIERS = (('PercentageTier0', 'Tier 0', 0.3), ('PercentageTier1', 'Tier 1', 0.3), ('PercentageTier2', 'Tier 2', 0.4))
BIOMES = (('PercentageBiomeArid', 'Desert', 0.4), ('PercentageBiomeTemperate', 'Temperate', 0.15),
          ('PercentageBiomeTundra', 'Tundra', 0.15), ('PercentageBiomeArctic', 'Snow', 0.3))
JUNGLE = ('PercentageBiomeJungle', 'Jungle (share of tier 0 turned into jungle)', 0.5)
# The game's biome names, as a monument's catalog row lists them: the share that makes each, and its name here.
BIOME_OF = {'Arid': ('PercentageBiomeArid', 'Desert'), 'Temperate': ('PercentageBiomeTemperate', 'Temperate'),
            'Tundra': ('PercentageBiomeTundra', 'Tundra'), 'Arctic': ('PercentageBiomeArctic', 'Snow'), 'Jungle': ('PercentageBiomeJungle', 'Jungle')}

# Monument file name -> the section it is listed under, in the order map makers know.
GROUPS = (
    ('Safe zones', ('compound', 'bandit_town', 'fishing_village_a', 'fishing_village_b', 'fishing_village_c', 'stables_a', 'stables_b')),
    ('Large monuments', ('airfield_1', 'junkyard_1', 'launch_site_1', 'military_tunnel_1', 'powerplant_1', 'trainyard_1',
                         'water_treatment_plant_1', 'sphere_tank', 'satellite_dish', 'nuclear_missile_silo', 'radtown_1',
                         'jungle_ziggurat_a', 'apartments_complex_1', 'excavator_1')),
    ('Small monuments', ('gas_station_1', 'supermarket_1', 'warehouse', 'lighthouse', 'radtown_small_3', 'swamp_a', 'swamp_b', 'swamp_c',
                         'desert_military_base_a', 'desert_military_base_b', 'desert_military_base_c', 'desert_military_base_d',
                         'arctic_research_base_a')),
    ('Harbors', ('harbor_2', 'harbor_1', 'ferry_terminal_1')),
    ('Oil rigs', ('oilrig_1', 'oilrig_2')),
    ('Quarries', ('mining_quarry_a', 'mining_quarry_b', 'mining_quarry_c')),
    ('Water wells', ('water_well_a', 'water_well_b', 'water_well_c', 'water_well_d', 'water_well_e')),
    ('Caves', ('cave_large_hard', 'cave_large_medium', 'cave_large_sewers_hard', 'cave_medium_easy', 'cave_medium_hard',
               'cave_medium_medium', 'cave_small_easy', 'cave_small_hard', 'cave_small_medium')),
    ('Ice lakes', ('ice_lake_1', 'ice_lake_2', 'ice_lake_3', 'ice_lake_4')),
    ('Ruins', ('jungle_ruins_a', 'jungle_ruins_b', 'jungle_ruins_c', 'jungle_ruins_d', 'jungle_ruins_e')),
    ('Underwater labs', ('underwater_lab_a', 'underwater_lab_b', 'underwater_lab_c', 'underwater_lab_d')),
)

# Kind of scenery -> (label, path fragments, folder of the placement step that the game gates by world size).
SCENERY = (
    ('road car wrecks', 'Car wrecks on roads', ('autospawn/decor/v3_road_vehicles',), None),
    ('road power poles', 'Power poles along roads', ('autospawn/decor/powerline-small/', 'autospawn/decor/powerline-pole-'), None),
    ('electrical boxes', 'Electrical boxes by roads', ('autospawn/decor/roadside/electrical_box',), None),
    ('road signs', 'Road signs', ('autospawn/decor/roadside/roadsign',), None),
    ('icebergs', 'Icebergs', ('autospawn/decor/iceberg/',), None),
    ('mountains', 'Mountains', ('autospawn/mountain/mountain_',), 'mountain'),
    ('canyons', 'Canyons', ('autospawn/unique_environment/canyon/',), 'unique_environment/canyon'),
    ('oases', 'Oases', ('autospawn/unique_environment/oasis/',), 'unique_environment/oasis'),
    ('lakes', 'Lakes', ('autospawn/unique_environment/lake/',), 'unique_environment/lake'),
    ('jungle swamps', 'Jungle swamps', ('autospawn/unique_environment/jungle/',), 'unique_environment/jungle'),
)

# Kinds that were one and are now several: a recipe that names the old one means each of the new ones.
SPLIT_SCENERY = {'roadside boxes and signs': ('electrical boxes', 'road signs')}

TERRAIN_PROFILE = ('p10', 'p50', 'p90', 'p99', 'max')


def scenery_states(recipe):
    """A recipe's state for each kind of scenery, kinds split since it was saved named by their new names."""
    scenery = (recipe or {}).get('scenery') or {}
    out = {}
    for key, state in scenery.items():
        for new in SPLIT_SCENERY.get(key, ()):
            out[new] = state
    out.update({key: state for key, state in scenery.items() if key not in SPLIT_SCENERY})
    return out


def _scenery_rows(by_folder):
    return [{'key': key, 'label': label, 'fragments': list(fragments), 'step': step,
             'game_min_world_size': by_folder.get(step, {}).get('game_min_world_size', 0) if step else 0}
            for key, label, fragments, step in SCENERY]


def identifier(value, label='name', maximum=60, allow_empty=False):
    """Names become local and remote path components, never paths or shell fragments."""
    if allow_empty and value == '':
        return value
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,' + str(maximum - 1) + r'}', value):
        raise ValueError(f'{label}: use 1 to {maximum} letters, numbers, underscores or hyphens, starting with a letter or number')
    return value


def load_catalog():
    """The catalog of the game this install runs, or the one that came with the code."""
    for path in (paths.data('tables', 'catalog.json'), CATALOG):
        try:
            with open(path) as handle:
                catalog = json.load(handle)
        except (OSError, ValueError):
            continue
        # The kinds of scenery are this code's own: a catalog an older version wrote gets today's.
        catalog['scenery'] = _scenery_rows({step['folder']: step for step in catalog.get('steps') or []})
        return catalog
    raise FileNotFoundError(CATALOG)


def build_catalog(table_path, census_path, out=None):
    """The catalog from the game's prefab table and one generation's census (the game's own values)."""
    with open(table_path) as handle:
        prefab_paths = sorted(json.load(handle).values())
    with open(census_path) as handle:
        census = json.load(handle)
    facts = {row['prefab']: row for row in census['monuments']}
    known = {path.rsplit('/', 1)[1][:-len('.prefab')]: path for path in prefab_paths if path.startswith(MONUMENT_ROOT)}
    listed = set()
    monuments = []
    for group, prefabs in GROUPS:
        for prefab in prefabs:
            path = known.get(prefab)
            if not path:
                continue                # gone from the game: the recipe key is then ignored
            listed.add(prefab)
            row = facts.get(path, {})
            monuments.append({'prefab': prefab, 'path': path[len('assets/bundled/prefabs/'):], 'folder': path[len(MONUMENT_ROOT):].split('/')[0],
                              'label': analyze.label(prefab), 'group': group,
                              'game_min_world_size': row.get('game_min_world_size', 0), 'game_priority': row.get('game_priority', 'Default'),
                              'tier': row.get('tier', ''), 'type': row.get('type', ''), 'biomes': row.get('biomes', 'any')})
    unlisted = sorted(prefab for prefab, path in known.items() if prefab not in listed and '/maintainables/' not in path)
    for prefab in unlisted:             # new in the game since GROUPS was written
        path = known[prefab]
        monuments.append({'prefab': prefab, 'path': path[len('assets/bundled/prefabs/'):], 'folder': path[len(MONUMENT_ROOT):].split('/')[0],
                          'label': analyze.label(prefab), 'group': 'Other', 'game_min_world_size': facts.get(path, {}).get('game_min_world_size', 0),
                          'game_priority': facts.get(path, {}).get('game_priority', 'Default'), 'tier': '', 'type': '',
                          'biomes': facts.get(path, {}).get('biomes', 'any')})
    steps = []
    for row in census['steps']:
        if row['step'] != 'PlaceMonuments':
            continue
        steps.append({'folder': row['folder'], 'label': row.get('description') or row['folder'],
                      'game_count': row.get('game_target_count', 0), 'game_min_world_size': row.get('game_min_world_size', 0),
                      'game_distance_same': row.get('min_distance_same', 0), 'game_distance_other': row.get('min_distance_other', 0)})
    by_folder = {step['folder']: step for step in steps}
    scenery = _scenery_rows(by_folder)
    rings = {row['step']: row.get('game_min_world_size') for row in census['steps'] if row['step'] in ('GenerateRailRing', 'GenerateRoadRing')}
    catalog = {'monuments': monuments, 'scenery': scenery, 'steps': steps,
               'rail_ring_game_size': rings.get('GenerateRailRing', 4250), 'road_ring_game_size': rings.get('GenerateRoadRing', 5000)}
    out = out or paths.data('tables', 'catalog.json')
    with open(out + '.partial', 'w') as handle:
        json.dump(catalog, handle, indent=1)
        handle.write('\n')
    os.replace(out + '.partial', out)
    return catalog


def step_of(row, catalog):
    """The placement step a monument is placed in (a catalog row of steps), or None."""
    folder = row['path'][len('autospawn/'):].rsplit('/', 1)[0]
    return next((step for step in catalog['steps'] if folder in step['folder'].split(',')), None)


def step_count(step, recipe):
    """How many monuments a step places on a map with these settings, on every seed: 0 is as many as fit.
    The underwater labs follow their own setting (a range: its smallest number)."""
    labs = recipe.get('underwater_labs')
    if step['folder'] == 'monument/underwater_lab' and labs:
        return labs['min']
    own = ((recipe.get('steps') or {}).get(step['folder']) or {}).get('count')
    return own if own is not None and own >= 0 else step['game_count']


def over_wanted(recipe, catalog):
    """The steps that place fewer monuments than the settings want of them: [(step, places, wanted rows)]."""
    rows = {row['prefab']: row for row in catalog['monuments']}
    by_step = {}
    for prefab, state in (recipe.get('monuments') or {}).items():
        step = step_of(rows[prefab], catalog) if state == 'want' and prefab in rows else None
        if step:
            by_step.setdefault(step['folder'], (step, []))[1].append(rows[prefab])
    return [(step, step_count(step, recipe), wanted) for step, wanted in by_step.values()
            if 0 < step_count(step, recipe) < len(wanted)]


def biomes_off(row, world):
    """The names of a monument's biomes when every one of them is at 0 % in these shares (it cannot be placed), else []."""
    allowed = row.get('biomes')
    if not isinstance(allowed, list) or not allowed or any(name not in BIOME_OF for name in allowed):
        return []
    if any(world.get(BIOME_OF[name][0], 1.0) > 0 for name in allowed):
        return []
    return [BIOME_OF[name][1] for name in allowed]


def defaults():
    """A recipe that changes nothing: the map the game would make by itself."""
    world = {key: True for key, _ in SWITCHES}
    world.update({key: value for key, _, value in TIERS + BIOMES + (JUNGLE,)})
    return {'name': '', 'title': '', 'size': 4250, 'source': '', 'world': world, 'monuments': {}, 'scenery': {}, 'block_extra': [],
            'rail_ring': 'game', 'road_ring': 'game', 'steps': {}, 'relax_distances': 1.0,
            'centre': {'monument': '', 'start_m': 4.0, 'grow_m': 0.35, 'within_m': 600, 'any_tier': False},
            'underwater_labs': None, 'terrain': {}, 'templates': [], 'template_skip': [], 'build_on_roads': False}


def normalise(recipe, catalog=None):
    """A recipe with every key present and every value of the right kind. Raises ValueError with the reason."""
    catalog = catalog or load_catalog()
    out = defaults()
    if not isinstance(recipe, dict):
        raise ValueError('a recipe is an object')
    out['name'] = identifier(recipe.get('name', ''), 'recipe name', allow_empty=True)
    for key, longest in (('title', 120), ('source', 1200)):
        out[key] = str(recipe.get(key, ''))[:longest]
    out['size'] = int(recipe.get('size', 4250))
    if not 1000 <= out['size'] <= 6000:
        raise ValueError('size must be 1000 to 6000')

    world = recipe.get('world') or {}
    for key, _ in SWITCHES:
        out['world'][key] = bool(world.get(key, True))
        if key in ALWAYS_ON and not out['world'][key]:
            raise ValueError(f'{key} cannot be off: a map file does not carry this switch, and a server playing the map '
                             'would show open holes where the tunnels meet the monuments')
    for key, label, default in TIERS + BIOMES + (JUNGLE,):
        value = float(world.get(key, default))
        if not 0.0 <= value <= 1.0:
            raise ValueError(f'{label}: a share is between 0 and 1')
        out['world'][key] = round(value, 4)
    for label, group in (('tier', TIERS), ('biome', BIOMES)):
        total = sum(out['world'][key] for key, _, _ in group)
        if abs(total - 1.0) > 0.011:
            raise ValueError(f'the {label} shares must add up to 1 (they add up to {total:.2f})')

    prefabs = {row['prefab'] for row in catalog['monuments']}
    for prefab, state in (recipe.get('monuments') or {}).items():
        if state not in STATES:
            raise ValueError(f'{prefab}: {state!r} is not one of want, block, any')
        if prefab in prefabs and state != 'any':
            out['monuments'][prefab] = state
    kinds = {row['key'] for row in catalog['scenery']}
    for key, state in scenery_states(recipe).items():
        if state not in STATES:
            raise ValueError(f'{key}: {state!r} is not one of want, block, any')
        if key in kinds and state != 'any':
            out['scenery'][key] = state
    out['block_extra'] = _fragments(recipe, 'block_extra')

    for key in ('rail_ring', 'road_ring'):
        out[key] = recipe.get(key, 'game')
        if out[key] not in RINGS:
            raise ValueError(f'{key}: {out[key]!r} is not one of always, never, game')
    folders = {row['folder'] for row in catalog['steps']}
    for folder, rule in (recipe.get('steps') or {}).items():
        if folder not in folders or not isinstance(rule, dict):
            continue
        kept = {}
        for key, low, high in (('count', 0, 200), ('min_world_size', 0, 6000), ('distance_same', 0, 5000), ('distance_other', 0, 5000)):
            if rule.get(key) is not None:
                kept[key] = int(rule[key])
                if not low <= kept[key] <= high:
                    raise ValueError(f'{folder}: {key} must be {low} to {high}')
        if kept:
            out['steps'][folder] = kept
    out['relax_distances'] = float(recipe.get('relax_distances', 1.0))
    if not 0.1 <= out['relax_distances'] <= 3.0:
        raise ValueError('relax_distances must be 0.1 to 3')

    centre = recipe.get('centre') or {}
    out['centre'] = {'monument': str(centre.get('monument', '')), 'start_m': float(centre.get('start_m', 4.0)),
                     'grow_m': float(centre.get('grow_m', 0.35)), 'within_m': int(centre.get('within_m', 600)),
                     'any_tier': bool(centre.get('any_tier', False))}
    if out['centre']['monument'] and out['centre']['monument'] not in prefabs:
        raise ValueError(f"{out['centre']['monument']}: not a monument of the game")
    centre_row = next((row for row in catalog['monuments'] if row['prefab'] == out['centre']['monument']), None)
    if centre_row and centre_row['folder'] in NOT_IN_THE_MIDDLE:
        raise ValueError('centre: roadside and offshore monuments, and those of the coast or under the sea, cannot be put in the middle of the map')
    # A search that does not widen stays within its start: the monument can then find no place at all.
    if not 0.5 <= out['centre']['start_m'] <= 500 or not 0.1 <= out['centre']['grow_m'] <= 20:
        raise ValueError('centre: the search starts 0.5 to 500 m out and widens by 0.1 to 20 m an attempt')
    labs = recipe.get('underwater_labs')
    if labs:
        low, high = int(labs.get('min', 1)), int(labs.get('max', labs.get('min', 1)))
        if not 0 <= low <= high <= 12:
            raise ValueError('underwater labs: 0 to 12, the smallest number first')
        out['underwater_labs'] = {'min': low, 'max': high}

    terrain = recipe.get('terrain') or {}
    kept = {}
    for key, low, high in (('land_share', 0.2, LAND_SHARE_MAX), ('coast_lift_m', -10.0, 20.0), ('coast_fade_m', 1.0, 60.0), ('height_scale', 0.2, 2.0),
                           ('relief_radius_m', 0.0, 400.0), ('relief_keep', 0.0, 2.0), ('middle_plateau_m', 0.0, 600.0)):
        if terrain.get(key) is not None:
            kept[key] = float(terrain[key])
            if not low <= kept[key] <= high:
                raise ValueError(f'terrain: {key} must be {low} to {high}')
    profile = terrain.get('profile')
    if profile:
        values = [float(profile[key]) for key in TERRAIN_PROFILE]
        if (not all(math.isfinite(value) for value in values) or values[0] < 0.5 or values[-1] > HEIGHT_MAX_M
                or any(a >= b for a, b in zip(values, values[1:]))):
            raise ValueError(f'terrain profile: the heights must rise from p10 to max, between 0.5 and {HEIGHT_MAX_M:.0f} m')
        kept['profile'] = dict(zip(TERRAIN_PROFILE, values))
    shallows = terrain.get('shallows')
    if shallows:
        rows = [(float(depth), float(share)) for depth, share in shallows]
        if (not all(math.isfinite(value) for row in rows for value in row)
                or not 1 <= len(rows) <= 8 or rows[0][0] <= 0 or rows[0][1] <= 0 or rows[-1][0] > 45 or rows[-1][1] > 0.4
                or any(a[0] >= b[0] or a[1] >= b[1] for a, b in zip(rows, rows[1:]))):
            raise ValueError('terrain shallows: up to 8 rows of [depth in metres, share of the square], both rising, at most 45 m and 0.4')
        kept['shallows'] = [[depth, share] for depth, share in rows]
    out['terrain'] = kept
    templates = recipe.get('templates')
    if templates is None:
        templates = []
    if not isinstance(templates, list):
        raise ValueError('templates must be a list of .map filenames')
    for name in templates:
        if (not isinstance(name, str) or not name.endswith('.map') or name == '.map'
                or os.path.basename(name) != name or '\\' in name or any(ord(char) < 32 for char in name)):
            raise ValueError('templates must contain .map filenames, not paths or invalid entries')
    out['templates'] = list(templates)
    out['template_skip'] = _fragments(recipe, 'template_skip')
    out['build_on_roads'] = bool(recipe.get('build_on_roads', False))
    return out


def _fragments(recipe, key):
    """A list of path fragments. Anything else is refused: a single text would be read letter by letter,
    and a list of letters matches nearly every prefab."""
    value = recipe.get(key)
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(fragment, str) for fragment in value):
        raise ValueError(f'{key} must be a list of path fragments, such as ["autospawn/decor/roadside/"]')
    return [fragment.strip().lower()[:200] for fragment in value if fragment.strip()]


def compile(recipe, out_folder, seed=0, catalog=None):          # noqa: A001 (the word for what it does)
    """(the game's world config, the mod's job) for one generation of a normalised recipe."""
    catalog = catalog or load_catalog()
    recipe = normalise(recipe, catalog)
    by_prefab = {row['prefab']: row for row in catalog['monuments']}
    by_kind = {row['key']: row for row in catalog['scenery']}

    blocked, priority, smallest = [], {}, {}
    for prefab, state in sorted(recipe['monuments'].items()):
        row = by_prefab[prefab]
        fragment = row['path'][len('autospawn/'):-len('.prefab')] + '.'        # 'monument/medium/compound.': one prefab, not its namesakes
        if state == 'block':
            blocked.append('autospawn/' + fragment)
        elif state == 'want':
            # In a step that places only so many (one desert military base a map), the game's own picks of
            # the same priority would take the place: a wanted one is tried before them.
            step = step_of(row, catalog)
            priority[fragment] = 'First' if step and step_count(step, recipe) > 0 else 'Highest'
            smallest[fragment] = 0
    centre = recipe['centre']['monument']
    if centre:
        fragment = by_prefab[centre]['path'][len('autospawn/'):-len('.prefab')] + '.'
        priority[fragment] = 'First'
        smallest[fragment] = 0

    steps = {folder: dict(rule) for folder, rule in recipe['steps'].items()}
    for key, state in sorted(recipe['scenery'].items()):
        row = by_kind[key]
        if state == 'block':
            blocked += row['fragments']
        elif state == 'want' and row['step']:
            steps.setdefault(row['step'], {}).setdefault('min_world_size', 0)
    labs = recipe['underwater_labs']
    if labs:        # a number in the recipe's range, the same every time for one seed
        steps.setdefault('monument/underwater_lab', {})['count'] = labs['min'] + seed % (labs['max'] - labs['min'] + 1)

    world = dict(recipe['world'])
    world['PrefabBlacklist'] = blocked + recipe['block_extra']
    ring = {'always': 0, 'never': 1000000, 'game': -1}
    terrain = recipe['terrain']
    profile = terrain.get('profile')
    job = {'Out': out_folder,
           'RingMinWorldSize': ring[recipe['rail_ring']], 'RailsideMinWorldSize': ring[recipe['rail_ring']] if recipe['rail_ring'] != 'never' else -1,
           'RoadRingMinWorldSize': ring[recipe['road_ring']],
           'MonumentMinWorldSize': smallest, 'MonumentPriority': priority,
           'Steps': {folder: {'Count': rule.get('count', -1), 'MinWorldSize': rule.get('min_world_size', -1),
                              'DistanceSame': rule.get('distance_same', -1), 'DistanceOther': rule.get('distance_other', -1)}
                     for folder, rule in sorted(steps.items())},
           'RelaxDistances': recipe['relax_distances'],
           'CentreMonument': by_prefab[centre]['path'][len('autospawn/'):-len('.prefab')] + '.' if centre else '',
           'CentreStart': recipe['centre']['start_m'], 'CentreGrow': recipe['centre']['grow_m'],
           'CentreAnyTier': recipe['centre']['any_tier'],
           'LandShare': terrain.get('land_share', 0.0), 'CoastLift': terrain.get('coast_lift_m', 0.0),
           'CoastFade': terrain.get('coast_fade_m', 25.0), 'HeightScale': terrain.get('height_scale', 1.0),
           'HeightProfile': [profile[key] for key in TERRAIN_PROFILE] if profile else [],
           'ReliefRadius': terrain.get('relief_radius_m', 0.0), 'ReliefKeep': terrain.get('relief_keep', 1.0),
           'MiddlePlateau': terrain.get('middle_plateau_m', 0.0),
           # The game's world config file drops this share and keeps its own: the mod sets it when it differs.
           'BiomeJungle': recipe['world'][JUNGLE[0]] if recipe['world'][JUNGLE[0]] != JUNGLE[2] else -1.0,
           'SeaDepths': [row[0] for row in terrain.get('shallows', [])], 'SeaShares': [row[1] for row in terrain.get('shallows', [])]}
    return world, job

