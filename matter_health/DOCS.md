# Matter Health

Open **Matter Health** from the sidebar. The page shows:

- **Findings**: what went wrong, why, what it meant and what you can do.
  Open a finding to see the whole chain; **Details** shows the technical
  facts behind it.
- **Timeline**: devices and border routers coming and going, plugs switched
  off, pairing attempts.
- **Network**: how your devices reach Home Assistant - over Thread, Wi-Fi or
  a cable - devices that do not respond, and whether Matter Health can see
  everything it needs.

## The network picture

There is one picture per way devices connect, drawn alike. Every device is
drawn once, on the way it takes into the network.

**Thread**: bridges (border routers) on your home network, devices that relay
for others on their best path to a bridge, battery devices on the device they
hang on. The many
other radio links between relaying devices are not drawn; pointing at a
relaying device tells how many other ways into the network it has. One with
none depends on a single neighbour.

Good links stay grey. A weak link, a device that does not respond or a device
with an open finding is coloured; **Problems only** hides everything else.
The picture is read from the Matter Server every ten minutes and does not
move in between.

**Wi-Fi**: each device on the access point it is connected to, with its
signal. Pointing at it shows the channel and encryption it uses.

**Ethernet**: wired devices, directly on your home network.

On a phone the pictures are shown as outlines, one bridge or access point at
a time.

Findings build up over time. Right after installing, the page is quiet
until something happens.

## Devices that come and go

Some devices are meant to be away now and then: an appliance plugged in only
when it is used, a button waiting in a drawer without batteries. Matter Health
does not hide them for good; it tells them apart:

- **Comes and goes.** A device that went away and came back by itself several
  times in the last two weeks is expected to do so again. It is reported only
  once it stays away clearly longer than it ever did (at least twice its
  longest absence, and at least 12 hours). You can also mark a device this
  way from its finding; **Always report** on the Network page undoes it.
- **I know.** Hides the current absence of a device. When the device comes
  back and later goes away again, it is reported again.
- **Keeps losing its connection.** A device that drops out briefly four or
  more times a day is reported even though each drop-out heals by itself.

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
- The Supervisor: versions of Home Assistant, its operating system and the
  two add-ons, whether Docker has IPv6, and the IPv6 setting of the primary
  network interface.

It changes nothing. It does not read the Thread network key.

## Pairing failures in more detail

The Matter Server logs every pairing step only at log level `info` or more
detailed. At `warning`, Matter Health still sees that pairing failed, but not
where.
