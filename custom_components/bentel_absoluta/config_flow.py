"""Config Flow for Bentel Absoluta.

See docs/phase5-implementation-guide.md sub-step 3. Validation runs the
exact same session-lifecycle sequence the coordinator's Tier 2 refresh
does (session -> credentials -> wait for the burst, watching for a
wrongPin deviceError -> settings/notifications -> close) rather than a
lighter-weight check - this is deliberate, not extra work, since it's the
one real proof the serial+PIN pair actually works and it seeds
`enabledNotifications` for the newly-generated clientId at the same time.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    BentelAbsolutaApiClient,
    BentelAbsolutaAuthError,
    BentelAbsolutaBusyError,
    BentelAbsolutaConnectionError,
    BentelAbsolutaError,
    generate_client_id,
)
from .const import (
    CONF_CLIENT_ID,
    CONF_NAME,
    CONF_PIN,
    CONF_SERIAL,
    CONF_SNAPSHOT_INTERVAL,
    CONF_SYNC_INTERVAL,
    DEFAULT_SNAPSHOT_INTERVAL,
    DEFAULT_SYNC_INTERVAL,
    DOMAIN,
    MIN_SNAPSHOT_INTERVAL,
    UNREACHABLE_RETRY_DELAY,
)

_LOGGER = logging.getLogger(__name__)

_REQUIRED_PAGES = {"main", "settings", "status", "scenario"}

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(CONF_SERIAL): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
        vol.Required(CONF_PIN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)
_REAUTH_SCHEMA = vol.Schema(
    {vol.Required(CONF_PIN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


class CannotConnect(HomeAssistantError):
    """Network/timeout, or the panel is busy (another client holds the session)."""


class InvalidAuth(HomeAssistantError):
    """Wrong serial (409 UNREACHABLE, persisting across a retry) or wrong PIN (wrongPin)."""


async def _bootstrap(hass: HomeAssistant, serial: str, pin: str, client_id: str) -> None:
    """Run the full session lifecycle once, as Config Flow validation.

    Raises CannotConnect / InvalidAuth - see docs/phase5-implementation-
    guide.md sub-step 3's "Error mapping" for the reasoning behind each
    branch below, especially the UNREACHABLE retry-once rule.
    """
    session = async_get_clientsession(hass)
    api = BentelAbsolutaApiClient(session, client_id)

    session_id: str | None = None
    for attempt in range(2):
        try:
            session_id = await api.create_session(serial)
            break
        except BentelAbsolutaBusyError as err:
            if str(err) == "BUSY":
                # A real client already holds the session - not an auth
                # problem, and retrying won't help within Config Flow.
                raise CannotConnect from err
            # UNREACHABLE: confirmed 2026-09-05 to also mean "wrong
            # serial", but it's ambiguous with a transient post-eviction
            # blip - retry once before concluding invalid_auth.
            if attempt == 0:
                await asyncio.sleep(UNREACHABLE_RETRY_DELAY)
                continue
            raise InvalidAuth from err
        except BentelAbsolutaConnectionError as err:
            raise CannotConnect from err

    assert session_id is not None
    try:
        await api.submit_credentials(session_id, pin)

        tracking_id, cache_date = "0", "0"
        seen_pages: set[str] = set()
        while not _REQUIRED_PAGES.issubset(seen_pages):
            events, tracking_id, cache_date = await api.poll(session_id, tracking_id, cache_date)
            for event in events:
                if event.page_name:
                    seen_pages.add(event.page_name)

        await api.put_settings_notifications(session_id)
        await api.register_push_panels([serial])
    except BentelAbsolutaAuthError as err:
        raise InvalidAuth from err
    except BentelAbsolutaConnectionError as err:
        raise CannotConnect from err
    finally:
        await api.delete_session(session_id)


class BentelAbsolutaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bentel Absoluta."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_SERIAL])
            self._abort_if_unique_id_configured()

            client_id = generate_client_id()
            try:
                await _bootstrap(self.hass, user_input[CONF_SERIAL], user_input[CONF_PIN], client_id)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001 - fallback bucket, logged
                _LOGGER.exception("Unexpected exception during Bentel Absoluta setup")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_NAME: user_input[CONF_NAME],
                        CONF_SERIAL: user_input[CONF_SERIAL],
                        CONF_PIN: user_input[CONF_PIN],
                        CONF_CLIENT_ID: client_id,
                    },
                )

        return self.async_show_form(step_id="user", data_schema=_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        assert self._reauth_entry is not None
        if user_input is not None:
            try:
                await _bootstrap(
                    self.hass,
                    self._reauth_entry.data[CONF_SERIAL],
                    user_input[CONF_PIN],
                    self._reauth_entry.data[CONF_CLIENT_ID],
                )
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected exception during Bentel Absoluta reauth")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, CONF_PIN: user_input[CONF_PIN]},
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(step_id="reauth_confirm", data_schema=_REAUTH_SCHEMA, errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> BentelAbsolutaOptionsFlow:
        return BentelAbsolutaOptionsFlow()


class BentelAbsolutaOptionsFlow(OptionsFlow):
    """Options: the two poll intervals from sub-step 2's two-tier design."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_SNAPSHOT_INTERVAL] < MIN_SNAPSHOT_INTERVAL:
                errors["base"] = "snapshot_interval_too_low"
            else:
                return self.async_create_entry(data=user_input)

        current_sync = self.config_entry.options.get(CONF_SYNC_INTERVAL, DEFAULT_SYNC_INTERVAL)
        current_snapshot = self.config_entry.options.get(CONF_SNAPSHOT_INTERVAL, DEFAULT_SNAPSHOT_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SYNC_INTERVAL, default=current_sync): NumberSelector(
                    NumberSelectorConfig(min=10, max=3600, unit_of_measurement="s")
                ),
                vol.Required(CONF_SNAPSHOT_INTERVAL, default=current_snapshot): NumberSelector(
                    NumberSelectorConfig(min=MIN_SNAPSHOT_INTERVAL, max=86400, unit_of_measurement="s")
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
