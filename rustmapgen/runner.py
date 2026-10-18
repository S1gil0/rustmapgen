"""Run the game on this computer: one generation (or one load test) at a time.

The game is the plain Rust dedicated server with the mod in its HarmonyMods folder. The runner:

  - works only in a game folder of its own: one that holds a server of someone else's is refused,
    since the mod in its HarmonyMods folder would stop that server from opening;
  - holds a lock in the game's folder for the whole run, so two runs never share it, whichever
    data folder they use;
  - starts nothing while the CPU is too hot (where the system reports a temperature) or while
    there is too little free memory for a map (about 5 GB);
  - starts the game at low priority, bound to 127.0.0.1, with no RCON password and no Rust+;
    on Linux in an empty network namespace too, when the system allows one (unshare -rn);
  - stops the game at once when its log says that patching failed, or begins the map before
    saying that the mod is in, or says nothing of the mod within three minutes: a game without
    the whole mod would go on as an ordinary server;
  - stops a game that runs past the time limit, and the game it started whenever the run ends
    early (an interruption, an error).

The mod quits the game as soon as the map is saved, before the server would listen for players.
"""
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import paths

MOD_FILE = 'RustMapGen.dll'
MOD_IN = '[RustMapGen] patches active'
MOD_IDLE = '[RustMapGen] patches idle'
MOD_BROKEN = ('Failed to patch all hooks', 'Failed to load harmony mod', 'Failed to apply patch')
MOD_PATIENCE = 180
# The game has begun the map: the mod says it is in long before this (line 42 against 207 of a log).
UNDERWAY = 'Generating procedural map'
WINDOWS = os.name == 'nt'
# The file that makes a game folder this generator's own, and the lock of a run, both in that folder.
MARKER = 'rustmapgen-game.txt'
LOCK_FILE = 'rustmapgen.lock'
# Present while steamcmd changes the game: no map is made on a game an update left half done.
UPDATING = 'rustmapgen-updating.txt'
MARKER_TEXT = ('This Rust dedicated server belongs to RustMapGen, the map generator. Its HarmonyMods folder holds\n'
               'the generator\'s mod, which makes a server quit instead of opening: never run a server people\n'
               'play on from this folder.\n')


class Busy(Exception):
    """Another run holds the game, or the computer cannot take a run now. Nothing was changed."""


# The server folders the generator's game writes: its own, and the one of installs made before the rename.
OWN_IDENTITIES = (paths.IDENTITY, 'rustmap')


def foreign():
    """Why the game folder looks like a server of someone else's, or None."""
    server = paths.game('server')
    others = sorted(name for name in os.listdir(server) if name not in OWN_IDENTITIES) if os.path.isdir(server) else []
    if others:
        return f"it holds a server of its own (server/{', server/'.join(others[:3])})"
    mods = paths.game('HarmonyMods')
    others = sorted(name for name in os.listdir(mods) if not name.startswith(MOD_FILE)) if os.path.isdir(mods) else []
    if others:
        return f"its HarmonyMods folder holds other mods ({', '.join(others[:3])})"
    if has_oxide() or os.path.isdir(paths.game('oxide')):
        return 'it has Oxide in it'
    if os.path.isdir(paths.game('carbon')):
        return 'it has Carbon in it'
    return None


def claim():
    """Make sure the game folder is this generator's own before anything is written into it.

    A folder RustMapGen made carries MARKER. One without it is taken when it is empty, when it is the
    folder beside the code, or when it holds RustMapGen's mod or its server folder (an install from
    before the marker); any other folder with files in it is refused. A folder that holds a server
    of someone else's (another server identity, other mods, Oxide, Carbon) is refused even with the
    marker: the mod would stop that server from opening.
    """
    folder = paths.game()
    reason = foreign() if os.path.isdir(folder) else None
    if reason:
        raise RuntimeError(f'{folder} is not a game folder of RustMapGen\'s own: {reason}. The generator\'s mod would stop a server '
                           'there from opening. Point RUSTMAPGEN_GAME at an empty folder.')
    marker = os.path.join(folder, MARKER)
    if os.path.isfile(marker):
        return
    present = [name for name in os.listdir(folder) if name not in (MARKER, LOCK_FILE, UPDATING)] if os.path.isdir(folder) else []
    ours = (os.path.normcase(folder) == os.path.normcase(os.path.join(paths.ROOT, 'game'))
            or os.path.isfile(paths.game('HarmonyMods', MOD_FILE)) or any(os.path.isdir(paths.game('server', name)) for name in OWN_IDENTITIES))
    if present and not ours:
        raise RuntimeError(f'{folder} already holds files and RustMapGen did not make it. Point RUSTMAPGEN_GAME at an empty folder; '
                           f'if this folder really is the generator\'s own, put a file named {MARKER} in it.')
    os.makedirs(folder, exist_ok=True)
    with open(marker, 'w') as handle:
        handle.write(MARKER_TEXT)


@contextlib.contextmanager
def lock():
    """The game's folder for one run, held by an OS lock that goes when the process goes. The folder is
    claimed first, so nothing (not even the lock) is written into a folder that is not the generator's."""
    claim()
    handle = open(paths.game(LOCK_FILE), 'a+')
    try:
        if WINDOWS:
            import msvcrt
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise Busy('another map is being made on this computer') from None
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Busy('another map is being made on this computer') from None
        yield
    finally:
        handle.close()


def executable():
    return paths.game('RustDedicated.exe' if WINDOWS else 'RustDedicated')


def game_branch():
    """The branch of the installed game (public, staging...), from steamcmd's manifest; None when there is no install."""
    try:
        with open(paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')) as handle:
            match = re.search(r'"BetaKey"\s+"([^"]*)"', handle.read())
        return match.group(1) if match and match.group(1) else 'public'
    except OSError:
        return None


def game_state():
    """steamcmd's StateFlags for the game ('4': fully installed; an update cut short leaves others); None when there is no install."""
    try:
        with open(paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')) as handle:
            match = re.search(r'"StateFlags"\s+"(\d+)"', handle.read())
        return match.group(1) if match else None
    except OSError:
        return None


def game_build():
    """The build of the installed game, from steamcmd's manifest; None when there is no install."""
    try:
        with open(paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')) as handle:
            match = re.search(r'"buildid"\s+"(\d+)"', handle.read())
        return match.group(1) if match else None
    except OSError:
        return None


def has_oxide():
    managed = paths.game('RustDedicated_Data', 'Managed')
    return os.path.isdir(managed) and any('oxide' in name.lower() for name in os.listdir(managed))


def cpu_temp():
    """The CPU's temperature in °C, or None where the system gives none (Windows, most VMs)."""
    if WINDOWS:
        return None
    best = None
    for folder in sorted(glob_hwmon()):
        try:
            with open(os.path.join(folder, 'name')) as handle:
                if handle.read().strip() not in ('k10temp', 'zenpower', 'coretemp', 'cpu_thermal'):
                    continue
            with open(os.path.join(folder, 'temp1_input')) as handle:
                value = round(int(handle.read().strip()) / 1000)
            best = value if best is None else max(best, value)
        except (OSError, ValueError):
            continue
    return best


def glob_hwmon():
    root = '/sys/class/hwmon'
    try:
        return [os.path.join(root, name) for name in os.listdir(root)]
    except OSError:
        return []


def free_memory_gb():
    """Memory the system can give a new process now, in GB; None when it cannot be read."""
    try:
        if WINDOWS:
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong), ('ullTotalPhys', ctypes.c_ulonglong),
                            ('ullAvailPhys', ctypes.c_ulonglong), ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                            ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong), ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
            status = Status()
            status.dwLength = ctypes.sizeof(Status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return round(status.ullAvailPhys / 1024 ** 3, 1)
        with open('/proc/meminfo') as handle:
            for line in handle:
                if line.startswith('MemAvailable:'):
                    return round(int(line.split()[1]) / 1024 ** 2, 1)
    except (OSError, ValueError, AttributeError):
        pass
    return None


def can_isolate():
    """Whether this Linux system lets an ordinary user run a program with no network at all."""
    if WINDOWS or not shutil.which('unshare'):
        return False
    try:
        return subprocess.run(['unshare', '-rn', 'true'], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def wait_until_cool(limit, patience=20 * 60, say=print):
    waited = 0
    while True:
        temp = cpu_temp()
        if temp is None or temp < limit:
            return temp
        if waited >= patience:
            raise Busy(f'the CPU has been at {temp} °C for {patience // 60} minutes; nothing is started at {limit} °C or more')
        if waited == 0:
            say(f'  the CPU is at {temp} °C: waiting for it to drop below {limit} °C', flush=True)
        time.sleep(20)
        waited += 20


def _digest(path):
    try:
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def source_sha256():
    """The mod's source as it is now (mod/RustMapGen.cs); None when it is missing."""
    return _digest(os.path.join(paths.ROOT, 'mod', 'RustMapGen.cs'))


def mod_source():
    """The mod to install: one built here (mod/build) from the source as it is now, else the one that came
    with the code (mod/prebuilt). A build of older source never wins: after the code is updated it would
    leave out what the new code asks of the mod, without a word. The record must also be of that very file."""
    built = os.path.join(paths.ROOT, 'mod', 'build', MOD_FILE)
    record = read_json(os.path.join(paths.ROOT, 'mod', 'build', 'build.json')) or {}
    if (os.path.isfile(built) and record.get('source_sha256') and record['source_sha256'] == source_sha256()
            and record.get('sha256') == _digest(built)):
        return built
    prebuilt = os.path.join(paths.ROOT, 'mod', 'prebuilt', MOD_FILE)
    if os.path.isfile(prebuilt):
        return prebuilt
    raise FileNotFoundError('no built mod: run "python -m rustmapgen build-mod" (needs the .NET SDK) or reinstall')


def describe_mod():
    """Which mod a run installs, in words."""
    try:
        source = mod_source()
    except FileNotFoundError as error:
        return str(error)
    folder = os.path.dirname(source)
    record = read_json(os.path.join(folder, 'build.json')) or {}
    made = 'built on this computer' if os.path.basename(folder) == 'build' else 'the one that came with the code'
    return f"{made}, for game build {record.get('game_build') or 'unknown'} ({source})"


def install_mod():
    """Put the mod in the game's HarmonyMods folder if it is not the same file already. Under the lock."""
    if not os.path.isfile(paths.game(MARKER)):
        raise RuntimeError(f'{paths.game()} has not been claimed by RustMapGen: the mod is not put there')
    source = mod_source()
    target = paths.game('HarmonyMods', MOD_FILE)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(source, 'rb') as handle:
        wanted = handle.read()
    try:
        with open(target, 'rb') as handle:
            if handle.read() == wanted:
                return target
    except OSError:
        pass
    shutil.copyfile(source, target + '.partial')
    os.replace(target + '.partial', target)
    return target


def _command(arguments, log, settings):
    port = int(settings['game_port'])
    argv = [executable(), '-batchmode', '-nographics', '-logfile', log,
            '+server.identity', paths.IDENTITY, '+server.hostname', 'RustMapGen generator'] + arguments + [
            '+server.ip', '127.0.0.1', '+server.port', str(port), '+server.queryport', str(port + 2),
            '+rcon.ip', '127.0.0.1', '+rcon.port', str(port + 1), '+app.port', '-1',
            '+server.secure', 'false', '+server.maxplayers', '1', '+server.saveinterval', '100000']
    isolated = False
    if not WINDOWS and settings.get('isolate_network') and can_isolate():
        argv = ['unshare', '-rn', '--'] + argv
        isolated = True
    return argv, isolated


def _environment(job_file):
    env = dict(os.environ)
    env['RUSTMAPGEN_JOB'] = job_file
    if not WINDOWS:
        libraries = [paths.game(), paths.game('RustDedicated_Data', 'Plugins', 'x86_64')]
        env['LD_LIBRARY_PATH'] = os.pathsep.join(libraries + ([env['LD_LIBRARY_PATH']] if env.get('LD_LIBRARY_PATH') else []))
    return env


def _start(argv, env):
    if WINDOWS:
        flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(argv, cwd=paths.game(), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=flags)
    return subprocess.Popen(argv, cwd=paths.game(), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True, preexec_fn=lambda: os.nice(19))


def _stop(process):
    """Stop the game and everything it started."""
    if process.poll() is not None:
        return
    try:
        if WINDOWS:
            if subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], capture_output=True, timeout=60).returncode:
                process.kill()
        else:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
    process.wait(timeout=60)


def stop_on_signals():
    """The signals that end a command (the terminal closing, a service stopping) become an exception, so
    that a run stops its game on the way out, as it does for Ctrl-C. A forced kill (SIGKILL) cannot be
    caught: the game then quits by itself once its map is saved."""
    import signal

    def leave(number, frame):
        raise SystemExit(128 + number)
    for name in ('SIGTERM', 'SIGHUP', 'SIGBREAK'):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), leave)
            except (OSError, ValueError):
                pass


def _scan(text, mod_in):
    """What the log says, in the order the game wrote it: (trouble, mod_in). trouble is None, 'early' (the map
    begun before the mod said it is in) or 'broken' (a patch failed, or the mod read no job)."""
    ready = text.find(MOD_IN)
    if not mod_in:
        underway = text.find(UNDERWAY)
        if underway >= 0 and not 0 <= ready < underway:
            return 'early', False
    mod_in = mod_in or ready >= 0
    if any(word in text for word in MOD_BROKEN) or MOD_IDLE in text:
        return 'broken', mod_in
    return None, mod_in


def _tail(path, start):
    """New bytes of a growing log from byte `start`: (bytes, new start)."""
    try:
        with open(path, 'rb') as handle:
            handle.seek(start)
            data = handle.read()
        return data, start + len(data)
    except OSError:
        return b'', start


# The game colours some lines of its log for a terminal ("\x1b[0m"): nothing of that is ever shown or matched.
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


def _lines(carry, data):
    """The whole lines of what the log has so far, without terminal colours, and the part line kept for
    the next read: a marker is never cut in two by the moment of a read."""
    carry += data
    cut = carry.rfind(b'\n') + 1
    return ANSI.sub('', carry[:cut].decode('utf-8', 'replace')), carry[cut:]


def run(job, arguments, progress=None, say=print):
    """Run the game once for a job folder (job.json in it). Returns (state, seconds, facts).

    state is 'done' (the mod wrote `done`), 'failed', 'timeout' or 'mod' (the mod did not load and
    the game was stopped). The caller holds the lock. However the run ends, the game is not left running.
    """
    settings = paths.settings()
    claim()
    if os.path.exists(paths.game(UPDATING)):
        raise RuntimeError('the last update of the game did not finish: run "update" again before making maps')
    if not os.path.isfile(executable()):
        raise FileNotFoundError(f'no game at {paths.game()}: run the installer first')
    # Every setting is read before the game starts: a bad one stops the run before there is a game to stop.
    try:
        limit = float(settings['timeout_minutes']) * 60
        cool = int(settings['max_temp_c'])
        memory = float(settings['min_free_gb'])
    except (TypeError, ValueError):
        raise ValueError('settings.json: timeout_minutes, max_temp_c and min_free_gb must be numbers') from None
    free = free_memory_gb()
    if free is not None and free < memory:
        raise Busy(f"{free} GB of memory free, {memory:g} GB needed for a map: close something and try again")
    temp_start = wait_until_cool(cool, say=say)
    install_mod()
    log = os.path.join(job, 'log.txt')
    argv, isolated = _command(arguments, log, settings)
    pattern = re.compile(progress) if progress else None
    process = _start(argv, _environment(os.path.join(job, 'job.json')))
    started = time.monotonic()
    state, mod_in, position, carry, hottest, last = None, False, 0, b'', temp_start, ''
    try:
        while process.poll() is None:
            time.sleep(2)
            data, position = _tail(log, position)
            text, carry = _lines(carry, data)
            # A whole marker counts at once, also on a line the game has not ended yet.
            trouble, mod_in = _scan(text + carry.decode('utf-8', 'replace'), mod_in)
            if trouble == 'early':
                state = 'mod'
                say('  the game began the map without saying that the mod is in: stopped', flush=True)
                break
            if trouble == 'broken':
                state = 'mod'
                say('  the game did not load the whole mod: stopped', flush=True)
                break
            if not mod_in and time.monotonic() - started > MOD_PATIENCE:
                state = 'mod'
                say(f'  the game said nothing about the mod in {MOD_PATIENCE} s: stopped', flush=True)
                break
            if time.monotonic() - started > limit:
                state = 'timeout'
                break
            temp = cpu_temp()
            if temp is not None:
                hottest = max(hottest or 0, temp)
            if pattern:
                for line in text.splitlines():
                    if pattern.search(line) and line.strip() != last:
                        last = line.strip()
                        say(f'  {int(time.monotonic() - started):4d}s  {last[:110]}', flush=True)
    finally:
        try:
            _stop(process)              # stopped here on every way out, before the caller lets go of the lock
        except subprocess.TimeoutExpired:
            pass                        # the wait below says so; an error that brought us here is not hidden
    code = process.wait()
    data, position = _tail(log, position)
    # What the game wrote after the last read, last line too, read the same way: trouble there outweighs `done`.
    trouble, mod_in = _scan((carry + data).decode('utf-8', 'replace'), mod_in)
    seconds = round(time.monotonic() - started)
    if state is None:
        if trouble:
            state = 'mod'
        elif code == 0 and os.path.exists(os.path.join(job, 'done')) and not os.path.exists(os.path.join(job, 'failed')):
            state = 'done'
        else:
            state = 'failed'
    return state, seconds, {'exit_code': code, 'network_isolated': isolated, 'mod_loaded': mod_in,
                            'cpu_c_start': temp_start, 'cpu_c_peak': hottest, 'free_gb_start': free}


def gzip_log(job):
    import gzip
    log = os.path.join(job, 'log.txt')
    if os.path.exists(log):
        with open(log, 'rb') as source, gzip.open(log + '.gz', 'wb') as target:
            shutil.copyfileobj(source, target)
        os.unlink(log)


def describe_install():
    """What this computer has, for `python -m rustmapgen doctor` and the panel."""
    settings = paths.settings()
    return {'game': paths.game(), 'installed': os.path.isfile(executable()), 'build': game_build(), 'branch': game_branch(),
            'update_unfinished': os.path.exists(paths.game(UPDATING)), 'oxide': has_oxide(), 'game_folder': 'RustMapGen\'s own' if os.path.isfile(paths.game(MARKER)) else 'not claimed yet',
            'mod': os.path.isfile(paths.game('HarmonyMods', MOD_FILE)), 'mod_used': describe_mod(), 'python': sys.version.split()[0],
            'free_gb': free_memory_gb(), 'cpu_c': cpu_temp(), 'isolate_network': bool(settings.get('isolate_network')) and can_isolate(),
            'data': paths.data()}


def read_json(path):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None
