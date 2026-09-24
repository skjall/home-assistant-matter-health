// How the network hangs together, as one tree that stays put.
//
// Matter is one network whatever carries it, so every transport hangs on the
// same home network: Thread border routers next to Wi-Fi access points next to
// wired devices. Every device is drawn once, on the one way it takes in; what
// that way is, its transport says. Positions are computed, not simulated, so
// nothing moves unless the network itself changed.
//
// The colours answer one question at a time, chosen by the user: where
// something is wrong (the default), which transport carries what, or how
// strong each link is. A device's state stays on its symbol in every view, so
// no answer depends on colour alone.
//
// Quiet by default: good links are thin and grey. Colour is kept for what
// needs attention - a weak link, a device away, a device with an open
// finding. Pointing at a device lights up its way to the home network.

import { cluster, hierarchy, type HierarchyPointNode } from "d3-hierarchy";
import {
  LitElement,
  css,
  html,
  nothing,
  svg,
  type SVGTemplateResult,
  type TemplateResult,
} from "lit";

import type { Finding, Severity, Topology, TopologyNode } from "../api";
import { DEVICE_TARGET, deviceHref, deviceName } from "../ha";
import { has, t } from "../i18n";
import { base } from "../theme";

type Status = "ok" | "resting" | "weak" | "warning" | "problem" | "offline";

/** What the colours in the picture tell. */
type ColorBy = "status" | "transport" | "signal";

const COLOR_BY: ColorBy[] = ["status", "transport", "signal"];

/** Where the viewer's choice of colours is kept, in their browser only. */
const COLOR_KEY = "mh-color-by";

function storedColorBy(): ColorBy {
  try {
    const stored = localStorage.getItem(COLOR_KEY);
    return COLOR_BY.includes(stored as ColorBy) ? (stored as ColorBy) : "status";
  } catch {
    return "status";
  }
}

interface Item {
  id: string;
  node?: TopologyNode;
  /** The home network a transport hangs on, or its devices without a way in. */
  special?: "home" | "no_way";
  children: Item[];
}

/** Where every transport's gateways hang, as the add-on names it. */
const HOME = "home";

/** Height of a row in the tree, in pixels. */
const ROW = 26;

/** Room for the names in the last column. */
const LEAF_LABEL = 230;

/** Below this width the tree turns into an indented outline. */
const NARROW = 720;

const KIND_ORDER: Record<string, number> = {
  gateway: 0,
  relay: 1,
  device: 2,
  sleepy: 3,
  unknown: 4,
};

const RANK: Record<Status, number> = {
  ok: 0,
  resting: 0,
  weak: 1,
  warning: 2,
  problem: 3,
  offline: 4,
};

const LABEL_FONT = "12.5px Inter, Roboto, system-ui, sans-serif";
const INNER_FONT = "600 12px Inter, Roboto, system-ui, sans-serif";

let measure: CanvasRenderingContext2D | null | undefined;

/** Width of ``text`` in pixels, measured in the font it is drawn in. */
function width(text: string, font: string): number {
  measure ??= document.createElement("canvas").getContext("2d");
  if (!measure) return text.length * 7;
  measure.font = font;
  return measure.measureText(text).width;
}

/** ``text`` shortened to fit ``room`` pixels; the full name is in the tooltip. */
function fit(text: string, room: number, inner: boolean): string {
  const font = inner ? INNER_FONT : LABEL_FONT;
  if (width(text, font) <= room) return text;
  let cut = text;
  while (cut.length > 1 && width(`${cut}…`, font) > room) cut = cut.slice(0, -1);
  return `${cut.trimEnd()}…`;
}

function quality(node: TopologyNode): "strong" | "medium" | "weak" | null {
  return node.link?.quality ?? null;
}

/** A node's symbol: a circle, or a diamond for a Matter bridge. */
function shape(radius: number, cls: string, bridge: boolean): SVGTemplateResult {
  if (!bridge) return svg`<circle class=${cls} r=${radius}></circle>`;
  const side = radius * 1.6;
  return svg`<rect class=${cls} x=${-side / 2} y=${-side / 2} width=${side} height=${side}
    transform="rotate(45)"></rect>`;
}

/** The transport's word for something, else the generic one. */
function word(transport: string, key: string, params = {}): string | null {
  const full = `transport.${transport}.${key}`;
  return has(full) ? t(full, params) : null;
}

export class MhTopology extends LitElement {
  static override properties = {
    topology: { attribute: false },
    findings: { attribute: false },
    onlyProblems: { state: true },
    colorBy: { state: true },
    pointed: { state: true },
    width: { state: true },
  };

  topology?: Topology;
  findings: Finding[] = [];
  onlyProblems = false;
  colorBy: ColorBy = storedColorBy();
  pointed: string | null = null;
  width = 0;
  private resize?: ResizeObserver;
  /** Height of the tree being drawn, for placing its tooltip. */
  private chartHeight = 0;

  static override styles = [
    base,
    css`
      :host {
        display: block;
      }
      .head {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: 6px 16px;
        margin-bottom: 14px;
      }
      h2 {
        margin: 0;
        font-size: 16px;
      }
      .switch {
        display: inline-flex;
        padding: 3px;
        border-radius: 999px;
        background: var(--mh-surface-2);
      }
      .switch button {
        border: 0;
        background: none;
        padding: 5px 12px;
        border-radius: 999px;
        font-size: 13px;
        color: var(--mh-muted);
        cursor: pointer;
      }
      .switch button[aria-pressed="true"] {
        background: var(--mh-surface);
        color: var(--mh-text);
        box-shadow: var(--mh-shadow);
      }
      .canvas {
        position: relative;
      }
      svg.tree {
        display: block;
        overflow: visible;
      }
      .link {
        fill: none;
        stroke: var(--mh-border);
        stroke-width: 1.5;
        transition: opacity 0.15s;
      }
      /* How a gateway or wired device reaches the home network - cable or
         Wi-Fi, nothing tells - is none of the transports' links. */
      .link.lan {
        stroke: var(--mh-muted);
        stroke-opacity: 0.45;
        stroke-width: 2;
      }
      .link.bridged {
        stroke: var(--mh-muted);
        stroke-opacity: 0.7;
      }
      .link.weak,
      .link.warning {
        stroke: var(--mh-warning);
        stroke-width: 2;
      }
      .link.problem {
        stroke: var(--mh-problem);
        stroke-width: 2;
      }
      .link.offline {
        stroke: var(--mh-problem);
        stroke-dasharray: 4 4;
        stroke-width: 1.5;
      }
      .link.resting,
      .link.dash-resting {
        stroke-dasharray: 3 4;
      }
      .link.dash-offline {
        stroke-dasharray: 4 4;
      }
      /* By transport: every link a transport carries in its colour. */
      .link.by-transport {
        stroke: var(--c);
        stroke-opacity: 0.7;
      }
      /* By signal: each device's link to its parent. */
      .link.q-strong {
        stroke: var(--mh-ok);
        stroke-width: 2;
      }
      .link.q-medium {
        stroke: var(--mh-warning);
        stroke-width: 2;
      }
      .link.q-weak {
        stroke: var(--mh-problem);
        stroke-width: 2;
      }
      .link.no-way {
        stroke-dasharray: 2 5;
      }
      .node {
        cursor: default;
        outline: none;
        transition: opacity 0.15s;
      }
      .node :is(circle, rect) {
        stroke-width: 2;
      }
      .node.gateway :is(circle, rect),
      .node.home :is(circle, rect) {
        fill: var(--c, var(--mh-primary));
        stroke: var(--mh-surface);
      }
      .node.relay :is(circle, rect) {
        fill: var(--mh-surface);
        stroke: var(--c, var(--mh-primary));
      }
      .node.device :is(circle, rect),
      .node.sleepy :is(circle, rect) {
        fill: var(--mh-muted);
        stroke: var(--mh-surface);
        stroke-width: 1.5;
      }
      .node.unknown :is(circle, rect) {
        fill: var(--mh-surface);
        stroke: var(--mh-muted);
        stroke-dasharray: 2 2;
        stroke-width: 1.5;
      }
      /* The state colours the symbol itself, so every device keeps the size of
         its kind whatever happens to it. */
      .node .ring {
        fill: none;
        stroke-width: 2;
      }
      .ring.offline,
      .ring.resting {
        fill: var(--mh-surface);
      }
      .ring.weak,
      .ring.warning {
        stroke: var(--mh-warning);
      }
      .ring.problem,
      .ring.offline {
        stroke: var(--mh-problem);
      }
      .ring.resting {
        stroke: var(--mh-muted);
        stroke-dasharray: 2 2;
      }
      .node a.device {
        cursor: pointer;
      }
      .node text {
        font-size: 12.5px;
        fill: var(--mh-text);
        paint-order: stroke;
        stroke: var(--mh-surface);
        stroke-width: 4px;
        stroke-linejoin: round;
      }
      .node.inner text {
        font-weight: 600;
        font-size: 12px;
      }
      .node.unknown text,
      .node.no-way text {
        fill: var(--mh-muted);
      }
      .node.offline text {
        fill: var(--mh-problem);
      }
      .node.resting text {
        fill: var(--mh-muted);
      }
      .node text.extra {
        font-size: 11px;
        font-weight: 400;
        fill: var(--mh-muted);
      }
      .dim {
        opacity: 0.18;
      }
      .tip {
        position: absolute;
        z-index: 2;
        width: 250px;
        padding: 10px 12px;
        border-radius: 12px;
        background: var(--mh-surface);
        border: 1px solid var(--mh-border);
        box-shadow: var(--mh-shadow);
        font-size: 13px;
        pointer-events: none;
      }
      .tip strong {
        display: block;
        margin-bottom: 2px;
        font-size: 14px;
      }
      .tip p {
        margin: 3px 0 0;
        color: var(--mh-muted);
      }
      .tip p.bad {
        color: var(--mh-problem);
      }
      .tip p.meh {
        color: var(--mh-warning);
      }
      .legend {
        display: flex;
        flex-wrap: wrap;
        gap: 6px 18px;
        margin-top: 14px;
        font-size: 12px;
        color: var(--mh-muted);
      }
      .legend span {
        display: inline-flex;
        align-items: center;
        gap: 6px;
      }
      .legend svg {
        overflow: visible;
      }
      .legends {
        display: flex;
        flex-wrap: wrap;
        gap: 0 36px;
      }
      .empty {
        margin: 0;
        font-size: 14px;
        color: var(--mh-muted);
      }
      /* The narrow outline. */
      ul.outline,
      ul.outline ul {
        list-style: none;
        margin: 0;
        padding: 0;
      }
      ul.outline ul {
        margin-left: 7px;
        padding-left: 14px;
        border-left: 1.5px solid var(--mh-border);
      }
      details {
        border-radius: 12px;
        background: var(--mh-surface-2);
        margin-bottom: 8px;
      }
      summary {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 12px;
        cursor: pointer;
        list-style: none;
        font-weight: 600;
        font-size: 15px;
      }
      summary::-webkit-details-marker {
        display: none;
      }
      summary > span:nth-child(2) {
        flex: 1;
        min-width: 0;
      }
      summary .count {
        flex: none;
        white-space: nowrap;
        font-weight: 400;
        font-size: 13px;
        color: var(--mh-muted);
      }
      ul.outline details > ul {
        margin-left: 0;
        padding: 0 12px 10px 12px;
        border-left: 0;
      }
      .row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-height: 30px;
        padding: 3px 0;
        font-size: 14px;
      }
      .row .name {
        flex: 1;
        min-width: 0;
      }
      .row .note {
        flex: none;
        font-size: 12px;
        color: var(--mh-muted);
        white-space: nowrap;
      }
      .row .note.bad {
        color: var(--mh-problem);
      }
      .row .note.meh {
        color: var(--mh-warning);
      }
      .row.inner > .name {
        font-weight: 600;
      }
      .dot {
        flex: none;
        width: 9px;
        height: 9px;
        border-radius: 50%;
        background: var(--mh-muted);
      }
      summary .via {
        font-size: 12px;
        font-weight: 400;
        color: var(--mh-muted);
      }
      .dot.bridge {
        border-radius: 2px;
        transform: rotate(45deg);
      }
      .dot.gateway {
        width: 12px;
        height: 12px;
        background: var(--c, var(--mh-primary));
      }
      .dot.relay {
        width: 11px;
        height: 11px;
        background: var(--mh-surface);
        border: 2px solid var(--c, var(--mh-primary));
      }
      .dot.by-transport {
        background: var(--c, var(--mh-muted));
      }
      .dot.relay.by-transport {
        background: var(--mh-surface);
      }
      .dot.q-strong {
        background: var(--mh-ok);
      }
      .dot.q-medium {
        background: var(--mh-warning);
      }
      .dot.q-weak {
        background: var(--mh-problem);
      }
      .controls {
        margin-left: auto;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px 12px;
      }
      .controls .pair {
        display: inline-flex;
        align-items: center;
        gap: 8px;
      }
      .controls .label {
        font-size: 13px;
        color: var(--mh-muted);
      }
      .dot.unknown {
        background: none;
        border: 1.5px dashed var(--mh-muted);
      }
      .dot.offline,
      .dot.problem {
        border: 2px solid var(--mh-problem);
      }
      .dot.offline {
        background: var(--mh-surface);
      }
      .dot.weak,
      .dot.warning {
        border: 2px solid var(--mh-warning);
      }
      .dot.resting {
        background: var(--mh-surface);
        border: 1.5px dashed var(--mh-muted);
      }
    `,
  ];

  override connectedCallback(): void {
    super.connectedCallback();
    this.resize = new ResizeObserver(([entry]) => {
      this.width = Math.floor(entry?.contentRect.width ?? 0);
    });
    this.resize.observe(this);
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.resize?.disconnect();
  }

  /** The worst open finding per device the user has not dealt with. */
  private findingStatus(): Map<string, Severity> {
    const worst = new Map<string, Severity>();
    const rank = { info: 0, warning: 1, problem: 2 };
    for (const finding of this.findings) {
      if (finding.ended_at || finding.dismissed || finding.severity === "info") continue;
      for (const subject of finding.subjects) {
        const before = worst.get(subject);
        if (!before || rank[finding.severity] > rank[before]) {
          worst.set(subject, finding.severity);
        }
      }
    }
    return worst;
  }

  private status(node: TopologyNode | undefined, worst: Map<string, Severity>): Status {
    if (!node) return "ok";
    if (!node.available) return node.resting ? "resting" : "offline";
    const finding = node.subject ? worst.get(node.subject) : undefined;
    if (finding === "problem") return "problem";
    if (finding === "warning") return "warning";
    return quality(node) === "weak" ? "weak" : "ok";
  }

  private name(item: Item): string {
    if (item.special === "home") return t("topology.home");
    if (item.special === "no_way") return t("topology.no_way");
    const node = item.node;
    if (!node) return t("generic.device");
    if (node.kind === "unknown") {
      return word(node.transport, "unknown") ?? t("generic.device");
    }
    if (node.name) return node.name;
    if (node.kind === "gateway" && node.detail?.address) {
      const named = word(node.transport, "gateway_named", { address: node.detail.address });
      if (named) return named;
    }
    return t("generic.device");
  }

  /** The whole network as one tree, every device under the one way it takes in. */
  private tree(statuses: Map<string, Status>, order: string[]): Item {
    const nodes = this.topology?.nodes ?? [];
    const items = new Map<string, Item>();
    const home: Item = { id: HOME, special: "home", children: [] };
    const noWay: Item = { id: "no_way", special: "no_way", children: [] };
    for (const node of nodes) items.set(node.id, { id: node.id, node, children: [] });
    for (const item of items.values()) {
      const parent = item.node?.parent;
      const up = parent === HOME ? home : parent ? items.get(parent) : undefined;
      (up ?? noWay).children.push(item);
    }
    if (noWay.children.length) home.children.push(noWay);
    const worst = (item: Item): number =>
      Math.max(RANK[statuses.get(item.id) ?? "ok"], ...item.children.map(worst));
    // On the home network the transports stay together, the busiest first.
    const rank = (item: Item) => {
      const at = order.indexOf(item.node?.transport ?? "");
      return at < 0 ? order.length : at;
    };
    const sort = (item: Item): void => {
      item.children.sort(
        (a, b) =>
          (item.special === "home" ? rank(a) - rank(b) : 0) ||
          (KIND_ORDER[a.node?.kind ?? ""] ?? 9) - (KIND_ORDER[b.node?.kind ?? ""] ?? 9) ||
          this.name(a).localeCompare(this.name(b)),
      );
      item.children.forEach(sort);
    };
    sort(home);
    if (this.onlyProblems) {
      const keep = (item: Item): Item | null => {
        if (worst(item) === 0) return null;
        const children = item.children.map(keep).filter((c): c is Item => c !== null);
        return { ...item, children };
      };
      return keep(home) ?? { ...home, children: [] };
    }
    return home;
  }

  /** The colour of a transport, where the view shows transports. */
  private tint(item: Item): string {
    const transport = item.node?.transport;
    // A device behind a bridge is on the bridge's radio, not on a transport.
    return this.colorBy === "transport" && transport && !item.node?.bridged
      ? `--c: var(--mh-${transport}, var(--mh-muted))`
      : "";
  }

  /** The colour of a way into the home network, where the view shows transports. */
  private uplinkTint(item: Item): string {
    const up = item.node?.kind === "gateway" ? item.node.uplink : null;
    if (!up) return this.tint(item);
    return this.colorBy === "transport"
      ? `--c: var(--mh-${up.wired ? "ethernet" : "wifi"})`
      : "";
  }

  /** How a gateway is connected to the home network, in words. */
  private uplinkLine(node: TopologyNode): TemplateResult | typeof nothing {
    const up = node.uplink;
    if (!up) return nothing;
    const parts = [t(up.wired ? "topology.uplink.wired" : "topology.uplink.wireless")];
    if (up.via) parts.push(t("topology.uplink.via", { via: up.via }));
    if (up.ssid) parts.push(t("transport.wifi.detail.ssid", { ssid: up.ssid }));
    return html`<p>${parts.join(" · ")}</p>`;
  }

  /** How a device's link to its parent is drawn in the chosen view. */
  private linkClass(target: Item, fromHome: boolean, status: Status): string {
    const dash =
      status === "offline" ? "dash-offline" : status === "resting" ? "dash-resting" : "";
    // A gateway's way into the home network is not its transport's; a wired
    // device's is. Where an integration tells how it is connected, the
    // transport view shows that.
    if (fromHome && target.node?.kind === "gateway") {
      return this.colorBy === "transport" && target.node.uplink
        ? `by-transport ${dash}`
        : `lan ${dash}`;
    }
    if (this.colorBy === "transport") {
      return `${target.node?.bridged ? "bridged" : "by-transport"} ${dash}`;
    }
    if (this.colorBy === "signal") {
      const q = target.node ? quality(target.node) : null;
      return q ? `q-${q} ${dash}` : dash;
    }
    if (fromHome) return `lan ${dash}`;
    return status === "ok" ? "" : status;
  }

  private note(node: TopologyNode | undefined, status: Status): [string, string] | null {
    if (!node) return null;
    if (status === "offline") return [t("topology.offline"), "bad"];
    if (status === "resting") return [t("topology.resting"), ""];
    if (status === "problem" || status === "warning") {
      return [t("topology.finding_open"), status === "problem" ? "bad" : "meh"];
    }
    if (status === "weak") return [t("topology.weak"), "meh"];
    return null;
  }

  private tooltip(
    point: HierarchyPointNode<Item>,
    status: Status,
    x: number,
    y: number,
  ): TemplateResult {
    const item = point.data;
    const node = item.node;
    const lines: TemplateResult[] = [];
    const children = point.children?.length ?? 0;
    const kind = node?.bridged
      ? t("topology.bridged")
      : node?.bridge
        ? t("topology.bridge", { count: children })
        : node
          ? word(node.transport, `kind.${node.kind}`)
          : null;
    if (kind) lines.push(html`<p>${kind}</p>`);
    // What the transport adds, each part only where the device reported it.
    const detail = Object.entries(node?.detail ?? {})
      .filter(([, value]) => value !== null && value !== undefined && value !== "")
      .map(([key, value]) => (node ? word(node.transport, `detail.${key}`, { [key]: value }) : null))
      .filter((part): part is string => !!part);
    if (detail.length) lines.push(html`<p>${detail.join(" · ")}</p>`);
    if (node?.uplink) lines.push(html`${this.uplinkLine(node)}`);
    const parent = point.parent?.data;
    const parentName = parent ? this.name(parent) : null;
    if (node && parentName && !parent?.special) {
      lines.push(
        html`<p>
          ${node.missing
            ? t("topology.last_via", { parent: parentName })
            : t("topology.via", { parent: parentName })}
        </p>`,
      );
    }
    const q = node ? quality(node) : null;
    if (node && typeof node.link?.rssi === "number" && q) {
      lines.push(
        html`<p class=${q === "weak" ? "meh" : ""}>
          ${t("topology.signal", { rssi: node.link.rssi, quality: t(`topology.quality.${q}`) })}
        </p>`,
      );
    }
    if (node && !node.bridge && (node.kind === "gateway" || node.kind === "relay") && children) {
      lines.push(html`<p>${t("topology.children", { count: children })}</p>`);
    }
    if (node?.kind === "relay") {
      lines.push(
        node.alternatives
          ? html`<p>${t("topology.alternatives", { count: node.alternatives })}</p>`
          : html`<p class="meh">${t("topology.only_way")}</p>`,
      );
    }
    const note = this.note(node, status);
    if (note) lines.push(html`<p class=${note[1]}>${note[0]}</p>`);
    const left = x + 270 > this.width ? x - 262 : x + 14;
    // Near the bottom of a short tree the tip opens upwards.
    const tall = 34 + lines.length * 20;
    const top = y + 14 + tall > this.chartHeight ? Math.max(0, y - 14 - tall) : y + 14;
    return html`<div class="tip" style="left:${left}px;top:${top}px">
      <strong>${this.name(item)}</strong>${lines}
    </div>`;
  }

  private path(a: { x: number; y: number }, b: { x: number; y: number }): string {
    const mid = (a.x + b.x) / 2;
    return `M${a.x},${a.y}C${mid},${a.y} ${mid},${b.y} ${b.x},${b.y}`;
  }

  /** One transport's tree; ``depth`` columns are shared by every transport. */
  private chart(root: Item, statuses: Map<string, Status>, depth: number): TemplateResult {
    const layout = cluster<Item>()
      .nodeSize([ROW, 1])
      .separation((a, b) => {
        // Rows of different transports keep a wider gap, so the groups on the
        // home network read as groups.
        const group = (n: HierarchyPointNode<Item>) =>
          n.ancestors().find((p) => p.depth === 1)?.data.node?.transport;
        if (group(a) !== group(b)) return 2;
        return a.parent === b.parent ? 1 : 1.35;
      })(
      hierarchy(root, (d) => (d.children.length ? d.children : null)),
    );
    const points = layout.descendants();
    // Every level has its column: home network, gateways, relaying devices.
    // Devices at the end of a branch share the last column, so they read like
    // a list - except a gateway without devices, which stays with the gateways.
    // The columns are the same for every transport, so gateways line up.
    const column = Math.max(120, (this.width - LEAF_LABEL - 24) / depth);
    const top = Math.min(...points.map((p) => p.x)) - ROW;
    const pos = (p: HierarchyPointNode<Item>) => {
      // A device right on the home network - a wired one - stands with the
      // gateways; drawn at the end, its line would cross every other.
      const last = !p.children && p.data.node?.kind !== "gateway" && p.depth > 1;
      return { x: 12 + (last ? depth : p.depth) * column, y: p.x - top };
    };
    const height = Math.max(...points.map((p) => p.x)) - top + ROW;
    this.chartHeight = height;

    // What lights up when a device is pointed at: its way home and what hangs on it.
    let lit: Set<string> | null = null;
    const focused = this.pointed ? points.find((p) => p.data.id === this.pointed) : undefined;
    if (focused) {
      lit = new Set([
        ...focused.ancestors().map((p) => p.data.id),
        ...focused.descendants().map((p) => p.data.id),
      ]);
    }
    const dim = (id: string) => (lit && !lit.has(id) ? "dim" : "");

    const links = layout.links().map((link) => {
      const target = link.target.data;
      const status = statuses.get(target.id) ?? "ok";
      const kind =
        target.special === "no_way" || link.source.data.special === "no_way"
          ? "no-way"
          : this.linkClass(target, link.source.data.special === "home", status);
      const style =
        link.source.data.special === "home" ? this.uplinkTint(target) : this.tint(target);
      return svg`<path class="link ${kind} ${dim(target.id)}" style=${style}
        d=${this.path(pos(link.source), pos(link.target))}></path>`;
    });

    const nodes = points.map((point) => {
      const item = point.data;
      const { x, y } = pos(point);
      const status = statuses.get(item.id) ?? "ok";
      const kind =
        item.special === "home" ? "home" : item.special === "no_way" ? "no-way" : item.node?.kind;
      const inner = !!point.children?.length;
      const radius =
        kind === "home"
          ? 9
          : kind === "gateway"
            ? 7
            : kind === "relay" || item.node?.bridge
              ? 5.5
              : 4;
      const extra = inner && item.node ? ` · ${point.children?.length}` : "";
      const name = this.name(item);
      const room = inner ? column - 24 : LEAF_LABEL - 14;
      const label = fit(name, room - (extra ? width(extra, LABEL_FONT) : 0), inner);
      return svg`<g class="node ${kind} ${inner ? "inner" : ""} ${status} ${dim(item.id)}"
          style=${this.tint(item)}
          transform="translate(${x},${y})"
          tabindex=${item.node ? 0 : -1}
          @pointerenter=${() => (this.pointed = item.id)}
          @pointerleave=${() => (this.pointed = null)}
          @focus=${() => (this.pointed = item.id)}
          @blur=${() => (this.pointed = null)}>
        ${kind === "no-way" ? nothing : shape(radius, "", !!item.node?.bridge)}
        ${status !== "ok" ? shape(radius, `ring ${status}`, !!item.node?.bridge) : nothing}
        ${label !== name ? svg`<title>${name}</title>` : nothing}
        ${this.linked(
          item.node?.device_id,
          inner
            ? svg`<text x="10" y="-9">${label}<tspan class="extra">${extra}</tspan></text>`
            : svg`<text x="10" dy="0.35em">${label}</text>`,
        )}
      </g>`;
    });

    const tip =
      focused && focused.data.node
        ? this.tooltip(focused, statuses.get(focused.data.id) ?? "ok", pos(focused).x, pos(focused).y)
        : nothing;

    return html`<div class="canvas">
      <svg class="tree" width=${this.width} height=${height} role="img"
        aria-label=${t("topology.title")}>
        ${links}${nodes}
      </svg>
      ${tip}
    </div>`;
  }

  /** A node's label, leading to the device's page where it has one. */
  private linked(deviceId: string | null | undefined, label: SVGTemplateResult) {
    return deviceId
      ? svg`<a class="device" href=${deviceHref(deviceId)} target=${DEVICE_TARGET}
          rel="noopener">${label}</a>`
      : label;
  }

  private outlineRow(item: Item, statuses: Map<string, Status>): TemplateResult {
    const status = statuses.get(item.id) ?? "ok";
    const note = this.note(item.node, status);
    const kind = item.node?.kind ?? "unknown";
    return html`<li>
      <div class="row ${item.children.length ? "inner" : ""}">
        <span
          class="dot ${kind} ${item.node?.bridge ? "bridge" : ""} ${this.dotClass(item, status)}"
          style=${this.tint(item)}
        ></span>
        <span class="name">${deviceName(this.name(item), item.node?.device_id)}</span>
        ${note ? html`<span class="note ${note[1]}">${note[0]}</span>` : nothing}
      </div>
      ${item.children.length
        ? html`<ul>
            ${item.children.map((child) => this.outlineRow(child, statuses))}
          </ul>`
        : nothing}
    </li>`;
  }

  /** A dot's colour in the chosen view; its state marks it in every view. */
  private dotClass(item: Item, status: Status): string {
    const q = item.node ? quality(item.node) : null;
    const signal = this.colorBy === "signal" && q && item.node?.kind !== "gateway";
    // Without lines, a device shows its transport on its own dot.
    const transport = this.colorBy === "transport" ? "by-transport" : "";
    return `${status === "ok" ? "" : status} ${signal ? `q-${q}` : ""} ${transport}`;
  }

  private outline(root: Item, statuses: Map<string, Status>): TemplateResult {
    const size = (item: Item): number =>
      item.children.reduce((sum, child) => sum + 1 + size(child), 0);
    const troubled = (item: Item): boolean =>
      (statuses.get(item.id) ?? "ok") !== "ok" || item.children.some(troubled);
    return html`<ul class="outline">
      ${root.children.map(
        (bridge) =>
          html`<li>
            <details ?open=${this.onlyProblems || troubled(bridge)}>
              <summary>
                <span
                  class="dot ${bridge.node?.kind ?? "unknown"} ${bridge.node?.bridge
                    ? "bridge"
                    : ""} ${this.dotClass(
                    bridge,
                    statuses.get(bridge.id) ?? "ok",
                  )}"
                  style=${this.tint(bridge)}
                ></span>
                <span>${this.name(bridge)}</span>
                ${bridge.node?.kind === "gateway" && bridge.node.uplink
                  ? html`<span class="via"
                      >${t(
                        bridge.node.uplink.wired
                          ? "topology.uplink.short_wired"
                          : "topology.uplink.short_wireless",
                      )}</span
                    >`
                  : nothing}
                ${size(bridge)
                  ? html`<span class="count">${t("topology.devices", { count: size(bridge) })}</span>`
                  : nothing}
              </summary>
              ${bridge.children.length
                ? html`<ul>
                    ${bridge.children.map((child) => this.outlineRow(child, statuses))}
                  </ul>`
                : nothing}
            </details>
          </li>`,
      )}
    </ul>`;
  }

  /** The symbols, named in the words of the transports that have them. */
  private symbols(order: string[]): TemplateResult {
    const nodes = this.topology?.nodes ?? [];
    const dot = (cls: string, r: number, bridge = false) =>
      html`<svg width="14" height="14" viewBox="-7 -7 14 14">
        ${svg`<g class="node ${cls}">${shape(r, "", bridge)}</g>`}
      </svg>`;
    const named = (kind: string) =>
      order
        .filter((transport) => nodes.some((n) => n.transport === transport && n.kind === kind))
        .map((transport) => word(transport, `legend.${kind}`))
        .filter((label): label is string => !!label)
        .join(" / ");
    const gateway = named("gateway");
    const relay = named("relay");
    return html`<div class="legend">
      ${gateway ? html`<span>${dot("gateway", 6)}${gateway}</span>` : nothing}
      ${relay ? html`<span>${dot("relay", 5)}${relay}</span>` : nothing}
      <span>${dot("sleepy", 4)}${t("topology.legend.device")}</span>
      ${nodes.some((n) => n.bridge)
        ? html`<span>${dot("device", 5, true)}${t("topology.legend.bridge")}</span>`
        : nothing}
    </div>`;
  }

  /** What the colours mean in the chosen view. */
  private colors(order: string[], lines = true): TemplateResult {
    const line = (cls: string, style = "") =>
      html`<svg width="26" height="10" viewBox="0 -5 26 10">
        ${svg`<path class="link ${cls}" style=${style} d="M0,0H26"></path>`}
      </svg>`;
    const nodes = this.topology?.nodes ?? [];
    const bridged = nodes.some((n) => n.bridged);
    // Ways into the home network may use a transport no Matter device does.
    const uplinks = nodes
      .filter((n) => n.kind === "gateway" && n.uplink)
      .map((n) => (n.uplink?.wired ? "ethernet" : "wifi"));
    const shown = [...order, ...uplinks.filter((u, i) => !order.includes(u) && uplinks.indexOf(u) === i)];
    const entries: [TemplateResult, string][] =
      this.colorBy === "transport"
        ? [
            ...shown.map((transport): [TemplateResult, string] => [
              line("by-transport", `--c: var(--mh-${transport}, var(--mh-muted))`),
              t(`transport.${transport}.name`),
            ]),
            ...(bridged
              ? [[line("bridged"), t("topology.legend.bridged")] as [TemplateResult, string]]
              : []),
          ]
        : this.colorBy === "signal"
          ? [
              [line("q-strong"), t("topology.legend.signal.strong")],
              [line("q-medium"), t("topology.legend.signal.medium")],
              [line("q-weak"), t("topology.legend.signal.weak")],
              [line(""), t("topology.legend.signal.unknown")],
              [line("dash-offline"), t("topology.legend.offline")],
            ]
          : [
              [line(""), t("topology.legend.good")],
              [line("weak"), t("topology.legend.weak")],
              [line("offline"), t("topology.legend.offline")],
            ];
    const home =
      this.colorBy === "transport" && uplinks.length
        ? t("topology.legend.home_unknown")
        : t("topology.legend.home");
    if (lines) entries.unshift([line("lan"), home]);
    return html`<div class="legend lines">
      ${entries.map(([symbol, label]) => html`<span>${symbol}${label}</span>`)}
    </div>`;
  }

  /** Transports with the most devices first. */
  private transports(): string[] {
    const count = new Map<string, number>();
    for (const node of this.topology?.nodes ?? []) {
      count.set(node.transport, (count.get(node.transport) ?? 0) + 1);
    }
    return [...count.keys()].sort(
      (a, b) => (count.get(b) ?? 0) - (count.get(a) ?? 0) || a.localeCompare(b),
    );
  }

  private setColorBy(colorBy: ColorBy): void {
    this.colorBy = colorBy;
    try {
      localStorage.setItem(COLOR_KEY, colorBy);
    } catch {
      // Not remembered then; the choice still holds for this visit.
    }
  }

  override render(): TemplateResult {
    const nodes = this.topology?.nodes ?? [];
    const head = (controls: TemplateResult | typeof nothing) => html`<div class="head">
      <h2>${t("topology.title")}</h2>
      ${controls}
    </div>`;
    if (!nodes.length) {
      return html`${head(nothing)}
        <p class="empty">${t("topology.empty")}</p>`;
    }
    const worst = this.findingStatus();
    const statuses = new Map(nodes.map((n) => [n.id, this.status(n, worst)] as const));
    const order = this.transports();
    const root = this.tree(statuses, order);
    const controls = html`<div class="controls">
      <div class="switch" role="group">
        <button aria-pressed=${!this.onlyProblems} @click=${() => (this.onlyProblems = false)}>
          ${t("topology.all")}
        </button>
        <button aria-pressed=${this.onlyProblems} @click=${() => (this.onlyProblems = true)}>
          ${t("topology.only_problems")}
        </button>
      </div>
      <div class="pair">
      <span class="label" id="color-by">${t("topology.color.label")}</span>
      <div class="switch" role="group" aria-labelledby="color-by">
        ${COLOR_BY.map(
          (colorBy) =>
            html`<button
              aria-pressed=${this.colorBy === colorBy}
              @click=${() => this.setColorBy(colorBy)}
            >
              ${t(`topology.color.${colorBy}`)}
            </button>`,
        )}
      </div>
      </div>
    </div>`;
    if (this.onlyProblems && !root.children.length) {
      return html`${head(controls)}
        <p class="empty">${t("topology.no_problems")}</p>`;
    }
    if (!this.width) return head(controls);
    if (this.width < NARROW) {
      // The outline shows no lines; only colours other than state need saying.
      return html`${head(controls)}${this.outline(root, statuses)}
      ${this.colorBy === "status" ? nothing : this.colors(order, false)}`;
    }
    const deepest = (item: Item): number => 1 + Math.max(0, ...item.children.map(deepest));
    return html`${head(controls)}
      ${this.chart(root, statuses, Math.max(1, deepest(root) - 1))}
      <div class="legends">${this.symbols(order)}${this.colors(order)}</div>`;
  }
}

customElements.define("mh-topology", MhTopology);
