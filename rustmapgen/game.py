"""The game this generator runs: the public Rust dedicated server, fetched by steamcmd.

Nothing of the game comes with this code. steamcmd downloads it from Steam with an anonymous
login (the dedicated server is free), into <root>/game. The mod is the only file added to it.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import zipfile

from . import paths, runner

WINDOWS = os.name == 'nt'
STEAMCMD_URL = ('https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip' if WINDOWS
                else 'https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz')


def steamcmd_program():
    return paths.steamcmd('steamcmd.exe' if WINDOWS else 'steamcmd.sh')


def _inside(base, name):
    target = os.path.realpath(os.path.join(base, name))
    return target == os.path.realpath(base) or target.startswith(os.path.realpath(base) + os.sep)


def _steamcmd_whole():
    """steamcmd is unpacked whole: on Linux the starter script and the program it starts (an unpacking
    cut short can leave the first without the second, and every later start would fail)."""
    return os.path.isfile(steamcmd_program()) and (WINDOWS or os.path.isfile(paths.steamcmd('linux32', 'steamcmd')))


def ensure_steamcmd(say=print):
    """Download and unpack steamcmd once, again when an earlier unpacking was cut short."""
    if _steamcmd_whole():
        return steamcmd_program()
    folder = paths.steamcmd()
    os.makedirs(folder, exist_ok=True)
    say(f'downloading steamcmd into {folder}', flush=True)
    with urllib.request.urlopen(STEAMCMD_URL, timeout=120) as response:
        blob = response.read()
    if WINDOWS:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            for member in archive.namelist():
                if not _inside(folder, member):
                    raise ValueError(f'unexpected path in the steamcmd archive: {member}')
            archive.extractall(folder)
    else:
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as archive:
            for member in archive.getmembers():
                if not _inside(folder, member.name) or member.issym() or member.islnk():
                    raise ValueError(f'unexpected entry in the steamcmd archive: {member.name}')
            archive.extractall(folder)
    if not _steamcmd_whole():
        raise RuntimeError(f'steamcmd was not unpacked whole into {folder}: is the disk full?')
    return steamcmd_program()


# " Update state (0x61) downloading, progress: 23.24 (1420611972 / 6111762123)", every 2 s while it works.
PROGRESS = re.compile(r'Update state \(0x[0-9a-f]+\) ([a-z ]+), progress: ([0-9.]+) \((\d+) / (\d+)\)')
SELF_UPDATE = re.compile(r'^\[\s*(\d+)%\] Downloading update')
STEPS = {'downloading': 'downloading the game', 'verifying install': 'checking the files already there',
         'verifying update': 'checking what was downloaded', 'committing': 'writing the game files',
         'preallocating': 'making room on the disk', 'reconfiguring': 'getting ready'}


def _progress(say):
    """What steamcmd is doing, from its lines: a bar that moves on a terminal, elsewhere (the output of
    ssh, a log) a line for each step and every 10 %."""
    live = say is print and sys.stdout.isatty()
    shown = {'step': None, 'tenth': -1, 'width': 0}

    def show(step, percent, done=None, whole=None):
        sizes = f'{done / 1e9:.1f} of {whole / 1e9:.1f} GB' if whole else ''
        new, tenth = step != shown['step'], int(min(percent, 100) // 10)
        if live:
            filled = int(min(percent, 100) / 5)
            text = f'  {step:<34} [{"#" * filled}{"-" * (20 - filled)}] {int(percent):3d} %  {sizes}'
            sys.stdout.write(('\n' if new and shown['step'] else '') + '\r' + text.ljust(0 if new else shown['width']))
            sys.stdout.flush()
            shown['width'] = len(text)
        elif new or tenth > shown['tenth']:
            say(f'  {step}: {int(percent)} %' + (f' ({sizes})' if sizes else ''), flush=True)
        shown['step'], shown['tenth'] = step, tenth if new else max(tenth, shown['tenth'])

    def seen(line):
        line = re.sub(r'\x1b\[[0-9;]*m', '', line).strip()
        match = PROGRESS.search(line)
        if match and int(match.group(4)):
            word = match.group(1).strip()
            show(STEPS.get(word, word), float(match.group(2)), int(match.group(3)), int(match.group(4)))
        elif SELF_UPDATE.match(line):
            show('steamcmd is updating itself', float(SELF_UPDATE.match(line).group(1)))

    def end():
        if live and shown['step']:
            sys.stdout.write('\n')
            sys.stdout.flush()
        shown.update(step=None, tenth=-1, width=0)
    return seen, end


def _quick_edit(mode=None):
    """A click in a Windows console window starts a selection, and while it lasts every program that
    writes to the window stops: steamcmd and its download too. Turned off while steamcmd writes there:
    returns the mode the window had, which is given back with _quick_edit(mode)."""
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.GetStdHandle.restype = ctypes.c_void_p
    handle = ctypes.c_void_p(kernel.GetStdHandle(-10))           # the console's input
    if mode is not None:
        kernel.SetConsoleMode(handle, mode)
        return mode
    now = ctypes.c_uint()
    if not kernel.GetConsoleMode(handle, ctypes.byref(now)):
        return None
    kernel.SetConsoleMode(handle, (now.value & ~0x40) | 0x80)     # no ENABLE_QUICK_EDIT_MODE; ENABLE_EXTENDED_FLAGS
    return now.value


def _steamcmd(command, say=print, timeout=None):
    """Run steamcmd in a process group of its own: whatever ends this command (an error, Ctrl-C, a stop
    signal, its `timeout` in seconds) ends steamcmd and the program it starts too, before the lock goes.
    (returncode, out, err).

    Its progress is shown as it comes. On Windows steamcmd holds its lines back until it ends when they
    go to a pipe, so in a console window it writes there itself, and out is None."""
    console = WINDOWS and say is print and sys.stdout.isatty()
    options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {'start_new_session': True}
    output = None if console else subprocess.PIPE
    process = subprocess.Popen(command, cwd=paths.steamcmd(), stdin=subprocess.DEVNULL, stdout=output,
                               stderr=None if console else subprocess.STDOUT, text=True, errors='replace', **options)
    lines, late = [], threading.Event()
    window = None
    if timeout:
        clock = threading.Timer(timeout, lambda: (late.set(), runner._stop(process)))
        clock.daemon = True
        clock.start()
    try:
        seen, end = _progress(say)
        window = _quick_edit() if console else None
        try:
            if console:
                process.wait()
            else:
                for line in process.stdout:
                    lines.append(line)
                    seen(line)
                process.wait()
        finally:
            end()                   # a terminal gone meanwhile may fail here: steamcmd is stopped all the same
    finally:
        if timeout:
            clock.cancel()
        runner._stop(process)
        if process.stdout:
            process.stdout.close()
        if window is not None:
            _quick_edit(window)
    if late.is_set():
        raise RuntimeError(f'steamcmd did not finish within {timeout} s, and was stopped')
    return process.returncode, None if console else ''.join(lines), ''


# In steamcmd's app_info_print: "public" { "buildid" "25902425" ... } under "branches".
BRANCH_BUILD = re.compile(r'"(public|staging)"\s*\{\s*"buildid"\s*"(\d+)"')


def steam_builds(timeout=120):
    """The builds Steam has now on the public and staging branches, asked with steamcmd (about 10 s):
    {'public': '...', 'staging': '...'}. Nothing in the game folder changes. A steamcmd that fails is not
    believed, whatever it printed, and one that does not answer within `timeout` seconds is stopped."""
    quiet = lambda *a, **k: None                   # noqa: E731 (a pipe: on Windows too, the lines are read at the end)
    program = ensure_steamcmd(quiet)
    code, out, err = _steamcmd([program, '+login', 'anonymous', '+app_info_update', '1',
                                '+app_info_print', str(paths.GAME_APP), '+quit'], say=quiet, timeout=timeout)
    if code:
        raise RuntimeError(f'steamcmd ended with {code} while asking Steam')
    found = dict(BRANCH_BUILD.findall(re.sub(r'\x1b\[[0-9;]*m', '', out or '')))
    if 'public' not in found:
        raise RuntimeError('Steam did not say which build it has')
    return found


def _as_steam_has(branch):
    """Whether the game's own record shows the build Steam has now on `branch`, fully installed: what proves an
    update steamcmd wrote to the window itself, where its word cannot be read."""
    try:
        steam = steam_builds().get(branch)
    except (RuntimeError, OSError):
        return False
    return bool(steam) and runner.game_build() == steam and runner.game_branch() == branch and runner.game_state() == '4'


def update_game(branch=None, say=print):
    """Install the game, or bring it up to the newest build of a branch: public unless another is named.

    The branch is always named: steamcmd otherwise keeps the branch the install is on, so one
    install made on staging would make every map on staging, which players of the public game
    may not be able to load.

    steamcmd cannot move an install to another branch in place (it stops with "Access Denied" on its
    record of the old one), so for a change of branch that record is put aside first: the new branch
    is then installed over the files, and `validate` fetches only what differs. The caller holds the lock.

    Until steamcmd has finished and the game is found on the branch asked for, the game folder holds
    runner.UPDATING, and no map is made: an update cut short leaves a game of no known build.
    """
    runner.claim()
    program = ensure_steamcmd(say)
    wanted = branch or 'public'
    updating = paths.game(runner.UPDATING)
    with open(updating, 'w') as handle:
        handle.write(f'steamcmd is updating this game to the {wanted} branch since '
                     f'{time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}; if nothing is running, run the update again\n')
    update = ['+app_update', str(paths.GAME_APP), '-beta', wanted, 'validate']
    command = [program, '+force_install_dir', paths.game(), '+login', 'anonymous'] + update + ['+quit']
    before, was = runner.game_build(), runner.game_branch()
    manifest = paths.game('steamapps', f'appmanifest_{paths.GAME_APP}.acf')
    aside = None
    if was and was != wanted and os.path.isfile(manifest):
        aside = f'{manifest}.{was}-{time.strftime("%Y%m%d%H%M%S", time.gmtime())}'
        os.replace(manifest, aside)
        say(f'steamcmd: moving the game from the {was} branch to {wanted}', flush=True)
    say(f'steamcmd: {"installing" if not before else "updating"} the Rust dedicated server in {paths.game()} '
        '(about 6 GB on the first install; it can take a while)', flush=True)
    last = ''
    # steamcmd updates itself on its first start and often stops there: a second pass does the work.
    for attempt in range(3):
        code, out, err = _steamcmd(command, say)
        last = (out[-1500:] + err[-500:] if out is not None else '(in its lines above)') + (f'\n(exit {code})' if code else '')
        # Written to the window itself, steamcmd's word cannot be read: Steam is asked which build it has, and the
        # game's record must show that one (a first start that only updated steamcmd ends with code 0 as well).
        if code == 0 and os.path.isfile(runner.executable()) and (
                "Success! App '258550'" in out if out is not None else _as_steam_has(wanted)):
            break
        if 'No such file or directory' in last and not WINDOWS and attempt == 0 and not os.path.exists('/lib/ld-linux.so.2'):
            raise RuntimeError('steamcmd needs the 32-bit C library: on Debian or Ubuntu run "sudo apt install lib32gcc-s1", '
                               'on Fedora "sudo dnf install glibc.i686 libstdc++.i686", then try again')
        say((f'  steamcmd ended with {code}' if code else '  steamcmd ended without its word that the game is in place' if out is not None
             else '  the game\'s record does not show the build Steam has yet')
            + '; on its first start it updates itself and often ends so: running it again to check', flush=True)
    else:
        kept = f' (the record of the {was} install was put aside as {aside})' if aside else ''
        raise RuntimeError(f'steamcmd did not install the game{kept}. Its last words:\n' + last)
    # Steam's own record decides: fully installed (StateFlags 4), on the branch asked for, with a build.
    if runner.game_branch() != wanted or not runner.game_build() or runner.game_state() != '4':
        raise RuntimeError(f'steamcmd said it finished, but its record of the game reads branch {runner.game_branch()}, build '
                           f'{runner.game_build()}, state {runner.game_state()} (a whole install on {wanted} reads state 4); '
                           'run the update again')
    if aside:
        os.unlink(aside)
    os.unlink(updating)
    build = runner.game_build()
    say(f'game build {build} ({runner.game_branch()})' + (f', was {before}' if before and before != build else ''), flush=True)
    return build


def build_mod(say=print):
    """Compile the mod against the installed game (needs the .NET SDK 8 or newer). Under the lock.

    It is built in a folder of its own and moved to mod/build only when the build succeeded, with a
    record of the source it was built from: the runner uses it only while the source is the same.
    """
    dotnet = shutil.which('dotnet')
    if not dotnet:
        raise RuntimeError('the .NET SDK is not installed: the mod that came with the code stays in use. To build the mod '
                           'against this game, install the .NET SDK 8 or newer (https://dotnet.microsoft.com/download) and run this again')
    managed = paths.game('RustDedicated_Data', 'Managed')
    if not os.path.isfile(os.path.join(managed, 'Assembly-CSharp.dll')):
        raise RuntimeError(f'no game at {paths.game()} to build against: install it first')
    project = os.path.join(paths.ROOT, 'mod', 'RustMapGen.csproj')
    source = runner.source_sha256()
    out = os.path.join(paths.ROOT, 'mod', 'build')
    os.makedirs(os.path.join(paths.ROOT, 'mod', 'obj'), exist_ok=True)
    work = tempfile.mkdtemp(prefix='rustmapgen-build-', dir=os.path.join(paths.ROOT, 'mod', 'obj'))
    try:
        result = subprocess.run([dotnet, 'build', project, '-c', 'Release', '-o', work, f'-p:RustManaged={managed}', '-nologo', '-v', 'q'],
                                capture_output=True, text=True, errors='replace')
        if result.returncode != 0 or not os.path.isfile(os.path.join(work, runner.MOD_FILE)):
            raise RuntimeError('the mod did not build against this game:\n' + (result.stdout or '')[-2000:])
        os.makedirs(out, exist_ok=True)
        target = os.path.join(out, runner.MOD_FILE)
        with open(os.path.join(work, runner.MOD_FILE), 'rb') as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        os.replace(os.path.join(work, runner.MOD_FILE), target)
        record = {'game_build': runner.game_build(), 'source_sha256': source, 'sha256': digest,
                  'built_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        with open(os.path.join(out, 'build.json.partial'), 'w') as handle:
            json.dump(record, handle, indent=1)
        os.replace(os.path.join(out, 'build.json.partial'), os.path.join(out, 'build.json'))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    say(f'mod built for game build {record["game_build"]}: {target}', flush=True)
    return target


def install(branch=None, say=print):
    """Everything a first start needs: the data folders, steamcmd, the game, the mod."""
    paths.ensure_data()
    with runner.lock():                 # never under a game that is making a map
        update_game(branch, say)
        if shutil.which('dotnet'):
            try:
                build_mod(say)
            except RuntimeError as error:
                say(f'  {error}', flush=True)
        say(f'  the mod used: {runner.describe_mod()}', flush=True)
        target = runner.install_mod()
    say(f'mod in place: {target}', flush=True)
    return runner.describe_install()


if __name__ == '__main__':
    sys.exit('use: python -m rustmapgen install')
