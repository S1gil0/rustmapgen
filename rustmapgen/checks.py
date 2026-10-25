"""What every map is checked for, read from its description (analyze.describe).

Two kinds:

  structure  can it be played: the rail ring closed and joined, rail grades and bends, rails out
             of the water, sea and land tagged as the heights say, monuments on the ground, power
             line towers on the ground, every way down to the train tunnels joined to an entrance;
  recipe     is it what was asked for: wanted monuments placed, nothing blocked on the map, the
             rail ring there, the share of land, roads that can be built on, the monument in the
             middle, the templates merged.

The limits stand a little outside the worst values measured on maps people play on.
"""
import json

import numpy as np

from . import analyze, paths

STRUCTURE = {'max_grade': 0.33, 'max_turn_deg': 36.0, 'side_max_grade': 0.43, 'side_max_turn_deg': 40.0,
             'lowest_rail_m': -1.0, 'sea_tag_agrees': 0.99, 'ground_off_m': 2.5,
             # A monument's own ground may be off its usual height by more than `ground_patch_m` on no more
             # than `ground_patch_m2` (analyze.ground_grid). A Launch Site seen floating in the game on
             # 2026-10-25 had over 8,000 m² more than 5 m low; in 400 maps 29 Launch Sites were over the
             # limit, other large monuments 1 in 400 or none. Off on more than `ground_patch_share` of its
             # ground, the monument itself has changed (a game update), not its neighbours: the usual
             # ground is then out of date, and the map is not turned down for it.
             'ground_patch_m': 5.0, 'ground_patch_m2': 400, 'ground_patch_share': 0.4,
             # A power line tower may stand this far above where the game puts one. The game lays the
             # line before a small cave cuts its hollow, and a tower over the hollow keeps its height:
             # its legs hang in the air. 99 towers in 100 stand within 0.9 m.
             'tower_air_m': 3.0,
             # Stairs to the train tunnels that end further under the ground than this need an entrance
             # bunker over them: under a bunker they end 15.0 m down, under a monument's own entrance 5.6 to 6.0.
             'stairs_open_m': 10.0}
# Monuments with a way down to the train tunnels of their own: no bunker stands over those stairs.
OWN_ENTRANCE = ('airfield_1', 'compound', 'excavator_1', 'ferry_terminal_1', 'harbor_1', 'harbor_2', 'launch_site_1',
                'military_tunnel_1', 'powerplant_1', 'trainyard_1', 'water_treatment_plant_1')
TOWER_RULE = 'Power line towers on the ground'
TUNNEL_RULE = 'Tunnel entrances joined to the tunnels'
GROUND_RULE = 'Monuments on the ground'
# How far the share of land may be from what the recipe asks.
LAND_SHARE_OFF = 0.05
# One power pole stays on a map with the road poles blocked: not a finding.
_STRAY = {'road power poles': 1}


def load_reference():
    try:
        with open(paths.package('reference.json')) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


# Each monument's usual ground, measured on many maps by the authors (scripts/ground_shapes.py) and
# made again when a game update changes monuments.
GROUND_SHAPES = 'ground.npz'


def load_ground_shapes(path=None):
    """{prefab: {'usual', 'own', 'radius'}}; empty when the table is missing or unreadable."""
    try:
        with np.load(path or paths.package(GROUND_SHAPES)) as table:
            prefabs = sorted(key[:-len(':usual')] for key in table.files if key.endswith(':usual'))
            return {prefab: {'usual': table[f'{prefab}:usual'].astype(np.float32), 'own': table[f'{prefab}:own'].astype(bool),
                             'radius': int(table[f'{prefab}:radius'])} for prefab in prefabs}
    except (OSError, ValueError, KeyError):
        return {}


def ground_shape(grids):
    """(usual, own) of a monument from its grids on many maps: the middle value of each cell, and the
    cells where nine maps in ten agree with it within a metre (its own ground, which it levels itself)."""
    stack = np.stack(grids).astype(np.float32)
    usual = np.median(stack, axis=0)
    return usual, np.percentile(np.abs(stack - usual), 90, axis=0) < 1.0


def structure(facts, reference=None):
    """The checks every map gets, whatever its recipe."""
    out = []

    def add(rule, ok, detail):
        out.append({'rule': rule, 'ok': bool(ok), 'detail': detail, 'kind': 'structure'})

    rails, coast = facts.get('rails') or {}, facts.get('coast') or {}
    if rails.get('paths'):
        where = rails.get('loose_at') or []
        miss = (rails.get('near_misses') or [None])[0]
        add('Rail ring closed, nothing cut off', rails['rings'] == 1 and rails['loose'] == 0,
            f"{rails['rings']} ring of {rails['ring_m'] / 1000:.1f} km, {rails['sidings']} sidings and {rails['spurs']} spurs joined to it"
            + (f", {rails['loose']} stretches disconnected from the ring" if rails['loose'] else '')
            + (f" (around x {where[0][0]}, z {where[0][1]}; red on the map)" if rails['loose'] and where else '')
            + ((f"; at x {miss['x']}, z {miss['z']} a track meets another at {miss['angle_deg']:.0f}°, and the game joins tracks only where they run within 10° of each other"
                if 'angle_deg' in miss else
                f"; at x {miss['x']}, z {miss['z']} a track ends {miss['gap_m']:.{3 if miss['gap_m'] < 0.2 else 2}f} m from another"
                + (f" ({'below' if miss['higher_m'] > 0 else 'above'} it)" if abs(miss['higher_m']) >= 0.5 * miss['gap_m'] else '')
                + ', and the game joins tracks only where they meet within 0.1 m') if rails['loose'] and miss else ''))
        # The ring is held to what rings people play on show. A track off it may be as steep as the game's
        # own ramps to the tunnels (42 %), which a workcart climbs at full speed.
        add('Rail grades and curves', rails['ring_grade'] <= STRUCTURE['max_grade'] and rails['ring_turn_deg'] <= STRUCTURE['max_turn_deg']
            and rails['side_grade'] <= STRUCTURE['side_max_grade'] and rails['side_turn_deg'] <= STRUCTURE['side_max_turn_deg'],
            f"ring: steepest climb {rails['ring_grade'] * 100:.0f} % over 30 m (limit {STRUCTURE['max_grade'] * 100:.0f} %), "
            f"sharpest bend {rails['ring_turn_deg']:.0f}° (limit {STRUCTURE['max_turn_deg']:.0f}°); "
            f"tracks off it: {rails['side_grade'] * 100:.0f} % (limit {STRUCTURE['side_max_grade'] * 100:.0f} %), "
            f"{rails['side_turn_deg']:.0f}° (limit {STRUCTURE['side_max_turn_deg']:.0f}°)")
        add('Rails above the water', rails['lowest_m'] is not None and rails['lowest_m'] >= STRUCTURE['lowest_rail_m'],
            f"lowest rail {rails['lowest_m']} m above sea level")
    if coast:
        add('Sea and land tagged as the heights say', coast['sea_tag_agrees'] >= STRUCTURE['sea_tag_agrees'],
            f"{coast['sea_tag_agrees'] * 100:.2f} % of the square agrees, {coast['land_tagged_sea'] * 100:.2f} % of the land is tagged as sea")
    yardstick = (reference or {}).get('ground_m')
    if yardstick:
        off = [(abs(item['ground_m'] - yardstick[item['prefab']]), item) for item in facts['monuments']
               if 'ground_m' in item and item['prefab'] in yardstick]
        bad = sorted(((gap, item) for gap, item in off if gap > STRUCTURE['ground_off_m']), key=lambda row: -row[0])
        # The whole ground of each monument (analyze.ground_patches), on a map measured with the usual grounds.
        shaped = [(max(item['ground_low_m2'], item['ground_high_m2']), item) for item in facts['monuments'] if item.get('ground_own_m2')]
        share = STRUCTURE['ground_patch_share']
        cut = sorted(((area, item) for area, item in shaped if STRUCTURE['ground_patch_m2'] < area <= share * item['ground_own_m2']),
                     key=lambda row: -row[0])
        changed = sorted({item['label'] for area, item in shaped if area > share * item['ground_own_m2']})
        # The two halves know different monuments (reference.json, ground.npz): each one is counted once.
        checked = len({id(item) for _, item in off} | {id(item) for _, item in shaped})
        detail = (f'{checked} monuments stand as the game stands them' + (f' ({len(shaped)} looked at on all of their ground)' if shaped else '')
                  if not bad and not cut else
                  ', '.join([f"{item['label']} {item['ground_m'] - yardstick[item['prefab']]:+.1f} m" for _, item in bad[:5]]
                            + [f"{item['label']}: {area:,} m² of its ground more than {STRUCTURE['ground_patch_m']:g} m "
                               + ('lower (it floats there)' if item['ground_low_m2'] >= item['ground_high_m2'] else 'higher (it is buried there)')
                               for area, item in cut[:5]]))
        if changed:
            detail += (f"; the game has changed {', '.join(changed)} since RustMapGen measured {'its' if len(changed) == 1 else 'their'} "
                       'ground: not counted (a newer RustMapGen brings new measurements)')
        add(GROUND_RULE, not bad and not cut, detail)
    towers = facts.get('towers') or {}
    if towers.get('count'):
        limit = STRUCTURE['tower_air_m']
        bad = sorted((item for item in towers['in_the_air'] if item['air_m'] > limit), key=lambda item: -item['air_m'])
        add(TOWER_RULE, not bad,
            f"{towers['count']} towers, the highest {max(towers['highest_air_m'], 0.0):.1f} m above where it should stand (limit {limit} m)" if not bad else
            '; '.join(f"a tower {item['air_m']:.1f} m in the air at x {item['x']:.0f}, z {item['z']:.0f}"
                      + (f" ({item['near_m']} m from {item['near']})" if item.get('near') else '') for item in bad[:3])
            + f' (limit {limit} m)')
    tunnels = facts.get('tunnels') or {}
    if tunnels.get('stations') or tunnels.get('bunkers') or tunnels.get('ways_down'):
        ways = tunnels['ways_down']
        open_air = [way for way in ways if not way['bunker'] and way['depth_m'] is not None and way['depth_m'] > STRUCTURE['stairs_open_m']]
        under_monument = sum(1 for way in ways if not way['bunker']) - len(open_air)
        own = sum(facts['monument_counts'].get(prefab, 0) for prefab in OWN_ENTRANCE)
        wrong = [f'the entrance at x {x:.0f}, z {z:.0f} has no stairs under it' for x, z in tunnels['bunkers_without_stairs']]
        wrong += [f"the stairs at x {way['x']:.0f}, z {way['z']:.0f} end {way['depth_m']:.0f} m under the ground with no entrance over them" for way in open_air]
        if tunnels['stations'] != len(ways):
            wrong.append(f"{tunnels['stations']} stations for {len(ways)} ways down")
        if under_monument < own:
            wrong.append(f'{own} monuments have an entrance of their own, only {under_monument} of them a way down')
        add(TUNNEL_RULE, not wrong, '; '.join(wrong[:4]) if wrong else
            f"{len(ways)} ways down for {tunnels['stations']} stations: {len(ways) - under_monument} under an entrance bunker, {under_monument} under a monument")
    return out


def checks(recipe, facts, reference=None, census=None):
    """The structure checks, then what the recipe asks of the map. The census (what the mod saw) explains some."""
    out = structure(facts, reference)
    if not recipe:
        return out

    def add(rule, ok, detail):
        out.append({'rule': rule, 'ok': bool(ok), 'detail': detail, 'kind': 'recipe'})

    counts = facts['monument_counts']
    states = recipe.get('monuments') or {}
    wanted = [prefab for prefab, state in states.items() if state == 'want']
    if wanted:
        try:
            from . import recipe as recipes
            catalog = {row['prefab']: row for row in recipes.load_catalog()['monuments']}
        except (OSError, ValueError, KeyError):
            catalog = {}

        try:
            crowded = {row['prefab']: (step, places) for step, places, rows in recipes.over_wanted(recipe, recipes.load_catalog())
                       for row in rows}
        except (OSError, ValueError, KeyError):
            crowded = {}

        def why(prefab):                # a biome at 0 % is a reason the game gives no room; so is a step that places fewer
            off = recipes.biomes_off(catalog.get(prefab, {}), recipe.get('world') or {}) if catalog else []
            if off:
                return f" (needs {' or '.join(off)}, set to 0 %)"
            if prefab in crowded:
                step, places = crowded[prefab]
                return f" (the {step['label']} step places {places} a map)"
            return ''
        missing = [analyze.label(prefab) + why(prefab) for prefab in wanted if not counts.get(prefab)]
        add('Wanted monuments placed', not missing, f'all {len(wanted)} placed' if not missing else 'not placed: ' + ', '.join(missing))
    present = [analyze.label(prefab) for prefab, state in states.items() if state == 'block' and counts.get(prefab)]
    from . import recipe as recipes
    scenery = [key for key, state in recipes.scenery_states(recipe).items()
               if state == 'block' and facts['features'].get(key, 0) > _STRAY.get(key, 0)]
    # The recipe's own fragments (Block): the game keeps them off the map, a template can bring one in.
    extra = [f'{fragment} ({count})' for fragment, count in sorted((facts.get('blocked_extra') or {}).items())]
    add('Nothing blocked is on the map', not present and not scenery and not extra,
        'none present' if not present and not scenery and not extra else 'present: ' + ', '.join(present + scenery + extra))
    rails_on = (recipe.get('world') or {}).get('AboveGroundRails', True)
    if rails_on and recipe.get('rail_ring') == 'always':
        ring = (facts.get('rails') or {}).get('ring_m', 0)
        add('Rail ring', (facts.get('rails') or {}).get('rings') == 1,
            f"a ring of {ring / 1000:.1f} km, {facts['rail_m'] / 1000:.1f} km of rails in all" if ring else 'no rail ring on the map: the game found no room for one')
    asked = (recipe.get('terrain') or {}).get('land_share')
    if asked:
        land = (facts.get('terrain') or {}).get('land')
        # The most this seed's land could give: the shore moves 20 m at the most (the mod's Land step says what that reaches).
        step = next((row for row in (census or {}).get('steps', []) if row.get('step') == 'Land'), {})
        reach = (step.get('land_share_by_shore_move_m') or {}).get('20')
        short = land is not None and reach is not None and land < asked - LAND_SHARE_OFF
        add('Land as asked', land is not None and abs(land - asked) <= LAND_SHARE_OFF,
            'the land of the map was not measured' if land is None else
            f'{land * 100:.0f} % of the square is land (asked for {asked * 100:.0f} %, give or take {LAND_SHARE_OFF * 100:.0f})'
            + (f'; the shore moves 20 m at the most, which on this seed reaches {reach * 100:.0f} %' if short else '')
            + (f'; a map of {facts["size"]:,} may ask at most {recipes.land_share_max(facts["size"]) * 100:.0f} %'
               if short and facts.get('size') and asked > recipes.land_share_max(facts['size']) else ''))
    if recipe.get('build_on_roads'):
        share = facts['terrain'].get('topology_road')
        add('Roads can be built on', share == 0, 'no cell of the map is tagged as road' if share == 0 else
            'the road tag was not measured' if share is None else f'{share * 100:.2f} % of the map is still tagged as road')
    centre = recipe.get('centre') or {}
    if centre.get('monument'):
        spots = [item for item in facts['monuments'] if item['prefab'] == centre['monument']]
        distance = round((spots[0]['x'] ** 2 + spots[0]['z'] ** 2) ** 0.5) if spots else None
        add(f"{analyze.label(centre['monument'])} around the middle", distance is not None and distance <= centre.get('within_m', 600),
            'not on the map' if distance is None else f"{distance} m from the middle (at most {centre.get('within_m', 600)} m)")
    return out


def template_check(merge):
    """Are the templates on the map: every copy of a monument holds all of its template's prefabs."""
    if not merge:
        return None
    # A template that cannot be checked (gone, with no record of the bytes on the map) fails, whatever the others show.
    unverifiable = [row for row in merge if row.get('unverifiable')]
    rows = [row for row in merge if row['copies'] and not row.get('unverifiable')]
    bad = [row for row in rows if row['placed'] != row['of'] or not row.get('ok', True)]
    absent = [row['monument'] for row in merge if not row['copies'] and not row.get('unverifiable')]
    detail = ', '.join(f"{row['monument']} {row['placed']} of {row['of']}" + (' (as merged)' if row.get('kept') else '')
                       for row in rows) or 'no monument with a template on the map'
    detail += f" (no {', '.join(absent)} on this map)" if absent else ''
    detail += f"; cannot be checked: {', '.join(row['template'] for row in unverifiable)}" if unverifiable else ''
    return {'rule': 'Templates merged', 'ok': bool(rows) and not bad and not unverifiable, 'kind': 'recipe', 'detail': detail}
