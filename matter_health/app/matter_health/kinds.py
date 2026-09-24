"""Every kind of event a source may emit, and what its data holds.

This is the contract between sources and rules: a source promises to emit
these shapes, a rule may rely on them. Add a kind here before emitting it.
"""

from typing import Final

# --- Thread network, seen from the Home Assistant border router -----------

#: The border router's view changed. data: role, partition_id, leader_router_id,
#: router_count
THREAD_STATE: Final = "thread.state"
#: The border router took a different role. data: previous, current
THREAD_ROLE_CHANGED: Final = "thread.role_changed"
#: The border router now belongs to a different partition. data: previous,
#: current
THREAD_PARTITION_CHANGED: Final = "thread.partition_changed"
#: A different router leads the partition. data: previous, current (router ids)
THREAD_LEADER_CHANGED: Final = "thread.leader_changed"
#: The border router stopped hearing from the leader. data: -
THREAD_LEADER_LOST: Final = "thread.leader_lost"
#: The border router heard a router of another partition, i.e. the mesh is
#: split. data: -
THREAD_FOREIGN_PARTITION: Final = "thread.foreign_partition"
#: Best radio link per device. data: devices: list of {subject, neighbour,
#: role, rssi, lqi, strength}
THREAD_TOPOLOGY: Final = "thread.topology"
#: The border router's radio could not send because the channel was busy, many
#: times in a short while. data: count, minutes
THREAD_CHANNEL_BUSY: Final = "thread.channel_busy"
#: The border router lost contact with its radio (the USB stick or module).
#: data: reason
THREAD_RADIO_FAULT: Final = "thread.radio_fault"
#: The border router add-on found IPv6 forwarding switched off on the host.
#: data: -
HOST_FORWARDING_OFF: Final = "host.forwarding_off"
#: The border router add-on started its agent. data: -
THREAD_AGENT_STARTED: Final = "thread.agent_started"

# --- Border routers ---------------------------------------------------------

#: A border router announced itself. subject: ``br:<ext>``. data: name, vendor,
#: model
BORDER_ROUTER_APPEARED: Final = "border_router.appeared"
#: A border router is no longer announced. subject: ``br:<ext>``. data: name,
#: vendor, model
BORDER_ROUTER_GONE: Final = "border_router.gone"

# --- Matter devices ----------------------------------------------------------

#: subject: ``node:<id>``. data: name
MATTER_NODE_AVAILABLE: Final = "matter.node_available"
#: subject: ``node:<id>``. data: name
MATTER_NODE_UNAVAILABLE: Final = "matter.node_unavailable"
#: subject: ``node:<id>``. data: name
MATTER_NODE_ADDED: Final = "matter.node_added"
#: subject: ``node:<id>``. data: name
MATTER_NODE_REMOVED: Final = "matter.node_removed"
#: The Matter Server knew devices before and now knows none. data: previous
MATTER_NODES_LOST: Final = "matter.nodes_lost"
#: The Matter Server has no route to a device's address. subject:
#: ``node:<id>`` when known. data: -
MATTER_ROUTE_UNREACHABLE: Final = "matter.route_unreachable"

# --- The system Home Assistant runs on, from the Supervisor ----------------

#: A piece of software was updated. subject: ``software:<slug>``. data: name,
#: previous, current
SYSTEM_UPDATED: Final = "system.updated"
#: What the Supervisor says about the host's network. data: docker_ipv6
#: (true, false or null), ipv6_method (of the primary interface), interface,
#: haos (whether Home Assistant OS runs the host)
SYSTEM_NETWORK: Final = "system.network"

# --- Adding a device (commissioning), from the Matter Server's log ------------

#: The Matter Server found the new device and starts the secure first contact.
COMMISSIONING_CONTACT: Final = "commissioning.contact"
#: The first contact (PASE) succeeded.
COMMISSIONING_CONTACT_OK: Final = "commissioning.contact_ok"
#: The device got its node id. data: node_id
COMMISSIONING_STARTED: Final = "commissioning.started"
#: A commissioning step began. data: step (e.g. "11.1"), name (matter.js name)
COMMISSIONING_STEP: Final = "commissioning.step"
#: A step failed but commissioning goes on. data: step, name, reason
COMMISSIONING_RETRY: Final = "commissioning.retry"
#: Commissioning stopped. data: step, name, reason (whatever is known)
COMMISSIONING_FAILED: Final = "commissioning.failed"
#: The device's certificate was not accepted. data: reason
COMMISSIONING_ATTESTATION_REJECTED: Final = "commissioning.attestation_rejected"
#: The device is in. data: node_id
COMMISSIONING_COMPLETED: Final = "commissioning.completed"

# --- Home Assistant -----------------------------------------------------------

#: Something that can cut power was switched off. subject: ``entity:<id>``.
#: data: name, origin (person|automation|unknown), by (name of the automation
#: or person, when known)
HA_POWER_OFF: Final = "ha.power_off"
#: subject: ``entity:<id>``. data: name, origin, by
HA_POWER_ON: Final = "ha.power_on"

# --- The add-on itself --------------------------------------------------------

#: A source started or stopped working. subject: ``source:<name>``. data: ok,
#: detail
SOURCE_STATUS: Final = "source.status"
