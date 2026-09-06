"""Insertion-mode group switches and zone-bypass switches.

See docs/phase5-implementation-guide.md sub-step 1's entity table.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BentelAbsolutaConfigEntry
from .const import CONF_NAME, CONF_SERIAL
from .coordinator import BentelAbsolutaCoordinator
from .entity import BentelAbsolutaEntity, all_zone_ids, find_zone, panel_device_info, zone_device_info


async def async_setup_entry(hass: HomeAssistant, entry: BentelAbsolutaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BentelAbsolutaCoordinator = entry.runtime_data.coordinator
    serial = entry.data[CONF_SERIAL]
    name = entry.data[CONF_NAME]

    entities: list[SwitchEntity] = []

    # Only present in armingLabels for a group that isn't "Disabled" in
    # the app - see docs/protocol.md's confirmed rule (entry present =
    # enabled button, absent = greyed-out placeholder = no entity here).
    for group in coordinator.data.get("main", {}).get("armingLabels", {}):
        entities.append(BentelAbsolutaGroupSwitch(coordinator, serial, name, group))

    for zone_id, label in all_zone_ids(coordinator.data).items():
        zone = find_zone(coordinator.data, zone_id)
        if zone and zone["bypassable"]:
            entities.append(BentelAbsolutaZoneBypassSwitch(coordinator, serial, zone_id, label))

    async_add_entities(entities)


class BentelAbsolutaGroupSwitch(BentelAbsolutaEntity, SwitchEntity):
    """One insertion-mode group (A-D) - `main.armingStatus[letter]`.

    Confirmed 2026-09-05: this is a stateless *toggle* endpoint (arming
    and disarming the same group send the identical body) - there is no
    explicit-end-state form. turn_on/turn_off therefore only call the API
    when a flip is actually needed, comparing against the coordinator's
    last-known state first, to avoid an accidental flip in the wrong
    direction from a stale read.
    """

    def __init__(self, coordinator: BentelAbsolutaCoordinator, serial: str, name: str, group: str) -> None:
        super().__init__(coordinator, f"{serial}:group:{group}")
        self._serial = serial
        self._group = group
        self._attr_device_info = panel_device_info(serial, name)

    @property
    def name(self) -> str | None:
        return self.coordinator.data.get("main", {}).get("armingLabels", {}).get(self._group, self._group)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get("main", {}).get("armingStatus", {}).get(self._group)

    async def _toggle_if_needed(self, target: bool) -> None:
        if self.is_on == target:
            return
        group = self._group
        await self.coordinator.async_run_command(lambda api, session_id: api.put_group_arming(session_id, group))

    async def async_turn_on(self, **kwargs) -> None:
        await self._toggle_if_needed(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._toggle_if_needed(False)


class BentelAbsolutaZoneBypassSwitch(BentelAbsolutaEntity, SwitchEntity):
    """`zoneBypass` - explicit end-state, unlike the group toggle above."""

    _attr_name = "Bypass"

    def __init__(self, coordinator: BentelAbsolutaCoordinator, serial: str, zone_id: int, label: str) -> None:
        super().__init__(coordinator, f"{serial}:zone:{zone_id}:bypass")
        self._zone_id = zone_id
        self._attr_device_info = zone_device_info(serial, zone_id, label)

    @property
    def is_on(self) -> bool | None:
        zone = find_zone(self.coordinator.data, self._zone_id)
        return zone["bypass"] if zone else None

    async def async_turn_on(self, **kwargs) -> None:
        zone_id = self._zone_id
        await self.coordinator.async_run_command(lambda api, session_id: api.put_zone_bypass(session_id, zone_id, True))

    async def async_turn_off(self, **kwargs) -> None:
        zone_id = self._zone_id
        await self.coordinator.async_run_command(
            lambda api, session_id: api.put_zone_bypass(session_id, zone_id, False)
        )
