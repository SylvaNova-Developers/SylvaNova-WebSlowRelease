# Setup Guide for Slow Release Client

## Required steps

- For the **Slow Release Web Manager**, Universal Tracker is already vendored under `worlds/tracker/` (no separate install).
- For the classic Launcher client, install [Universal Tracker](https://github.com/FarisTheAncient/Archipelago/releases) and/or [Slow Release Client](https://github.com/gjgfuj/AP-SlowRelease/releases) apworlds into `Archipelago/custom_worlds` if they are not already present in this tree.
- To slow release a particular slot, you need that slot's yaml file available (Players folder for the desktop client, or paste/upload YAML in the web UI).

## Using the Slow Release Client

Open Archipelago Launcher, and open `Slow Release Client`. Two commands are relevant here, `/time` and `/region_mode`.

- The `/time` command takes a value, in seconds. This value will be the delay between released checks.
- The `/region_mode` command makes the slow release client act more like a player by handling one region of the world at a time. It's a toggle, so the value changes everytime you run the command.

Those commands and what they do are also listed at the bottom of the command list displayed every time you launch the Slow Release Client.

From there you just need to connect to the slot, and it will start slow releasing.
