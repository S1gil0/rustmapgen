# Changes

What is new in each release of RustMapGen, newest first. **Updates → What is new** in the panel
opens this page.

## 0.2.0 · 2026-10-22

- **The forced wipe's update** (the first Thursday of the month) changes how maps are made. From
  that day on, a game last updated before it is brought to that update before the next batch of
  maps, once Steam has it. Until then, and when Steam cannot be asked, the maps are made on the game
  you have. An update later in the month is still yours to make. On the command line, `generate`
  does the same first, and `update --forced-wipe` does it alone.
- **Maps for the next update,** ready for the forced wipes to come: once the authors have checked by
  hand that a Steam branch makes the maps of the coming update, an update of RustMapGen declares it.
  New Map then offers **Game: Next update** until the update is out, and **What changes** says, once
  and in plain words, what changes in the maps. These maps are made on a second copy of the game
  (`game-next/`, about 6 GB), brought to Steam's newest build of that branch before each batch and
  each load test, and carry a **Next update** badge with their build. The release is read on Steam:
  on the Thursday each such map asks first, and the choice closes once the public branch has moved.
  After it, their **Load test** runs on the public game, brought to the release first. **Updates →
  Next update → Remove** deletes that game afterwards. On the command line: `--game next` for
  `update`, `generate` and `load-test`, and `remove-next`. Until such an update, nothing of it shows.
- **Settings that name a template this computer does not have** (for example kept by your browser
  from an earlier install) no longer stop every map: New Map takes the template out and says so;
  add it again under Custom prefabs to use it.
- **The installer** ends with what it set up (the game's build, the mod, where your maps go) in a few
  lines instead of its whole record, and builds the mod only when the one that comes with RustMapGen
  is for another build of the game. A .NET SDK older than 8 is said in one line instead of a compiler
  error, and the mod that came with the code is used.
- **On the command line**, `update --branch` is gone: `update` brings the game up to the newest
  public build (`--if-newer` asks Steam first), and `update --game next` the next update's game.
- **3D view** (a map's page → **3D ↗**): the whole map in 3D in a tab of its own, with its monuments,
  cliffs, rocks, pylons, substations and tunnel entrances, trees, roads and rails, and the zones where
  the game will not let a base be built. The monuments and rocks are made by themselves, on your
  computer from your copy of the game, once for each build of the game: after the install, after
  every game update and when the panel starts without them (a few minutes, about 2.5 GB of memory).
  Until then the view shows the ground; if it fails, **Prepare 3D** tries again. Also `models-3d`
  from the command line.
- **Load test** is the button that was **Test in the game**: the dedicated server on your computer
  loads the map and stops (it was read as a way to join the game). **3D ↗** sits right of it, in the
  accent colour.
- **A map the game itself cannot make** is stopped within about a minute, with the game's error
  named ("the game's own map generation stopped with an error"), instead of after the 45-minute
  limit.
- **When the mod does not load** on a build of the game, the panel says so and no more maps are
  tried on that build, until a newer RustMapGen brings a mod that does (**Updates → Check for
  updates**).
- **The panel's address follows what is shown:** the bare address opens New Map (it used to turn
  into the newest map's address, so a reload opened that map), a map has its own, and Maps comes
  back to the map you were on.
- **Updates:** an update that stops half way is finished before anything else runs. Until then no
  map, load test or game update starts, in the panel or from the command line. Finish it under
  **Updates**, or with `finish-update`, which works even when the panel cannot start. Once an update
  is in, the panel takes no new work until it has started again, and one update runs at a time.
- **Templates** are read within limits of their own, so a damaged file cannot take up the
  computer's memory.
- **Linux:** whatever the game leaves running when a map ends is stopped with it. When network
  isolation is asked for and the system does not allow it, the run says so.
- The README says what network isolation and the mod block, that the panel has no password, and
  how to reach it from another computer (an SSH tunnel).

## 0.1.0 · 2026-10-18

The first release.

- A panel in your browser: settings and presets, a queue of maps, every map checked and drawn, the
  game's own test of a finished map, your prefabs merged around any monument.
- Installers for Linux and Windows that show each step and the game's download as it goes.
- **Updates** in the panel: check for a newer RustMapGen and for a newer game build on Steam, and
  update either with a click.
