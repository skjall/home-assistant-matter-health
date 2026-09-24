"""The Matter Server's log: how adding a device went, step by step.

It also says when the host has no route to a device's address, which is
how a missing route into the Thread mesh shows up.

The Matter Server (matter.js) reports the steps of commissioning only in its
log, and only from log level ``info`` up. At ``warning`` the final failure is
still there, so a parser that knows both still gives an answer - just a
shorter one.

The patterns match messages of ``@matter/protocol`` (``ControllerCommissioner``,
``ControllerCommissioningFlow``, ``PaseClient``), ``@matter/node``
(``CommissioningClient``) and ``@matter-server/ws-controller``.
"""

from __future__ import annotations

import re

from .. import kinds
from . import PARSERS, LineParser, Parsed, clean, peer_node_id

PEER = r"(@\d+:[0-9a-f]+)"

#: The host has no route to a device's address: the device lives in a network
#: (usually the Thread mesh) Home Assistant's host cannot reach.
UNREACHABLE = r"address is unreachable|ENETUNREACH|Network is unreachable"

#: (pattern, kind, names of the captured groups). Order matters: the first
#: match wins, so the specific failure lines come before the generic step line.
PATTERNS: list[tuple[re.Pattern[str], str, tuple[str, ...]]] = [
    (re.compile(r"Establish PASE to device"), kinds.COMMISSIONING_CONTACT, ()),
    (
        re.compile(r"PaseClient.*Paired successfully"),
        kinds.COMMISSIONING_CONTACT_OK,
        (),
    ),
    (
        re.compile(rf"Start commissioning of node {PEER} into fabric"),
        kinds.COMMISSIONING_STARTED,
        ("peer",),
    ),
    (
        re.compile(
            r"Commissioning step (\S+): ([\w.]+) failed with recoverable error:?\s*(.*)"
        ),
        kinds.COMMISSIONING_RETRY,
        ("step", "name", "reason"),
    ),
    (
        re.compile(r"Commissioning step (\S+): ([\w.]+) failed with error:?\s*(.*)"),
        kinds.COMMISSIONING_FAILED,
        ("step", "name", "reason"),
    ),
    (
        re.compile(r"Commissioning step (\S+): ([\w.]+) succeeded, but .*too long"),
        kinds.COMMISSIONING_FAILED,
        ("step", "name"),
    ),
    (
        re.compile(r"Executing commissioning step (\S+): ([\w.]+)"),
        kinds.COMMISSIONING_STEP,
        ("step", "name"),
    ),
    (
        re.compile(r"Attestation finding, rejecting:\s*(.*)"),
        kinds.COMMISSIONING_ATTESTATION_REJECTED,
        ("reason",),
    ),
    (
        re.compile(r"Commission failed:\s*(.*)"),
        kinds.COMMISSIONING_FAILED,
        ("reason",),
    ),
    (
        re.compile(rf"Commissioned peer\d* as {PEER}"),
        kinds.COMMISSIONING_COMPLETED,
        ("peer",),
    ),
    (
        re.compile(rf"{PEER}\b.*(?:{UNREACHABLE})"),
        kinds.MATTER_ROUTE_UNREACHABLE,
        ("peer",),
    ),
    (re.compile(UNREACHABLE), kinds.MATTER_ROUTE_UNREACHABLE, ()),
]


@PARSERS.register("matter_js")
class MatterJsParser(LineParser):
    """Commissioning progress from the Matter Server's log."""

    def parse(self, line: str) -> list[Parsed]:
        """Return the commissioning event this line reports, if any."""
        text = clean(line)
        for pattern, kind, names in PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            data: dict[str, object] = {
                name: value.strip()
                for name, value in zip(names, match.groups(), strict=True)
                if value is not None
            }
            if "reason" in data and not data["reason"]:
                del data["reason"]
            subject = None
            if "peer" in data:
                node_id = peer_node_id(str(data.pop("peer")))
                data["node_id"] = node_id
                subject = f"node:{node_id}"
            return [Parsed(kind, subject, data)]
        return []
