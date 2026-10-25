"""The maps made on this computer, each in a folder of its own under <data>/maps/<name>/:

    raw.map        the map as the game saved it
    final.map      the same with the templates merged in: the file a server loads
    map.json       everything the panel shows (card.json is the short form for the list)
    terrain.webp, thumb.webp, map_labels.webp   pictures
    recipe.json, run.json, report.json, census.json, log.txt.gz   what made it

final.map is written once. A server and its players must hold the very same file, so a map that
is described again keeps the file it has unless asked to merge again.
"""
import glob
import hashlib
import json
import os
import re
import shutil

import numpy as np

from . import analyze, checks, paths, render
from .mapfile import MapFile, load_names
from .templates import Template, compare_all

NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$')


def prefab_table():
    """The game's prefab table, as the newest map's run wrote it."""
    return paths.data('tables', 'prefabs.json')


def template_paths(recipe):
    """Every template a recipe names, or an error before anything is made. One template per monument: two
    around the same monument would both be added to every copy of it, one on top of the other."""
    out = []
    around = {}
    for name in (recipe or {}).get('templates', []):
        path = paths.data('templates', name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f'the recipe asks for a template that is not in {paths.data("templates")}: {name} '
                                    '(add it there, or take it out of the recipe\'s "templates")')
        try:
            with open(path + '.json') as handle:
                info = json.load(handle)
        except (OSError, ValueError):
            info = {}            # not described yet: its monument is known only once the game's prefab table is
        if info.get('prefab'):
            if info['prefab'] in around:
                raise ValueError(f"two templates around the {info.get('monument') or info['prefab']}: {around[info['prefab']]} and {name}; "
                                 'tick one of them')
            around[info['prefab']] = name
        out.append(path)
    return out


def _templates(recipe, names):
    out = [Template(path, names, (recipe or {}).get('template_skip', ())) for path in template_paths(recipe)]
    anchors = [template.anchor for template in out]
    for anchor in set(anchors):
        if anchors.count(anchor) > 1:       # templates not described before the map was made
            raise ValueError(f'two templates around {analyze.label(_short(anchor))}: tick one of them')
    return out


def _short(path):
    return path.rsplit('/', 1)[1][:-len('.prefab')]


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _card(entry):
    keep = ('name', 'title', 'size', 'seed', 'recipe', 'created', 'note', 'state', 'files')
    card = {key: entry.get(key) for key in keep}
    load = entry.get('load_test')
    card['loads'] = None if not load else bool(load.get('state') == 'done' and load.get('loaded'))
    facts = entry['facts']
    card.update({'rail_km': round(facts['rail_m'] / 1000, 1), 'monuments': len(set(facts['monument_counts'])),
                 'outpost_m': facts['outpost_from_centre_m'], 'land': facts['terrain'].get('land'),
                 'highest_m': facts['terrain'].get('highest_m'),
                 'checks_ok': sum(1 for c in entry['checks'] if c['ok']), 'checks': len(entry['checks']),
                 'seconds': (entry.get('run') or {}).get('wall_seconds'),
                 'game_branch': (entry.get('run') or {}).get('game_branch') or 'public', 'game_build': (entry.get('run') or {}).get('game_build')})
    return card


def _write(folder, entry):
    for name, value in (('map.json', entry), ('card.json', _card(entry))):
        partial = os.path.join(folder, name + '.partial')
        with open(partial, 'w') as handle:
            json.dump(value, handle, separators=(',', ':'))
        os.replace(partial, os.path.join(folder, name))


def _read(folder, file):
    try:
        with open(os.path.join(folder, file)) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _template_sha256(path):
    try:
        return _sha256(path)
    except OSError:
        return None


def _merge(folder, recipe, names, world, fresh, map_sha256):
    """How each template sits on the final map, with the template's checksum. When the map is described
    again without being merged again, a template that is gone or has changed since keeps the record of the
    merge, as long as final.map is the very file that record was made of: final.map holds the template as
    it was then, and it is not checked against another one. A record of other bytes fails."""
    previous = _read(folder, 'map.json') or {}
    before = {row.get('template'): row for row in previous.get('merge') or []}
    same_file = previous.get('map_sha256') == map_sha256
    slots, templates = [], []
    for name in recipe.get('templates') or []:
        path = paths.data('templates', name)
        digest = _template_sha256(path)
        old = before.get(name)
        template = None
        if fresh or digest is not None and (old or {}).get('template_sha256', digest) == digest:
            try:
                template = Template(path, names, recipe.get('template_skip', ()))
            except (OSError, ValueError, KeyError):
                if fresh:
                    raise
        if template is None:
            if old and same_file:
                slots.append(dict(old, kept='the template is gone or has changed since this map was made: this is how it was merged'))
            elif old:
                slots.append(dict(old, ok=False, unverifiable=True, kept='final.map has changed since this merge was recorded, and '
                                                                         'the template is gone or has changed: it cannot be checked'))
            else:
                slots.append({'template': name, 'monument': name, 'copies': 0, 'placed': 0, 'of': 0, 'ok': False, 'unverifiable': True,
                              'kept': 'the template is gone and no record of its merge was kept: it cannot be checked'})
            continue
        slots.append((template, digest))
        templates.append(template)
    results = iter(compare_all(templates, world))
    merge = []
    for slot in slots:
        if isinstance(slot, dict):
            merge.append(slot)
            continue
        template, digest = slot
        result = next(results)
        merge.append({'template': os.path.basename(template.path), 'monument': analyze.label(_short(template.anchor)),
                      'copies': result['copies'], 'placed': result['found'], 'of': result['expected'],
                      'worst_m': result['worst_m'], 'worst_deg': result['worst_deg'], 'worst_scale': result['worst_scale'],
                      'ok': result['ok'], 'dropped': len(template.dropped), 'moved': template.moved, 'template_sha256': digest})
    return merge


def _blocked_present(world, names, fragments):
    """Prefabs on the final map whose path holds a fragment of the recipe's own block list: {fragment: how many}.
    The game keeps them off the map; a template can still bring one in."""
    out = {}
    if not fragments or not len(world.id):
        return out
    ids, counts = np.unique(world.id, return_counts=True)
    for ident, count in zip(ids.tolist(), counts.tolist()):
        path = (names.get(ident) or '').lower()
        for fragment in fragments:
            if fragment in path:
                out[fragment] = out.get(fragment, 0) + count
    return out


def add(name, names=None, remerge=False):
    """A map the generator brought back: merge the templates, describe, check and draw it."""
    names = names or load_names(prefab_table())
    folder = paths.data('maps', name)
    run = _read(folder, 'run.json') or {}
    recipe = _read(folder, 'recipe.json')
    if not isinstance(recipe, dict):
        raise ValueError(f'{folder}: recipe.json is missing')
    final = os.path.join(folder, 'final.map')
    fresh = remerge or not os.path.exists(final)
    if fresh:
        templates = _templates(recipe, names)
        world = MapFile(os.path.join(folder, 'raw.map'))
        for template in templates:
            template.apply(world)
        if recipe.get('build_on_roads'):
            world.clear_topology(analyze.TOPOLOGY['road'])
        # A load test belongs to the bytes it loaded: a new file needs a new one.
        load_test = _read(folder, 'load.json')
        if load_test:
            stale = dict(load_test, state='stale', reason='final.map was written again; test it again')
            with open(os.path.join(folder, 'load.json.partial'), 'w') as handle:
                json.dump(stale, handle)
            os.replace(os.path.join(folder, 'load.json.partial'), os.path.join(folder, 'load.json'))
        world.save(final)
        del world
    map_sha256 = _sha256(final)
    world = MapFile(final)             # what is described and drawn is the file a server would load
    load_test = _read(folder, 'load.json')
    if load_test and load_test.get('state') == 'done' and load_test.get('map_sha256') != map_sha256:
        load_test = dict(load_test, state='stale', reason='the load test was of other bytes')
    merge = _merge(folder, recipe, names, world, fresh, map_sha256)
    facts = analyze.describe(world, names)
    analyze.ground_patches(world, facts['monuments'], checks.load_ground_shapes(), checks.STRUCTURE['ground_patch_m'])
    facts['blocked_extra'] = _blocked_present(world, names, recipe.get('block_extra'))
    lines = facts.pop('lines')
    files = render.save(world, folder)
    files['map'] = 'final.map'
    entry = {'name': name, 'title': f"{recipe.get('title') or recipe.get('name') or 'Custom'} · seed {run.get('seed')}",
             'size': facts['size'], 'seed': run.get('seed'), 'recipe': run.get('recipe'), 'note': run.get('note', ''),
             'state': run.get('state'), 'created': run.get('started_utc'), 'run': run, 'map_bytes': os.path.getsize(final),
             'map_sha256': map_sha256, 'facts': facts, 'lines': lines, 'merge': merge,
             'checks': checks.checks(recipe, facts, checks.load_reference(), _read(folder, 'census.json'))
             + [check for check in [checks.template_check(merge)] if check],
             'census': _read(folder, 'census.json'), 'report': _read(folder, 'report.json'), 'load_test': load_test,
             'settings': recipe, 'files': files, 'margin_m': render.MARGIN}
    files['labels'] = render.labelled(folder, entry)
    if _sha256(final) != map_sha256:
        raise ValueError('final.map changed while it was checked; try again')
    _write(folder, entry)
    card = _card(entry)
    return {'name': name, 'checks': f"{card['checks_ok']} of {card['checks']}", 'rail_km': card['rail_km'],
            'failed': [c['rule'] + ': ' + c['detail'] for c in entry['checks'] if not c['ok']]}


def cards():
    out = []
    for path in glob.glob(paths.data('maps', '*', 'card.json')):
        try:
            with open(path) as handle:
                out.append(json.load(handle))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda card: card.get('created') or '', reverse=True)


def entry(name):
    if not NAME.match(name or ''):
        raise ValueError('bad name')
    with open(paths.data('maps', name, 'map.json')) as handle:
        return json.load(handle)


def remove(name):
    """Delete one map and everything made with it."""
    if not NAME.match(name or ''):
        raise ValueError('bad name')
    folder = paths.data('maps', name)
    if not os.path.isfile(os.path.join(folder, 'map.json')) and not os.path.isfile(os.path.join(folder, 'run.json')):
        raise FileNotFoundError(name)
    shutil.rmtree(folder)
