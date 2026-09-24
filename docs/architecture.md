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
| `matter_server`     | Matter Server WebSocket: node availability, border routers, Thread topology |
| `matter_server_log` | Matter Server add-on log: every commissioning step                |
| `otbr`              | OpenThread Border Router REST: role, partition, leader            |
| `otbr_log`          | OpenThread Border Router add-on log: leader timeouts, foreign partitions |
| `home_assistant`    | Core WebSocket: device names, plugs switched off and who did it   |

Log sources reuse one class, `AddonLogSource`, with a line parser from
`matter_health/parsers/`. A parser turns one log line into zero or one
`Parsed` event; adding support for a new log message is a pattern in its
table.

Only read-only interfaces are used. The Matter Server source sends commands
from a fixed allow-list; the OTBR source never touches the dataset endpoints.

## Rules

A rule (`matter_health/rules/`) subclasses `Rule`, registers under a name and
declares the event kinds it `listens` to. The engine calls `on_event` for
each of them and `on_tick` every 30 s, so a rule can close a situation that
ended without an event of its own ("nothing happened for five minutes").

Rules look back through the store (`ctx.store.events(...)`) to connect what
they see with what happened before. `rules/common.py` holds the questions
several rules ask, such as "was a plug switched off shortly before?".

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
| `GET /api/overview`   | the state right now: sources, Thread, border routers, devices |
| `GET /api/findings`   | findings of the last days, open ones always      |
| `GET /api/events`     | the timeline                                     |
| `GET /api/stream`     | server-sent events: new findings and events      |
| `GET /api/i18n/{lang}`| the UI words for one language                    |

The page (`matter_health/frontend/`) is a few Lit components bundled with
esbuild into the Python package. It follows the language and dark mode of the
Home Assistant frontend it is embedded in.

## Adding things

**A new observation.** Add the event kind to `kinds.py`. If it comes from a
log, add a pattern to the parser; otherwise extend or add a source.

**A new explanation.** Add a rule module, import it in `rules/__init__.py`,
and add its keys to all four files in `matter_health/translations/ui/`. The
page needs no change.

**A new source.** Add a module to `sources/`, register it and import it in
`sources/__init__.py`. If it needs configuration, add an option to
`config.yaml`, the `Options` dataclass and the option translations.
