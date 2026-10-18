"""Make one map from a recipe, or have the game load one already made.

Generating:
  1. turn the recipe into the game's world config and the mod's job file (recipe.py);
  2. run the game until the mod has saved the map and its report (runner.py);
  3. move the map into the library, then merge the templates, describe, check and draw it.

The load test answers one question: does the game accept final.map, the file with the templates
merged in? The game loads it the way a server loads a custom map, up to the moment it would open
its network, and the mod writes down what it loaded.
"""
import glob
import gzip
import hashlib
import json
import os
import random
import shutil
import time
import uuid

from . import library, paths, recipe as recipes, runner

PROGRESS = r'^(Generating|Loading|\[RustMapGen\])|Process|Monument|Rail|Road'
LOAD_PROGRESS = r'^(Loading|Spawning|\[RustMapGen\])|World|Monument|Navmesh|Initial'
# What a log line must look like to count as a problem in a load test.
TROUBLE = ('Exception', 'World File Mismatch', 'World File Outdated', "Couldn't find prefab", 'Prefab not found',
           'missing prefab', 'Failed to load', 'Error spawning')


def find_recipe(name):
    """A recipe by name: one saved in <data>/recipes before a preset that came with the code."""
    for folder in (paths.data('recipes'), paths.package('presets')):
        path = os.path.join(folder, name + '.json')
        if name and os.path.isfile(path):
            return path
    return None


def _write_json(path, value):
    with open(path + '.partial', 'w') as handle:
        json.dump(value, handle, indent=1)
    os.replace(path + '.partial', path)


def _identity():
    folder = paths.game('server', paths.IDENTITY)
    os.makedirs(folder, exist_ok=True)
    return folder


def generate(recipe_path, seed=None, size=None, note='', say=print):
    """Make one map. Returns the library's summary of it."""
    paths.ensure_data()
    with open(recipe_path) as handle:
        recipe = recipes.normalise(json.load(handle))
    if not recipe['name']:
        recipe['name'] = recipes.identifier(os.path.splitext(os.path.basename(recipe_path))[0].replace('.', '-')[:60], 'recipe filename')
    size = size or recipe['size']
    seed = seed or random.randint(100000000, 999999999)
    if not 1000 <= size <= 6000 or not 1 <= seed <= 2147483647:
        raise ValueError('size must be 1000 to 6000 and seed 1 to 2147483647')
    library.template_paths(recipe)          # a missing template fails before anything is made
    recipe['size'] = size
    # Settings made for a larger map may ask more land than this size reaches: brought down, and the map's own
    # record of its settings (what its check holds it to) says so.
    asked, most = recipe['terrain'].get('land_share'), recipes.land_share_max(size)
    if asked and asked > most:
        recipe['terrain']['land_share'] = most
        say(f'land {asked * 100:.0f} % brought to {most * 100:.0f} %, the most for a map of {size:,}', flush=True)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.gmtime())
    name = f"{recipe['name']}-{size}-{seed}-{stamp}-{uuid.uuid4().hex[:6]}"
    job = paths.data('jobs', name)
    os.makedirs(job)
    world, job_settings = recipes.compile(recipe, job, seed)
    _write_json(os.path.join(job, 'job.json'), job_settings)
    build = runner.game_build()
    say(f'{name}: generating (game build {build}), size {size}, seed {seed}', flush=True)
    started = time.time()
    try:
        with runner.lock():
            identity = _identity()
            # A map of this size and seed already in the game's folder would be loaded, not made.
            for stale in glob.glob(os.path.join(identity, f'proceduralmap.{size}.{seed}.*')):
                os.unlink(stale)
            _write_json(os.path.join(identity, 'world.json'), world)
            state, seconds, facts = runner.run(job, ['+server.level', 'Procedural Map', '+server.seed', str(seed),
                                                     '+server.worldsize', str(size), '+world.configfile', 'world.json'], PROGRESS, say)
            made = None
            if state == 'done':
                report = runner.read_json(os.path.join(job, 'report.json')) or {}
                file = os.path.basename(str(report.get('file') or '').replace('\\', '/'))
                if report.get('size') == size and report.get('seed') == seed and file.startswith(f'proceduralmap.{size}.{seed}.') and file.endswith('.map'):
                    made = os.path.join(identity, file)
                if not made or not os.path.isfile(made):
                    state, made = 'failed', None
            runner.gzip_log(job)
            folder = paths.data('maps', name)
            os.makedirs(folder)
            if made:
                shutil.move(made, os.path.join(folder, 'raw.map'))
            for stale in glob.glob(os.path.join(identity, f'proceduralmap.{size}.{seed}.*')):
                os.unlink(stale)                # the save and anything else the game left of this map
    except BaseException:
        shutil.rmtree(job, ignore_errors=True)  # refused or interrupted before a map: no job folder is left behind
        raise
    for file in ('report.json', 'census.json', 'log.txt.gz'):
        if os.path.exists(os.path.join(job, file)):
            shutil.move(os.path.join(job, file), os.path.join(folder, file))
    table = os.path.join(job, 'prefabs.json')
    if os.path.exists(table):
        os.replace(table, library.prefab_table())
    _write_json(os.path.join(folder, 'recipe.json'), recipe)
    run = {'name': name, 'recipe': recipe['name'], 'size': size, 'seed': seed, 'note': note, 'state': state,
           'game_build': build, 'wall_seconds': seconds, 'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(started)), **facts}
    _write_json(os.path.join(folder, 'run.json'), run)
    shutil.rmtree(job, ignore_errors=True)
    say(f'{name}: {state} after {seconds} s', flush=True)
    if not made:
        reason = {'mod': 'the game did not load the mod (see log.txt.gz; after a game update run "python -m rustmapgen build-mod")',
                  'timeout': 'the game took too long'}.get(state, 'the game made no map')
        raise RuntimeError(f'no map: {reason}. Folder: {folder}')
    census = os.path.join(folder, 'census.json')
    if os.path.exists(census) and os.path.exists(library.prefab_table()):
        try:
            recipes.build_catalog(library.prefab_table(), census)
        except (OSError, ValueError, KeyError) as error:
            say(f'  the catalog was not rebuilt: {error}', flush=True)
    summary = library.add(name)
    say(json.dumps(summary, indent=1), flush=True)
    return summary


def load_test(name, say=print):
    """Have the game load a map's final.map. Writes load.json beside it and returns it."""
    folder = paths.data('maps', name)
    final = os.path.join(folder, 'final.map')
    if not library.NAME.match(name) or not os.path.isfile(final):
        raise FileNotFoundError(f'{final}: no such map')
    token = uuid.uuid4().hex[:12]
    job = paths.data('jobs', f'load-{token}')
    os.makedirs(job)
    try:                                # the copy of the map goes with the job, however the test ends
        file = os.path.join(job, f'loadtest{token}.map')
        shutil.copyfile(final, file)
        digest = hashlib.sha256()
        with open(file, 'rb') as handle:
            for block in iter(lambda: handle.read(1 << 20), b''):
                digest.update(block)
        _write_json(os.path.join(job, 'job.json'), {'Out': job, 'LoadTest': True})
        build = runner.game_build()
        say(f'{name}: the game (build {build}) loads final.map', flush=True)
        url = 'file:///' + file.replace('\\', '/').lstrip('/')
        with runner.lock():
            identity = _identity()
            state, seconds, facts = runner.run(job, ['+server.level', 'Procedural Map', '+server.levelurl', url], LOAD_PROGRESS, say)
            for left in glob.glob(os.path.join(identity, f'loadtest{token}*')):
                os.unlink(left)
        runner.gzip_log(job)
        loaded = runner.read_json(os.path.join(job, 'loaded.json'))
        trouble = []
        log = os.path.join(job, 'log.txt.gz')
        if os.path.exists(log):
            shutil.copyfile(log, os.path.join(folder, 'load-log.txt.gz'))
            with gzip.open(log, 'rt', errors='replace') as handle:
                for line in handle:
                    if '[RustMapGen] the map generator never opens' in line:
                        break
                    if any(word in line for word in TROUBLE):
                        trouble.append(line.strip()[:200])
        result = {'state': state, 'seconds': seconds, 'game_build': build, 'map_sha256': digest.hexdigest(), 'loaded': loaded,
                  'log_trouble': trouble[:12], 'tested_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **facts}
        _write_json(os.path.join(folder, 'load.json'), result)
    finally:
        shutil.rmtree(job, ignore_errors=True)
    library.add(name)
    say(json.dumps(result, indent=1), flush=True)
    if state != 'done' or not loaded:
        raise RuntimeError('the game did not load the map: see load-log.txt.gz in its folder')
    if trouble:
        say(f'{name}: the game loaded it, with {len(trouble)} problem line{"s" if len(trouble) != 1 else ""} in its log '
            '(log_trouble above; the whole log is load-log.txt.gz)', flush=True)
    return result
