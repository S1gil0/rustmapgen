"""RustMapGen: procedural Rust maps made on your own computer.

    python -m rustmapgen install                  steamcmd, the Rust dedicated server and the mod
    python -m rustmapgen panel                    the control panel in your browser (http://127.0.0.1:5350)
    python -m rustmapgen generate --recipe large [--seed N] [--size N] [--count N] [--note TEXT] [--game next]
    python -m rustmapgen load-test <map> [--game next]   have the game load a finished map
    python -m rustmapgen models-3d                the monuments and rocks of the 3D view, from the installed game
    python -m rustmapgen list                     the maps made so far
    python -m rustmapgen recheck <map>            describe, check and draw a map again
    python -m rustmapgen update [--if-newer | --forced-wipe | --game next]   bring the game up to its newest build
    python -m rustmapgen remove-next              delete the next update's game (about 6 GB)
    python -m rustmapgen build-mod                build the mod for the installed game (needs the .NET SDK)
    python -m rustmapgen doctor                   what this computer has
    python -m rustmapgen finish-update            finish an update of RustMapGen that stopped half way

From the forced wipe on (the first Thursday of the month), `generate` first brings a game last updated before that
day to the wipe's update, once it is out (game.catch_up). The next update (`--game next`) is a game folder of its own,
on the Steam branch a RustMapGen update declared, used from that update until the forced wipe's update is out: see
staging.py.
"""
import argparse
import json
import os
import sys

from . import __version__, paths

NEXT = 'next: the next update, on a game of its own, once a RustMapGen update has declared its maps (before the forced wipe)'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='rustmapgen', description='Procedural Rust maps made on your own computer.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    install = commands.add_parser('install', help='steamcmd, the game and the mod')
    install.add_argument('--game', choices=('public', 'next'), default='public', help=NEXT)
    update = commands.add_parser('update', help='bring the game up to its newest build')
    update.add_argument('--game', choices=('public', 'next'), default='public', help=NEXT)
    update.add_argument('--if-newer', action='store_true', help='ask Steam first (an answer of the last half hour does), and '
                        'leave a game already at its newest build as it is')
    update.add_argument('--forced-wipe', action='store_true', help='only a game last updated before the latest forced wipe, and '
                        'only once that update is out (what generate does first)')
    commands.add_parser('build-mod', help='build the mod for the installed game (needs the .NET SDK)')
    panel = commands.add_parser('panel', help='the control panel')
    panel.add_argument('--port', type=int)
    panel.add_argument('--host', default='127.0.0.1', help='127.0.0.1 keeps the panel to this computer (default)')
    panel.add_argument('--no-browser', action='store_true')
    generate = commands.add_parser('generate', help='make maps from a recipe')
    generate.add_argument('--recipe', required=True, help='a preset or saved recipe by name, or a recipe file')
    generate.add_argument('--seed', type=int)
    generate.add_argument('--size', type=int)
    generate.add_argument('--count', type=int, default=1)
    generate.add_argument('--note', default='')
    generate.add_argument('--game', choices=('public', 'next'), default='public', help=NEXT)
    test = commands.add_parser('load-test', help='have the game load a finished map')
    test.add_argument('name')
    test.add_argument('--game', choices=('public', 'next'), default='public', help=NEXT)
    commands.add_parser('models-3d', help='the monuments and rocks of the 3D view, from the installed game')
    commands.add_parser('list', help='the maps made so far')
    recheck = commands.add_parser('recheck', help='describe, check and draw a map again')
    recheck.add_argument('name')
    recheck.add_argument('--remerge', action='store_true', help='write final.map again from raw.map and the templates')
    commands.add_parser('doctor', help='what this computer has')
    commands.add_parser('remove-next', help='delete the next update\'s game (about 6 GB)')
    commands.add_parser('finish-update', help='finish an update of RustMapGen that stopped half way')
    args = parser.parse_args(argv)
    if args.command == 'finish-update':
        # Only this module: it must work when a half-updated copy cannot load the rest.
        from . import release
        release.finish()
        return
    if args.command in ('install', 'update', 'build-mod', 'generate', 'load-test', 'models-3d', 'remove-next', 'panel'):
        from . import release
        unfinished = release.pending()
        if unfinished and args.command != 'panel':
            sys.exit(f'an update of RustMapGen to {unfinished["version"]} did not finish, so nothing runs until it is: '
                     'run "rustmapgen finish-update" (or Updates → Finish it, in the panel)')
    on_staging = getattr(args, 'game', None) == 'next'
    if on_staging or args.command == 'remove-next':
        _use_staging(window=args.command != 'remove-next', installed=args.command in ('generate', 'load-test'))
    if args.command in ('install', 'update', 'build-mod', 'generate', 'load-test', 'remove-next'):
        from . import runner
        runner.stop_on_signals()        # these hold the game: a stop signal ends them through their cleanup

    if args.command == 'install':
        from . import game, models3d, staging
        installed = game.install(staging.branch() if on_staging else None)
        if not on_staging:
            models3d.after_game_change()    # the 3D view's monuments and rocks, from the game just installed
        print(_installed(installed, "the next update's game" if on_staging else 'game'))
    elif args.command == 'update':
        from . import game, models3d, runner, staging
        if on_staging:
            with runner.lock():
                game.bring_to_newest(staging.branch())
        else:
            with runner.lock():
                if args.forced_wipe:
                    game.catch_up()
                elif args.if_newer:
                    game.bring_to_newest('public')
                else:
                    game.update_game()
            models3d.after_game_change()    # made again for the new build (nothing to do when it did not change)
    elif args.command == 'remove-next':
        from . import game
        if os.path.isdir(paths.game()):
            game.remove_staging()
        else:
            print('there is no game of the next update')
    elif args.command == 'build-mod':
        from . import game, runner
        with runner.lock():             # never while a run or an update uses the game it is built against
            game.build_mod()
            runner.install_mod()
    elif args.command == 'panel':
        from . import server
        # Ends with release.RESTART after an update of its own: the launcher then starts the new panel, which
        # opens no second browser tab (the page that asked reloads itself).
        again = paths.data('panel-restarting')
        restarted = os.path.exists(again)
        if restarted:
            os.unlink(again)
        code = server.serve(args.host, args.port or paths.settings()['panel_port'], open_browser=not args.no_browser and not restarted)
        if code:
            open(again, 'w').close()
            sys.exit(code)
    elif args.command == 'generate':
        from . import generate as make
        path = make.find_recipe(args.recipe) or (args.recipe if os.path.isfile(args.recipe) else None)
        if not path:
            sys.exit(f'no recipe "{args.recipe}": name a preset ({", ".join(presets())}), a saved recipe or a file')
        if not 1 <= args.count <= 50:
            sys.exit('--count is 1 to 50')
        if args.seed and args.count > 1:
            print(f'--seed is for one map: the {args.count} maps get seeds of their own', file=sys.stderr, flush=True)
        from . import game, runner, staging
        failed = 0
        for index in range(args.count):
            if on_staging:
                # Before every map: the next update may have closed meanwhile (the release, the day's end), and its branch
                # can be given another build up to the release, so each map starts from Steam's newest, or not at all;
                # and asked again after that, which can take a while and can learn of the release itself.
                why = _next_closed() if index else None
                if not why:
                    try:
                        with runner.lock():
                            game.bring_to_newest(staging.branch())
                    except (RuntimeError, OSError, runner.Busy) as error:
                        why = f'the next update\'s game was not brought to Steam\'s newest build ({error})'
                    else:
                        why = staging.refusal()
                if why:
                    failed += args.count - index
                    print(f'{why}: {args.count - index} of the maps are not made', file=sys.stderr, flush=True)
                    break
            elif game.behind_forced_wipe():
                with runner.lock():        # before each map, as the panel's jobs do: the update may come out meanwhile
                    game.catch_up()
            try:
                make.generate(path, args.seed if args.count == 1 else None, args.size, args.note)
            except RuntimeError as error:
                failed += 1
                print(error, file=sys.stderr, flush=True)
        if failed:
            sys.exit(f'{failed} of {args.count} maps were not made')
    elif args.command == 'load-test':
        from . import game, generate as make, library, runner, staging
        try:
            made_on = (library.entry(args.name).get('run') or {}).get('game_branch')
        except (OSError, ValueError):
            made_on = None
        if on_staging or made_on not in (None, 'public'):
            # On the next update's game, Steam's newest build of its branch; a map of the next update on the public game,
            # the released game once it is out: each brought there first, or no test.
            with runner.lock():
                game.bring_to_newest(staging.branch() if on_staging else 'public')
            if on_staging and staging.refusal():
                sys.exit(staging.refusal())     # what Steam said meanwhile may close it
        make.load_test(args.name)
    elif args.command == 'models-3d':
        from . import models3d
        models3d.build()
    elif args.command == 'list':
        from . import library
        for card in library.cards():
            print(f"{card['name']:64s} {card['size']}  rails {card['rail_km']:5.1f} km  checks {card['checks_ok']}/{card['checks']}")
    elif args.command == 'recheck':
        from . import library
        print(json.dumps(library.add(args.name, remerge=args.remerge), indent=1))
    elif args.command == 'doctor':
        from . import runner
        print(json.dumps(runner.describe_install(), indent=1))


def _use_staging(window=True, installed=False):
    """Work on the next update's game: in its days only, never in the public game's own folder."""
    from . import runner, staging
    if staging.same_place(staging.folder(), paths.game()):
        sys.exit('the next update\'s game and the public game would share a folder (one is, holds or sits in the other, links '
                 'followed): set RUSTMAPGEN_GAME_NEXT to a folder of its own')
    if window:
        why = _next_closed()
        if why:
            sys.exit(why)
    staging.use()
    if installed and not os.path.isfile(runner.executable()):
        sys.exit('the next update\'s game is not installed yet: run "update --game next" first (about 6 GB)')


def _installed(info, which='game'):
    """What an install made, for a person (`doctor` gives the whole record)."""
    lines = ['', 'In place:',
             f'  {which}: build {info.get("build") or "?"}, {info.get("branch") or "public"} branch, in {info.get("game")}',
             f'  mod: {(info.get("mod_used") or "none").split(" (")[0]}',
             f'  maps and settings: {info.get("data")}']
    if info.get('mod_broken'):
        lines.append(f'  the mod did not load on build {info["mod_broken"].get("build")}: check for a newer RustMapGen')
    return '\n'.join(lines)


def _next_closed():
    """Why work on the next update may not go on now, or None. On the day of the forced wipe it goes on only while
    Steam says the update is not out yet, asked again when its last word is older than staging.FRESH_MINUTES."""
    from . import game, staging
    if staging.needs_word():
        try:
            game.steam_builds()
        except (RuntimeError, OSError) as error:
            return (f'on the day of the forced wipe, work on the next update waits for Steam\'s word that the update is not '
                    f'out yet, and Steam did not answer ({error}): try again')
    return staging.refusal()


def presets():
    folder = paths.package('presets')
    return sorted(name[:-len('.json')] for name in os.listdir(folder) if name.endswith('.json'))


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:          # one line for a person, not a trace
        if os.environ.get('RUSTMAPGEN_DEBUG'):
            raise
        sys.exit(f'rustmapgen: {error}')
