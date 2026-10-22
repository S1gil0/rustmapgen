"""The control panel: settings for new maps, a queue, and every map made so far.

    python -m rustmapgen panel [--port 5350] [--host 127.0.0.1] [--no-browser]

It listens on this computer only unless told otherwise. A page of another site cannot use it:
writes need a header such a page cannot send, and requests that name another host are refused
(so a site cannot reach it by pointing its own name at this computer's address). Opened to the
network, it answers to this computer's addresses and names, and to the names in the setting
`panel_hosts`.

Maps are made one at a time by `python -m rustmapgen generate`, started by the panel; more wait in
a queue. Closing the panel stops the queue, not a map that is being made.
"""
import gzip
import hashlib
import ipaddress
import json
import os
import random
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__, analyze, bake3d, game, library, models3d, paths, recipe as recipes, release, runner, staging
from .mapfile import TEMPLATE_RECORDS, TEMPLATE_WORLD, MapFile, load_names
from .templates import Template

PAGE = paths.package('page')
# The 3D view: its page, the scripts it loads (three.js among them) and, under /3d/data/, each map's files and the models.
PAGE3D = paths.package('page3d')
PAGE3D_FILES = {'map3d.js', 'ground.js', 'forest.js', 'monuments.js', 'scenery.js', 'nobuild.js', 'words.js', 'viewer/sky.js',
                'viewer/nature.js', 'viewer/noise.js', 'viewer/vendor/three.module.min.js', 'viewer/vendor/three.core.min.js',
                'viewer/vendor/OrbitControls.js', 'viewer/vendor/Sky.js'}
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.json': 'application/json', '.webp': 'image/webp', '.png': 'image/png', '.map': 'application/octet-stream',
         '.svg': 'image/svg+xml', '.gz': 'application/octet-stream', '.bin': 'application/octet-stream'}
RECIPE_NAME = re.compile(r'^[a-z0-9][a-z0-9-]{1,48}$')
TEMPLATE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,80}\.map$')
FILES = ('terrain.webp', 'thumb.webp', 'map_labels.webp', 'final.map')
QUEUE_LIMIT = 24
UPDATING = 'RustMapGen is updating itself: try again in a minute'


def refusal(queue, control):
    """Why no job may start now, or None: an update under way, done and waiting for the panel to start again,
    or one that did not finish (its files may be of two releases)."""
    if control.get('restarting'):
        return f'RustMapGen was updated to {control["restarting"]}: start the panel again'
    if queue.held:
        return UPDATING
    unfinished = release.pending()
    if unfinished:
        return f'the update of RustMapGen to {unfinished["version"]} did not finish: finish it first (Updates)'
    return None
TEMPLATE_LIMIT = 32 * 1024 * 1024
LOCAL_HOSTS = ('127.0.0.1', 'localhost', '[::1]')
METRICS = (('rail_km', 'Rails', 'km', 1), ('ring_km', 'Rail ring', 'km', 1), ('road_km', 'Roads', 'km', 1), ('river_km', 'Rivers', 'km', 1),
           ('powerline_km', 'Power lines', 'km', 1), ('monument_kinds', 'Different monuments', '', 0),
           ('outpost_m', 'Outpost from the middle', 'm', 0), ('land', 'Land', '%', 1),
           ('sea_to_1m', 'Sea no deeper than 1 m', '%', 1), ('sea_to_3m', 'Sea no deeper than 3 m', '%', 1),
           ('sea_to_6m', 'Sea no deeper than 6 m', '%', 1), ('sea_to_12m', 'Sea no deeper than 12 m', '%', 1),
           ('sea_to_20m', 'Sea no deeper than 20 m', '%', 1), ('sea_to_40m', 'Sea no deeper than 40 m', '%', 1),
           ('beach', 'Beach', '%', 1), ('low_coast', 'Land below 3 m', '%', 1),
           ('land_p50_m', 'Half of the land is below', 'm', 1), ('land_p90_m', '90 % of the land is below', 'm', 1),
           ('highest_m', 'Highest point', 'm', 0), ('land_steeper_30deg', 'Land steeper than 30°', '%', 1),
           ('rail_grade', 'Steepest rail climb', '%', 0), ('rail_turn', 'Sharpest rail bend', '°', 0),
           ('biome_arid', 'Desert', '%', 0), ('biome_temperate', 'Temperate', '%', 0), ('biome_tundra', 'Tundra', '%', 0),
           ('biome_arctic', 'Snow', '%', 0), ('biome_jungle', 'Jungle', '%', 0),
           ('topology_tier0', 'Tier 0 ground', '%', 0), ('topology_tier1', 'Tier 1 ground', '%', 0),
           ('topology_tier2', 'Tier 2 ground', '%', 0), ('prefabs', 'Prefabs', '', 0))


def numbers(facts):
    """The METRICS of one described map."""
    paths_of, terrain = facts['paths'], facts['terrain']
    rails, coast = facts.get('rails') or {}, facts.get('coast') or {}
    out = {'rail_km': facts['rail_m'] / 1000, 'road_km': paths_of.get('Road', {}).get('length_m', 0) / 1000,
           'river_km': paths_of.get('River', {}).get('length_m', 0) / 1000,
           'powerline_km': paths_of.get('Powerline', {}).get('length_m', 0) / 1000,
           'monument_kinds': len(facts['monument_counts']), 'outpost_m': facts['outpost_from_centre_m'],
           'prefabs': facts['prefabs']}
    if rails.get('paths'):
        out.update({'ring_km': rails['ring_m'] / 1000, 'rail_grade': rails['max_grade'] * 100, 'rail_turn': rails['max_turn_deg']})
    for key, _, unit, _ in METRICS:
        for source in (terrain, coast):
            if key in source:
                out[key] = source[key] * 100 if unit == '%' else source[key]
    return out


def job_arguments(job):
    """The command a queued job runs, after `python -m rustmapgen`. A job of the next update (game 'staging' in a job)
    names that game: the command keeps its days too, and works in that game's folder."""
    on_staging = ['--game', 'next'] if job.get('game') == 'staging' else []
    if job['kind'] == 'generate':
        out = ['generate', '--recipe', job['recipe_file'], '--note', job.get('note', '')] + on_staging
        if job.get('seed'):
            out += ['--seed', str(job['seed'])]
        if job.get('size'):
            out += ['--size', str(job['size'])]
        return out
    if job['kind'] == 'update':
        return ['update'] + (on_staging or (['--forced-wipe'] if job.get('forced_wipe')
                                            else ['--if-newer'] if job.get('if_newer') else []))
    if job['kind'] == 'remove-staging':
        return ['remove-next']
    if job['kind'] == 'models-3d':
        return [job['kind']]
    return ['load-test', job['name']] + on_staging


class Queue:
    """Jobs run one after another, each by `python -m rustmapgen ...` in a process of its own."""

    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = []
        self.process = None
        self.current = None
        self.last = None
        self.held = False               # while RustMapGen updates itself: nothing is taken in or started
        threading.Thread(target=self._pump, daemon=True).start()

    def add(self, jobs, whole=False):
        """Jobs into the queue, as many as there is room for (all or none when `whole`); returns how many."""
        with self.lock:
            if self.held or release.pending():
                return 0
            room = max(0, QUEUE_LIMIT - len(self.jobs))
            if whole and room < len(jobs):
                return 0
            self.jobs += jobs[:room]
        return min(len(jobs), room)

    def hold(self):
        """Keep the queue still for an update of RustMapGen itself. False, and nothing held, while a job runs or waits:
        the test and the hold are one step, so no job gets in between."""
        with self.lock:
            if self.held or self.process or self.jobs:
                return False
            self.held = True
            return True

    def let_go(self):
        with self.lock:
            self.held = False

    def clear(self):
        with self.lock:
            dropped, self.jobs = len(self.jobs), []
        return dropped

    def _start(self, job):
        os.makedirs(paths.data('runs'), exist_ok=True)
        log = paths.data('runs', time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + f"-{job['kind']}.log")
        command = [sys.executable, '-m', 'rustmapgen'] + job_arguments(job)
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        with open(log, 'w') as handle:
            process = subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       cwd=paths.ROOT, env=dict(os.environ, PYTHONUNBUFFERED='1'), **options)
        return process, log

    def _pump(self):
        while True:
            time.sleep(2)
            try:
                with self.lock:
                    if self.process and self.process.poll() is not None:
                        lines = self._lines(self.current['log'])
                        self.last = {'ok': self.process.returncode == 0, 'job': self.current['job'], 'name': self._name(lines),
                                     'finished': round(time.time(), 3),
                                     'seconds': round(time.time() - self.current['started']),
                                     'tail': [line for line in lines if line.strip()][-6:]}
                        self.process, self.current = None, None
                    if not self.process and self.jobs and not self.held:
                        job = self.jobs.pop(0)
                        process, log = self._start(job)
                        self.process, self.current = process, {'job': job, 'log': log, 'started': time.time()}
            except Exception:           # the queue must outlive one bad job
                self.process, self.current = None, None

    @staticmethod
    def _lines(path):
        try:
            with open(path, errors='replace') as handle:
                return [runner.ANSI.sub('', line).rstrip() for line in handle.readlines()[-80:]]
        except OSError:
            return []

    @staticmethod
    def _name(lines):
        for line in lines:
            match = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]+): (generating|done|failed|mod|timeout|the game)', line)
            if match:
                return match.group(1)
        return None

    def busy(self, kind=None):
        """Whether a job (of this kind) is running or waiting."""
        with self.lock:
            jobs = ([self.current['job']] if self.current else []) + self.jobs
        return any(kind is None or job.get('kind') == kind for job in jobs)

    def status(self):
        with self.lock:
            waiting = [{key: job.get(key) for key in ('kind', 'recipe', 'seed', 'size', 'name', 'game', 'forced_wipe')} for job in self.jobs]
            if self.current:
                lines = self._lines(self.current['log'])
                if self.current['job']['kind'] == 'update':      # the updater's own lines, as they come
                    steps = [line.strip() for line in lines if line.strip()]
                else:
                    steps = [re.sub(r'^\d+s\s+', '', line.strip()) for line in lines if re.match(r'\s+\d+s\s', line)]
                return {'running': True, 'seconds': round(time.time() - self.current['started']), 'job': self.current['job'],
                        'name': self._name(lines), 'step': steps[-1] if steps else '',
                        'queue': waiting, 'last': self.last, 'version': __version__, 'three_d': three_d_state(), 'staging': staging.describe()}
            return {'running': False, 'seconds': 0, 'job': None, 'name': None, 'step': '', 'queue': waiting, 'last': self.last,
                    'version': __version__, 'three_d': three_d_state(), 'staging': staging.describe()}


# The preset that cannot be taken off the list: the map the game makes by itself, always there to start from.
KEPT_PRESET = 'game-default'


def removed_presets():
    """The presets taken off the panel's list. Their files stay with the code; this list is in the data."""
    names = runner.read_json(paths.data('presets-removed.json'))
    return set(names) - {KEPT_PRESET} if isinstance(names, list) else set()


def _keep_removed(names):
    target = paths.data('presets-removed.json')
    with open(target + '.partial', 'w') as handle:
        json.dump(sorted(names), handle)
    os.replace(target + '.partial', target)


def all_recipes():
    """Every recipe, whole: the saved ones (<data>/recipes) and the presets that came with the code,
    but those taken off the list."""
    out, seen = [], set(removed_presets())
    for folder, saved in ((paths.data('recipes'), True), (paths.package('presets'), False)):
        if not os.path.isdir(folder):
            continue
        for file in sorted(os.listdir(folder)):
            name = file[:-len('.json')]
            if not file.endswith('.json') or name in seen and not saved:
                continue
            try:
                with open(os.path.join(folder, file)) as handle:
                    recipe = recipes.normalise(json.load(handle))
            except (OSError, ValueError, KeyError, TypeError) as error:
                out.append({'name': name, 'title': name, 'saved': saved, 'broken': str(error)[:200]})
                continue
            seen.add(name)
            recipe.update({'name': name, 'saved': saved})
            out.append(recipe)
    return out


def templates_list():
    folder = paths.data('templates')
    if not os.path.isdir(folder):
        return []
    out = []
    for file in sorted(os.listdir(folder)):
        if not file.endswith('.map'):
            continue
        info = runner.read_json(os.path.join(folder, file + '.json')) or {}
        out.append({'file': file, 'monument': info.get('monument'), 'prefab': info.get('prefab'), 'prefabs': info.get('prefabs'), 'dropped': info.get('dropped')})
    return out


def describe_template(path):
    """What a template holds: its monument and how many prefabs it adds. Raises ValueError when it is no template."""
    world = MapFile(path, paths=False, limit=TEMPLATE_WORLD, records=TEMPLATE_RECORDS)
    table = library.prefab_table()
    if not os.path.exists(table):
        return {'monument': None, 'prefabs': int(len(world.id)), 'dropped': None,
                'note': 'checked again once the first map is made (the game\'s prefab table comes with it)'}
    template = Template(path, load_names(table))
    short = template.anchor.rsplit('/', 1)[1][:-len('.prefab')]
    return {'monument': analyze.label(short), 'prefab': short, 'prefabs': len(template.rows), 'dropped': len(template.dropped)}


def network_names(extra=()):
    """The names a panel opened to the network answers to: this computer's own, and those the settings add.
    Addresses are answered too (see `_host_ok`); any other name could be a site's own, pointed here."""
    names = {'localhost'}
    for name in (socket.gethostname(), socket.getfqdn()):
        if name:
            names.add(name.lower())
            if '.' not in name:
                names.add(name.lower() + '.local')
    return names | {str(name).lower() for name in extra or () if str(name).strip()}


def _is_address(host):
    try:
        ipaddress.ip_address(host[1:-1] if host.startswith('[') else host)
        return True
    except ValueError:
        return False


def three_d_state():
    """Whether the 3D view has its models for the installed game ({'ready', 'game', 'failed'}), for the panel's page."""
    try:
        made = models3d.version()
        ready = bool(made) and models3d.ready() == made
        return {'ready': ready, 'game': bool(made), 'failed': None if ready or not made else models3d.failed()}
    except OSError:
        return {'ready': False, 'game': False, 'failed': None}


def queue_models(queue):
    """The 3D models into the queue when the installed game has none (a new RustMapGen, a game updated by hand)
    and no try for this game failed: Prepare 3D tries again then."""
    state = three_d_state()
    if state['game'] and not state['ready'] and not state['failed'] and not queue.busy('models-3d'):
        queue.add([{'kind': 'models-3d'}])


def three_d_key(name):
    """A map as the 3D page names maps: 16 hex characters."""
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def _empty_far():
    text = b'{}  '
    return gzip.compress(struct.pack('<I', len(text)) + text, 9, mtime=0)


# What the page reads while no models are made: nothing to draw, and it draws the ground alone.
NO_MODELS = {'index.json': (b'{}', 'application/json'), 'far.bin.gz': (_empty_far(), 'application/octet-stream'),
             'nobuild.json.gz': (gzip.compress(b'{}', 9, mtime=0), 'application/octet-stream')}


class ThreeD:
    """The 3D files of the maps, made the first time they are asked for (bake3d.py, seconds), one map at a
    time, beside each map's other files; made again when its final.map is newer."""

    def __init__(self):
        self.lock = threading.Lock()
        self.names = {}

    def name(self, key):
        if key not in self.names:
            folder = paths.data('maps')
            for name in os.listdir(folder) if os.path.isdir(folder) else []:
                if library.NAME.match(name):
                    self.names.setdefault(three_d_key(name), name)
        return self.names.get(key)

    def make(self, name):
        folder = paths.data('maps', name)
        source, meta = os.path.join(folder, 'final.map'), os.path.join(folder, '3d_meta.json')
        with self.lock:
            if os.path.exists(meta) and os.path.getmtime(meta) >= os.path.getmtime(source):
                return
            record = runner.read_json(os.path.join(folder, 'map.json'))
            seed = record.get('seed') if isinstance(record, dict) else None
            bake3d.bake(source, folder, int(seed) if str(seed or '').isdigit() else 0)


def make_handler(queue, allowed_hosts, addresses=False, control=None):
    """allowed_hosts: the names (and on this computer only, the addresses) a request may name.
    addresses: whether any address is answered too (a panel opened to the network).
    control: {'server': the server, 'exit': None}, for the panel to end itself after an update of its own."""
    control = control if control is not None else {}
    checking = threading.Lock()                     # one steamcmd at a time for a check
    updating = threading.Lock()                     # one update of RustMapGen at a time
    reading = threading.Lock()                      # one template taken in at a time: reading one costs memory
    three_d = ThreeD()
    presets ={file[:-len('.json')] for file in os.listdir(paths.package('presets')) if file.endswith('.json')}

    class Handler(BaseHTTPRequestHandler):
        server_version = 'RustMapGen'

        def log_message(self, *args):
            pass

        def _send(self, status, body, kind='application/json', cache='no-store'):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path, cache='no-store', download=None):
            if not os.path.isfile(path):        # a file only: on Windows a name like CON is a device, not a file
                return self._send(404, {'error': 'not found'})
            try:
                with open(path, 'rb') as handle:
                    body = handle.read()
            except OSError:
                return self._send(404, {'error': 'not found'})
            self.send_response(200)
            self.send_header('Content-Type', TYPES.get(os.path.splitext(path)[1], 'application/octet-stream'))
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self):
            host = (self.headers.get('Host') or '').lower()
            host = host.rsplit(':', 1)[0] if not host.endswith(']') else host
            # A site that points its own name at this computer sends that name: an address is never one.
            if host in allowed_hosts or addresses and _is_address(host):
                return True
            self._send(403, {'error': 'refused: this panel answers requests to its own address only'})
            return False

        def _ours(self):
            """A write from the panel's own page: a page of another site cannot send this header."""
            if self.headers.get('X-RustMapGen') != '1':
                self._send(403, {'error': 'refused'})
                return False
            return True

        def _json(self, limit=200000):
            if not self._ours():
                return None
            if 'application/json' not in (self.headers.get('Content-Type') or ''):
                self._send(400, {'error': 'bad request'})
                return None
            try:
                return json.loads(self.rfile.read(min(int(self.headers.get('Content-Length') or 0), limit)) or b'{}')
            except (ValueError, TypeError):
                self._send(400, {'error': 'bad request'})
                return None

        def do_GET(self):
            if not self._host_ok():
                return None
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == '/':
                return self._file(os.path.join(PAGE, 'index.html'))
            if url.path in ('/app.js', '/app.css', '/editor.js'):
                return self._file(os.path.join(PAGE, url.path[1:]))
            if url.path == '/api/maps':
                return self._send(200, {'maps': library.cards(), 'metrics': [list(m) for m in METRICS],
                                        'generator': queue.status(), 'install': runner.describe_install()})
            if url.path == '/api/recipes':
                return self._send(200, {'recipes': all_recipes(), 'catalog': recipes.load_catalog(),
                                        'defaults': recipes.defaults(), 'presets': sorted(presets),
                                        'switches': [list(row) for row in recipes.SWITCHES if row[0] not in recipes.ALWAYS_ON],
                                        'limits': {'land_share': recipes.LAND_SHARE_MAX, 'height_m': recipes.HEIGHT_MAX_M,
                                                   'land_reach': [list(row) for row in recipes.LAND_REACH], 'land_spare': recipes.LAND_SPARE,
                                                   'not_in_the_middle': list(recipes.NOT_IN_THE_MIDDLE)},
                                        'tiers': [list(row) for row in recipes.TIERS],
                                        'biomes': [list(row) for row in recipes.BIOMES], 'jungle': list(recipes.JUNGLE),
                                        'templates': templates_list()})
            if url.path == '/api/status':
                return self._send(200, queue.status())
            if url.path == '/api/map':
                name = (query.get('name') or [''])[0]
                try:
                    entry = library.entry(name)
                except (OSError, ValueError):
                    return self._send(404, {'error': 'no such map'})
                entry['numbers'] = numbers(entry['facts'])
                return self._send(200, entry)
            match = re.match(r'^/files/([^/]+)/([^/]+)$', url.path)
            if match and library.NAME.match(match.group(1)) and match.group(2) in FILES:
                name, file = match.groups()
                is_map = file.endswith('.map')
                return self._file(paths.data('maps', name, file), cache='no-store' if is_map else 'max-age=600',
                                  download=f'{name}.map' if is_map else None)
            if url.path.startswith('/3d'):
                return self._three_d(url, query)
            return self._send(404, {'error': 'not found'})

        def _three_d(self, url, query):
            """The 3D view of a map, in a tab of its own: /3d/open goes on to the page with the map's key; the page
            asks for its scripts under /3d/, and for the map's files and the models under /3d/data/."""
            path = unquote(url.path)
            if path == '/3d/open':
                name = (query.get('name') or [''])[0]
                if not library.NAME.match(name) or not os.path.isfile(paths.data('maps', name, 'final.map')):
                    return self._send(404, {'error': 'no such map'})
                three_d.names[three_d_key(name)] = name
                title = (query.get('for') or [name])[0][:80]
                self.send_response(302)
                self.send_header('Location', f'/3d/?id={three_d_key(name)}&for={quote(title)}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return None
            if path in ('/3d', '/3d/'):
                return self._file(os.path.join(PAGE3D, 'index.html'))
            if path[len('/3d/'):] in PAGE3D_FILES:
                return self._file(os.path.join(PAGE3D, *path[len('/3d/'):].split('/')), cache='max-age=600')
            match = re.match(r'^/3d/data/3d-models/([0-9a-z-]+)/(monuments|scenery)/([A-Za-z0-9_-][A-Za-z0-9 ._-]*)$', path)
            if match:
                version, kind, file = match.groups()
                if version == 'none' and file in NO_MODELS:
                    body, type_ = NO_MODELS[file]
                    return self._send(200, body, kind=type_)
                if not models3d.VERSION.match(version):
                    return self._send(404, {'error': 'not found'})
                return self._file(os.path.join(models3d.folder(version), kind, file), cache='max-age=3600')
            match = re.match(r'^/3d/data/([0-9a-f]{16})/(3d_[a-z]+\.[a-z]+)$', path)
            if match and match.group(2) in bake3d.FILES:
                name = three_d.name(match.group(1))
                if name is None or not os.path.isfile(paths.data('maps', name, 'final.map')):
                    return self._send(404, {'error': 'not found'})
                try:
                    three_d.make(name)
                except Exception as error:      # a file this view cannot read: said, and the panel goes on
                    return self._send(500, {'error': f'the 3D view of this map could not be made: {error}'[:300]})
                file = paths.data('maps', name, match.group(2))
                if match.group(2) != '3d_meta.json':
                    return self._file(file)
                # The models the page draws are those made for the installed game: a map keeps its view across updates.
                meta = runner.read_json(file)
                if not isinstance(meta, dict):
                    return self._send(404, {'error': 'not found'})
                meta['models'] = models3d.ready() or 'none'
                return self._send(200, meta)
            return self._send(404, {'error': 'not found'})

        def do_PUT(self):
            if not self._host_ok():
                return None
            path = urlparse(self.path).path
            match = re.match(r'^/api/recipes/([^/]+)$', path)
            if match:
                body = self._json()
                if body is None:
                    return None
                name = match.group(1)
                if not RECIPE_NAME.match(name):
                    return self._send(400, {'error': 'a recipe name is 2 to 49 small letters, digits and dashes'})
                if name in presets:
                    return self._send(409, {'error': f'"{name}" is a preset: save under another name'})
                try:
                    recipe = recipes.normalise(body)
                except (ValueError, KeyError, TypeError) as error:
                    return self._send(400, {'error': str(error)[:300]})
                recipe['name'] = name
                os.makedirs(paths.data('recipes'), exist_ok=True)
                target = paths.data('recipes', name + '.json')
                with open(target + '.partial', 'w') as handle:
                    json.dump(recipe, handle, indent=2)
                os.replace(target + '.partial', target)
                return self._send(200, {'ok': True, 'name': name})
            match = re.match(r'^/api/templates/([^/]+)$', path)
            if match:
                if not self._ours():
                    return None
                file = unquote(match.group(1))
                if not TEMPLATE_NAME.match(file):
                    return self._send(400, {'error': 'a template is a .map file with a plain name (letters, digits, spaces, dots, dashes)'})
                size = int(self.headers.get('Content-Length') or 0)
                if not 0 < size <= TEMPLATE_LIMIT:
                    return self._send(400, {'error': f'a template is at most {TEMPLATE_LIMIT // 1048576} MB'})
                os.makedirs(paths.data('templates'), exist_ok=True)
                target = paths.data('templates', file)
                partial = target + '.upload'
                if not reading.acquire(blocking=False):
                    return self._send(409, {'error': 'another template is being read: try again in a moment'})
                try:
                    with open(partial, 'wb') as handle:
                        left = size
                        while left:
                            chunk = self.rfile.read(min(left, 1 << 20))
                            if not chunk:
                                break
                            handle.write(chunk)
                            left -= len(chunk)
                    try:
                        info = describe_template(partial)
                    except (ValueError, OSError, KeyError) as error:
                        os.unlink(partial)
                        return self._send(400, {'error': f'not a template: {error}'})
                    os.replace(partial, target)
                finally:
                    reading.release()
                with open(target + '.json', 'w') as handle:
                    json.dump(info, handle)
                return self._send(200, dict(info, ok=True, file=file))
            return self._send(404, {'error': 'not found'})

        def do_DELETE(self):
            if not self._host_ok() or not self._ours():
                return None
            path = urlparse(self.path).path
            if path == '/api/queue':
                return self._send(200, {'ok': True, 'dropped': queue.clear()})
            match = re.match(r'^/api/recipes/([^/]+)$', path)
            if match and RECIPE_NAME.match(match.group(1)):
                name = match.group(1)
                if name == KEPT_PRESET:
                    return self._send(409, {'error': 'the game default stays: it is the map the game makes by itself'})
                target = paths.data('recipes', name + '.json')
                if os.path.exists(target):
                    os.unlink(target)
                elif name in presets and name not in removed_presets():
                    _keep_removed(removed_presets() | {name})      # its file stays with the code
                else:
                    return self._send(404, {'error': 'no such settings'})
                return self._send(200, {'ok': True})
            match = re.match(r'^/api/templates/([^/]+)$', path)
            if match and TEMPLATE_NAME.match(unquote(match.group(1))):
                target = paths.data('templates', unquote(match.group(1)))
                if not os.path.exists(target):
                    return self._send(404, {'error': 'no such template'})
                os.unlink(target)
                if os.path.exists(target + '.json'):
                    os.unlink(target + '.json')
                return self._send(200, {'ok': True})
            match = re.match(r'^/api/maps/([^/]+)$', path)
            if match:
                status = queue.status()
                if status['running'] and status.get('name') == match.group(1):
                    return self._send(409, {'error': 'the game is making or testing this map right now'})
                try:
                    library.remove(match.group(1))
                except (ValueError, FileNotFoundError):
                    return self._send(404, {'error': 'no such map'})
                return self._send(200, {'ok': True})
            return self._send(404, {'error': 'not found'})

        def _updates(self, path):
            if path == '/api/game-check':
                if queue.busy('update'):
                    return self._send(409, {'error': 'the game is being updated: see Maps'})
                if not checking.acquire(blocking=False):
                    return self._send(409, {'error': 'a check is under way'})
                try:
                    steam = game.steam_builds()
                except (RuntimeError, OSError) as error:
                    return self._send(502, {'error': f'Steam could not be asked: {str(error)[:300]}'})
                finally:
                    checking.release()
                return self._send(200, {'steam': steam, 'build': runner.game_build(), 'branch': runner.game_branch()})
            if path == '/api/game-update':
                if refusal(queue, control):
                    return self._send(409, {'error': refusal(queue, control)})
                if not queue.busy('update'):
                    queue.add([{'kind': 'update'}])
                return self._send(200, {'ok': True, 'generator': queue.status()})
            if path == '/api/app-check':
                try:
                    return self._send(200, release.check())
                except (OSError, ValueError, RuntimeError) as error:
                    return self._send(502, {'error': f'the releases could not be read: {str(error)[:300]}'})
            # One update of RustMapGen at a time, the queue held still from before the release is fetched until
            # its files are in, and the game's lock taken, so that no map starts meanwhile, here or from a command.
            # Once an update is in, the panel stays held until it starts again: it still runs the code it had.
            if control.get('restarting'):
                return self._send(409, {'error': refusal(queue, control)})
            if not updating.acquire(blocking=False):
                return self._send(409, {'error': 'an update of RustMapGen is under way'})
            done = None
            try:
                if not queue.hold():
                    return self._send(409, {'error': 'a map or a game update is running or waiting: update once the queue is empty'})
                try:
                    found = release.check()
                    with runner.lock():
                        done = release.apply(found)
                except runner.Busy:
                    return self._send(409, {'error': 'a map is being made on this computer (from the command line): update once it is done'})
                except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
                    return self._send(500, {'error': str(error)[:400]})
                finally:
                    if done is None:
                        queue.let_go()
                    else:
                        control['restarting'] = done['version']
            finally:
                if done is None:
                    updating.release()
            self._send(200, done)
            if done['restart'] == 'auto' and control.get('server'):
                # The answer is sent; the panel then ends, and its launcher starts the new one.
                control['exit'] = release.RESTART
                threading.Timer(0.5, control['server'].shutdown).start()
            return None

        def do_POST(self):
            if not self._host_ok():
                return None
            path = urlparse(self.path).path
            if path in ('/api/game-check', '/api/game-update', '/api/app-check', '/api/app-update'):
                if self._json() is None:
                    return None
                return self._updates(path)
            if path in ('/api/load-test', '/api/generate', '/api/models-3d', '/api/remove-staging') and refusal(queue, control):
                return self._send(409, {'error': refusal(queue, control), 'message': refusal(queue, control)})
            if path == '/api/remove-staging':
                if self._json() is None:
                    return None
                if not os.path.isdir(staging.folder()):
                    return self._send(404, {'error': 'there is no game of the next update'})
                if queue.busy():
                    return self._send(409, {'error': 'a job is running or waiting: remove the next update\'s game when the queue is empty'})
                if not queue.add([{'kind': 'remove-staging'}]):
                    return self._send(409, {'error': refusal(queue, control) or 'the queue is full'})
                return self._send(200, {'ok': True, 'message': 'the next update\'s game is being removed', 'generator': queue.status()})
            if path == '/api/models-3d':
                if self._json() is None:
                    return None
                state = three_d_state()
                if not state['game']:
                    return self._send(409, {'error': 'install the game first: the 3D models are made from its files'})
                if state['ready'] or queue.busy('models-3d'):
                    return self._send(200, {'ok': True, 'message': '3D models made' if state['ready'] else '3D models already queued',
                                            'generator': queue.status()})
                if not queue.add([{'kind': 'models-3d'}]):
                    return self._send(409, {'error': refusal(queue, control) or f'the queue is full ({QUEUE_LIMIT} waiting)'})
                return self._send(200, {'ok': True, 'message': '3D models queued', 'generator': queue.status()})
            if path == '/api/load-test':
                body = self._json()
                if body is None:
                    return None
                name = str(body.get('name', ''))
                if not library.NAME.match(name) or not os.path.isfile(paths.data('maps', name, 'final.map')):
                    return self._send(404, {'error': 'no such map'})
                on = body.get('game') or 'public'
                if on not in ('public', 'staging'):
                    return self._send(400, {'error': 'game is public or staging'})
                if on == 'staging' and (staging.refusal() or not os.path.isfile(runner.executable(staging.folder()))):
                    return self._send(409, {'error': staging.refusal() or 'the next update\'s game is not installed'})
                jobs = [{'kind': 'load-test', 'name': name, 'game': on}]
                try:
                    made_on = (library.entry(name).get('run') or {}).get('game_branch')
                except (OSError, ValueError):
                    made_on = None
                if made_on not in (None, 'public') and on == 'public':
                    # A map of the next update on the released game: the public game is brought to Steam's newest build first, or the
                    # test would be of last month's game.
                    jobs.insert(0, {'kind': 'update', 'if_newer': True})
                elif on == 'staging':
                    # Its branch can have another build since the map was made: the test is on Steam's newest.
                    jobs.insert(0, {'kind': 'update', 'game': 'staging'})
                if not queue.add(jobs, whole=True):     # the game's update and its test go in together, or neither
                    return self._send(409, {'error': refusal(queue, control) or f'the queue is full ({QUEUE_LIMIT} waiting)'})
                return self._send(200, {'ok': True, 'message': 'load test queued', 'generator': queue.status()})
            if path != '/api/generate':
                return self._send(404, {'error': 'not found'})
            body = self._json()
            if body is None:
                return None
            try:
                seed = int(body['seed']) if body.get('seed') not in (None, '') else 0
                size = int(body['size']) if body.get('size') not in (None, '') else 0
                count = int(body.get('count') or 1)
                note = str(body.get('note', ''))[:200]
            except (ValueError, TypeError):
                return self._send(400, {'error': 'bad request'})
            if seed and not 1 <= seed <= 2147483647 or size and not 1000 <= size <= 6000 or not 1 <= count <= 12:
                return self._send(400, {'error': 'seed must be 1 to 2147483647, size 1000 to 6000, and 1 to 12 maps at a time'})
            on = body.get('game') or 'public'
            if on not in ('public', 'staging'):
                return self._send(400, {'error': 'game is public or staging'})
            if on == 'staging' and staging.refusal():
                return self._send(409, {'error': staging.refusal(), 'message': staging.refusal()})
            if isinstance(body.get('settings'), dict):
                # Settings straight from the editor: kept with the map, not saved as a recipe.
                try:
                    settings = recipes.normalise(body['settings'])
                    library.template_paths(settings)
                except (ValueError, KeyError, TypeError, FileNotFoundError) as error:
                    return self._send(400, {'error': str(error)[:300]})
                label = settings['name'] if RECIPE_NAME.match(settings['name'] or '') else 'custom'
                settings['name'] = label
                os.makedirs(paths.data('runs'), exist_ok=True)
                file = paths.data('runs', f"settings-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{random.randint(1000, 9999)}.json")
                with open(file, 'w') as handle:
                    json.dump(settings, handle, indent=1)
            else:
                label = str(body.get('recipe', ''))
                from . import generate as make
                file = make.find_recipe(label) if RECIPE_NAME.match(label) else None
                if not file:
                    return self._send(400, {'error': 'unknown recipe'})
            jobs = [{'kind': 'generate', 'recipe_file': file, 'recipe': label, 'size': size, 'note': note, 'game': on,
                     'seed': seed if seed and count == 1 else random.randint(100000000, 999999999)} for _ in range(count)]
            first = None
            if on == 'staging':
                # The next update's game first, at Steam's newest build of its branch: it can change up to the last day.
                first = {'kind': 'update', 'game': 'staging'}
            elif game.behind_forced_wipe():
                # A game last updated before the latest forced wipe is brought to that update first, once it is out.
                first = {'kind': 'update', 'forced_wipe': True}
            if first:
                jobs.insert(0, first)
            taken = queue.add(jobs)
            if not taken:
                return self._send(409, {'ok': False, 'message': refusal(queue, control) or f'the queue is full ({QUEUE_LIMIT} waiting)', 'generator': queue.status()})
            maps = taken - (1 if first else 0)
            message = f'{maps} map{"s" if maps != 1 else ""} queued' + (
                ' for the next update, after its game is brought to Steam\'s newest build of its branch' if on == 'staging'
                else ', after the game is brought to the forced wipe\'s update, when it is out' if first else '')
            if seed and count > 1:
                message += ': a seed is for one map, so each of these has a seed of its own'
            return self._send(200, {'ok': True, 'message': message, 'generator': queue.status()})

    return Handler


def _network_address():
    """This computer's address on its network, as another computer would type it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1', 9))     # picks the outgoing interface; a UDP connect sends nothing
            return probe.getsockname()[0]
    except OSError:
        return socket.gethostname()


def serve(host='127.0.0.1', port=5350, open_browser=True):
    paths.ensure_data()
    local = host in ('127.0.0.1', 'localhost', '::1')
    # On this computer only: requests must name it. Opened to a network: this computer's addresses and names.
    allowed = set(LOCAL_HOSTS) if local else network_names(paths.settings().get('panel_hosts'))
    control = {'exit': None}
    queue = Queue()
    server = ThreadingHTTPServer((host, port), make_handler(queue, allowed, addresses=not local, control=control))
    queue_models(queue)
    control['server'] = server
    wildcard = host in ('0.0.0.0', '::', '')
    address = f'http://{"127.0.0.1" if local or wildcard else host}:{port}/'
    print(f'RustMapGen panel on {address}  (data: {paths.data()})', flush=True)
    if not local:
        if wildcard:
            print(f'  from another computer: http://{_network_address()}:{port}/  (http, not https)', flush=True)
        print('  the panel is open to your network: anyone who can reach this computer can make and delete maps', flush=True)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    if control['exit'] == release.RESTART:
        print('RustMapGen was updated: the panel starts again', flush=True)
    return control['exit'] or 0
