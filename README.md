# Matter Health

A Home Assistant add-on that explains why Matter and Thread devices
misbehave, in words anyone can follow.

Matter over Thread fails quietly. A plug refuses to pair at "Configuring", a
sensor drops out every other night, a switch reacts a second late. Home
Assistant shows that something is wrong, rarely why. Matter Health watches the
Matter Server, the OpenThread Border Router and Home Assistant at the same
time, connects what happens across them and tells you:

- **why**: "The plug your TV box is on was switched off. The TV box was
  coordinating your Thread network."
- **what happened**: "Your Thread network fell apart for 40 seconds."
- **what it means for you**: "Devices could not be reached, and adding a
  device failed in the meantime."
- **what to do**: "Keep border routers on a plug that is never switched off."

Technical detail is one click away, for whoever wants it.

<p align="center">
  <img src="docs/images/findings.png" width="640" alt="Findings: a Thread network split, told from cause to fix, with the failed pairing and the vanished border router shown as its consequences">
  &nbsp;
  <img src="docs/images/mobile-dark.png" width="200" alt="The same page on a phone in dark mode">
</p>

The screenshots show invented data from `scripts/demo.py`.

## What it recognises

| Situation                         | Example of what you read                                 |
|-----------------------------------|----------------------------------------------------------|
| Thread network splits or reorganises | which border router disappeared, and what switched it off |
| A border router on a switched plug | the plug, once off-gone-on-back has happened twice      |
| Adding a device fails             | the step in plain words, and what usually helps there    |
| A device has a weak connection    | the device, its nearest neighbour and the signal         |
| A device stays unreachable        | whether the network or the power is the likelier reason  |

Every rule is its own module. New knowledge becomes a new rule, without
touching the rest; see [docs/architecture.md](docs/architecture.md).

## Requirements

- Home Assistant OS or Supervised, with the **Matter Server** add-on.
- The **OpenThread Border Router** add-on is optional; without it, Thread
  network events come only from the Matter Server.

## Installation

1. In Home Assistant, open **Settings → Add-ons → Add-on Store**, then
   **⋮ → Repositories**, and add
   `https://github.com/skjall/home-assistant-matter-health`.
2. Install **Matter Health** and start it.
3. Open it from the sidebar.

It starts recording when it starts. Problems that happened before are not
known to it.

## Privacy

Everything stays on your Home Assistant host. Matter Health reads logs and
state; it never changes a device, the network or another add-on. It never
reads the Thread network key.

## Languages

English, German, French, Spanish. The page follows the language set in your
Home Assistant profile.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
