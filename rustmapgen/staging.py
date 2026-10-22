"""The next update, for the forced wipe: when maps may be made on it, and its game folder.

Facepunch's monthly update reaches the public game on the first Thursday of the month, the forced wipe, and
it is the update that changes how maps are made. Before it goes public, a Steam branch of the dedicated server
holds it (`release`, the build to be released, or `staging`, everything still in development). Which branch,
and when its maps are final, is decided by people, not guessed by this code: the authors measure each build
(does it make the same maps as the public game, and what changes), and once the maps of the update are final
they release a RustMapGen that declares it, in DECLARATION: the day of the forced wipe, the Steam branch, the
build they checked, and what changes in the maps, in plain words. The panel shows those changes once.

- Open from the release that declares it until the update is out on the day of the forced wipe, and at the
  latest until that day ends (UTC). Without a declaration nothing of it is offered.
- The release is seen on Steam, not on a clock (its hour moves, and with summer time): Steam says when each
  branch was last set to a build, and a public branch set on the day of the forced wipe (UTC) is the release;
  so is a public branch that holds the declared branch's build that day. On that day work on the next update
  asks Steam first when its last answer is older than FRESH_MINUTES. From the release on, maps are made on the
  public game, brought to the release first.
- Before each batch its game is brought to Steam's newest build of the branch: users get what the branch holds
  then, not the very build the authors checked (Steam's anonymous downloads cannot pick an older one). If the
  branch is given a build that changes the maps again, the authors release a correction.

The panel and the command line keep the same rule; RUSTMAPGEN_STAGING_ANYDAY=1 opens it on any day, and
RUSTMAPGEN_NEXT_UPDATE names another declaration file, for testing. Every answer Steam gives RustMapGen is kept
(steam-builds.json in the data folder).

The next update has a game folder of its own (game-next/ beside game/, or RUSTMAPGEN_GAME_NEXT), so public
maps can still be made those days. A command works on it when RUSTMAPGEN_GAME names that folder: the panel
starts its jobs so, and `--game next` does the same on the command line. Its game listens on ports of its
own (PORT_OFFSET above the public game's), so the two never meet. In the code this game is called
"staging", the name of its first version, which used Steam's staging branch.
"""
import datetime
import json
import os

from . import paths

DECLARATION = 'next-update.json'    # in the package; a release that declares the next update ships it
BRANCHES = ('public', 'release', 'staging')
OVERRIDE = 'RUSTMAPGEN_STAGING_ANYDAY'
PORT_OFFSET = 10
SEEN = 'steam-builds.json'          # in the data folder: what Steam said, and when
SEEN_KEPT = 200
FRESH_MINUTES = 30
UTC = datetime.timezone.utc


def folder(*parts):
    """The next update's game folder."""
    base = os.environ.get('RUSTMAPGEN_GAME_NEXT') or os.path.join(paths.ROOT, 'game-next')
    return os.path.join(os.path.abspath(base), *parts)


def active():
    """Whether the game this process works on is the next update's."""
    return os.path.normcase(os.path.abspath(paths.game())) == os.path.normcase(folder())


_public = None


def use():
    """Work on the next update's game from here on: this process and the commands it starts (the command line
    only; the panel names the folder to each job it starts instead). The public game's folder is kept (public())."""
    global _public
    if not active():
        _public = paths.game()
    os.environ['RUSTMAPGEN_GAME'] = folder()


def public():
    """The public game's folder, also after use()."""
    return _public or paths.game()


def same_place(one, two):
    """Whether two folders are one, or one holds the other, once links and junctions are followed: the next
    update's game must never be the public game, whatever names the settings give them."""
    a, b = (os.path.normcase(os.path.realpath(path)) for path in (one, two))
    if a == b or a.startswith(b.rstrip(os.sep) + os.sep) or b.startswith(a.rstrip(os.sep) + os.sep):
        return True
    try:
        return os.path.samefile(one, two)     # one folder under two names no link shows (a bind mount)
    except OSError:
        return False                    # one of them is not there yet


def first_thursday(year, month):
    first = datetime.date(year, month, 1)
    return first + datetime.timedelta(days=(3 - first.weekday()) % 7)


def last_wipe(now=None):
    """The day of the latest forced wipe, today's included: this month's first Thursday once it has come, else last month's."""
    now = now or datetime.datetime.now(UTC)
    wipe = first_thursday(now.year, now.month)
    if wipe > now.date():
        wipe = first_thursday(*((now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)))
    return wipe


def wipe_start(now=None):
    """The latest forced wipe's day, from 00:00 UTC."""
    wipe = last_wipe(now)
    return datetime.datetime(wipe.year, wipe.month, wipe.day, tzinfo=UTC)


def declared(now=None):
    """The next update this release declares, while its forced wipe has not ended: {'wipe' (a date), 'branch',
    'checked', 'build', 'changes' (plain sentences, at least one)}; None without one, or with one that is not whole."""
    now = now or datetime.datetime.now(UTC)
    found = _declaration()
    return found if found and now.date() <= found['wipe'] else None


def _declaration():
    """The declaration this release ships, whole, whatever its day; None without one."""
    try:
        with open(os.environ.get('RUSTMAPGEN_NEXT_UPDATE') or paths.package(DECLARATION)) as handle:
            found = json.load(handle)
        wipe = datetime.date.fromisoformat(found['wipe'])
        branch = found['branch']
        changes = [str(line) for line in found['changes'] if str(line).strip()] if isinstance(found['changes'], list) else []
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if branch not in ('release', 'staging') or wipe != first_thursday(wipe.year, wipe.month) or not changes:
        return None
    return {'wipe': wipe, 'branch': branch, 'checked': str(found.get('checked') or ''), 'build': str(found.get('build') or ''),
            'changes': changes}


def branch():
    """The Steam branch the next update is made on."""
    return (declared() or {}).get('branch') or 'release'


def key(now=None):
    """The declaration in force, as a map records it ('<wipe>/<checked>/<build>', the build too, so that a correction
    made the same day is one): a map made under an earlier one predates a correction of the authors."""
    found = declared(now)
    return f'{found["wipe"]}/{found["checked"]}/{found["build"]}' if found else None


def window(now=None):
    """The next update declared and not over, with `days_left` to its forced wipe; None otherwise."""
    now = now or datetime.datetime.now(UTC)
    found = declared(now)
    return dict(found, days_left=(found['wipe'] - now.date()).days) if found else None


def seen():
    """What Steam said, oldest first: [{'utc', 'public', 'release', 'staging', 'public_updated', ...}]."""
    try:
        with open(paths.data(SEEN)) as handle:
            rows = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:                    # a damaged line is left out, not allowed to stop every decision
        if isinstance(row, dict) and row.get('utc') and row.get('public'):
            try:
                _stamp(row)
            except (TypeError, ValueError):
                continue
            out.append(row)
    return out


def remember(builds, now=None):
    """Keep what Steam said ({'public': ..., 'release': ..., 'staging': ..., '<branch>_updated': ...}), with the time."""
    now = now or datetime.datetime.now(UTC)
    row = {'utc': now.strftime('%Y-%m-%dT%H:%M:%SZ')}
    for name in BRANCHES:
        row[name] = str(builds.get(name) or '')
        row[f'{name}_updated'] = builds.get(f'{name}_updated')
    rows = seen() + [row]
    os.makedirs(paths.data(), exist_ok=True)
    target = paths.data(SEEN)
    with open(target + '.partial', 'w') as handle:
        json.dump(rows[-SEEN_KEPT:], handle)
    os.replace(target + '.partial', target)


def _stamp(row):
    return datetime.datetime.strptime(row['utc'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC)


def recent(now=None):
    """Steam's last answer while it is younger than FRESH_MINUTES, else None."""
    now = now or datetime.datetime.now(UTC)
    rows = seen()
    if rows and datetime.timedelta(0) <= now - _stamp(rows[-1]) <= datetime.timedelta(minutes=FRESH_MINUTES):
        return rows[-1]
    return None


def released(now=None):
    """Steam's answer that shows the forced wipe's update out: {'utc', 'public', 'since'} ('at HH:MM' or 'by HH:MM',
    UTC); None while nothing shows it (or with no next update declared). The public branch was set to a build on
    that day, or on that day it holds the declared branch's build. Neither needs an answer from earlier in the
    week: a first look at Steam after the release sees it."""
    now = now or datetime.datetime.now(UTC)
    found = declared(now)
    if not found:
        return None
    day = datetime.datetime(found['wipe'].year, found['wipe'].month, found['wipe'].day, tzinfo=UTC)
    for row in seen():
        if _stamp(row) > now:
            continue
        updated = row.get('public_updated')
        if isinstance(updated, int) and day.timestamp() <= updated <= now.timestamp():
            return dict(row, since='at ' + datetime.datetime.fromtimestamp(updated, UTC).strftime('%H:%M'))
        if _stamp(row) >= day and row['public'] == row.get(found['branch']):
            return dict(row, since='by ' + row['utc'][11:16])
    return None


def needs_word(now=None):
    """Whether Steam has to be asked before work on the next update: on the day of the forced wipe, when its last
    answer is older than FRESH_MINUTES (the update may be out since)."""
    now = now or datetime.datetime.now(UTC)
    found = window(now)
    if not found or found['days_left'] != 0 or os.environ.get(OVERRIDE) == '1':
        return False
    rows = seen()
    # An answer from the future (a clock set back) is no fresh answer either.
    return not rows or not datetime.timedelta(0) <= now - _stamp(rows[-1]) <= datetime.timedelta(minutes=FRESH_MINUTES)


def is_open(now=None):
    if os.environ.get(OVERRIDE) == '1':
        return True
    return bool(window(now)) and not released(now)


def day(value):
    """'Thu 5 Nov', on every system (strftime's day without a zero is not portable)."""
    return f'{value:%a} {value.day} {value:%b}'


def notice(now=None):
    """What the panel says while the next update is open."""
    found = window(now)
    if not found or not is_open(now):
        return None
    on = f'Steam\'s {found["branch"]} branch'
    if found['days_left'] == 0:
        return (f'The forced wipe is today ({day(found["wipe"])}). Its maps are made on {on} until the update is out: '
                'RustMapGen asks Steam before each of them. From then on New Map makes maps on the public game, brought to the '
                'update first; maps of the next update still waiting are not made.')
    when = 'tomorrow' if found['days_left'] == 1 else f'in {found["days_left"]} days'
    checked = f' on {found["checked"]}' if found['checked'] else ''
    return (f'The forced wipe is {when} ({day(found["wipe"])}). Its maps are made on {on}: the RustMapGen authors '
            f'checked{checked} that it makes the maps of that update.')


def refusal(now=None):
    """Why no map may be made on the next update now, or None."""
    now = now or datetime.datetime.now(UTC)
    if is_open(now):
        return None
    out = released(now)
    if out:
        return (f'the forced wipe\'s update came out {out["since"]} UTC (Steam\'s public build {out["public"]}): maps for the '
                'next update are over; make them on the public game, which is brought to the update first')
    shipped = _declaration()
    if shipped and now.date() > shipped['wipe']:
        return (f'the forced wipe of {day(shipped["wipe"])} is over: maps are made on the public game, brought to its update '
                'once Steam has it')
    return ('maps for the next update come with an update of RustMapGen before the forced wipe, once the authors have '
            'checked its maps: Updates → Check for updates')


def describe(now=None):
    """The next update, for the panel: open or not and why, its forced wipe and branch, and what changes."""
    now = now or datetime.datetime.now(UTC)
    found, out, open_now = window(now), released(now), is_open(now)
    return {'open': open_now, 'override': os.environ.get(OVERRIDE) == '1' and not found,
            'reason': None if open_now else refusal(now),
            'released': {'public': out['public'], 'utc': out['utc']} if out else None,
            'wipe': day(found['wipe']) if found else None, 'days_left': found['days_left'] if found else None,
            'branch': found['branch'] if found else None, 'checked': found['checked'] if found else None,
            'build': found['build'] if found else None, 'changes': found['changes'] if found else [],
            'key': key(now), 'notice': notice(now)}
