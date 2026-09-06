"""Thin REST client for the Bentel Absoluta mobile-api.

One method per endpoint, plus the long-poll chunk parser and the
exception hierarchy. Deliberately has no knowledge of Tiers/locking/
reauth - that orchestration lives in coordinator.py, keeping this module
a focused, easily-testable REST wrapper.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import API_BASE, APP_NAME, APP_VERSION, PANEL_TYPE

_LOGGER = logging.getLogger(__name__)

_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Dart/3.12 (dart:io)",
}
_REQUEST_TIMEOUT = ClientTimeout(total=15)


class BentelAbsolutaError(Exception):
    """Base error for all Bentel Absoluta API failures."""


class BentelAbsolutaConnectionError(BentelAbsolutaError):
    """Network/timeout failure, or the long-poll reported connectionLost.

    Transient and unrelated to who holds the session - abandon the
    session and retry as a completely fresh one (no resume exists).
    """


class BentelAbsolutaAuthError(BentelAbsolutaError):
    """The PIN is (now) wrong.

    Raised when the long-poll's first event is a deviceError with
    errorType "wrongPin" - the credentials PUT itself never fails for a
    bad PIN (always 200, echoing back whatever was submitted). This is
    the *only* signal that means "reauth", and it must not be confused
    with BentelAbsolutaConnectionError even though both arrive as the
    same {"eventType": "deviceError", ...} envelope - the discriminator
    is the errorType field.
    """


class BentelAbsolutaBusyError(BentelAbsolutaError):
    """409 BUSY or 409 UNREACHABLE creating a session.

    BUSY means another client currently holds the exclusive session.
    UNREACHABLE is ambiguous - it can also mean a simply wrong/nonexistent
    serial, not just "right after an eviction". Both are treated as
    transient/retry-later by the coordinator; Config Flow (which
    validates a serial for the first time) additionally retries once
    before concluding a serial is actually wrong.
    """


@dataclass
class LongPollEvent:
    """One parsed event from a long-poll response."""

    event_type: str
    page_name: str | None
    page_data: dict[str, Any]


def parse_longpoll_chunk(text: str) -> list[dict[str, Any]]:
    """Parse a long-poll response body into its individual JSON objects.

    A single response can contain multiple newline-separated JSON objects
    at once (observed up to 3 in one chunk) - this is pure, dependency-free
    parsing so it can be unit tested against fixed sample payloads with no
    live panel needed.
    """
    import json

    events: list[dict[str, Any]] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            _LOGGER.debug("Skipping unparseable long-poll line: %s", line)
    return events


class BentelAbsolutaApiClient:
    """Raw REST wrapper. Takes an injected aiohttp session (HA's shared one)."""

    def __init__(self, session: ClientSession, client_id: str) -> None:
        self._session = session
        self.client_id = client_id

    def _push_registration(self, credentials: str = "") -> dict[str, Any]:
        return {
            "clientId": self.client_id,
            "platform": "Android_FCM",
            "version": "14",
            "appName": APP_NAME,
            "credentials": credentials,
        }

    async def _request(self, method: str, path: str, json: dict[str, Any] | None = None):
        _LOGGER.debug("-> %s %s", method, path)
        try:
            resp = await self._session.request(
                method,
                API_BASE + path,
                json=json,
                headers=_HEADERS,
                timeout=_REQUEST_TIMEOUT,
            )
        except ClientError as err:
            _LOGGER.debug("<- %s %s failed: %s", method, path, err)
            raise BentelAbsolutaConnectionError(str(err)) from err
        except TimeoutError as err:
            _LOGGER.debug("<- %s %s timed out", method, path)
            raise BentelAbsolutaConnectionError("timeout") from err
        _LOGGER.debug("<- %s %s: %s", method, path, resp.status)
        return resp

    # ---- session lifecycle ----

    async def create_session(self, serial: str) -> str:
        """POST session/ -> sessionId. Raises BentelAbsolutaBusyError on
        409 BUSY/UNREACHABLE."""
        resp = await self._request(
            "POST",
            "/mobile-api/rest/session/",
            json={
                "clientVersion": APP_VERSION,
                "deviceId": serial,
                "panelType": PANEL_TYPE,
                "pushRegistration": self._push_registration(),
            },
        )
        if resp.status == 409:
            body = await resp.json(content_type=None)
            raise BentelAbsolutaBusyError(body.get("errorCode", "unknown"))
        if resp.status != 201:
            text = await resp.text()
            raise BentelAbsolutaError(f"unexpected status {resp.status}: {text[:200]}")
        body = await resp.json()
        return body["sessionId"]

    async def submit_credentials(self, session_id: str, pin: str) -> None:
        """PUT session/{id}/credentials.

        This always returns 200 regardless of whether the PIN is correct
        - it does not validate anything, it just echoes back whatever
        was submitted. The real PIN check happens on the first long-poll
        event (see poll() below) - do not add a status-code check here
        expecting it to ever fail for a bad PIN, it won't.
        """
        resp = await self._request(
            "PUT",
            f"/mobile-api/rest/session/{session_id}/credentials",
            json={"devicePassword": pin},
        )
        if resp.status != 200:
            text = await resp.text()
            raise BentelAbsolutaError(f"unexpected status {resp.status}: {text[:200]}")

    async def delete_session(self, session_id: str) -> None:
        """DELETE session/{id}. A 404 here is expected/harmless - the
        server can already have torn the session down on its own
        (connectionLost, wrongPin, or a natural expiry)."""
        try:
            await self._request("DELETE", f"/mobile-api/rest/session/{session_id}")
        except BentelAbsolutaConnectionError:
            pass

    # ---- long-poll ----

    async def poll(
        self, session_id: str, tracking_id: str = "0", cache_date: str = "0"
    ) -> tuple[list[LongPollEvent], str, str]:
        """One long-poll GET. Returns (events, new_tracking_id, new_cache_date).

        Raises BentelAbsolutaAuthError if the response contains a
        deviceError with errorType "wrongPin", and
        BentelAbsolutaConnectionError for a 404 (session invalidated
        server-side) or a deviceError with any other errorType (e.g.
        "connectionLost"). Both deviceError variants arrive in the
        identical envelope shape - the errorType field is what's actually
        being branched on.
        """
        params = {
            "Content-type": "application/json",
            "X-Atmosphere-tracking-id": tracking_id,
            "X-Atmosphere-Framework": "1.0.13",
            "X-Atmosphere-Transport": "long-polling",
            "X-Cache-Date": cache_date,
            "_": str(int(time.time() * 1000)),
        }
        _LOGGER.debug("-> GET long-poll session=%s tracking_id=%s", session_id, tracking_id)
        try:
            resp = await self._session.get(
                f"{API_BASE}/mobile-api/async/{session_id}",
                params=params,
                headers={"User-Agent": _HEADERS["User-Agent"]},
                timeout=ClientTimeout(total=30),
            )
        except ClientError as err:
            _LOGGER.debug("<- long-poll failed: %s", err)
            raise BentelAbsolutaConnectionError(str(err)) from err
        except TimeoutError as err:
            _LOGGER.debug("<- long-poll timed out")
            raise BentelAbsolutaConnectionError("timeout") from err

        if resp.status == 404:
            _LOGGER.debug("<- long-poll: 404 (session invalidated)")
            raise BentelAbsolutaConnectionError("session invalidated (404)")

        new_tracking_id = resp.headers.get("x-atmosphere-tracking-id", tracking_id)
        new_cache_date = str(int(time.time() * 1000))
        text = await resp.text()
        _LOGGER.debug("<- long-poll: %s, %d byte(s)", resp.status, len(text))

        events: list[LongPollEvent] = []
        for raw in parse_longpoll_chunk(text):
            event_type = raw.get("eventType")
            if event_type == "deviceError":
                error_type = raw.get("errorType")
                _LOGGER.debug("<- long-poll deviceError: errorType=%s", error_type)
                if error_type == "wrongPin":
                    raise BentelAbsolutaAuthError("wrong PIN")
                raise BentelAbsolutaConnectionError(error_type or "deviceError")
            if event_type == "heartBeat":
                continue
            events.append(
                LongPollEvent(
                    event_type=event_type,
                    page_name=raw.get("pageName"),
                    page_data=raw.get("pageData", {}),
                )
            )
        if events:
            _LOGGER.debug(
                "<- long-poll: %d page event(s): %s",
                len(events),
                [e.page_name for e in events],
            )
        return events, new_tracking_id, new_cache_date

    # ---- commands (all fire-and-forget: 204 No Content, confirmation
    # arrives asynchronously over the long-poll) ----

    async def _put_action(self, session_id: str, path: str, body: dict[str, Any]) -> None:
        resp = await self._request(
            "PUT", f"/mobile-api/rest/session/{session_id}{path}", json=body
        )
        if resp.status != 204:
            text = await resp.text()
            raise BentelAbsolutaError(f"unexpected status {resp.status}: {text[:200]}")

    async def put_global_arming(self, session_id: str, value: bool) -> None:
        await self._put_action(
            session_id, "/main/globalArmingStatus", {"globalArmingStatus": value, "force": False}
        )

    async def put_group_arming(self, session_id: str, group: str) -> None:
        """Stateless toggle - there's no explicit end-state form for this
        endpoint."""
        await self._put_action(session_id, "/main/armingStatus", {"armingStatus": group, "force": False})

    async def put_partition_arming(self, session_id: str, partition_id: int, mode: int) -> None:
        await self._put_action(
            session_id, f"/status/partitionarming/{partition_id}", {"partitionArming": mode, "force": False}
        )

    async def put_zone_bypass(self, session_id: str, zone_id: int, value: bool) -> None:
        await self._put_action(session_id, f"/status/bypass/{zone_id}", {"zoneBypass": value})

    async def put_settings_notifications(self, session_id: str) -> None:
        """Must only be called after the initial pageReady burst has
        arrived - calling it earlier reliably 404s."""
        await self._request(
            "PUT",
            f"/mobile-api/rest/session/{session_id}/settings/notifications",
            json={"enableNotifications": True, "useCustomSounds": True},
        )

    # ---- session-independent (these never compete for the exclusive
    # session lock) ----

    async def register_push_panels(self, panels: list[str], credentials: str = "") -> dict[str, Any]:
        resp = await self._request(
            "POST",
            "/mobile-api/rest/notification/panels",
            json={
                "clientVersion": APP_VERSION,
                "pushRegistration": self._push_registration(credentials),
                "panels": panels,
            },
        )
        if resp.status != 200:
            text = await resp.text()
            raise BentelAbsolutaError(f"unexpected status {resp.status}: {text[:200]}")
        return await resp.json()

    async def synchronize(self, credentials: str = "") -> list[dict[str, Any]]:
        resp = await self._request(
            "POST",
            "/mobile-api/rest/notification/synchronize",
            json={
                "clientVersion": APP_VERSION,
                "pushRegistration": self._push_registration(credentials),
            },
        )
        if resp.status != 200:
            text = await resp.text()
            raise BentelAbsolutaError(f"unexpected status {resp.status}: {text[:200]}")
        body = await resp.json()
        return body.get("notifications", [])


def generate_client_id() -> str:
    """Generate a new persistent clientId - called once at Config Flow
    time, never regenerated afterward."""
    return str(uuid.uuid4())
