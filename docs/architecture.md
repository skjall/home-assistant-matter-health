# Architecture

Matter Health watches, remembers and explains. It never changes anything in
the installation it observes.

```
 sources ──events──▶ engine ──▶ store (SQLite)
                        │
                        └──▶ rules ──findings──▶ store ──▶ web API / SSE ──▶ page
```

Sources, rules and the page only meet through two data types, `Event` and
`Finding` (`matter_health/model.py`). That is what keeps each part replaceable:
a new source needs no rule to change, a new rule needs no source to know about
it, and the page renders any finding without knowing which rule wrote it.

## Events

An event is one observation: a kind (`matter_health/kinds.py`), a time, the
source that saw it, an optional subject and a small `data` dict.

Subjects name the thing an event is about, with a prefix per namespace:

| Prefix    | Thing                                      |
|-----------|--------------------------------------------|
| `node:`   | a Matter node, by node ID                  |
| `br:`     | a Thread border router                     |
| `entity:` | a Home Assistant entity (a switch, a plug) |
| `source:` | one of Matter Health's own sources         |

The `NameBook` on the context maps subjects to the names the user knows. Any
source may teach it names; rules and the web API read from it. A subject
without a name falls back to a generic word ("a device") in the UI.

## Sources

A source (`matter_health/sources/`) subclasses `Source`, registers under a
name and implements `run()`, a coroutine that emits events until cancelled.
It calls `connected()` once it can see what it observes. When `run()` raises,
the engine marks the source as down and restarts it after 5 s, doubling up to
five minutes.

| Source              | Observes                                                         |
|---------------------|------------------------------------------------------------------|
| `matter_server`     | Matter Server WebSocket: node availability; attributes and polls for the transports |
| `matter_server_log` | Matter Server add-on log: every commissioning step                |
| `home_assistant`    | Core WebSocket: device names, plugs switched off and who did it   |
| `system`            | Supervisor: versions of the pieces involved, the host's IPv6 settings |

Log sources reuse one class, `AddonLogSource`, with a line parser from
`matter_health/parsers/`. A parser turns one log line into zero or more
`Parsed` events; adding support for a new log message is a pattern in its
table.

The Supervisor starts every log stream with the last lines already written.
Each line comes with its journal time, events carry that time rather than
the moment they were read, and the source stores how far it has read, so a
restart neither loses nor repeats anything.

Only read-only interfaces are used. The Matter Server source sends commands
from a fixed allow-list: its own and those the transports declare. The OTBR
source never touches the dataset endpoints.

## Transports

Matter runs over Thread, Wi-Fi and Ethernet, and what can go wrong differs
completely between them. Each transport is a module of its own in
`matter_health/transports/`, built alike and standing next to the others:

```
transports/
  thread/     border routers, the mesh, the OTBR add-on's API and log,
              and the rules about them
  wifi/       access points and each device's link to its own
  ethernet/   wired devices, straight on the home network
```

A transport subclasses `Transport` and registers under its name. A device
says which one it uses in its Network Commissioning feature map
(`0/49/65532`, one bit per transport); the Matter Server source reads that
and hands each transport the attributes of its devices from the clusters it
declares, such as Wi-Fi diagnostics (`0/54`). A transport may also `poll`
the Matter Server with read-only commands it declares - Thread asks for its
border routers and radio links. It answers two questions for the page, in
words every transport shares: its part of the network `picture` (gateway,
relay, device, sleepy, unknown; links rated strong, medium or weak) and a
`summary` for the overview. Its own sources and rules register like any
other; a rule shared in idea but not in detail, such as a weak link, is a
base class in `rules/` that each transport's rule fills in.

Nothing outside a transport's package knows it exists. A new transport is a
new package, imported in `transports/__init__.py`, with its words under
`transport.<name>` in the UI translations.

### Bridges

A Matter bridge - for Zigbee, Z-Wave or a vendor's own radio - is one node
on some transport; each device behind it is an endpoint of that node with
the Bridged Device Basic Information cluster (`57`). Bridging is therefore
not a transport. `bridges.py` reads those endpoints, and the Matter Server
source follows each as a subject of its own, `node:<node id>:<endpoint>`:
it is added, removed, reachable or not by the bridge's `Reachable`
attribute, and so goes through the same rules as any device. While the
bridge itself is away, only the bridge counts as away. Home Assistant names
such a device by a registry identifier ending in its endpoint instead of
`MatterNodeDevice`. In the picture it hangs on its bridge, marked
`bridged`; its link is not a Matter transport's.

## Rules

A rule (`matter_health/rules/`) subclasses `Rule`, registers under a name and
declares the event kinds it `listens` to. The engine calls `on_event` for
each of them and `on_tick` every 30 s, so a rule can close a situation that
ended without an event of its own ("nothing happened for five minutes").

Rules look back through the store (`ctx.store.events(...)`) to connect what
they see with what happened before. `rules/common.py` holds the questions
several rules ask, such as "was a plug switched off shortly before?" or "was
something updated the day before?".

| Rule            | Where     | Tells                                                  |
|-----------------|-----------|--------------------------------------------------------|
| `offline`       | core      | a device stays unreachable                             |
| `flaky`         | core      | a device keeps dropping out for a moment               |
| `pairing`       | core      | how far adding a device got, why it stopped, what to try |
| `server`        | core      | the Matter Server forgot its devices                   |
| `mesh`          | thread    | the mesh lost its leader or fell apart                 |
| `border_router` | thread    | a border router went away, and whether a switched plug did it |
| `relay`         | thread    | a device that relayed for others went, and took them along |
| `wave`          | thread    | most Thread devices went at the same moment            |
| `signal`        | thread    | devices hear their parent only faintly                 |
| `radio`         | thread    | Home Assistant's own radio: interference, faults, detached |
| `host`          | thread    | the host does not forward IPv6 into the mesh           |
| `wifi_signal`   | wifi      | devices far from their access point                    |

A rule can declare that its findings may be one consequence of another's
(`part_of`): a failed pairing during a mesh split, a device gone with its
relay. `stories.py` nests such findings under the one that explains them.

### What is normal for a device

Some devices are away by habit: a plug that gets unplugged, a button without
batteries. `rules/habits.py` learns from the last two weeks how often and how
long each device is usually away. `offline` stays quiet while an absence is
within that, and says "longer than usual" when it is not. The user can also
say it themselves - "comes and goes" or "always report" - which is kept apart
from what was learned and wins over it.

## Findings

A finding is a rule's conclusion, told as a chain of `Link`s, each with a
role:

- **cause**: why it happened
- **effect**: what happened
- **impact**: what it means for the user
- **fix**: what they can do

A link carries a translation key, the values for its placeholders, a
confidence (`certain`, `likely`, `possible`) and the IDs of the events it
rests on. The rule decides what is known; the translation decides how it is
said. A finding's `key` makes it unique: publishing the same key again
updates it, and while it is open it keeps the time it was first seen.

## Web

`matter_health/web/app.py` serves the page and a small JSON API behind Home
Assistant's ingress; requests from anywhere else are refused.

| Route                 | Returns                                          |
|-----------------------|--------------------------------------------------|
| `GET /api/overview`   | the state right now: sources, devices, each transport's summary |
| `GET /api/findings`   | findings of the last days, open ones always      |
| `GET /api/topology`   | every transport's part of the network, with who is away now |
| `POST /api/dismiss`   | mark a finding as dealt with, or take that back  |
| `POST /api/habit`     | say a device comes and goes, or always report it |
| `GET /api/events`     | the timeline                                     |
| `GET /api/stream`     | server-sent events: new findings and events      |
| `GET /api/languages`  | the languages the UI speaks                      |
| `GET /api/i18n/{lang}`| the UI words for one language                    |

The page (`matter_health/frontend/`) is a few Lit components bundled with
esbuild into the Python package. It follows the language and dark mode of the
Home Assistant frontend it is embedded in. The overview names the build it
belongs to; a page left open across an update reloads itself.

Names of devices are looked up when a finding is shown, not when it is
written: a device renamed after pairing appears under its new name. Where
Home Assistant knows the device, its name leads to the device's page.

## The network picture

Matter is one network whatever carries it, so the page draws one tree: every
transport's gateways - border routers, access points - and its wired devices
hang on the same home network, grouped by transport, and every device has
one line upwards. The colours answer one question at a time, chosen by the
viewer and remembered in their browser: state (the default), transport or
signal strength. A device's state stays on its symbol in every view. The
line from the home network to a gateway is drawn neutral in every view:
whether a border router or access point is wired or on Wi-Fi, nothing the
add-on reads tells, and it is not the transport's link. For Thread that takes work: the Matter Server reports every radio link
it knows, hundreds in a home with a few dozen mains-powered devices.
`transports/thread/tree.py` reduces them to one way in per device: a battery
device hangs on its parent; a relaying device takes the cheapest path to a
border router, priced by link quality the way Thread does. Each relaying
device keeps the number of neighbours it could switch to. A Wi-Fi device
names its access point itself; a wired one hangs on the home network. The
page draws the trees once and does not move; devices away are shown where
they last were.

## Confinement

The add-on runs under its own AppArmor profile (`matter_health/apparmor.txt`).
Python may reach the network, read its code and write only to `/data` and
`/tmp`; it runs no other program and holds no capabilities. The test deploy
copies code in with `docker cp` for that reason: nothing inside the container
may write to its own code.

## Adding things

**A new observation.** Add the event kind to `kinds.py`. If it comes from a
log, add a pattern to the parser; otherwise extend or add a source.

**A new explanation.** Add a rule module, import it in `rules/__init__.py`,
and add its keys to all four files in `matter_health/translations/ui/`. The
page needs no change.

**A new source.** Add a module to `sources/` - or to its transport's
package, if it is about one transport - register it and import it in the
package's `__init__.py`. If it needs configuration, add an option to
`config.yaml`, the `Options` dataclass and the option translations.
