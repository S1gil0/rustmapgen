"""RustMapGen: procedural Rust maps made on your own computer.

    python -m rustmapgen install                  steamcmd, the Rust dedicated server and the mod
    python -m rustmapgen panel                    the control panel in your browser (http://127.0.0.1:5350)
    python -m rustmapgen generate --recipe large [--seed N] [--size N] [--count N] [--note TEXT]
    python -m rustmapgen load-test <map>          have the game load a finished map
    python -m rustmapgen list                     the maps made so far
    python -m rustmapgen recheck <map>            describe, check and draw a map again
    python -m rustmapgen update [--branch staging]   bring the game up to the newest public build (or of that branch)
    python -m rustmapgen build-mod                build the mod for the installed game (needs the .NET SDK)
    python -m rustmapgen doctor                   what this computer has
"""
import argparse
import json
import os
import sys

from . import __version__, paths


def main(argv=None):
    parser = argparse.ArgumentParser(prog='rustmapgen', description='Procedural Rust maps made on your own computer.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    install = commands.add_parser('install', help='steamcmd, the game and the mod')
    install.add_argument('--branch', help='a branch of the game other than the public one (staging)')
    update = commands.add_parser('update', help='bring the game up to its newest build')
    update.add_argument('--branch', help='another branch than the public one (staging); without it the game goes back to public')
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
    test = commands.add_parser('load-test', help='have the game load a finished map')
    test.add_argument('name')
    commands.add_parser('list', help='the maps made so far')
    recheck = commands.add_parser('recheck', help='describe, check and draw a map again')
    recheck.add_argument('name')
    recheck.add_argument('--remerge', action='store_true', help='write final.map again from raw.map and the templates')
    commands.add_parser('doctor', help='what this computer has')
    args = parser.parse_args(argv)
    if args.command in ('install', 'update', 'build-mod', 'generate', 'load-test'):
        from . import runner
        runner.stop_on_signals()        # these hold the game: a stop signal ends them through their cleanup

    if args.command == 'install':
        from . import game
        print(json.dumps(game.install(args.branch), indent=1))
    elif args.command == 'update':
        from . import game, runner
        with runner.lock():
            game.update_game(args.branch)
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
        failed = 0
        for index in range(args.count):
            try:
                make.generate(path, args.seed if args.count == 1 else None, args.size, args.note)
            except RuntimeError as error:
                failed += 1
                print(error, file=sys.stderr, flush=True)
        if failed:
            sys.exit(f'{failed} of {args.count} maps were not made')
    elif args.command == 'load-test':
        from . import generate as make
        make.load_test(args.name)
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
