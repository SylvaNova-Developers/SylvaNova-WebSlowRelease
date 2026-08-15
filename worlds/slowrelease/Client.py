from CommonClient import logger, server_loop, gui_enabled, get_base_parser
from worlds.AutoWorld import World
from BaseClasses import Region
from NetUtils import ClientStatus
import asyncio
import json
import os
import random
import time
import typing

tracker_loaded = True
from worlds.tracker import DeferredEntranceMode
from worlds.tracker.TrackerClient import TrackerGameContext, TrackerCommandProcessor


ProgressCallback = typing.Callable[[dict], typing.Awaitable[None] | None]

# #region agent log
_DEBUG_LOG_PATH = "/opt/cursor/logs/debug.log"


def _agent_log(hypothesis_id: str, location: str, message: str, data: dict | None = None) -> None:
    try:
        payload = {
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
            "pid": os.getpid(),
        }
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass
# #endregion


class SlowReleaseCommandProcessor(TrackerCommandProcessor):
    def _cmd_time(self, time_min=None, time_max=None):
        """If no arguments are provided, show the time per check. Else, set the time per check. Value in seconds. If two numbers are provided, then set a range to be randomly decided per check."""
        self.ctx.set_time(time_min, time_max)

    def _cmd_region_mode(self):
        """Toggle Region mode (i.e. make the slow release client act more like a player by handling one region of the world at a time.)"""
        self.ctx.region_mode = not self.ctx.region_mode
        logger.info(f"Set region mode to {self.ctx.region_mode}")

    def _cmd_auto_goal(self):
        """Toggle auto-goal when Universal Tracker reports go mode (completion reachable)."""
        self.ctx.auto_goal_on_go_mode = not self.ctx.auto_goal_on_go_mode
        logger.info(f"Set auto-goal on go mode to {self.ctx.auto_goal_on_go_mode}")


class SlowReleaseContext(TrackerGameContext):
    time_per_min = 10
    time_per_max = 10
    tags = ["SlowRelease", "Tracker"]
    game = ""
    has_game = False
    region_mode = True
    auto_goal_on_go_mode = False
    command_processor = SlowReleaseCommandProcessor
    autoplayer_task = None
    progress_callback: ProgressCallback | None = None
    _completed: bool = False
    _stop_requested: bool = False
    _in_bk: bool = False
    _current_location_name: str = ""
    _current_region_name: str = ""
    _logic_wakeup: asyncio.Event | None = None
    # #region agent log
    _dbg_last_bk_tick_ms: int = 0
    _dbg_last_emit_key: str = ""
    # #endregion

    def autoplayer_log(self, message):
        logger.info(message)
        self._emit_progress({"log": message})

    def _ensure_logic_wakeup(self) -> asyncio.Event:
        if self._logic_wakeup is None:
            self._logic_wakeup = asyncio.Event()
        return self._logic_wakeup

    def _wake_logic(self) -> None:
        # #region agent log
        ev = self._logic_wakeup
        _agent_log(
            "H3",
            "Client.py:_wake_logic",
            "wake_logic",
            {
                "has_event": ev is not None,
                "was_set": bool(ev is not None and ev.is_set()),
                "in_bk": self._in_bk,
                "auth": getattr(self, "auth", None),
            },
        )
        # #endregion
        if self._logic_wakeup is not None:
            self._logic_wakeup.set()

    def set_time(self, time_min=None, time_max=None):
        if time_min:
            self.time_per_min = float(time_min)
            if time_max and float(time_min) < float(time_max):
                self.time_per_max = float(time_max)
            else:
                self.time_per_max = float(time_min)
            logger.info(f"Set time per check to {self.time_per_min}-{self.time_per_max}s")
        else:
            logger.info(f"Time per check is {self.time_per_min}-{self.time_per_max}s")

    def request_stop(self):
        self._stop_requested = True
        self.exit_event.set()

    def _emit_progress(self, extra: dict | None = None):
        if not self.progress_callback:
            return
        checked = len(self.checked_locations)
        missing = len(self.missing_locations)
        total = checked + missing
        available = 0
        if getattr(self, "tracker_core", None) is not None:
            try:
                available = len(self.tracker_core.locations_available)
            except Exception:
                available = 0
        payload = {
            "status": self._status_label(),
            "checked_count": checked,
            "total_count": total,
            "available_count": available,
            "current_location": self._current_location_name,
            "current_region": self._current_region_name,
            "connected": bool(self.server and self.server.socket and not self.server.socket.closed),
            "completed": self._completed,
        }
        if extra:
            payload.update(extra)
        # #region agent log
        status = payload.get("status")
        emit_key = f"{status}:{available}:{checked}:{self._in_bk}"
        if emit_key != self._dbg_last_emit_key and (
            status in ("bk", "running") or (extra and "status" in extra)
        ):
            self._dbg_last_emit_key = emit_key
            _agent_log(
                "H6",
                "Client.py:_emit_progress",
                "emit_progress",
                {
                    "auth": getattr(self, "auth", None),
                    "status": status,
                    "available_count": available,
                    "checked_count": checked,
                    "in_bk": self._in_bk,
                    "extra_status": (extra or {}).get("status"),
                    "items_received_len": len(getattr(self, "items_received", []) or []),
                },
            )
        # #endregion
        result = self.progress_callback(payload)
        if asyncio.iscoroutine(result):
            asyncio.create_task(result)

    def _status_label(self) -> str:
        if self._completed:
            return "completed"
        if self._stop_requested:
            return "stopped"
        if not (self.server and self.server.socket and not self.server.socket.closed):
            return "connecting"
        if self._in_bk:
            return "bk"
        return "running"

    async def _mark_completed(self, reason: str | None = None):
        if self._completed:
            return
        self._completed = True
        self.finished_game = True
        self._current_location_name = ""
        self.autoplayer_log(reason or "Slow release complete: all locations checked.")
        try:
            await self.send_msgs([{"cmd": "StatusUpdate", "status": ClientStatus.CLIENT_GOAL}])
        except Exception:
            logger.exception("Failed to send CLIENT_GOAL status update")
        self._emit_progress({"status": "completed", "completed": True})
        self.exit_event.set()

    def _is_in_go_mode(self, tracker_state=None) -> bool:
        """True when Universal Tracker says completion is reachable (logical go mode)."""
        if not getattr(self, "tracker_core", None):
            return False
        if not self.tracker_core.multiworld or not self.tracker_core.player_id:
            return False
        if tracker_state is None:
            try:
                tracker_state = self.updateTracker()
            except Exception:
                logger.exception("Failed to refresh Universal Tracker for go-mode check")
                return False
        if tracker_state is None or tracker_state.state is None:
            return False
        return bool(
            self.tracker_core.multiworld.has_beaten_game(
                tracker_state.state,
                self.tracker_core.player_id,
            )
        )

    async def autoplayer(self):
        print("Autoplayer")
        self._in_bk = False
        waited = 0
        while not self.tracker_core.player_id:
            if self._stop_requested:
                return
            waited += 1
            if waited > 30:
                msg = (
                    "Universal Tracker did not become ready within 30s. "
                    "Is the game world installed and generating correctly?"
                )
                self.autoplayer_log(msg)
                self._emit_progress({"status": "error", "error": msg})
                return
            await asyncio.sleep(1)
        world: World = self.tracker_core.multiworld.worlds[self.tracker_core.player_id]
        current_region: Region = self.tracker_core.multiworld.get_region(
            world.origin_region_name, self.tracker_core.player_id
        )
        self._current_region_name = current_region.name
        self._emit_progress({"status": "running"})
        wakeup = self._ensure_logic_wakeup()
        while not self._stop_requested and not self._completed:
            if self.missing_locations is not None and len(self.missing_locations) == 0 and (
                self.checked_locations or self.server_locations
            ):
                await self._mark_completed()
                return
            # ReceivedItems (e.g. progression from other slots) does not trigger
            # TrackerGameContext.on_package refresh. Recompute in-logic locations
            # each tick so BK can clear when items arrive.
            try:
                tracker_state = self.updateTracker()
            except Exception as exc:
                # #region agent log
                _agent_log(
                    "H5",
                    "Client.py:autoplayer",
                    "updateTracker_tick_exception",
                    {
                        "auth": getattr(self, "auth", None),
                        "error": str(exc),
                        "disconnected_intentionally": bool(
                            getattr(self, "disconnected_intentionally", False)
                        ),
                    },
                )
                # #endregion
                logger.exception("Universal Tracker refresh failed")
                await asyncio.sleep(1)
                continue
            # #region agent log
            if self._in_bk:
                now_ms = int(time.time() * 1000)
                avail_now = len(self.tracker_core.locations_available)
                if avail_now > 0 or now_ms - self._dbg_last_bk_tick_ms >= 5000:
                    self._dbg_last_bk_tick_ms = now_ms
                    _agent_log(
                        "H2",
                        "Client.py:autoplayer",
                        "tick_while_bk",
                        {
                            "auth": getattr(self, "auth", None),
                            "available": avail_now,
                            "items_received_len": len(getattr(self, "items_received", []) or []),
                            "state_none": tracker_state is None or tracker_state.state is None,
                            "disconnected_intentionally": bool(
                                getattr(self, "disconnected_intentionally", False)
                            ),
                            "items_handling": getattr(self, "items_handling", None),
                            "tags": list(getattr(self, "tags", []) or []),
                        },
                    )
            # #endregion
            if self.auto_goal_on_go_mode and self._is_in_go_mode(tracker_state):
                await self._mark_completed("Go mode detected; sending goal.")
                return
            if len(self.tracker_core.locations_available) > 0:
                if self._in_bk:
                    # #region agent log
                    _agent_log(
                        "H2",
                        "Client.py:autoplayer",
                        "leaving_bk",
                        {
                            "auth": getattr(self, "auth", None),
                            "available": len(self.tracker_core.locations_available),
                            "items_received_len": len(getattr(self, "items_received", []) or []),
                        },
                    )
                    # #endregion
                    self.autoplayer_log(
                        f"Out of BK ({len(self.tracker_core.locations_available)} in logic)."
                    )
                    self._in_bk = False
                    self._emit_progress({"status": "running"})
                goal_location = None
                visited_regions = []
                regions = [
                    *map(
                        lambda e: e.connected_region,
                        self.tracker_core.multiworld.get_region(
                            world.origin_region_name, self.tracker_core.player_id
                        ).get_exits(),
                    )
                ]
                if self.region_mode:
                    while not goal_location and not self._stop_requested:
                        randolocs = self.tracker_core.locations_available.copy()
                        random.shuffle(randolocs)
                        for location in randolocs:
                            location = world.get_location(world.location_id_to_name[location])
                            if location.parent_region == current_region:
                                goal_location = location.address
                                self._current_location_name = self.location_names.lookup_in_game(goal_location)
                                self._current_region_name = current_region.name
                                self.autoplayer_log(f"Going for {self._current_location_name}")
                                break
                        if not goal_location:
                            if not regions:
                                break
                            current_region = random.choice(regions)
                            if current_region not in visited_regions:
                                regions += [
                                    *filter(
                                        lambda e: e not in regions and e not in visited_regions,
                                        map(lambda e: e.connected_region, current_region.get_exits()),
                                    )
                                ]
                            visited_regions.append(current_region)
                            regions.remove(current_region)
                            self._current_region_name = current_region.name
                            self.autoplayer_log(f"Attempting to go to: {current_region.name}")
                            await asyncio.sleep(0.1)
                else:
                    goal_location = random.choice(self.tracker_core.locations_available)
                    self._current_location_name = self.location_names.lookup_in_game(goal_location)
                    self.autoplayer_log(f"Going for {self._current_location_name}")
                if goal_location is None:
                    await asyncio.sleep(1)
                    continue
                await asyncio.sleep(random.uniform(self.time_per_min, self.time_per_max))
                if self._stop_requested:
                    return
                await self.check_locations([goal_location])
                self._emit_progress()
                await asyncio.sleep(0.1)
            else:
                if not self._in_bk:
                    # #region agent log
                    _agent_log(
                        "H2",
                        "Client.py:autoplayer",
                        "entering_bk",
                        {
                            "auth": getattr(self, "auth", None),
                            "available": len(self.tracker_core.locations_available),
                            "items_received_len": len(getattr(self, "items_received", []) or []),
                            "missing": len(self.missing_locations or []),
                            "items_handling": getattr(self, "items_handling", None),
                        },
                    )
                    # #endregion
                    self.autoplayer_log("In BK.")
                    self._in_bk = True
                    self._emit_progress({"status": "bk"})
                # Sleep until items/room updates wake us, or poll again after 1s.
                # #region agent log
                was_set_before_clear = wakeup.is_set()
                if was_set_before_clear:
                    _agent_log(
                        "H3",
                        "Client.py:autoplayer",
                        "bk_wait_clear_lost_wakeup",
                        {
                            "auth": getattr(self, "auth", None),
                            "was_set_before_clear": True,
                            "available": len(self.tracker_core.locations_available),
                        },
                    )
                # #endregion
                wakeup.clear()
                try:
                    await asyncio.wait_for(wakeup.wait(), timeout=1.0)
                    # #region agent log
                    _agent_log(
                        "H3",
                        "Client.py:autoplayer",
                        "bk_wait_woke",
                        {
                            "auth": getattr(self, "auth", None),
                            "available": len(self.tracker_core.locations_available),
                            "items_received_len": len(getattr(self, "items_received", []) or []),
                        },
                    )
                    # #endregion
                except asyncio.TimeoutError:
                    pass

    def make_gui(self):
        ui = super().make_gui()
        ui.base_title = "Slow Release Client"
        return ui

    def on_package(self, cmd, args):
        super().on_package(cmd, args)
        if cmd == "ReceivedItems":
            # Parent TrackerGameContext does not refresh on ReceivedItems.
            # Items from other slots are how slots usually leave BK.
            # #region agent log
            items = (args or {}).get("items") or []
            avail_before = 0
            try:
                avail_before = len(self.tracker_core.locations_available)
            except Exception:
                avail_before = -1
            item_ids = []
            try:
                item_ids = [int(it[0]) if not hasattr(it, "item") else int(it.item) for it in items[:8]]
            except Exception:
                item_ids = []
            _agent_log(
                "H4",
                "Client.py:on_package",
                "ReceivedItems_before_update",
                {
                    "auth": getattr(self, "auth", None),
                    "game": getattr(self, "game", None),
                    "pkg_index": (args or {}).get("index"),
                    "pkg_item_count": len(items),
                    "items_received_len": len(getattr(self, "items_received", []) or []),
                    "avail_before": avail_before,
                    "items_handling": getattr(self, "items_handling", None),
                    "tags": list(getattr(self, "tags", []) or []),
                    "disconnected_intentionally": bool(
                        getattr(self, "disconnected_intentionally", False)
                    ),
                    "in_bk": self._in_bk,
                    "item_ids_sample": item_ids,
                },
            )
            # #endregion
            update_err = None
            try:
                self.updateTracker()
            except Exception as exc:
                update_err = str(exc)
                logger.exception("Universal Tracker refresh failed after ReceivedItems")
            # #region agent log
            avail_after = 0
            try:
                avail_after = len(self.tracker_core.locations_available)
            except Exception:
                avail_after = -1
            dice_frag_count = None
            try:
                names = []
                id_to_name = getattr(
                    self.tracker_core.multiworld.worlds.get(self.tracker_core.player_id),
                    "item_id_to_name",
                    None,
                ) if self.tracker_core.multiworld and self.tracker_core.player_id else None
                if id_to_name:
                    for ni in getattr(self, "items_received", []) or []:
                        names.append(id_to_name.get(ni.item, str(ni.item)))
                dice_frag_count = names.count("Dice Fragment")
                dice_count = names.count("Dice")
            except Exception:
                dice_frag_count = None
                dice_count = None
            _agent_log(
                "H2",
                "Client.py:on_package",
                "ReceivedItems_after_update",
                {
                    "auth": getattr(self, "auth", None),
                    "avail_before": avail_before,
                    "avail_after": avail_after,
                    "avail_changed": avail_after != avail_before,
                    "update_err": update_err,
                    "items_received_len": len(getattr(self, "items_received", []) or []),
                    "disconnected_intentionally": bool(
                        getattr(self, "disconnected_intentionally", False)
                    ),
                    "dice_fragment_count": dice_frag_count,
                    "dice_count": dice_count,
                    "in_bk": self._in_bk,
                    "wakeup_set": bool(
                        self._logic_wakeup is not None and self._logic_wakeup.is_set()
                    ),
                },
            )
            # #endregion
            self._wake_logic()
        elif cmd == "Connected":
            if "Tracker" in self.tags:
                self.tags.remove("Tracker")
                asyncio.create_task(self.send_msgs([{"cmd": "ConnectUpdate", "tags": self.tags}]))
                # #region agent log
                _agent_log(
                    "H4",
                    "Client.py:on_package",
                    "Connected_removed_Tracker_tag",
                    {
                        "auth": getattr(self, "auth", None),
                        "tags_after": list(self.tags),
                        "items_handling": getattr(self, "items_handling", None),
                    },
                )
                # #endregion
            if self.autoplayer_task:
                self.autoplayer_task.cancel()
            # UT init happens in TrackerGameContext.on_package. Without a local
            # world + successful generation, player_id never gets set and the
            # old wait-loop looked "running" forever without releasing checks.
            if not self.tracker_core.player_id or not self.tracker_core.multiworld:
                game = getattr(self, "game", None) or "unknown"
                msg = (
                    f"Universal Tracker failed to initialize for '{game}'. "
                    "Install that game's apworld locally so Slow Release can "
                    "compute in-logic checks."
                )
                self.autoplayer_log(msg)
                self._emit_progress({"status": "error", "error": msg})
                return
            self._in_bk = False
            self._ensure_logic_wakeup()
            self._emit_progress({"status": "running"})
            self.autoplayer_task = asyncio.create_task(self.autoplayer())
            self.autoplayer_task.add_done_callback(self.autoplayer_done)
        elif cmd == "RoomUpdate":
            self._wake_logic()
            self._emit_progress()
            if self.missing_locations is not None and len(self.missing_locations) == 0 and (
                self.checked_locations or self.server_locations
            ):
                asyncio.create_task(self._mark_completed())

    def autoplayer_done(self, autoplayer_task):
        try:
            _ = autoplayer_task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("Autoplayer Error", exc_info=True)
            self._emit_progress({"status": "error", "error": "Autoplayer crashed"})

    def disconnect(self, *args):
        if self.autoplayer_task:
            self.autoplayer_task.cancel()
        if "Tracker" not in self.tags:
            self.tags.append("Tracker")
        return super().disconnect(*args)


async def run_headless(
    connect: str,
    name: str,
    password: str | None = None,
    time_min: float = 10.0,
    time_max: float | None = None,
    region_mode: bool = True,
    auto_goal_on_go_mode: bool = False,
    progress_callback: ProgressCallback | None = None,
    stop_event: asyncio.Event | None = None,
    players_dir: str | None = None,
    on_ready: typing.Callable[[SlowReleaseContext], None] | None = None,
) -> SlowReleaseContext:
    """Run Slow Release without GUI/CLI until completion, stop, or fatal error."""
    import settings

    settings.no_gui = True
    if players_dir:
        players_path = settings.GeneratorOptions.PlayerFilesPath(players_dir)
        settings.get_settings().generator.player_files_path = players_path
        try:
            from worlds.tracker import TrackerWorld

            TrackerWorld.settings["player_files_path"] = (
                TrackerWorld.settings.__class__.TrackerPlayersPath(players_dir)
            )
        except Exception:
            logger.exception("Failed to set Universal Tracker player_files_path")

    ctx = SlowReleaseContext(connect, password)
    ctx.auth = name
    ctx.region_mode = region_mode
    ctx.auto_goal_on_go_mode = auto_goal_on_go_mode
    ctx.progress_callback = progress_callback
    ctx.set_time(time_min, time_max)
    ctx._emit_progress({"status": "connecting"})
    if on_ready is not None:
        on_ready(ctx)

    if tracker_loaded:
        ctx.tracker_core.enforce_deferred_connections = DeferredEntranceMode.disabled
        if players_dir:
            ctx.tracker_core.player_folder_override = players_dir
        # Use super_override for the Players path; do not pass override_yaml_path
        # (that branch is for reconnect regen and requires self.game).
        ctx.tracker_core.run_generator(None, None, players_dir)
        ctx.use_split = getattr(ctx.tracker_core, "use_split", True)

    ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")

    async def watch_stop():
        if stop_event is None:
            return
        await stop_event.wait()
        ctx.request_stop()
        await ctx.disconnect()

    stop_task = asyncio.create_task(watch_stop(), name="stop watcher")
    try:
        await ctx.exit_event.wait()
    finally:
        stop_task.cancel()
        if ctx.autoplayer_task:
            ctx.autoplayer_task.cancel()
        await ctx.shutdown()
    return ctx


def launch(*args):
    async def main(args):
        ctx = SlowReleaseContext(args.connect, args.password)
        ctx.auth = args.name
        ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")
        ctx.set_time(args.time, args.time_max)
        ctx.auto_goal_on_go_mode = bool(getattr(args, "auto_goal_on_go_mode", False))

        if tracker_loaded:
            ctx.tracker_core.enforce_deferred_connections = DeferredEntranceMode.disabled
            ctx.run_generator()
        if gui_enabled:
            ctx.run_gui()
        ctx.run_cli()

        await ctx.exit_event.wait()
        await ctx.shutdown()

    import colorama

    parser = get_base_parser(description="Slow Release Archipelago Client, for text interfacing.")
    parser.add_argument("--name", default=None, help="Slot Name to connect as.")
    parser.add_argument(
        "--time",
        type=float,
        default=10.0,
        help="Minimum time per check in seconds. If maximum is not specified, defaults to this.",
    )
    parser.add_argument("--time_max", type=float, default=None, help="Maximum time per check.")
    parser.add_argument(
        "--auto-goal-on-go-mode",
        action="store_true",
        help="Send CLIENT_GOAL when Universal Tracker reports logical go mode.",
    )
    parser.add_argument("url", nargs="?", help="Archipelago connection url")
    args = parser.parse_args(args)

    # handle if text client is launched using the "archipelago://name:pass@host:port" url from webhost
    if args.url:
        import urllib

        url = urllib.parse.urlparse(args.url)
        if url.scheme == "archipelago":
            args.connect = url.netloc
            if url.username:
                args.name = urllib.parse.unquote(url.username)
            if url.password:
                args.password = urllib.parse.unquote(url.password)
        else:
            parser.error(f"bad url, found {args.url}, expected url in form of archipelago://archipelago.gg:38281")

    # use colorama to display colored text highlighting on windows
    colorama.init()

    asyncio.run(main(args))
    colorama.deinit()
