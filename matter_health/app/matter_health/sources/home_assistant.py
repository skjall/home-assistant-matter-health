"""Names and power switches, from Home Assistant itself.

Two things only Home Assistant knows. First, what the user calls a device:
the Matter Server knows node 42, the user knows "Hallway Light". Second, when
something that can cut power was switched off, and by whom. A border router or
Thread router that loses power takes part of the mesh with it; the switch that
did it is usually the missing piece of the explanation.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from typing import Any, ClassVar

import aiohttp

from .. import kinds
from ..engine import SOURCES, Source

_LOGGER = logging.getLogger(__name__)

#: Matter devices are identified in the device registry as
#: ``deviceid_<compressed fabric id>-<node id in hex>-MatterNodeDevice``.
MATTER_IDENTIFIER = re.compile(r"^deviceid_[0-9A-Fa-f]+-([0-9A-Fa-f]+)-")

#: A switch counts as able to cut power when it says it is an outlet, or when
#: its device also measures power - which a smart plug does and a software
#: toggle does not.
POWER_DEVICE_CLASSES = frozenset({"power", "energy", "current"})

#: Wait for a burst of registry updates to finish before reading names.
REFRESH_DELAY_S = 5.0

#: Automation runs remembered to tell which one switched something.
AUTOMATION_MEMORY = 200


class HomeAssistantApi:
    """Minimal websocket client for Home Assistant's API."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Use an already opened websocket."""
        self._ws = ws
        self._next = 1
        #: Events that arrived while waiting for a command's result; the
        #: caller handles them once it listens, so none is lost in between.
        self.pending: list[dict[str, Any]] = []

    async def authenticate(self, token: str) -> None:
        """Log in; raises when the token is refused."""
        greeting = await self._ws.receive_json()
        if greeting.get("type") != "auth_required":
            raise ConnectionError(f"unexpected greeting {greeting.get('type')}")
        await self._ws.send_json({"type": "auth", "access_token": token})
        answer = await self._ws.receive_json()
        if answer.get("type") != "auth_ok":
            raise PermissionError("Home Assistant refused the token")

    async def send(self, message: dict[str, Any]) -> int:
        """Send a command and return its id."""
        message_id = self._next
        self._next += 1
        await self._ws.send_json({"id": message_id, **message})
        return message_id

    async def call(self, message: dict[str, Any]) -> Any:
        """Send a command and wait for its result."""
        message_id = await self.send(message)
        while True:
            answer = await self._ws.receive_json()
            if answer.get("type") == "event":
                self.pending.append(answer)
            elif answer.get("id") == message_id and answer.get("type") == "result":
                if not answer.get("success"):
                    raise RuntimeError(str(answer.get("error")))
                return answer.get("result")


def _log_failure(task: asyncio.Task[None]) -> None:
    """Report a failed name refresh; the old names stay in use meanwhile."""
    if not task.cancelled() and (err := task.exception()) is not None:
        _LOGGER.warning("could not refresh device names: %s", err)


@SOURCES.register("home_assistant")
class HomeAssistantSource(Source):
    """Learns names and reports power switches turning off or on."""

    name: ClassVar[str] = "home_assistant"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Start without knowledge of the installation."""
        super().__init__(*args, **kwargs)
        self.power_switches: set[str] = set()
        self.people: dict[str, str] = {}
        self.automations: OrderedDict[str, str] = OrderedDict()
        self._refresh: asyncio.Task[None] | None = None

    async def run(self) -> None:
        """Connect, learn the installation, then follow state changes."""
        token = self.ctx.options.supervisor_token
        if not token:
            raise PermissionError("no Supervisor token")
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(
                self.ctx.options.core_websocket_url, max_msg_size=0, heartbeat=55
            ) as ws,
        ):
            api = HomeAssistantApi(ws)
            await api.authenticate(token)
            await self.connected()
            await self.learn(
                devices=await api.call({"type": "config/device_registry/list"}),
                entities=await api.call({"type": "config/entity_registry/list"}),
                states=await api.call({"type": "get_states"}),
            )
            await api.call({"type": "subscribe_events", "event_type": "state_changed"})
            await api.call(
                {"type": "subscribe_events", "event_type": "automation_triggered"}
            )
            await api.call(
                {"type": "subscribe_events", "event_type": "device_registry_updated"}
            )
            for raw in api.pending:
                await self.on_event(raw["event"])
            api.pending.clear()
            async for message in ws:
                if message.type is not aiohttp.WSMsgType.TEXT:
                    break
                raw = message.json()
                if raw.get("type") == "event":
                    await self.on_event(raw["event"])

    async def learn(
        self,
        devices: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        states: list[dict[str, Any]],
    ) -> None:
        """Take names and power switches from the registries and states."""
        self.learn_device_names(devices)

        attributes = {
            state["entity_id"]: state.get("attributes", {}) for state in states
        }
        measuring_devices = {
            entity["device_id"]
            for entity in entities
            if entity.get("device_id")
            and entity["entity_id"].startswith("sensor.")
            and attributes.get(entity["entity_id"], {}).get("device_class")
            in POWER_DEVICE_CLASSES
        }
        self.power_switches = {
            entity["entity_id"]
            for entity in entities
            if entity["entity_id"].startswith("switch.")
            and (
                attributes.get(entity["entity_id"], {}).get("device_class") == "outlet"
                or entity.get("device_id") in measuring_devices
            )
        }
        for entity_id, attrs in attributes.items():
            if attrs.get("friendly_name"):
                self.ctx.names.set(f"entity:{entity_id}", attrs["friendly_name"])
            if entity_id.startswith("person.") and attrs.get("user_id"):
                self.people[attrs["user_id"]] = attrs.get("friendly_name", entity_id)

    async def on_event(self, event: dict[str, Any]) -> None:
        """Handle one event from the subscription."""
        data = event.get("data", {})
        if event.get("event_type") == "device_registry_updated":
            self._schedule_refresh()
            return
        if event.get("event_type") == "automation_triggered":
            context_id = (event.get("context") or {}).get("id")
            if context_id:
                self.automations[context_id] = data.get("name") or data.get(
                    "entity_id", ""
                )
                while len(self.automations) > AUTOMATION_MEMORY:
                    self.automations.popitem(last=False)
            return
        entity_id = data.get("entity_id", "")
        if entity_id not in self.power_switches:
            return
        old = (data.get("old_state") or {}).get("state")
        new_state = data.get("new_state") or {}
        new = new_state.get("state")
        if old == new or {old, new} - {"on", "off"}:
            return
        name = new_state.get("attributes", {}).get("friendly_name", entity_id)
        origin, by = self.origin(new_state.get("context") or {})
        await self.emit(
            kinds.HA_POWER_OFF if new == "off" else kinds.HA_POWER_ON,
            f"entity:{entity_id}",
            name=name,
            origin=origin,
            by=by,
        )

    def _schedule_refresh(self) -> None:
        """Re-read device names soon; several updates in a row cost one read."""
        if self._refresh is None or self._refresh.done():
            self._refresh = asyncio.create_task(self.refresh_names())
            self._refresh.add_done_callback(_log_failure)

    async def refresh_names(self) -> None:
        """Read the device registry again over a connection of its own.

        A new device gets its name in the registry a moment after it was
        added. Reading it over the event connection would swallow the state
        changes that arrive meanwhile.
        """
        await asyncio.sleep(REFRESH_DELAY_S)
        token = self.ctx.options.supervisor_token or ""
        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(self.ctx.options.core_websocket_url) as ws,
        ):
            api = HomeAssistantApi(ws)
            await api.authenticate(token)
            devices = await api.call({"type": "config/device_registry/list"})
        self.learn_device_names(devices)

    def learn_device_names(self, devices: list[dict[str, Any]]) -> None:
        """Remember what the user calls each Matter device."""
        for device in devices:
            name = device.get("name_by_user") or device.get("name")
            for domain, identifier in device.get("identifiers", []):
                match = MATTER_IDENTIFIER.match(str(identifier))
                if domain == "matter" and match:
                    self.ctx.names.set(f"node:{int(match.group(1), 16)}", name)

    def origin(self, context: dict[str, Any]) -> tuple[str, str | None]:
        """Who caused a state change, as far as Home Assistant records it.

        An automation acts under its own context, which is also the context
        of its ``automation_triggered`` event; the run's trigger is the
        parent. A change made in the UI carries the user. A change with
        neither was reported by the device itself - a button on the plug,
        power restored - or by an integration that does not pass the context
        on, as MQTT-based ones do; it cannot be attributed.
        """
        context_id = context.get("id")
        if isinstance(context_id, str) and context_id in self.automations:
            return "automation", self.automations[context_id]
        parent = context.get("parent_id")
        if parent:
            return "automation", self.automations.get(parent)
        user = context.get("user_id")
        if user:
            return "person", self.people.get(user)
        return "unknown", None
