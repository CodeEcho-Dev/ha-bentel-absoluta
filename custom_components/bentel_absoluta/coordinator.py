"""Two-tier data-update coordinator.

- Tier 1 (`notification/synchronize`, session-independent, cheap): polled
  every tick at `update_interval`. Drives HA event-bus notifications and
  triggers an opportunistic Tier 2 refresh when something new is seen.
- Tier 2 (a real session, opened on-demand): the *only* source of the
  structured per-zone/per-partition data every entity needs - opened at
  most every `snapshot_interval`, held only long enough to read the
  initial page burst, then closed immediately.

Tier 1 also applies a best-effort "fast path": on a new arm/disarm/alarm
notification, it optimistically updates the relevant partition's (or the
global) state immediately, rather than waiting for the next Tier 2
refresh. This is provisional by design - Tier 2 always overwrites it with
the authoritative value regardless, so a wrong guess self-corrects rather
than sticking. One real ambiguity exists: `eventType 255`'s own text
(written in whatever language the panel itself is configured for, not
necessarily Home Assistant's) cannot distinguish Stay from No Delay when
it reads as a partial arm, since a plain synchronize entry carries no
armingStatus enum value, just human text - see PARTIAL_ARM_TEXT_MARKERS
in const.py. That's resolved by defaulting to Stay (the more common
real-world case) for that one text - not by skipping the fast path, since
"armed" is correct either way and only the specific sub-mode shown could
be briefly wrong, exactly the kind of provisional gap Tier 2 already
exists to correct.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any, Awaitable, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BentelAbsolutaApiClient,
    BentelAbsolutaAuthError,
    BentelAbsolutaBusyError,
    BentelAbsolutaConnectionError,
    BentelAbsolutaError,
)
from .const import (
    ARMING_AWAY,
    ARMING_DISARM,
    ARMING_STAY,
    CONF_PIN,
    CONF_SERIAL,
    CONF_SNAPSHOT_INTERVAL,
    CONF_SYNC_INTERVAL,
    DEFAULT_SNAPSHOT_INTERVAL,
    DEFAULT_SYNC_INTERVAL,
    DOMAIN,
    PARTIAL_ARM_TEXT_MARKERS,
    SESSION_CEILING_COMMAND,
    SESSION_CEILING_SNAPSHOT,
    TIER1_FAILURE_THRESHOLD,
    TIER2_FAILURE_THRESHOLD,
    ZONE_STATUS_ALARM,
)

# notification/synchronize eventType values.
_EVENT_ARMED = 255
_EVENT_DISARMED_A = 3
_EVENT_DISARMED_B = 254
_EVENT_ALARM = 0

_LOGGER = logging.getLogger(__name__)

_REQUIRED_PAGES = {"main", "settings", "status", "scenario"}


def _is_partial_arm_text(event_type_text: str) -> bool:
    """Whether a `notification/synchronize` "armed" entry's own text
    reads as a partial/Stay arm rather than a full/Away arm - see
    PARTIAL_ARM_TEXT_MARKERS for how to extend this to another panel
    language."""
    lowered = event_type_text.lower()
    return any(marker in lowered for marker in PARTIAL_ARM_TEXT_MARKERS)
_STORE_VERSION = 1


class BentelAbsolutaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator holding the merged main/settings/status/scenario/alarm
    page data, refreshed per the two-tier design above."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: BentelAbsolutaApiClient) -> None:
        sync_interval = entry.options.get(CONF_SYNC_INTERVAL, DEFAULT_SYNC_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=sync_interval),
            config_entry=entry,
        )
        self.entry = entry
        self.api = api
        self.serial: str = entry.data[CONF_SERIAL]
        self.pin: str = entry.data[CONF_PIN]

        self._session_lock = asyncio.Lock()
        self._tier1_failures = 0
        self._tier2_failures = 0
        self._last_tier2_time: float = 0.0
        self._store: Store[dict[str, Any]] = Store(hass, _STORE_VERSION, f"{DOMAIN}_{entry.entry_id}_sync")
        self._last_synchronize_id = 0
        self._synchronize_baseline_set = False

        self.data: dict[str, Any] = {
            "main": {},
            "settings": {},
            "status": {},
            "scenario": {},
            "alarm": {},
        }

    @property
    def tier1_failures(self) -> int:
        return self._tier1_failures

    @property
    def tier2_failures(self) -> int:
        return self._tier2_failures

    @property
    def snapshot_interval(self) -> float:
        return self.entry.options.get(CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL)

    async def async_setup(self) -> None:
        """Register push once at startup - session-independent, safe to
        leave registered indefinitely."""
        last = await self._store.async_load()
        # `synchronize` returns a *full* history, not a delta - on a
        # brand-new install there's no persisted last_id yet, so the very
        # first Tier 1 poll must only
        # establish a baseline (highest id seen so far) rather than
        # treating the entire historical backlog as "new" and firing a
        # storm of HA events / fast-path guesses for old history.
        self._synchronize_baseline_set = last is not None
        if last:
            self._last_synchronize_id = last.get("last_id", 0)
        _LOGGER.debug(
            "Coordinator setup: sync_interval=%ss snapshot_interval=%ss last_synchronize_id=%s (baseline_set=%s)",
            self.update_interval.total_seconds() if self.update_interval else None,
            self.snapshot_interval,
            self._last_synchronize_id,
            self._synchronize_baseline_set,
        )
        try:
            result = await self.api.register_push_panels([self.serial])
            _LOGGER.debug("notification/panels registration at startup: %s", result)
        except BentelAbsolutaError:
            _LOGGER.debug("notification/panels registration failed at startup", exc_info=True)

    def _merge_page(self, page_name: str | None, page_data: dict[str, Any]) -> None:
        if not page_name:
            return
        self.data.setdefault(page_name, {})
        self.data[page_name].update(page_data)

    async def _async_update_data(self) -> dict[str, Any]:
        new_event_seen = await self._poll_tier1()

        elapsed = time.monotonic() - self._last_tier2_time
        due = elapsed >= self.snapshot_interval
        _LOGGER.debug(
            "Update cycle: new_event_seen=%s tier2_elapsed=%.0fs/%ss due=%s",
            new_event_seen,
            elapsed,
            self.snapshot_interval,
            due,
        )
        if due or new_event_seen:
            _LOGGER.debug("Running Tier 2 refresh (due=%s, new_event_seen=%s)", due, new_event_seen)
            await self._run_tier2()

        if self._tier1_failures >= TIER1_FAILURE_THRESHOLD or self._tier2_failures >= TIER2_FAILURE_THRESHOLD:
            raise UpdateFailed(
                f"tier1_failures={self._tier1_failures} tier2_failures={self._tier2_failures}"
            )
        return self.data

    # ---- Tier 1: notification/synchronize ----

    async def _poll_tier1(self) -> bool:
        """Returns True if at least one new notification was seen."""
        try:
            notifications = await self.api.synchronize()
        except BentelAbsolutaError:
            self._tier1_failures += 1
            _LOGGER.debug("Tier 1 poll failed (%s consecutive)", self._tier1_failures, exc_info=True)
            return False

        if self._tier1_failures:
            _LOGGER.info("Tier 1 poll recovered after %s consecutive failures", self._tier1_failures)
        self._tier1_failures = 0
        _LOGGER.debug("Tier 1 poll: %d notification(s) in backlog", len(notifications))

        if not self._synchronize_baseline_set:
            # First-ever poll for this config entry: `synchronize` is a
            # full history, not a delta, so treat every entry already in
            # it as "already seen" rather than firing events/fast-path
            # guesses for old history - only genuinely new activity from
            # here on should do that.
            if notifications:
                self._last_synchronize_id = max(n.get("id", 0) for n in notifications)
                await self._store.async_save({"last_id": self._last_synchronize_id})
            self._synchronize_baseline_set = True
            _LOGGER.debug("Tier 1: seeded synchronize baseline at id=%s", self._last_synchronize_id)
            return False

        new_ids = [n for n in notifications if n.get("id", 0) > self._last_synchronize_id]
        if not new_ids:
            return False

        _LOGGER.debug("Tier 1: %d new notification(s) since id=%s", len(new_ids), self._last_synchronize_id)
        new_ids.sort(key=lambda n: n.get("id", 0))
        for notification in new_ids:
            self.hass.bus.async_fire(
                f"{DOMAIN}_event",
                {
                    "serial": self.serial,
                    "text": notification.get("text"),
                    "event_type": notification.get("eventType"),
                    "partition_text": notification.get("partitionText"),
                    "who_text": notification.get("whoText"),
                    "event_time": notification.get("eventTime"),
                },
            )
            self._apply_fast_path(notification)
        self._last_synchronize_id = new_ids[-1]["id"]
        await self._store.async_save({"last_id": self._last_synchronize_id})
        return True

    def _partition_index(self, partition_text: str | None) -> int | None:
        """Match a synchronize entry's `partitionText` (e.g. "(CONTACTOS
        )") against the last-known partitionLabels. Returns None both for
        a null/multi-partition partitionText (null means the event covers
        more than one partition) and for a label that doesn't match
        anything we know - callers treat both cases the same way (fall
        back to a global update), since guessing the wrong partition
        would be worse than not guessing at all."""
        if not partition_text:
            return None
        target = partition_text.strip("() ").strip()
        labels = self.data.get("status", {}).get("partitionLabels", [])
        for i, label in enumerate(labels):
            if label.strip() == target:
                return i
        return None

    def _mark_zone_alarm(self, zone_label: str) -> None:
        zone_label = zone_label.strip()
        for group in self.data.get("status", {}).get("zones", []):
            labels = group.get("zoneLabels", [])
            for i, label in enumerate(labels):
                if label.strip() == zone_label:
                    group["zoneStatus"][i] = ZONE_STATUS_ALARM

    def _apply_fast_path(self, notification: dict[str, Any]) -> None:
        """Best-effort, provisional entity-state update from one
        `notification/synchronize` entry - see the module docstring for
        why the Stay/No-Delay ambiguity doesn't block this. Every value
        touched here is unconditionally overwritten by the next Tier 2
        refresh, so a wrong guess self-corrects rather than sticking."""
        event_type = notification.get("eventType")
        status = self.data.setdefault("status", {})
        main = self.data.setdefault("main", {})
        arming_status = status.get("armingStatus")
        idx = self._partition_index(notification.get("partitionText"))

        if event_type == _EVENT_ARMED:
            # A partial-arm text is ambiguous between Stay and No Delay
            # (both produce the same text) - default to Stay, the more
            # common real-world case. Any other text is treated as
            # unambiguous - a full/Away arm.
            event_type_text = notification.get("eventTypeText") or ""
            mode = ARMING_STAY if _is_partial_arm_text(event_type_text) else ARMING_AWAY
            if idx is not None and arming_status is not None and idx < len(arming_status):
                arming_status[idx] = mode
            else:
                main["globalArmingStatus"] = True
        elif event_type in (_EVENT_DISARMED_A, _EVENT_DISARMED_B):
            if idx is not None and arming_status is not None and idx < len(arming_status):
                arming_status[idx] = ARMING_DISARM
            else:
                main["globalArmingStatus"] = False
        elif event_type == _EVENT_ALARM:
            main["alarmNum"] = main.get("alarmNum", 0) + 1
            who_text = (notification.get("whoText") or "").strip()
            if who_text:
                self._mark_zone_alarm(who_text)
            else:
                partition_status = status.get("partitionStatus")
                if idx is not None and partition_status is not None and idx < len(partition_status):
                    partition_status[idx] = ZONE_STATUS_ALARM

    # ---- Tier 2: on-demand session snapshot ----

    async def _run_tier2(self) -> None:
        _LOGGER.debug("Tier 2 refresh starting")
        try:
            await self._run_session()
        except BentelAbsolutaAuthError as err:
            _LOGGER.debug("Tier 2 refresh: wrong PIN, triggering reauth")
            raise ConfigEntryAuthFailed("Bentel Absoluta PIN rejected") from err
        except BentelAbsolutaError as err:
            self._tier2_failures += 1
            _LOGGER.debug(
                "Tier 2 refresh failed (%s consecutive): %s", self._tier2_failures, err, exc_info=True
            )
            return

        if self._tier2_failures:
            _LOGGER.info("Tier 2 refresh recovered after %s consecutive failures", self._tier2_failures)
        self._tier2_failures = 0
        self._last_tier2_time = time.monotonic()
        _LOGGER.debug("Tier 2 refresh completed successfully")

    async def async_run_command(self, action: Callable[[BentelAbsolutaApiClient, str], Awaitable[None]]) -> None:
        """Open a session, run `action` (an arm/disarm/bypass PUT), wait
        (best-effort) for its confirming diff, then close. Raises the
        underlying BentelAbsolutaError subclasses directly - callers
        (platform entities) translate these into HomeAssistantError /
        reauth."""
        _LOGGER.debug("Running command via a new session")
        await self._run_session(action=action, ceiling=SESSION_CEILING_COMMAND)
        self._last_tier2_time = time.monotonic()
        _LOGGER.debug("Command completed successfully")

    async def _run_session(
        self,
        action: Callable[[BentelAbsolutaApiClient, str], Awaitable[None]] | None = None,
        ceiling: float = SESSION_CEILING_SNAPSHOT,
    ) -> None:
        """The shared session lifecycle: open -> credentials -> wait for
        the initial burst (watching for a wrongPin deviceError first) ->
        optional command + best-effort confirm -> close. Wrapped in a
        lock so this integration never contends with *itself*, and in a
        hard ceiling independent of the per-step timeouts, so a bug in
        those can never turn into holding the exclusive session lock
        indefinitely."""
        if self._session_lock.locked():
            _LOGGER.debug("Session lock is held - waiting for the in-progress session to finish")
        async with self._session_lock:
            async with asyncio.timeout(ceiling):
                session_id = await self.api.create_session(self.serial)
                _LOGGER.debug("Session created: %s", session_id)
                try:
                    await self.api.submit_credentials(session_id, self.pin)
                    _LOGGER.debug("Credentials submitted, waiting for initial page burst")

                    tracking_id, cache_date = "0", "0"
                    seen_pages: set[str] = set()
                    while not _REQUIRED_PAGES.issubset(seen_pages):
                        events, tracking_id, cache_date = await self.api.poll(session_id, tracking_id, cache_date)
                        for event in events:
                            self._merge_page(event.page_name, event.page_data)
                            if event.page_name:
                                seen_pages.add(event.page_name)
                    _LOGGER.debug("Initial page burst complete: %s", seen_pages)

                    # Safe to call every session, not just the first ever -
                    # no observed downside once past the initial burst.
                    # Simpler than tracking a persisted "already called
                    # once" flag.
                    await self.api.put_settings_notifications(session_id)

                    if action is not None:
                        _LOGGER.debug("Issuing command")
                        await action(self.api, session_id)
                        try:
                            async with asyncio.timeout(5):
                                events, tracking_id, cache_date = await self.api.poll(
                                    session_id, tracking_id, cache_date
                                )
                                for event in events:
                                    self._merge_page(event.page_name, event.page_data)
                                _LOGGER.debug("Command confirmed: %s", [e.page_name for e in events])
                        except TimeoutError:
                            # Not an error - the command already succeeded
                            # (fire-and-forget by design). The next Tier 2
                            # refresh corrects any optimistic state.
                            _LOGGER.debug(
                                "No confirming diff within the timeout - not an error, "
                                "the command already succeeded (fire-and-forget by design)"
                            )
                finally:
                    _LOGGER.debug("Closing session: %s", session_id)
                    await self.api.delete_session(session_id)
