"""Where things live. Everything sits beside the code unless an environment variable says otherwise:

    <root>/game       the Rust dedicated server (installed by steamcmd)
    <root>/steamcmd   steamcmd itself
    <root>/data       maps, templates, saved recipes, the game's prefab table, jobs, settings

RUSTMAPGEN_GAME and RUSTMAPGEN_DATA move the game and the data elsewhere. A relative one is taken from the
folder the command was started in, so the game and steamcmd (which run in folders of their own) see the
same place.
"""
import json
import os

PACKAGE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(PACKAGE)
IDENTITY = 'rustmapgen'
GAME_APP = 258550           # the Rust dedicated server on Steam

DEFAULT_SETTINGS = {
    'panel_port': 5350,
    # Nothing new is started while the CPU is this hot (°C), where the system reports a temperature.
    'max_temp_c': 85,
    # A map takes about 5 GB of memory while it is made.
    'min_free_gb': 5.5,
    # Linux: run the game in an empty network namespace when the system allows it (unshare -rn).
    'isolate_network': True,
    # How long one map may take before the game is stopped.
    'timeout_minutes': 45,
    # Ports the generator's game binds on 127.0.0.1. Never opened to anyone: the mod quits before.
    'game_port': 28715,
    # A panel opened to the network answers requests to this computer's addresses and names only.
    # More names it may be reached by (a name of your own network's DNS, say) go here.
    'panel_hosts': [],
}


def _root(variable, default):
    return os.path.abspath(os.environ.get(variable) or os.path.join(ROOT, default))


# Written back whole, so that the commands the panel starts (from another folder) find the same places.
for _variable in ('RUSTMAPGEN_DATA', 'RUSTMAPGEN_GAME', 'RUSTMAPGEN_GAME_NEXT', 'RUSTMAPGEN_STEAMCMD'):
    if os.environ.get(_variable):
        os.environ[_variable] = os.path.abspath(os.environ[_variable])


def data(*parts):
    return os.path.join(_root('RUSTMAPGEN_DATA', 'data'), *parts)


def game(*parts):
    return os.path.join(_root('RUSTMAPGEN_GAME', 'game'), *parts)


def steamcmd(*parts):
    return os.path.join(_root('RUSTMAPGEN_STEAMCMD', 'steamcmd'), *parts)


def package(*parts):
    return os.path.join(PACKAGE, *parts)


def settings():
    """The defaults, with what <data>/settings.json changes."""
    out = dict(DEFAULT_SETTINGS)
    try:
        with open(data('settings.json')) as handle:
            mine = json.load(handle)
        out.update({key: value for key, value in mine.items() if key in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    return out


def ensure_data():
    for folder in ('maps', 'templates', 'recipes', 'tables', 'jobs', 'runs'):
        os.makedirs(data(folder), exist_ok=True)
