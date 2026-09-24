# Field reports: how Matter and Thread fail in real homes

A collection of publicly reported Matter, Thread and Matter-over-Wi-Fi problems
whose cause was found, grouped by that cause. It exists to decide which rules
Matter Health should have and which log lines they can rely on.

Sources: GitHub issues (matter-js, python-matter-server, home-assistant,
openthread, connectedhomeip, esp-idf, esp-matter), the Home Assistant
Community forum, Reddit (via the Arctic Shift archive), vendor documentation
and blogs. About 220 cases were read; duplicates are merged below. Causes are
those stated in the thread by a maintainer, a vendor, or confirmed by the
reporter. Entries marked *(weak)* rest on one person's impression or on
several changes made at once. Log lines are quoted verbatim.

**Where Matter Health could see it** (the `Detect` line):

| Tag | Source |
|-----|--------|
| `HA` | Home Assistant states, events, config entries, Thread datasets |
| `MS` | Matter Server WebSocket (`server_info`, nodes, `get_thread_border_routers`, topology) |
| `MSL` | Matter Server add-on log |
| `OTBR` | OTBR REST `/node` only (role, partition, leader, router count, ext PAN ID) |
| `OTBRL` | OTBR add-on log |
| `SV` | Supervisor API (`/network/info`, `/docker/info`, `/host/info`, add-on state and version) |
| `none` | Not observable from Home Assistant (phone, switch, access point) |

Matter Health runs without host networking, so `ip -6 route`, `ip -6 neigh`
and host sysctls are **not** directly readable; the Supervisor exposes only
part of that (interface IPv6 method and addresses, Docker `enable_ipv6`).

---

## 1. The host has no route into the Thread mesh

The largest group by far. Thread devices live in their own IPv6 prefix (the
OMR prefix). Border routers announce it in Router Advertisements as a Route
Information Option (RIO). If the host does not install that route, every
packet to a Thread device goes to the default gateway and is lost. Wi-Fi
Matter devices keep working, which makes the pattern recognisable.

**Typical logs**

```
Starting Matter commissioning using Node ID 2 and IP fd99:…
PASESession timed out while waiting for a response from the peer. Expected message type was 33
CHIP_ERROR [chip.native.CTL] Discovery timed out
Failed to advertise records: …UDPEndPointImplSockets.cpp:416: OS Error 0x02000065: Network is unreachable
[network-unreachable] send ENETUNREACH
Operational address found / Establish PASE / Address unreachable
address is unreachable (…:5540)
```

**Cases**

- **RIO ignored: `accept_ra_rt_info_max_plen=0`, or `accept_ra=1` while forwarding is on.**
  NAS, Proxmox LXC, TrueNAS, Unraid, Docker hosts.
  Fix: `accept_ra=2`, `accept_ra_rt_info_max_plen=64`.
  [matterjs-server#911](https://github.com/matter-js/matterjs-server/issues/911),
  [python-matter-server#296](https://github.com/matter-js/python-matter-server/issues/296),
  [HA forum 828006](https://community.home-assistant.io/t/828006),
  [HA forum 909840](https://community.home-assistant.io/t/909840),
  [Reddit 1roljrb](https://www.reddit.com/r/homeassistant/comments/1roljrb/),
  [Reddit 1inehuy](https://www.reddit.com/r/homeassistant/comments/1inehuy/),
  [UGREEN write-up](https://sergeytihon.com/2026/01/03/running-home-assistant-matter-server-on-a-ugreen-nas-a-deep-dive-into-thread-device-commissioning/).
  Detect: `MSL` (PASE timeout to an `fd…` address while Wi-Fi nodes answer). Host sysctls not visible.
- **Kernel too old for RIO** (Synology kernel 4.4). Static route per boot.
  [HA forum 1011565](https://community.home-assistant.io/t/1011565),
  [HA forum 848242](https://community.home-assistant.io/t/848242).
  DSM 7.3 with Open vSwitch drops the route: [matterjs-server#795](https://github.com/matter-js/matterjs-server/issues/795).
- **dhcpcd handles RAs and ignores RIO**, or rewrites the route on every RA from several Apple TVs.
  Fix: let the kernel or systemd-networkd handle RAs.
  [HA forum 971460](https://community.home-assistant.io/t/971460),
  [matterjs-server#940](https://github.com/matter-js/matterjs-server/issues/940).
  Log: `Error sending success after final data report chunk [peer-unresponsive] Peer is no longer responding to active session (timed out after 44.2s)`.
- **Static IPv6 on Home Assistant**, so RAs are not processed. Fix: IPv6 Automatic.
  [majornetwork.net](https://majornetwork.net/2026/01/home-assistant-was-unable-to-add-ikea-matter-devices/).
  Detect: `SV` (`/network/info` ipv6 method `static`).
- **IPv6 disabled in HA or on the hypervisor.**
  [HA forum 945555](https://community.home-assistant.io/t/945555),
  [HA forum 1013449](https://community.home-assistant.io/t/1013449) (`all.disable_ipv6=1` in `99-proxmox.conf`),
  [HA forum 695924](https://community.home-assistant.io/t/695924) *(weak)*.
  Detect: `SV` (ipv6 method `disabled`, or no address besides `fe80`).
- **No SLAAC address, wrong source address.** `ip -6 route get <thread ULA>` picked a Docker-internal ULA as source.
  [Reddit 1v3nglz](https://www.reddit.com/r/homeassistant/comments/1v3nglz/).
- **LAN has only link-local IPv6** (router cannot announce a ULA). Fix: radvd or a router announcing a ULA `/64`.
  [HA forum 1004018](https://community.home-assistant.io/t/1004018),
  [HA forum 731906](https://community.home-assistant.io/t/731906),
  [Reddit 1l4sq9y](https://www.reddit.com/r/Ubiquiti/comments/1l4sq9y/).
  Detect: `SV` (interface without ULA or global address).
- **Border router on another VLAN.** RAs are link-scoped; HA never hears them.
  Fix: a second, IPv6-only interface in that VLAN.
  [HA forum 1017793](https://community.home-assistant.io/t/1017793),
  [Reddit 1qhdi7c](https://www.reddit.com/r/homeassistant/comments/1qhdi7c/),
  [HA forum 940351](https://community.home-assistant.io/t/940351).
  Detect: `MS` border router list empty or foreign while the phone sees one; `MSL` unreachable.
- **Route expires and is not renewed** (HomePod as the only border router, 20–30 min cycle). *(open)*
  [HA forum 1011614](https://community.home-assistant.io/t/1011614).
  Detect: `HA` every Thread node unavailable in the same second, `MSL` `address is unreachable`, border routers still listed in `MS`.
- **Stale route to a border router that is gone.** RIO lifetime is up to 1800 s; without router reachability probing the kernel keeps using a dead next hop for up to 30 min. Fixed by a kernel patch in HAOS 11.1; NetworkManager 1.34 lost track of routes with several RIOs (fixed ≥1.40.10).
  [derekseaman.com](https://www.derekseaman.com/2023/10/part-3-smart-home-matter-and-thread-deep-dive.html),
  [operating-system#2367](https://github.com/home-assistant/operating-system/issues/2367).
  A crashed network-attached RCP leaves the same 30 min gap ([OTBR DOCS](https://github.com/home-assistant/addons/blob/master/openthread_border_router/DOCS.md)).
- **Stale OMR prefix in the RA after the dataset was re-formed** (SLZB firmware, confirmed and fixed by the vendor).
  [HA forum 1019472](https://community.home-assistant.io/t/1019472).
  Log: `[network-unreachable] send ENETUNREACH`. Detect: `MSL` ENETUNREACH to a prefix that differs from the current node addresses.
- **Router re-announces prefix changes** (ISP prefix delegation in router firmware breaks local IPv6).
  [Reddit 1ovjsbn](https://www.reddit.com/r/MatterProtocol/comments/1ovjsbn/),
  [Reddit 1qs3bly](https://www.reddit.com/r/homeassistant/comments/1qs3bly/) (unavailable after every router reboot).
  Detect: `HA` unavailable waves that line up with router reboots.
- **OTBR injects `fc00::/7` via `wpan0`**, so replies to LAN ULA addresses go into the mesh.
  [riddix guide](https://riddix.github.io/home-assistant-matter-hub/guides/connectivity-issues).
- **Thread network re-formed with a new prefix**; phone or host keeps a stale source address.
  [connectedhomeip#25526](https://github.com/project-chip/connectedhomeip/issues/25526).
  Log: `Performing next commissioning step 'FindOperational'` without progress.

## 2. Forwarding off or a firewall in the way

The route exists, but replies from the mesh are dropped on the host.

- **HAOS 17.2+: Docker IPv6 not enabled, so `net.ipv6.conf.all.forwarding=0`.**
  Existing devices keep working (direct `wpan0` route), new ones fail at
  "Checking Thread connection". Fix: `ha docker options --enable-ipv6=true`, then a full host reboot.
  [HA forum 1006775](https://community.home-assistant.io/t/1006775),
  [HA forum 1006114](https://community.home-assistant.io/t/1006114),
  [HA forum 1020295](https://community.home-assistant.io/t/1020295),
  [HA forum 1002896](https://community.home-assistant.io/t/1002896),
  [Reddit 1sih2xf](https://www.reddit.com/r/homeassistant/comments/1sih2xf/),
  operating-system#4630. HAOS 18.3 forces forwarding off again: [operating-system#5030](https://github.com/home-assistant/operating-system/issues/5030) *(open)*.
  Log (OTBR start): `WARNING: IPv6 routing/forwarding is not enabled! Make sure the Home Assistant host has IPv6 forwarding enabled.`
  Detect: `OTBRL` (that line), `SV` (`/docker/info` `enable_ipv6` not true). **Strong, cheap signal.**
- **Forwarding sysctl lost after reboot** (OTBR in Docker on Debian).
  [HA forum 953420](https://community.home-assistant.io/t/953420),
  [HA forum 1013784](https://community.home-assistant.io/t/1013784).
- **ip6tables FORWARD policy DROP** (Docker default; otbr-agent only adds inbound rules).
  [ot-br-posix#3515](https://github.com/openthread/ot-br-posix/issues/3515),
  [Reddit 1r6as8k](https://www.reddit.com/r/homeassistant/comments/1r6as8k/).
- **Stateful firewall drops the first report after a quiet period** (conntrack UDP timeout 120 s, sleepy devices report every 15–30 min).
  ufw, firewalld, Proxmox VM firewall, NixOS. HAOS is not affected.
  Fix: raise `nf_conntrack_udp_timeout_stream` or allow `wpan0`.
  [matterjs-server#526](https://github.com/matter-js/matterjs-server/issues/526),
  [HA forum 1004932](https://community.home-assistant.io/t/1004932).
  Log: `Subscription successful « @1:1c•04d0⇵8c78 2↔2 id: 25ca5ec9 interval: 15m timeout: 15m 39s`, then a timeout at exactly that moment, every cycle.
  Detect: `MSL` subscription timeouts at `interval + ~39 s` for sleepy nodes while `OTBR` stays stable; `SV` host is not HAOS.
- **Router firewall blocks ICMPv6** (UDM Pro), so RA and neighbour discovery never arrive.
  [HA forum 578184](https://community.home-assistant.io/t/578184).

## 3. Multicast and mDNS broken on the LAN

Matter finds devices by mDNS. Pairing may work once, then devices fall away
when their records expire, or discovery never succeeds.

**Typical logs**

```
Timeout waiting for mDNS resolution.
Mdns: Resolve failure (kDNSServiceErr_Timeout)
CHIP Error 0x00000032: Timeout
CHIP_ERROR [chip.native.DIS] DNSSD packet parsing failed (for SRV records)
Resolving(no address known)
```

- **Wi-Fi controller "multicast enhancement", multicast-to-unicast, IGMP/MLD snooping, multicast filtering.**
  UniFi, Ruckus and others. UniFi release note: "The Matter protocol may be blocked when Multicast Filtering is set to Auto."
  [core#91459](https://github.com/home-assistant/core/issues/91459),
  [HA forum 802895](https://community.home-assistant.io/t/802895),
  [HA forum 837020](https://community.home-assistant.io/t/837020),
  [Reddit 1nyziau](https://www.reddit.com/r/Ubiquiti/comments/1nyziau/),
  [Reddit 1nt3crw](https://www.reddit.com/r/HomeKit/comments/1nt3crw/),
  [1home docs](https://www.1home.io/docs/en/server/matter-networking).
  Detect: `MSL` mDNS timeouts after `Established secure session`, while routes are fine; `HA` Wi-Fi nodes unavailable together after an access point update.
- **Switch without working MLD snooping**, or buggy switch firmware.
  [python-matter-server#1108](https://github.com/matter-js/python-matter-server/issues/1108) *(weak)*,
  [HA forum 623502](https://community.home-assistant.io/t/623502).
- **Proxmox bridge filters IPv6 multicast** (`multicast_snooping` on by default). Fix: `bridge-mcsnoop no`.
  [HA forum 990037](https://community.home-assistant.io/t/990037).
  Detect: `SV` host runs virtualised; `MSL` CASE timeouts while `OTBR` role is stable.
- **mDNS reflectors** duplicate or garble IPv6 mDNS (UniFi mDNS, pfSense avahi, AirCast add-on).
  [core#105790](https://github.com/home-assistant/core/issues/105790),
  [python-matter-server#323](https://github.com/matter-js/python-matter-server/issues/323),
  [addons#4634](https://github.com/home-assistant/addons/issues/4634) (host renames itself after hearing its own name back).
  Detect: `MSL` rate of `DNSSD packet parsing failed`, correlated with `SV` add-on starts.
- **The opposite: mDNS proxy off across wired and wireless.** UniFi blocked mDNS from Ethernet to Wi-Fi even in one VLAN; enabling its mDNS proxy (Auto, or Custom with `_matter._tcp`, `_matterc._udp`, `_matterd._udp`) fixed pairing.
  [Reddit 1uuc94t](https://www.reddit.com/r/homeassistant/comments/1uuc94t/),
  [Reddit 1uep7yb](https://www.reddit.com/r/homeassistant/comments/1uep7yb/),
  [Reddit 1w86jjl](https://www.reddit.com/r/homeassistant/comments/1w86jjl/).
- **mDNS storm** from HA's own avahi plus router, high CPU in the Matter Server.
  [addons#4676](https://github.com/home-assistant/addons/issues/4676). Fixed in Matter Server 9.0.3/9.0.4.
  Detect: `SV` add-on CPU.
- **VLANs between HA and Wi-Fi Matter devices, or client isolation.** Unsupported by design; commissioning over VLANs hands HA a link-local address.
  [core#154522](https://github.com/home-assistant/core/issues/154522),
  [HA forum 908308](https://community.home-assistant.io/t/908308),
  [Reddit 1j3khey](https://www.reddit.com/r/SmartThings/comments/1j3khey/),
  [Reddit 1iwhhie](https://www.reddit.com/r/MatterProtocol/comments/1iwhhie/).
  Log: `Starting Matter commissioning using Node ID 1 and IP fe80::…%end0.` Detect: `MSL` (`fe80::` in that line, then timeout).
- **HA VM bridged to the host's Wi-Fi.** mDNS does not pass. Fix: bridge to Ethernet.
  [Reddit 1v58riy](https://www.reddit.com/r/homeassistant/comments/1v58riy/).
- **Two active host interfaces** (Wi-Fi and Ethernet): OTBR sends mDNS on the wrong one.
  [HA forum 1010466](https://community.home-assistant.io/t/1010466).
  Log: `mDNSPlatformSendUDP got error 99 (Cannot assign requested address) sending packet to ff02::fb`.
  On Docker virtual interfaces the same line is harmless noise ([HA forum 904091](https://community.home-assistant.io/t/904091)).
  Detect: `SV` more than one connected interface; `OTBRL` error 99 on a physical interface.
- **DHCP domain `.local`** breaks mDNS ([1home docs](https://www.1home.io/docs/en/server/matter-networking)).
- **Python Matter Server used mDNS only at start-up** and forgot addresses; devices stayed offline after every reboot until opened in the UI. Fixed by the matter.js server.
  [addons#4214](https://github.com/home-assistant/addons/issues/4214).

## 4. Several Thread networks, or border routers that disagree

- **Separate networks side by side.** Devices cannot move between them; a device dies with "its" network's border router.
  Aqara hubs and cameras, SmartThings, Nest, Eero, ESP border routers and a re-formed HA network all create their own.
  [Thread docs](https://www.home-assistant.io/integrations/thread/),
  [HA forum 971460](https://community.home-assistant.io/t/971460),
  [Reddit 1t8w5sv](https://www.reddit.com/r/HomeKit/comments/1t8w5sv/),
  [Reddit 1ql11bl](https://www.reddit.com/r/homeassistant/comments/1ql11bl/),
  [Reddit 1mveur3](https://www.reddit.com/r/homeautomation/comments/1mveur3/).
  Detect: `MS` border routers with more than one network name or extended PAN ID. **Already shown as "Andere Thread-Netze".**
- **Apple keeps forming new `MyHome<digits>` networks**; devices stay on the old one. *(unsolved for the reporter)*
  [Reddit 1g32wp8](https://www.reddit.com/r/HomeKit/comments/1g32wp8/),
  [core#147898](https://github.com/home-assistant/core/issues/147898) (two `MyHome…` networks, credentials only for the stale one; log `Failed to decode state bitmap`).
  Detect: `MS` a new network name appearing among Apple border routers.
- **A neighbour's Apple home** (a van with HomePods parked outside) knocked devices out within 1–5 min.
  [Apple discussions 255307824](https://discussions.apple.com/thread/255307824).
  Detect: `MS` a foreign network appears, together with an unavailable wave in `HA`.
- **Mixing border router vendors on one network makes it flaky.** Apple plus Eero, Apple plus Nest Hub, Nanoleaf plus Nest. Turning Thread off on one vendor fixed it.
  [Reddit 1r3b8tx](https://www.reddit.com/r/homeassistant/comments/1r3b8tx/),
  [HA forum 915890](https://community.home-assistant.io/t/915890),
  [Reddit 1s0gals](https://www.reddit.com/r/googlehome/comments/1s0gals/).
  Log: `CASESession timed out while waiting for a response from peer <000000000000003E, 1>. Current state was 4`.
  Detect: `MS` border router vendors; `MSL` CASE timeouts across many nodes starting when one appears.
- **A Dirigera joining the mesh triggers leader changes**; booting Apple border routers first avoided it. Removing the Dirigera as border router fixed random unavailability for others.
  [Reddit 1s66o0k](https://www.reddit.com/r/HomeKit/comments/1s66o0k/).
  Detect: `OTBR` leader or partition change within minutes of `MS` border router appearance. **Covered by the partition rule.**
- **Partitions that cannot merge.** After losing the leader, routers drop parent requests until NETWORK_ID_TIMEOUT (~120 s); a weak link between partitions on the same channel and PAN ID can flap Leader↔Detached every 5–10 s.
  [openthread#4751](https://github.com/openthread/openthread/issues/4751),
  [openthread#13138](https://github.com/openthread/openthread/issues/13138).
  Log: `Failed to process Parent Request: Drop`, `Processing Announce - channel 4, panid 0x4444`.
  Detect: `OTBR` role changes more than once a minute; `MS` border routers of one network reporting different partition IDs for more than 120 s.
- **ESP border routers announce role bits 0 (detached) while leader**, so other controllers reject them.
  [esp-idf#18721](https://github.com/espressif/esp-idf/issues/18721).
  Detect: `MS` state bitmap of a border router says detached while it is the leader.
- **TREL peers never rediscovered after a border router restart** (single PTR query).
  [esp-idf#19017](https://github.com/espressif/esp-idf/issues/19017).

## 5. A border router misbehaves

- **Dirigera stopped routing after ~2 min idle.** Sleepy IKEA devices stop reporting to HA but work in the IKEA app. Fixed in Dirigera firmware 2.934.5.
  [matterjs-server#187](https://github.com/matter-js/matterjs-server/issues/187),
  [core#158954](https://github.com/home-assistant/core/issues/158954),
  [Reddit 1py3h1m](https://www.reddit.com/r/IKEA/comments/1py3h1m/).
  Log: `Subscription Liveness timeout with SubscriptionID = 0xcf7b37b7, Peer = 01:0000000000000006`, `<Node:6> Re-Subscription succeeded` (and still no data).
  Detect: `MSL` recurring liveness timeouts for sleepy nodes; `MS` a Dirigera among the border routers.
- **Apple border routers break OTA updates** from HA. Fix: matter.js OTA provider, or power the Apple ones down ≥30 min.
  [core#131939](https://github.com/home-assistant/core/issues/131939),
  [HA forum 976445](https://community.home-assistant.io/t/976445),
  [HA forum 883435](https://community.home-assistant.io/t/883435) (BDX fails through intermediate routers).
  Log: `Error updating: Target node did not process the update file`; `Update state changed from <UpdateStateEnum.kDownloading: 4> to <UpdateStateEnum.kIdle: 1>`.
  Detect: `HA` update entity error; `MSL` that line; `MS` Apple border routers present.
- **Apple TV with a badly shielded HDMI cable** jammed Thread whenever the TV was on.
  [Reddit 1v6kdgd](https://www.reddit.com/r/HomeKit/comments/1v6kdgd/).
  Detect: `HA` unavailable wave in step with the TV's `media_player` turning on.
- **Apple TV wired to a mesh repeater** instead of the main router.
  [Reddit 1prbbst](https://www.reddit.com/r/HomeKit/comments/1prbbst/),
  [Reddit 1qdyqyx](https://www.reddit.com/r/homeassistant/comments/1qdyqyx/).
- **Hub without a Thread radio picked as home hub** (Apple TV 4K 1st gen, original HomePod).
  [Reddit 1evsk00](https://www.reddit.com/r/HomeKit/comments/1evsk00/),
  [Apple discussions 254933864](https://discussions.apple.com/thread/254933864).
  Detect: `none` (Apple hub choice is invisible).
- **tvOS/HomePod 26 regression**, fixed in 26.1. [Apple discussions 256138785](https://discussions.apple.com/thread/256138785).
- **Border router needs a power cycle after HA restarts** (Google TV Streamer), or a reboot of the active Apple hub.
  [Reddit 1tnqj64](https://www.reddit.com/r/homeassistant/comments/1tnqj64/),
  [Reddit 1wknxbv](https://www.reddit.com/r/homeassistant/comments/1wknxbv/).
- **Nest Hub stops announcing itself**; unplugging it fixed the mesh.
  [Reddit 18cc3wk](https://www.reddit.com/r/googlehome/comments/18cc3wk/).
  Detect: `MS` border router gone and back repeatedly. **Covered by the border router rule.**
- **Border router powered off by a switch or plug.** Partition, leader loss, pairing fails. Not in any public report found, but matches the timeline in [matterjs-server#173](https://github.com/matter-js/matterjs-server/issues/173) (the prefix changes when a border router drops out). **Covered by the switched border router rule.**

## 6. OTBR, radio and OpenThread

**Typical logs**

```
[W] P-RadioSpinel-: radio tx timeout
[W] P-RadioSpinel-: RCP failure detected
Failed to communicate with RCP - no response from RCP during initialization
[W] P-SpinelDrive-: Wait for response timeout
Failed to send SpinelFrame(…) trying again in 0.10s (attempt 4 of 4)
Error processing result: NoBufs
Handle transmit done failed: ChannelAccessFailure
Dropping rx frag frame, error:Drop / ReassemblyTimeout
Error decoding hdlc frame: Parse
```

- **RCP firmware wrong or unstable**, UART flow control missing, network-attached adapter not answering.
  [addons#3810](https://github.com/home-assistant/addons/issues/3810),
  [addons#3683](https://github.com/home-assistant/addons/issues/3683),
  [addons#4210](https://github.com/home-assistant/addons/issues/4210),
  [Reddit 1qq73qq](https://www.reddit.com/r/homeassistant/comments/1qq73qq/).
  Detect: `OTBRL` those lines; `SV` add-on stopped or restarting; `OTBR` unreachable.
- **Degraded radio stick buffers run out** (ConBee II): `NoBufs`. Replacing the adapter fixed it.
  [HA forum 962416](https://community.home-assistant.io/t/962416).
- **OTBR add-on regressions**: 2.9.0 (nRF), 2.10.0 (Eve Motion lost), 2.15.0 (network adapters), 2.16.2 (start race).
  [addons#4409](https://github.com/home-assistant/addons/issues/4409),
  [Reddit 1f27jq2](https://www.reddit.com/r/homeassistant/comments/1f27jq2/).
  Detect: `SV` OTBR version change right before the problem started.
- **`Too many open files`** on large meshes (HAOS 16 lowered the limit to 1024).
  [addons#4501](https://github.com/home-assistant/addons/issues/4501) *(weak)*.
  Log: `otPlatInfraIfHasAddress() at infra_if.cpp:70: Too many open files`, `otbr-agent exited with code 5`.
- **HAOS 17.x netlink errors leave the OTBR detached.** Fixed in HAOS 18.
  [core#170452](https://github.com/home-assistant/core/issues/170452).
  Log: `[W] P-Netif-------: Failed to process request#2: No such process`, `Role disabled -> detached`.
  Detect: `OTBR` state `detached`; `SV` host OS 17.x.
- **Dataset lost on restart** (settings written to tmpfs): `OTBR` state `disabled`. [ot-br-posix#3181](https://github.com/openthread/ot-br-posix/issues/3181).
- **otbr-agent segfault triggered by REST diagnostics requests.** [ot-br-posix#3264](https://github.com/openthread/ot-br-posix/issues/3264). **Reason Matter Health reads `/node` only.**
- **100 % CPU in the mDNS send loop**, IPv6 mDNS blocked. [openthread#13601](https://github.com/openthread/openthread/issues/13601). Detect: `SV` add-on CPU.
- **SRP server drops updates** (fragmented, or `_matterc._udp` next to `_matter._tcp`).
  [openthread#7742](https://github.com/openthread/openthread/issues/7742),
  [openthread#13607](https://github.com/openthread/openthread/issues/13607).
  Log: `[W] SrpServer-----: Failed to process DNS Update section: Parse`, `Failed to handle DNS message - Drop`.
  An SRP timeout ends in the fail-safe: `SRP update error: timed out waiting on server response`, `Failsafe timer expired` ([Nordic devzone](https://devzone.nordicsemi.com/f/nordic-q-a/109251/matter-commissioning-fail-with-srp)).
- **Leader keeps a phantom route** in network data. [openthread#9415](https://github.com/openthread/openthread/issues/9415).
- **OTBR integration fails on new TLV types** after Thread 1.4.
  [core#161912](https://github.com/home-assistant/core/issues/161912).
  Log: `ValueError: 218 is not a valid MeshcopTLVType`. Detect: `HA` config entry `otbr` in setup error.
- **Harmless:** `Got dataset with same extended PAN ID and same or older active timestamp` ([core#134306](https://github.com/home-assistant/core/issues/134306)).

## 7. Radio conditions and mesh shape

- **2.4 GHz interference.** Wi-Fi on 1/6/11, Zigbee on the same channel, BLE proxy on the same chip, a backup job on Wi-Fi 13 every 6 hours.
  Fix: choose channels apart (example: Wi-Fi 1, Zigbee 20, Thread 25).
  [core#123835](https://github.com/home-assistant/core/issues/123835),
  [matterjs-server#173](https://github.com/matter-js/matterjs-server/issues/173),
  [HA forum 936346](https://community.home-assistant.io/t/936346),
  [esphome#7138](https://github.com/esphome/issues/issues/7138),
  [Reddit 1r4svtp](https://www.reddit.com/r/homeassistant/comments/1r4svtp/),
  [Reddit 1vdmk80](https://www.reddit.com/r/homeassistant/comments/1vdmk80/).
  `ChannelAccessFailure` is logged after 16 failed attempts; occasional is normal, several per minute means interference.
  Detect: `OTBRL` `ChannelAccessFailure` rate; `HA` periodicity of unavailable waves.
- **Losing a mains-powered relay** takes its sleepy children with it.
  [tarahome guide](https://tarahome.ai/blog/home-assistant-matter-thread-devices-offline/),
  [HA forum 1006018](https://community.home-assistant.io/t/1006018) *(weak)*.
  Detect: `MS` topology: nodes failing together share a parent that disappeared. **Candidate rule.**
- **A mains-powered device joined as end device, not router**, so the mesh has a hole.
  [HA forum 1008040](https://community.home-assistant.io/t/1008040).
  Detect: `MS` topology: a powered device in a child role. **Candidate rule.**
- **Marginal signal plus parent changes.** Target about −73 dBm or better.
  [HA forum 979203](https://community.home-assistant.io/t/979203),
  [core#118715](https://github.com/home-assistant/core/issues/118715).
  Log: `Failed to Send CHIP MessageCounter:128639958 … sendCount: 4 max retries: 4`.
  Detect: `MS` topology RSSI/LQI. **Covered by the weak signal rule.**
- **Too few routers or a border router too far away**; pairing works only next to the border router.
  [Reddit 1djhbw6](https://www.reddit.com/r/HomeKit/comments/1djhbw6/),
  [Reddit 1swo1ec](https://www.reddit.com/r/homeassistant/comments/1swo1ec/).
- **Faulty devices dragging the mesh down** (one Onvis and one Eve plug). [Reddit 1nsoh5c](https://www.reddit.com/r/HomeKit/comments/1nsoh5c/).
- **Device leaves the mesh every few weeks, needs a power cycle** (IKEA TIMMERFLOTTE, ALPSTUGA; Aqara W100 stays attached but stops answering).
  [matterjs-server#411](https://github.com/matter-js/matterjs-server/issues/411),
  [Reddit 1rfooqu](https://www.reddit.com/r/homeassistant/comments/1rfooqu/).
  Sequence: subscription timeout, node offline, mDNS records expire ~10 min later. **Covered by the offline rule.**

## 8. Matter Server storage and versions

- **Corrupt `chip.json` after power loss**, or the add-on's `/data` deleted by a reinstall: every node gone.
  [python-matter-server#977](https://github.com/matter-js/python-matter-server/issues/977),
  [HA forum 734774](https://community.home-assistant.io/t/734774).
  Log: `CRITICAL [chip.storage] Could not load configuration from /data/chip.json - resetting configuration...`, `Loaded 0 nodes from stored configuration`.
  Detect: `MSL` that line; `MS` node count drops to 0 while `HA` still has Matter devices. **Candidate rule.**
- **Leftover lock files** after a hard stop: crash loop.
  [matterjs-server#452](https://github.com/matter-js/matterjs-server/issues/452). Log: `[storage-lock] Storage is locked by another process (pid 1)`.
- **Failed migration** from the Python server: `Fabric index #1 does not exist` ([matterjs-server#355](https://github.com/matter-js/matterjs-server/issues/355)), or `No device could be commissioned` after a botched data move ([Reddit 1v0sau1](https://www.reddit.com/r/homeassistant/comments/1v0sau1/)).
- **Version regressions**: python-matter-server 5.1 (nodes marked unavailable too soon), 6.1.0 (attestation), 8.1.1, 9.0.4 (delayed state changes), HA 2024.8.1.
  [core#110218](https://github.com/home-assistant/core/issues/110218),
  [python-matter-server#729](https://github.com/matter-js/python-matter-server/issues/729),
  [addons#4178](https://github.com/home-assistant/addons/issues/4178),
  [Reddit 1uqgqjb](https://www.reddit.com/r/homeassistant/comments/1uqgqjb/).
  Detect: `SV` Matter Server version change followed by more unavailability. **Candidate rule: "since the update".**
- **Offline every N hours on the dot** with the Python server; moving to matter.js fixed it.
  [HA forum 936346](https://community.home-assistant.io/t/936346),
  [HA forum 590222](https://community.home-assistant.io/t/590222).
- **IP address confusion after an OMR prefix change** (old addresses kept). Fixed in matter.js 0.3.x.
  [matterjs-server#170](https://github.com/matter-js/matterjs-server/issues/170).
  Log: `ENETUNREACH fd37:5639:3134:1:…:5540`.
- **Time sync not enabled** (ALPSTUGA `timeSynchronization.timeFailure`). [matterjs-server#934](https://github.com/matter-js/matterjs-server/issues/934).
- **PAA certificate download times out at start** (GitHub down, or broken IPv6 egress). [python-matter-server#284](https://github.com/matter-js/python-matter-server/issues/284).
- **Wrong primary interface in Docker**: `Using 'None' as primary interface (for link-local addresses)`. [HA forum 983030](https://community.home-assistant.io/t/983030).

## 9. Pairing fails before Home Assistant is involved

The phone does Bluetooth, attestation and Thread credentials alone. If it
fails there, the Matter Server never logs a `commission_with_code`. That
silence is the signature.

- **Phone has no or stale Thread credentials.** "Sync Thread credentials" in the Companion app, clear Google Play services storage, delete stale iCloud Keychain entries.
  [HA forum 638332](https://community.home-assistant.io/t/638332),
  [HA forum 904091](https://community.home-assistant.io/t/904091),
  [HA forum 847644](https://community.home-assistant.io/t/847644),
  [Reddit 1p3f55a](https://www.reddit.com/r/homeassistant/comments/1p3f55a/),
  [Google troubleshooting](https://developers.home.google.com/matter/troubleshooting).
  Messages: "Can't connect to thread network home-assistant", "Thread network credentials does not match with any of the active thread networks around".
- **Phone prefers another network than HA** (NEST-PAN vs ha-thread). [HA forum 969483](https://community.home-assistant.io/t/969483).
  Detect: `HA` preferred dataset network differs from the network of most `MS` border routers.
- **Only the Apple Home owner can share Thread credentials.** [Reddit 1tzz1b4](https://www.reddit.com/r/HomeKit/comments/1tzz1b4/).
- **Phone model or OS cannot commission** (some Android builds, GrapheneOS, F-Droid app, old iOS).
  [android#5604](https://github.com/home-assistant/android/issues/5604),
  [Reddit 1qq73qq](https://www.reddit.com/r/homeassistant/comments/1qq73qq/),
  [HA forum 565543](https://community.home-assistant.io/t/565543).
- **Bluetooth stuck on the phone**; toggling radios fixed it. [Reddit 1py3h1m](https://www.reddit.com/r/IKEA/comments/1py3h1m/).
- **Leftover Apple Home entry** produced the iOS error, not HA. [HA forum 888723](https://community.home-assistant.io/t/888723).
- **Workaround used by many:** commission from the Matter Server itself over its Bluetooth (BLE proxy), after `set_thread_dataset`.
  [HA forum 777011](https://community.home-assistant.io/t/777011),
  [Reddit 1vdmk80](https://www.reddit.com/r/homeassistant/comments/1vdmk80/).

Detect for this whole group: an attempt the user made (Companion app) with no
matching `MSL` entry. Matter Health cannot see the attempt itself, so the most
it can say is "nothing reached Home Assistant in that period".

## 10. Pairing fails inside the Matter Server

The Matter Server logs every failed command as ERROR with the cause:
`WebSocket error response (commission_with_code) … Commission failed: <cause>`.

| Cause string | Meaning | Source |
|---|---|---|
| `No Wi-Fi/Thread network credentials are configured for commissioning …` | the server has no Thread dataset (`server_info.thread_credentials_set: false`) | [Reddit 1vdmk80](https://www.reddit.com/r/homeassistant/comments/1vdmk80/), [HA forum 777011](https://community.home-assistant.io/t/777011) |
| `discovery of node with discriminator 9 failed: No commissionable device was discovered` | device not found by mDNS; with `network_only` BLE is never tried | [matterjs-server#524](https://github.com/matter-js/matterjs-server/issues/524), [#904](https://github.com/matter-js/matterjs-server/issues/904) |
| `PAA not found in trust store for authority key identifier …` | test/dev device | [matterjs-server#657](https://github.com/matter-js/matterjs-server/issues/657) |
| `Failed Device Attestation`, `err 604` | vendor ID mismatch between certificate and device | [esp-matter#1764](https://github.com/espressif/esp-matter/issues/1764) |
| `Commission error for "addNoc": InvalidNoc (3)` | device-side signature check failed | [matterjs-server#588](https://github.com/matter-js/matterjs-server/issues/588) |
| `Trying to add a NOC for a fabric that already exists` | device still in the old fabric after an add-on reinstall | [Reddit 1kq482b](https://www.reddit.com/r/homeassistant/comments/1kq482b/) |
| `[ble-channel-closed]` after AddNOC | non-compliant Wi-Fi device drops BLE early | [matterjs-server#918](https://github.com/matter-js/matterjs-server/issues/918) |
| `[peer-unresponsive]` over `tcp://` | Matter 1.4 device announced TCP it did not support | [matterjs-server#735](https://github.com/matter-js/matterjs-server/issues/735) |
| `Failsafe timer expired` | steps took longer than the fail-safe (often SRP) | [connectedhomeip#30151](https://github.com/project-chip/connectedhomeip/issues/30151) |

Also: a PASE response overtaken by a small ack was discarded as a duplicate
(`SC/PbkdfParamResponse … reqAck dup`), fixed in matter.js 0.17.6
([matterjs-server#886](https://github.com/matter-js/matterjs-server/issues/886)).

Detect: `MSL`. **Covered by the pairing rule**; the cause strings above are
worth mapping to plain-language explanations.

## 11. Matter over Wi-Fi

- **2.4 GHz only.** Band steering, WPA3-only, SSID or password changes drop devices.
  [HA Matter docs](https://www.home-assistant.io/integrations/matter/),
  [HA forum 623199](https://community.home-assistant.io/t/623199).
- **DTIM 1 and "Enhanced IoT Connectivity"** caused drops after days; DTIM 2–3 fixed it. "IoT Auto Discovery" multicast forwarding caused broadcast storms.
  [Reddit 1p1xm3j](https://www.reddit.com/r/Ubiquiti/comments/1p1xm3j/).
- **DHCPv6 stateful mode** made Tapo switches collect hundreds of addresses. [Reddit 1v4wn7f](https://www.reddit.com/r/MatterProtocol/comments/1v4wn7f/).
- **Firewall between VLANs** made devices "no response" after 5 min. [Reddit 1hik7rg](https://www.reddit.com/r/HomeKit/comments/1hik7rg/).
- **Powered-off Wi-Fi devices never reconnected** with the Python server. [python-matter-server#354](https://github.com/matter-js/python-matter-server/issues/354).

## 12. Device firmware

| Device | Problem | Fix | Source |
|---|---|---|---|
| IKEA MYGGBETT/MYGGSPRAY | drop after hours, battery pull needed | IKEA firmware, still partly open | [core#162230](https://github.com/home-assistant/core/issues/162230) |
| IKEA KLIPPBOK | a third dropped off | firmware 1.0.13 | [Reddit 1t4h4td](https://www.reddit.com/r/homeassistant/comments/1t4h4td/) |
| Nanoleaf | no update entity | app update to ≥3.6.196 | [Reddit 1hhdy3g](https://www.reddit.com/r/homeassistant/comments/1hhdy3g/) |
| Leviton | would not pair | firmware 2.3.7 | [Reddit 1ggri51](https://www.reddit.com/r/homeassistant/comments/1ggri51/) |
| Onvis S4 | no response since iOS 17.4 | vendor firmware | [Reddit 1d47eu9](https://www.reddit.com/r/HomeKit/comments/1d47eu9/) |
| Nuki | picky about radio, reconnect fails | border router closer | [core#118715](https://github.com/home-assistant/core/issues/118715) |
| ESPHome Thread device | fails to attach with BLE proxy on | no BLE proxy on Thread chips | [esphome#7138](https://github.com/esphome/issues/issues/7138) |

---

## What this means for Matter Health

**Already covered:** leader loss and partition changes, border routers going
and coming (including a switched one), foreign Thread networks, unreachable
devices, weak links, pairing phases with their failure cause.

**Cheap to add, high value** (data already reachable):

1. **IPv6 forwarding off.** `OTBRL` line `IPv6 routing/forwarding is not enabled!`
   plus `SV` `/docker/info` `enable_ipv6`. This was the most-reported single cause of
   2026 (HAOS 17.2 and 18.3), and it has an exact fix.
2. **Every Thread node unreachable at once while border routers are still there.**
   A route problem on the host, not the mesh. `HA` unavailable wave limited to
   Thread nodes, `MSL` `address is unreachable` / `ENETUNREACH`, `MS` border routers
   unchanged. Distinguishes "network" from "devices".
3. **Radio interference.** `OTBRL` `ChannelAccessFailure` rate above one per minute.
4. **Radio stick failing.** `OTBRL` `RCP failure detected`, `NoBufs`, `Wait for response timeout`, `Failed to communicate with RCP`.
5. **Commission failure causes mapped to explanations** (table in section 10),
   in particular `thread_credentials_set: false`.
6. **Relay lost:** several nodes unreachable together that share a parent in the last topology.
7. **Mains-powered device acting as end device** instead of router.
8. **Matter Server lost its data:** node count to 0, `Could not load configuration`.
9. **"Since the update":** a Matter Server, OTBR or HAOS version change right before a problem started (`SV`).
10. **Host IPv6 set to static or disabled** (`SV` `/network/info`).

**Signals worth recording, not alerting on:** subscription timeouts at a fixed
period for sleepy devices (firewall/conntrack or Dirigera), mDNS parse errors
(reflectors), `fe80::` commissioning addresses (VLANs), OTA failures through
Apple border routers.

**Out of reach:** anything on the phone, the switch or the access point
(multicast settings, IGMP/MLD snooping, client isolation, VLAN layout). The
best Matter Health can do there is recognise the pattern (Wi-Fi nodes vanish
together, mDNS timeouts with a healthy route) and name the likely place to look.

**Reference timings**

| What | Value |
|---|---|
| Leader lost → new partition | ~120 s (NETWORK_ID_TIMEOUT) |
| MLE advertisement | every 32 s |
| Child timeout | 240 s |
| Sleepy child re-attach backoff | up to 20 min |
| RIO route lifetime | up to 1800 s (stale route up to 30 min) |
| SRP lease | 2 h; registration gone ≤2 h after refresh stops |
| Resubscription after a hiccup | 3–30 s |
| Unreachable device → mDNS record expired | ~10 min |
| Channel change takes effect | ~5 min |
| Fail-safe during pairing | 60 s, extendable |
| Apple recovery advice | 5 min off, then wait 10 min |
| Link quality | margin >20 dB = LQ 3, >10 = LQ 2, >2 = LQ 1 |
