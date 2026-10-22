"""RustMapGen's own updates: the newest release of the public repository, and putting it in place.

A release is a version tag of the repository (vX.Y.Z). A copy cloned with git is brought to the newest
tag with git; a copy from a ZIP gets the release's files written over its own. What is this copy's own
(the data, the game, steamcmd, the Python environment, a mod built here) is never written.

An update is put in place so that it ends whole or can be finished: the release is checked whole and its
Python packages are installed before any file of this copy changes, every changed file is written beside
its place first, and a record in the data folder (PENDING) says an update is under way until the last
file, the version, is in. While the record is there, the same release is offered again, however far the
update came, and done again from the start (files already right are left as they are).
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

from . import __version__, paths

REPOSITORY = 'S1gil0/rustmapgen'
# Where releases are read: the repository on GitHub, or another address that answers the same way (a test).
API = os.environ.get('RUSTMAPGEN_RELEASES', f'https://api.github.com/repos/{REPOSITORY}')
PAGE = f'https://github.com/{REPOSITORY}'
TAG = re.compile(r'^v(\d+)\.(\d+)\.(\d+)$')
KEEP = ('data', 'game', 'game-next', 'game-staging', 'steamcmd', '.venv', '.git', 'mod/build', 'mod/obj')
# Windows reads a running batch file as it goes: a launcher that changes is never followed by a restart from it.
LAUNCHERS = ('rustmapgen.cmd', 'rustmapgen.sh')
VERSION_FILE = 'rustmapgen/__init__.py'       # put in last: it says which release this copy is
PENDING = 'update-pending.json'               # in the data folder, while an update is under way
ZIP_LIMIT = 64 * 1024 * 1024
RESTART = 3                         # the panel's exit code that has its launcher start it again
# The public repository's address as git may have it: https, ssh, with or without .git.
ORIGIN = re.compile(r'^(https://github\.com/|ssh://git@github\.com/|git@github\.com:)' + re.escape(REPOSITORY) + r'(\.git)?/?$')


def version(text):
    match = TAG.match(text or '')
    return tuple(int(part) for part in match.groups()) if match else None


def _get(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': f'RustMapGen/{__version__}', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        blob = response.read(limit + 1)
    if len(blob) > limit:
        raise RuntimeError(f'{url} sent more than a release can be')
    return blob


def _git(*arguments, timeout=600):
    return subprocess.run(['git', '-C', paths.ROOT] + list(arguments), capture_output=True, text=True, timeout=timeout)


def how():
    """'git' for a clone of the public repository, 'zip' for a copy without git, 'development' for a clone of anything else."""
    if not os.path.isdir(os.path.join(paths.ROOT, '.git')):
        return 'zip'
    try:
        origin = _git('remote', 'get-url', 'origin', timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 'development'
    return 'git' if ORIGIN.match(origin) else 'development'


def pending():
    """The update under way, {'version', 'started'}, or None."""
    try:
        with open(paths.data(PENDING)) as handle:
            record = json.load(handle)
        return record if isinstance(record, dict) and version('v' + str(record.get('version'))) else None
    except (OSError, ValueError):
        return None


def _keep_pending(value):
    target = paths.data(PENDING)
    if value is None:
        if os.path.exists(target):
            os.unlink(target)
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target + '.partial', 'w') as handle:
        json.dump({'version': value, 'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, handle)
    os.replace(target + '.partial', target)


def check():
    """The newest release, and whether to install it: when it is newer than this copy, or when an update
    to it did not finish (its version file may already say it is there). A repository that is not public
    (yet) answers 404 to everyone outside it: that is no release."""
    try:
        tags = json.loads(_get(f'{API}/tags?per_page=100', 2 * 1024 * 1024))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        tags = []
    releases = sorted(((version(tag.get('name')), tag) for tag in tags if isinstance(tag, dict) and version(tag.get('name'))), key=lambda pair: pair[0])
    out = {'current': __version__, 'latest': None, 'newer': False, 'how': how()}
    unfinished = pending()
    if unfinished:
        out['unfinished'] = unfinished['version']
    if releases:
        newest, tag = releases[-1]
        # What is new: the changelog as that release has it, on GitHub.
        out.update(latest=tag['name'][1:], zip=tag.get('zipball_url'), notes=f'{PAGE}/blob/{tag["name"]}/CHANGELOG.md',
                   newer=newest > version('v' + __version__) or bool(unfinished and newest >= version('v' + unfinished['version'])))
    return out


def install_requirements(text):
    """The release's Python packages, into this copy's environment (nothing to fetch when they are there).
    Run before any file of the copy changes, on every attempt: a retry never skips them."""
    folder = paths.data()
    os.makedirs(folder, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', suffix='-requirements.txt', dir=folder, delete=False) as handle:
        handle.write(text)
    try:
        result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', '-r', handle.name],
                                capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        raise RuntimeError('the Python packages of the release took too long to install: nothing was changed, try again') from None
    finally:
        os.unlink(handle.name)
    if result.returncode:
        raise RuntimeError('the Python packages of the release did not install, nothing was changed: '
                           + (result.stderr or result.stdout).strip()[-300:])


def _protected():
    """This copy's own folders, as the file system has them (links followed, case as Windows compares it)."""
    return [os.path.normcase(os.path.realpath(os.path.join(paths.ROOT, *keep.split('/')))) for keep in KEEP]


def _is_kept(target, kept):
    target = os.path.normcase(target)
    return any(target == keep or target.startswith(keep + os.sep) for keep in kept)


def _plan(blob):
    """The release's files that differ from this copy's: [(relative name, target path, data, executable)].
    The archive is read whole first, and every name checked where it would really be written."""
    root = os.path.realpath(paths.ROOT)
    kept = _protected()
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        broken = archive.testzip()
        if broken:
            raise RuntimeError(f'the release is damaged ({broken}): nothing was changed')
        plan = []
        for member in archive.infolist():
            if member.is_dir():
                continue
            parts = [part for part in member.filename.replace('\\', '/').split('/')[1:] if part not in ('', '.')]
            target = os.path.realpath(os.path.join(root, *parts)) if parts else root
            if (not parts or '..' in parts or member.filename.startswith('/') or ':' in parts[0]
                    or not os.path.normcase(target).startswith(os.path.normcase(root) + os.sep)):
                raise RuntimeError(f'the release holds an unexpected name: {member.filename}')
            if _is_kept(target, kept):
                raise RuntimeError(f'the release would write {"/".join(parts)}, which is this copy\'s own: nothing was changed')
            data = archive.read(member)
            try:
                with open(target, 'rb') as handle:
                    if handle.read() == data:
                        continue
            except OSError:
                pass
            plan.append(('/'.join(parts), target, data, bool((member.external_attr >> 16) & 0o111)))
    return plan


def _put(plan):
    """Every changed file written beside its place, then each moved over the old one, the version file last.
    A failure while writing them beside leaves this copy as it was."""
    written = []
    try:
        for name, target, data, executable in plan:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target + '.partial', 'wb') as handle:
                handle.write(data)
            if executable:
                os.chmod(target + '.partial', 0o755)
            written.append(target)
    except OSError as error:
        for target in written:
            try:
                os.unlink(target + '.partial')
            except OSError:
                pass
        raise RuntimeError(f'{name} could not be written ({error.strerror or error}): nothing was changed') from None
    return written


@contextlib.contextmanager
def _install_lock():
    """One update of this copy at a time, whichever panel or command asks, and whatever data or game folder
    it uses: an OS lock on a file beside the code, which goes when the process goes."""
    handle = open(os.path.join(paths.ROOT, 'rustmapgen-update.lock'), 'a+')
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError('another update of RustMapGen is under way in this folder') from None
        yield
    finally:
        handle.close()


def finish(say=print):
    """`rustmapgen finish-update`: the update that did not finish, done again from the start. It needs nothing
    of RustMapGen but this module, so it works when a half-updated copy cannot start its panel."""
    unfinished = pending()
    if not unfinished:
        say('no update of RustMapGen is unfinished')
        return None
    found = check()
    if not found.get('newer'):
        raise RuntimeError(f'the update to {unfinished["version"]} cannot be finished: the release is not offered any more')
    say(f'finishing the update to {found["latest"]}…', flush=True)
    done = apply(found)
    say(f'RustMapGen {done["version"]} is in place: start the panel again', flush=True)
    return done


def apply(found):
    """Put the release `check()` found in place. {'version', 'restart': 'auto' or 'by hand', 'files'}."""
    with _install_lock():
        return _apply(found)


def _apply(found):
    if not found.get('newer'):
        raise RuntimeError('this copy is up to date')
    if found['how'] == 'development':
        raise RuntimeError('this is a development copy (a git clone of another repository): update it with git')
    tag = 'v' + found['latest']
    if found['how'] == 'git':
        before = {name: _read(name) for name in LAUNCHERS}
        fetched = _git('fetch', '--tags', 'origin')
        if fetched.returncode:
            raise RuntimeError('git fetch did not work, nothing was changed: ' + (fetched.stderr or fetched.stdout).strip()[-300:])
        names = _git('ls-tree', '-r', '--name-only', tag).stdout.split('\n')
        kept = [name for name in names if any(name == keep or name.startswith(keep + '/') for keep in KEEP)]
        if kept:
            raise RuntimeError(f'the release would write {kept[0]}, which is this copy\'s own: nothing was changed')
        install_requirements(_git('show', f'{tag}:requirements.txt').stdout)
        _keep_pending(found['latest'])
        merged = _git('merge', '--ff-only', tag)
        if merged.returncode:
            raise RuntimeError(f'git merge did not work; Update again to finish {found["latest"]}: ' + (merged.stderr or merged.stdout).strip()[-300:])
        changed = {name for name, old in before.items() if _read(name) != old}
        files = None
    else:
        plan = _plan(_get(found['zip'], ZIP_LIMIT))
        requirements = next((data for name, _, data, _ in plan if name == 'requirements.txt'), None)
        install_requirements((requirements or _read('requirements.txt') or b'').decode('utf-8', 'replace'))
        plan.sort(key=lambda row: row[0] == VERSION_FILE)
        written = _put(plan)
        _keep_pending(found['latest'])
        try:
            for target in written:
                os.replace(target + '.partial', target)
        except OSError as error:
            raise RuntimeError(f'the update to {found["latest"]} stopped half way ({error}): Update again to finish it') from None
        changed = {name for name, _, _, _ in plan}
        files = len(plan)
    _keep_pending(None)
    by_hand = bool(changed & set(LAUNCHERS)) or not os.environ.get('RUSTMAPGEN_LAUNCHER')
    return {'version': found['latest'], 'restart': 'by hand' if by_hand else 'auto', 'files': files}


def _read(name):
    try:
        with open(os.path.join(paths.ROOT, name), 'rb') as handle:
            return handle.read()
    except OSError:
        return None
