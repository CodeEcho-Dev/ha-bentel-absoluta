"""Global + per-partition alarm_control_panel entities.

Two kinds, mirroring the app's own two independent control surfaces:
- one "global" entity (panel device) - boolean only, no stay/away/night.
- one entity per partition (partition device) - the real 4-mode control.
"""

from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import BentelAbsolutaConfigEntry
from .const import ARMING_AWAY, ARMING_DISARM, ARMING_NO_DELAY, ARMING_STAY, CONF_NAME, CONF_SERIAL
from .coordinator import BentelAbsolutaCoordinator
from .entity import BentelAbsolutaEntity, panel_device_info, partition_device_info

# State mapping: a direct 1:1 passthrough of the partitionArming request
# enum into status.armingStatus[i]. armed_night is a deliberate reuse of
# HA's 4th arm-state slot for "No Delay" (arms immediately, skips the
# exit delay) - not a semantic claim that this is "night mode". A stock
# Lovelace card will show a button labeled "Night" for this.
_ARMING_STATUS_TO_STATE = {
    ARMING_DISARM: AlarmControlPanelState.DISARMED,
    ARMING_STAY: AlarmControlPanelState.ARMED_HOME,
    ARMING_AWAY: AlarmControlPanelState.ARMED_AWAY,
    ARMING_NO_DELAY: AlarmControlPanelState.ARMED_NIGHT,
}


async def async_setup_entry(hass: HomeAssistant, entry: BentelAbsolutaConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: BentelAbsolutaCoordinator = entry.runtime_data.coordinator
    serial = entry.data[CONF_SERIAL]
    name = entry.data[CONF_NAME]

    entities: list[AlarmControlPanelEntity] = [BentelAbsolutaGlobalAlarm(coordinator, serial, name)]

    status = coordinator.data.get("status", {})
    partition_ids = status.get("partitionIds", [])
    partition_labels = status.get("partitionLabels", [])
    for i, partition_id in enumerate(partition_ids):
        label = partition_labels[i] if i < len(partition_labels) else f"Partition {partition_id}"
        entities.append(BentelAbsolutaPartitionAlarm(coordinator, serial, partition_id, label))

    async_add_entities(entities)


class BentelAbsolutaGlobalAlarm(BentelAbsolutaEntity, AlarmControlPanelEntity):
    """Whole-panel arm/disarm - `main.globalArmingStatus`."""

    _attr_name = "Alarm"
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    def __init__(self, coordinator: BentelAbsolutaCoordinator, serial: str, name: str) -> None:
        super().__init__(coordinator, f"{serial}:global")
        self._serial = serial
        self._attr_device_info = panel_device_info(serial, name)

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        main = self.coordinator.data.get("main", {})
        if main.get("alarmNum", 0) > 0:
            return AlarmControlPanelState.TRIGGERED
        if main.get("globalArmingStatus"):
            return AlarmControlPanelState.ARMED_AWAY
        return AlarmControlPanelState.DISARMED

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self.coordinator.async_run_command(
            lambda api, session_id: api.put_global_arming(session_id, False)
        )

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self.coordinator.async_run_command(
            lambda api, session_id: api.put_global_arming(session_id, True)
        )


class BentelAbsolutaPartitionAlarm(BentelAbsolutaEntity, AlarmControlPanelEntity):
    """Per-partition 4-mode arm/disarm - `status.armingStatus[i]`."""

    _attr_name = "Alarm"
    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
    )

    def __init__(
        self, coordinator: BentelAbsolutaCoordinator, serial: str, partition_id: int, label: str
    ) -> None:
        super().__init__(coordinator, f"{serial}:partition:{partition_id}:alarm")
        self._serial = serial
        self._partition_id = partition_id
        self._attr_device_info = partition_device_info(serial, partition_id, label)

    def _index(self) -> int | None:
        ids = self.coordinator.data.get("status", {}).get("partitionIds", [])
        return ids.index(self._partition_id) if self._partition_id in ids else None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        status = self.coordinator.data.get("status", {})
        idx = self._index()
        if idx is None:
            return None
        partition_status = status.get("partitionStatus", [])
        if idx < len(partition_status) and partition_status[idx] == 1:
            return AlarmControlPanelState.TRIGGERED
        arming_status = status.get("armingStatus", [])
        if idx >= len(arming_status):
            return None
        return _ARMING_STATUS_TO_STATE.get(arming_status[idx])

    async def _arm(self, mode: int) -> None:
        partition_id = self._partition_id
        await self.coordinator.async_run_command(
            lambda api, session_id: api.put_partition_arming(session_id, partition_id, mode)
        )

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self._arm(ARMING_DISARM)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        await self._arm(ARMING_STAY)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self._arm(ARMING_AWAY)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        await self._arm(ARMING_NO_DELAY)
