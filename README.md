# RustMapGen

Make procedural maps for Rust on your own computer, with the game's own generator, and decide
what goes on them: which monuments, how much land, how high the hills, your own prefabs around
the Outpost or any other monument. A panel in your browser holds the settings, makes maps in a
queue, checks every map and shows it.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/panel-map-dark.jpg">
  <img alt="A finished map in the panel, with its checks" src="docs/images/panel-map.jpg">
</picture>

The maps are real procedural Rust maps, made by the official Rust dedicated server with a small
mod that steers its generator. They load on any server like a map from a map editor.

**In real use.** RustMapGen is the generator behind a network of public Rust servers, taken out of
it to run on its own. Every wipe there, about twenty a week, has run on one of its maps: made ahead,
checked and tested in the game as described below, put to the players' vote, and served.

## What you need

- A 64-bit PC with **Linux** (x86-64) or **Windows 10/11**.
- **8 GB of memory** or more (a map uses about 5 GB while it is made).
- **About 7 GB of disk**: 6 GB for the Rust dedicated server, and 15 to 90 MB per map.
- **Python 3.9 or newer.**
  - Linux (Debian, Ubuntu): `sudo apt install python3 python3-venv lib32gcc-s1`
  - Windows: the installer uses the Python it finds; without one it tries to install it with
    winget. If that fails, install Python from [python.org](https://www.python.org/downloads/)
    with **Add python.exe to PATH** ticked.

Nothing of the game comes with this code: the installer downloads steamcmd and the free Rust
dedicated server from Steam.

## Install

**Linux**

```sh
git clone https://github.com/S1gil0/rustmapgen.git
cd rustmapgen
./install.sh
```

**Windows:** on GitHub, **Code → Download ZIP**, extract it, and double-click `install.cmd`.

The first install fetches the game (about 6 GB) and takes a few minutes. Each step is shown as it
starts, and the download as it goes (on Windows in steamcmd's own lines). Run the installer again
at any time to bring the game up to date.

## Start the panel

- Linux: `./rustmapgen.sh`
- Windows: double-click `rustmapgen.cmd`

The panel opens in your browser at http://127.0.0.1:5350. Keep its window open while you use it.

## Make your first map

1. Click **New Map**. It opens on the **Game default** preset: the map the game makes by itself.
2. Pick a preset under **Settings**, or change anything in the sections on the left.
3. Choose the **Size**, a **Seed** (empty for a random one) and how many **Maps**, then click
   **Make maps**.
4. Open **Maps** to watch it being made. A small map takes from 30 seconds to two minutes, a large
   one from two to six, depending on the computer.
5. The finished map shows **At a glance** (green, amber, red) and every check. **Test in the game**
   has the game load the finished file the way a server would.
6. **Download .map** gives you the file.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/panel-settings-dark.jpg">
  <img alt="New Map with the Large preset: monuments wanted (green), left to the game (grey) or never placed (red)" src="docs/images/panel-settings.jpg">
</picture>

Settings you change can be kept with **Save as…** (in `data/recipes`); the presets themselves
never change. **Remove** takes saved settings, or a preset you do not use, off the list; **Game
default** always stays.

### Presets

| Preset | Size | What it is |
|---|---|---|
| Game default | 4250 | Nothing changed: the map the game makes by itself. From 4250 the game places every monument and both rings (rail and road) by itself. |
| Small, no safe zones | 2000 | A fighting map with 40 % land: no Outpost, Bandit Camp, fishing villages or ranches, few large monuments, no caves, building on roads allowed, a rail ring. |
| Medium | 3000 | 21 monuments wanted, the Outpost near the middle, 61 % land, a rail ring; no Bandit Camp, ranches, Radtown, jungle ruins, caves, swamps or wells. |
| Large | 4000 | The medium map with Radtown and the jungle ruins back, no canyons, and two or three underwater labs. |

The three samples also leave out icebergs, mountains, car wrecks and power poles along roads. Any
preset can be made at another size, and each one's description in the panel says which sections
it changes.

Maps made with the four presets, as the panel's **Picture** button gives them (click one to see it
whole):

<table>
<tr>
<td width="50%"><a href="docs/images/map-small.jpg"><img alt="A map of the Small preset" src="docs/images/map-small.jpg"></a><br><b>Small, no safe zones</b> · 2000</td>
<td width="50%"><a href="docs/images/map-medium.jpg"><img alt="A map of the Medium preset" src="docs/images/map-medium.jpg"></a><br><b>Medium</b> · 3000</td>
</tr>
<tr>
<td width="50%"><a href="docs/images/map-large.jpg"><img alt="A map of the Large preset" src="docs/images/map-large.jpg"></a><br><b>Large</b> · 4000</td>
<td width="50%"><a href="docs/images/map-default.jpg"><img alt="A map of the Game default preset" src="docs/images/map-default.jpg"></a><br><b>Game default</b> · 4250</td>
</tr>
</table>

### Your own prefabs around a monument

Make a small map in a map editor (such as RustEdit) with one of the game's monuments and your
prefabs around it: recyclers, vendors, decorations. Add it under **New Map → Custom prefabs →
Add template**, tick it in the settings, and every copy of that monument on every new map gets
your prefabs, exactly where they stand in your file.

- A template holds exactly one monument; one template per monument can be used at a time.
- Prefabs the game does not know (editor-only objects) are left out, and counted on the map's page.

## Every map is tested

A map is checked as soon as it is made, on the file a server would load, in three layers:

- **Can it be played:** what any map must show. The rail ring is closed with nothing cut off from it,
  the rails are not too steep and stay above the water, sea and land are tagged as the heights say,
  monuments and power line towers stand on the ground, and every tunnel entrance leads into the
  tunnels.
- **Is it what was asked:** your settings, checked against the result. The monuments you wanted are
  there and the ones you blocked are not, the land is the share you asked for, the rail ring and the
  Outpost are as you set them, and your templates were merged whole.
- **Does the game load it:** **Test in the game** has the dedicated server load the finished file the
  way your server will, and reads its log: what it loaded, and anything that went wrong.

**At a glance** on each map's page sums it up in green, amber or red, and **passing only** over the
list of maps hides the ones that failed a check. A failed check deletes nothing: see what failed,
keep the map or make another seed.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/checks-dark.png">
  <img alt="The checks of a Large map that the game has loaded: all 12 pass" src="docs/images/checks.png">
</picture>

## Put a map on your server

A server, and every player who joins it, downloads a custom map from an address:

```
+server.levelurl "https://your.host/path/your-map.map"
```

The address must be **HTTPS** (the game refuses plain HTTP) and serve the file as it is. With a
`levelurl` the server ignores its seed and size settings. Give every map an address of its own and
never put other bytes under it: the game keeps a downloaded map by its name. The map names the panel
gives are never repeated, so `<name>.map` makes a good address.

### The simplest: mapstr.gg

[mapstr.gg](https://mapstr.gg/) hosts Rust maps for free, with no account: get the file with
**Download .map**, upload it on their page (up to 100 MB a map), and put the direct link they give
you in `+server.levelurl`. Make sure the link starts with `https://`. Signing in there with Discord
lets you see and delete your uploads.

### Many maps, by script: Cloudflare R2

[Cloudflare R2](https://developers.cloudflare.com/r2/) is a proven home for maps when you make many or
want them uploaded by a script: free at this scale (10 GB of storage, a million uploads and ten
million downloads a month, and nothing charged for data sent out:
[R2 pricing](https://developers.cloudflare.com/r2/pricing/)), served from Cloudflare's network near
your players, and filled from the command line.

1. In the Cloudflare dashboard, make an R2 bucket and connect a domain of yours to it (the bucket's
   **Settings → Custom Domains**). Its `r2.dev` address is fine for a test but rate-limited: use your
   own domain for a server.
2. Make an R2 API token with **Object Read & Write** on that bucket and give it to
   [rclone](https://developers.cloudflare.com/r2/examples/rclone/) (`rclone config`, provider
   Cloudflare).
3. Upload a map from `data/maps/<name>/` and point the server at it:

```sh
rclone copyto data/maps/<name>/final.map r2:<bucket>/<name>.map
```
```
+server.levelurl "https://maps.example.com/<name>.map"
```

Every finished map in one go (a map already in the bucket is left as it is):

```sh
for map in data/maps/*/final.map; do
  rclone copyto --ignore-existing "$map" "r2:<bucket>/$(basename "$(dirname "$map")").map"
done
```

Cloudflare caches only some kinds of files by itself: a cache rule that caches everything on that
domain has players served from its cache, with the bucket read about once per Cloudflare location.

## Updates

**Updates**, at the top right of the panel, keeps both parts up to date:

- **RustMapGen:** **Check for updates**, then **Update to …**. Your maps, settings and the game stay
  as they are; a copy cloned with git is updated with git, a copy from a ZIP gets the new files, and
  the panel starts again by itself.
- **The game:** **Check** asks Steam for its newest build, and **Update the game** brings it there.
  Maps wait in the queue while it updates.

Rust updates on the first Thursday of the month. The mod that comes with RustMapGen usually keeps
working; if maps stop being made after an update ("the game did not load the mod"), check for a
RustMapGen update. From the command line, `./rustmapgen.sh update` updates the game, and with the
[.NET SDK 8](https://dotnet.microsoft.com/download) or newer `./rustmapgen.sh build-mod` builds the
mod against the game you have.

## From the command line

Everything the panel does (`rustmapgen.cmd` on Windows):

```sh
./rustmapgen.sh generate --recipe large --count 3          # a preset, a saved setting or a .json file
./rustmapgen.sh generate --recipe small --seed 12345 --size 2500
./rustmapgen.sh load-test <map name>
./rustmapgen.sh list
./rustmapgen.sh doctor                                     # what this computer has
```

## Use the panel from another computer

Start it with `./rustmapgen.sh panel --host 0.0.0.0` and open the `http://` address it prints
(not `https://`). Anyone who can reach the computer can then make and delete maps, and update
RustMapGen and the game (only ever from their official sources): do this on a network you trust. The panel answers this computer's addresses and names only; to reach it by
another name, add it to `panel_hosts` in `data/settings.json`.

## Settings

Optional, in `data/settings.json`:

| Setting | Default | |
|---|---|---|
| `panel_port` | 5350 | the panel's port |
| `panel_hosts` | `[]` | more names the panel answers to |
| `min_free_gb` | 5.5 | free memory needed to start a map |
| `max_temp_c` | 85 | no map starts while the CPU is this hot (Linux; it waits for it to cool) |
| `timeout_minutes` | 45 | a map that takes longer is stopped |
| `isolate_network` | true | Linux: run the game with no network at all, where the system allows it |
| `game_port` | 28715 | ports the generator's game uses on 127.0.0.1 |

`RUSTMAPGEN_GAME` and `RUSTMAPGEN_DATA` move the game and the data to other folders (the game to an
empty folder of its own).

## Troubleshooting

| You see | Do this |
|---|---|
| "Python 3.9 or newer is needed" | Install Python (see [What you need](#what-you-need)), then run the installer again. |
| "steamcmd needs the 32-bit C library" | `sudo apt install lib32gcc-s1`, then run the installer again. |
| The panel does not open from another computer | Start it with `--host 0.0.0.0` and use `http://`, not `https://`. |
| "the game did not load the mod" | After a Rust update: `./rustmapgen.sh build-mod` (needs the .NET SDK). |
| "GB of memory free, … needed" | Close other programs; a map needs about 5 GB. |
| "the last update of the game did not finish" | **Updates → Update the game** again, or `./rustmapgen.sh update`. |
| A map fails a check | It is still a map: look at what failed, keep it or make another seed. |
| On Windows the window stops moving and its title starts with "Select" | A click in it started a selection, which pauses it: press Esc. |

## Good to know

- **Never put the mod on a server people play on**: a server that loads it quits instead of
  opening. RustMapGen keeps the game in a folder of its own (`game/`) and refuses to work in a
  folder that holds another server.
- The game is only run to make and test maps: it is bound to 127.0.0.1, quits before it would
  open to players, and never uploads a map. On Linux it also runs with no network at all.
- Maps are made on the public game build. `update --branch staging` follows the next one; an
  update without `--branch` goes back to public.
- The same seed gives the same land on every computer, but monuments can land elsewhere.
- Some monuments need their biome (the Excavator the desert, the Ziggurat the jungle): with that
  biome at 0 % the game finds no room for them. The panel marks them.
- "Island size" moves the shore by 20 m at most, so 70 % land is the most a setting may ask.
- The map's picture has 600 m of sea around the square: the game puts the oil rigs out there.
- Windows reports no CPU temperature, so the temperature limit works on Linux only.

## How it works

The settings become the game's own world configuration and a job for the mod. The runner starts
the dedicated server with the mod; the mod shapes the land, places the monuments as asked and quits
once the map is saved. The map is then moved to `data/maps/<name>/`, your templates are merged in,
and it is described, checked and drawn.

## License

MIT: see [LICENSE](LICENSE). It covers this code only; the Rust dedicated server the installer
fetches is Facepunch's, under its own terms.
