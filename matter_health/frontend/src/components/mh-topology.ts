// How the network hangs together, as a tree that stays put.
//
// Every device is drawn once, on the one way it takes into the network:
// border routers on the home network, relaying devices on their cheapest path
// to a border router, battery devices on their parent. The other radio links
// between relaying devices are counted, not drawn - drawn, they turn the
// picture into a ball of lines. Positions are computed, not simulated, so
// nothing moves unless the network itself changed.
//
// Quiet by default: good links are thin and grey. Colour is kept for what
// needs attention - a weak link, a device away, a device with an open
// finding. Pointing at a device lights up its way to the home network.

import { cluster, hierarchy, type HierarchyPointNode } from "d3-hierarchy";
import { LitElement, css, html, nothing, svg, type TemplateResult } from "lit";

import type { Finding, Severity, Topology, TopologyNode } from "../api";
import { t } from "../i18n";
import { base } from "../theme";

type Status = "ok" | "resting" | "weak" | "warning" | "problem" | "offline";

interface Item {
  id: string;
  node?: TopologyNode;
  children: Item[];
}

const HOME = "home";
const NO_WAY = "no_way";

/** Height of a row in the tree, in pixels. */
const ROW = 26;

/** Room for the names in the last column. */
const LEAF_LABEL = 230;

/** Below this width the tree turns into an indented outline. */
const NARROW = 720;

const KIND_ORDER: Record<string, number> = {
  border_router: 0,
  router: 1,
  end_device: 2,
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
  const { strength, lqi } = node.link ?? {};
  if (strength === "strong" || strength === "medium" || strength === "weak") return strength;
  if (typeof lqi !== "number") return null;
  return lqi >= 3 ? "strong" : lqi === 2 ? "medium" : "weak";
}

export class MhTopology extends LitElement {
  static override properties = {
    topology: { attribute: false },
    findings: { attribute: false },
    onlyProblems: { state: true },
    pointed: { state: true },
    width: { state: true },
  };

  topology?: Topology;
  findings: Finding[] = [];
  onlyProblems = false;
  pointed: string | null = null;
  width = 0;
  private resize?: ResizeObserver;

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
        margin-left: auto;
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
        overflow-x: auto;
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
      .link.lan {
        stroke: var(--mh-primary);
        stroke-opacity: 0.35;
        stroke-width: 2;
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
      .link.resting {
        stroke-dasharray: 3 4;
      }
      .link.no-way {
        stroke-dasharray: 2 5;
      }
      .node {
        cursor: default;
        outline: none;
        transition: opacity 0.15s;
      }
      .node circle {
        stroke-width: 2;
      }
      .node.border_router circle,
      .node.home circle {
        fill: var(--mh-primary);
        stroke: var(--mh-surface);
      }
      .node.router circle {
        fill: var(--mh-surface);
        stroke: var(--mh-primary);
      }
      .node.end_device circle,
      .node.sleepy circle {
        fill: var(--mh-muted);
        stroke: var(--mh-surface);
        stroke-width: 1.5;
      }
      .node.unknown circle {
        fill: var(--mh-surface);
        stroke: var(--mh-muted);
        stroke-dasharray: 2 2;
        stroke-width: 1.5;
      }
      .node .ring {
        fill: none;
        stroke-width: 2;
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
      .dot.border_router {
        width: 12px;
        height: 12px;
        background: var(--mh-primary);
      }
      .dot.router {
        width: 11px;
        height: 11px;
        background: var(--mh-surface);
        border: 2px solid var(--mh-primary);
      }
      .dot.unknown {
        background: none;
        border: 1.5px dashed var(--mh-muted);
      }
      .dot.offline,
      .dot.problem {
        box-shadow: 0 0 0 2px var(--mh-surface), 0 0 0 4px var(--mh-problem);
      }
      .dot.weak,
      .dot.warning {
        box-shadow: 0 0 0 2px var(--mh-surface), 0 0 0 4px var(--mh-warning);
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
    if (item.id === HOME) return t("topology.home");
    if (item.id === NO_WAY) return t("topology.no_way");
    const node = item.node;
    if (node?.kind === "unknown") return t("topology.unknown_device");
    return node?.name ?? t("generic.device");
  }

  /** The tree of items, with every device under the one way it takes in. */
  private tree(statuses: Map<string, Status>): Item {
    const nodes = this.topology?.nodes ?? [];
    const items = new Map<string, Item>();
    const home: Item = { id: HOME, children: [] };
    const noWay: Item = { id: NO_WAY, children: [] };
    for (const node of nodes) items.set(node.id, { id: node.id, node, children: [] });
    for (const item of items.values()) {
      const parent = item.node?.parent;
      const up = parent === HOME ? home : parent ? items.get(parent) : undefined;
      (up ?? noWay).children.push(item);
    }
    if (noWay.children.length) home.children.push(noWay);
    const worst = (item: Item): number =>
      Math.max(RANK[statuses.get(item.id) ?? "ok"], ...item.children.map(worst));
    const order = (item: Item): void => {
      item.children.sort(
        (a, b) =>
          (KIND_ORDER[a.node?.kind ?? ""] ?? 9) - (KIND_ORDER[b.node?.kind ?? ""] ?? 9) ||
          this.name(a).localeCompare(this.name(b)),
      );
      item.children.forEach(order);
    };
    order(home);
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
    if (node) lines.push(html`<p>${t(`topology.kind.${node.kind}`)}</p>`);
    const parent = point.parent?.data;
    const parentName = parent ? this.name(parent) : null;
    if (node && parentName && parent?.id !== HOME && parent?.id !== NO_WAY) {
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
    const children = point.children?.length ?? 0;
    if (node && (node.kind === "border_router" || node.kind === "router") && children) {
      lines.push(html`<p>${t("topology.children", { count: children })}</p>`);
    }
    if (node?.kind === "router") {
      lines.push(
        node.alternatives
          ? html`<p>${t("topology.alternatives", { count: node.alternatives })}</p>`
          : html`<p class="meh">${t("topology.only_way")}</p>`,
      );
    }
    const note = this.note(node, status);
    if (note) lines.push(html`<p class=${note[1]}>${note[0]}</p>`);
    const left = x + 270 > this.width ? x - 262 : x + 14;
    return html`<div class="tip" style="left:${left}px;top:${y + 14}px">
      <strong>${this.name(item)}</strong>${lines}
    </div>`;
  }

  private path(a: { x: number; y: number }, b: { x: number; y: number }): string {
    const mid = (a.x + b.x) / 2;
    return `M${a.x},${a.y}C${mid},${a.y} ${mid},${b.y} ${b.x},${b.y}`;
  }

  private chart(root: Item, statuses: Map<string, Status>): TemplateResult {
    const layout = cluster<Item>()
      .nodeSize([ROW, 1])
      .separation((a, b) => (a.parent === b.parent ? 1 : 1.35))(
      hierarchy(root, (d) => (d.children.length ? d.children : null)),
    );
    const points = layout.descendants();
    // Every level has its column: home network, bridges, relaying devices.
    // Devices at the end of a branch share the last column, so they read like
    // a list - except a bridge without devices, which stays with the bridges.
    const depth = Math.max(1, ...points.map((p) => p.depth));
    const column = Math.max(120, (this.width - LEAF_LABEL - 24) / depth);
    const top = Math.min(...points.map((p) => p.x)) - ROW;
    const pos = (p: HierarchyPointNode<Item>) => {
      const last = !p.children && p.data.node?.kind !== "border_router";
      return { x: 12 + (last ? depth : p.depth) * column, y: p.x - top };
    };
    const height = Math.max(...points.map((p) => p.x)) - top + ROW;

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
      const status = statuses.get(link.target.data.id) ?? "ok";
      const kind =
        link.source.data.id === HOME
          ? link.target.data.id === NO_WAY
            ? "no-way"
            : "lan"
          : link.source.data.id === NO_WAY
            ? "no-way"
            : status === "ok"
              ? ""
              : status;
      return svg`<path class="link ${kind} ${dim(link.target.data.id)}"
        d=${this.path(pos(link.source), pos(link.target))}></path>`;
    });

    const nodes = points.map((point) => {
      const item = point.data;
      const { x, y } = pos(point);
      const status = statuses.get(item.id) ?? "ok";
      const kind = item.id === HOME ? "home" : item.id === NO_WAY ? "no-way" : item.node?.kind;
      const inner = !!point.children?.length;
      const radius =
        kind === "home" ? 9 : kind === "border_router" ? 7 : kind === "router" ? 5.5 : 4;
      const extra = inner && item.node ? ` · ${point.children?.length}` : "";
      const name = this.name(item);
      const room = inner ? column - 24 : LEAF_LABEL - 14;
      const label = fit(name, room - (extra ? width(extra, LABEL_FONT) : 0), inner);
      return svg`<g class="node ${kind} ${inner ? "inner" : ""} ${status} ${dim(item.id)}"
          transform="translate(${x},${y})"
          tabindex=${item.node ? 0 : -1}
          @pointerenter=${() => (this.pointed = item.id)}
          @pointerleave=${() => (this.pointed = null)}
          @focus=${() => (this.pointed = item.id)}
          @blur=${() => (this.pointed = null)}>
        ${kind === "no-way" ? nothing : svg`<circle r=${radius}></circle>`}
        ${status !== "ok" ? svg`<circle class="ring ${status}" r=${radius + 4}></circle>` : nothing}
        ${label !== name ? svg`<title>${name}</title>` : nothing}
        ${inner
          ? svg`<text x="10" y="-9">${label}<tspan class="extra">${extra}</tspan></text>`
          : svg`<text x="10" dy="0.35em">${label}</text>`}
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

  private outlineRow(item: Item, statuses: Map<string, Status>): TemplateResult {
    const status = statuses.get(item.id) ?? "ok";
    const note = this.note(item.node, status);
    const kind = item.node?.kind ?? "unknown";
    return html`<li>
      <div class="row ${item.children.length ? "inner" : ""}">
        <span class="dot ${kind} ${status === "ok" ? "" : status}"></span>
        <span class="name">${this.name(item)}</span>
        ${note ? html`<span class="note ${note[1]}">${note[0]}</span>` : nothing}
      </div>
      ${item.children.length
        ? html`<ul>
            ${item.children.map((child) => this.outlineRow(child, statuses))}
          </ul>`
        : nothing}
    </li>`;
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
                <span class="dot ${bridge.node?.kind ?? "unknown"}"></span>
                <span>${this.name(bridge)}</span>
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

  private legend(): TemplateResult {
    const dot = (cls: string, r: number) =>
      html`<svg width="14" height="14" viewBox="-7 -7 14 14">
        ${svg`<g class="node ${cls}"><circle r=${r}></circle></g>`}
      </svg>`;
    const line = (cls: string) =>
      html`<svg width="26" height="10" viewBox="0 -5 26 10">
        ${svg`<path class="link ${cls}" d="M0,0H26"></path>`}
      </svg>`;
    return html`<div class="legend">
      <span>${dot("border_router", 6)}${t("topology.legend.bridge")}</span>
      <span>${dot("router", 5)}${t("topology.legend.router")}</span>
      <span>${dot("sleepy", 4)}${t("topology.legend.device")}</span>
      <span>${line("")}${t("topology.legend.good")}</span>
      <span>${line("weak")}${t("topology.legend.weak")}</span>
      <span>${line("offline")}${t("topology.legend.offline")}</span>
    </div>`;
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
    const root = this.tree(statuses);
    const controls = html`<div class="switch" role="group">
        <button aria-pressed=${!this.onlyProblems} @click=${() => (this.onlyProblems = false)}>
          ${t("topology.all")}
        </button>
        <button aria-pressed=${this.onlyProblems} @click=${() => (this.onlyProblems = true)}>
          ${t("topology.only_problems")}
        </button>
      </div>`;
    if (this.onlyProblems && !root.children.length) {
      return html`${head(controls)}
        <p class="empty">${t("topology.no_problems")}</p>`;
    }
    if (!this.width) return head(controls);
    return html`${head(controls)}
      ${this.width < NARROW
        ? this.outline(root, statuses)
        : html`${this.chart(root, statuses)}${this.legend()}`}`;
  }
}

customElements.define("mh-topology", MhTopology);
