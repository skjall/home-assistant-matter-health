# Matter Health

Open **Matter Health** from the sidebar. The page shows:

- **Findings**: what went wrong, why, what it meant and what you can do.
  Open a finding to see the whole chain; **Details** shows the technical
  facts behind it.
- **Timeline**: devices and border routers coming and going, plugs switched
  off, pairing attempts.
- **Network**: your border routers, devices that do not respond, and whether
  Matter Health can see everything it needs.

Findings build up over time. Right after installing, the page is quiet
until something happens.

## Options

| Option              | Default   | Meaning                                                 |
|---------------------|-----------|---------------------------------------------------------|
| `retention_days`    | `30`      | How long events and findings are kept.                  |
| `log_level`         | `info`    | How much the add-on writes to its own log.              |
| `matter_server_url` | automatic | Only if the Matter Server does not run as an add-on.    |
| `otbr_url`          | automatic | Only if the border router does not run as an add-on.    |

## What it reads

- The Matter Server add-on: its WebSocket API (read-only commands) and its log.
- The OpenThread Border Router add-on: its REST status and its log.
- Home Assistant: device names, and which plugs were switched off by whom.

It changes nothing. It does not read the Thread network key.

## Pairing failures in more detail

The Matter Server logs every pairing step only at log level `info` or more
detailed. At `warning`, Matter Health still sees that pairing failed, but not
where.
