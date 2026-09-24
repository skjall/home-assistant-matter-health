// The whole page: a verdict at the top, then findings, timeline or network.

import {
  mdiAlertCircleOutline,
  mdiAlertOutline,
  mdiDevices,
  mdiEthernet,
  mdiLan,
  mdiShieldCheckOutline,
  mdiSourceBranch,
  mdiWifi,
} from "@mdi/js";
import { LitElement, css, html, nothing, type TemplateResult } from "lit";

import {
  api,
  follow,
  type Finding,
  type Overview,
  type TimelineEvent,
  type Topology,
} from "../api";
import { t } from "../i18n";
import { base, tokens } from "../theme";
import { icon } from "./mh-finding";
import "./mh-finding";
import "./mh-network";
import "./mh-timeline";

/** A symbol per transport; a new one without its own still gets a network. */
const TRANSPORT_ICON: Record<string, string> = {
  thread: mdiSourceBranch,
  wifi: mdiWifi,
  ethernet: mdiEthernet,
};

type Tab = "findings" | "timeline" | "network";

const POLL_MS = 30_000;

const SEVERITY_RANK = { problem: 0, warning: 1, info: 2 };

export class MhApp extends LitElement {
  static override properties = {
    overview: { state: true },
    topology: { state: true },
    findings: { state: true },
    events: { state: true },
    tab: { state: true },
    live: { state: true },
    offline: { state: true },
    showDone: { state: true },
    failed: { state: true },
  };

  overview?: Overview;
  topology?: Topology;
  findings: Finding[] = [];
  events: TimelineEvent[] = [];
  tab: Tab = "findings";
  live = false;
  /** Disconnected for long enough to say so; brief gaps are normal. */
  offline = false;
  showDone = false;
  private offlineTimer?: number;
  failed = false;
  private stop?: () => void;
  private refresh?: number;
  private regroup?: number;
  private poll?: number;

  static override styles = [
    tokens,
    base,
    css`
      :host {
        display: block;
        min-height: 100vh;
        background: var(--mh-bg);
      }
      /* Wide screens get the room: the network picture and a second column
         of findings use it. Beyond this even the picture has nothing to add. */
      .page {
        max-width: 1680px;
        margin: 0 auto;
        padding: 24px 20px 48px;
      }
      .hero {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 18px;
        align-items: center;
        padding: 22px 24px;
        --tone: var(--mh-ok);
        background: linear-gradient(
            120deg,
            color-mix(in srgb, var(--tone) 14%, var(--mh-surface)) 0%,
            var(--mh-surface) 70%
          );
      }
      .hero.problem {
        --tone: var(--mh-problem);
      }
      .hero.warning {
        --tone: var(--mh-warning);
      }
      .hero .big {
        display: grid;
        place-items: center;
        width: 56px;
        height: 56px;
        border-radius: 16px;
        color: var(--tone);
        background: color-mix(in srgb, var(--tone) 14%, transparent);
      }
      .hero .big svg.icon {
        width: 32px;
        height: 32px;
      }
      .hero h2 {
        margin: 0;
        font-size: 22px;
        line-height: 1.25;
      }
      .hero p {
        margin: 4px 0 0;
        font-size: 15px;
        line-height: 1.5;
        color: var(--mh-muted);
      }
      /* One row whatever the number of transports: devices, then one tile each. */
      .tiles {
        display: grid;
        grid-auto-flow: column;
        grid-auto-columns: minmax(0, 1fr);
        gap: 12px;
        margin: 14px 0 24px;
      }
      .tile {
        display: flex;
        gap: 12px;
        align-items: center;
        padding: 14px 16px;
      }
      .tile .ic {
        display: grid;
        place-items: center;
        width: 36px;
        height: 36px;
        border-radius: 10px;
        color: var(--mh-primary);
        background: var(--mh-primary-soft);
      }
      .tile .v {
        font-size: 18px;
        font-weight: 700;
        line-height: 1.2;
      }
      .tile .l {
        font-size: 13px;
        color: var(--mh-muted);
      }
      .tile .short {
        display: none;
      }
      .tile .bad {
        color: var(--mh-problem);
        font-weight: 600;
      }
      .bar {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 18px;
      }
      .offline {
        color: var(--mh-muted);
        background: var(--mh-surface-2);
        border: 1px solid var(--mh-border);
      }
      nav {
        display: inline-flex;
        padding: 4px;
        gap: 4px;
        border-radius: 999px;
        background: var(--mh-surface-2);
        border: 1px solid var(--mh-border);
      }
      nav button {
        border: 0;
        background: transparent;
        padding: 8px 18px;
        border-radius: 999px;
        font-size: 14px;
        font-weight: 600;
        color: var(--mh-muted);
        cursor: pointer;
      }
      nav button[aria-selected="true"] {
        background: var(--mh-surface);
        color: var(--mh-text);
        box-shadow: var(--mh-shadow);
      }
      .group {
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--mh-muted);
        margin: 20px 4px 10px;
      }
      .group {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
      }
      button.link {
        border: 0;
        padding: 0;
        background: none;
        font: inherit;
        color: var(--mh-primary);
        text-transform: none;
        letter-spacing: 0;
        font-weight: 600;
        cursor: pointer;
      }
      .group:first-child {
        margin-top: 0;
      }
      .list {
        display: grid;
        gap: 12px;
        align-items: start;
      }
      /* Findings are text; two columns keep their lines readable. */
      @media (min-width: 1280px) {
        .list {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }
      .empty {
        padding: 36px 24px;
        text-align: center;
      }
      .empty h3 {
        margin: 12px 0 6px;
      }
      .empty p {
        margin: 0;
        color: var(--mh-muted);
      }
      .empty .ic {
        color: var(--mh-ok);
      }
      .empty .ic svg.icon {
        width: 40px;
        height: 40px;
      }
      @media (max-width: 700px) {
        .page {
          padding: 16px 12px 40px;
        }
        .hero {
          padding: 18px;
          gap: 14px;
        }
        .hero h2 {
          font-size: 19px;
        }
        .tiles {
          gap: 8px;
          grid-auto-flow: row;
          grid-template-columns: 1fr 1fr;
        }
        .tile {
          padding: 10px 12px;
        }
        .tile .ic,
        .tile .long {
          display: none;
        }
        .tile .short {
          display: inline;
        }
        .tile .v {
          font-size: 16px;
        }
        .bar {
          flex-wrap: wrap;
        }
        nav {
          display: flex;
          flex: 1;
        }
        nav button {
          flex: 1;
          padding: 8px 6px;
        }
      }
    `,
  ];

  override connectedCallback(): void {
    super.connectedCallback();
    this.addEventListener("mh-dismiss", (e) => void this.dismiss(e as CustomEvent));
    this.addEventListener("mh-habit", (e) => void this.habit(e as CustomEvent));
    void this.load();
    // Some embedded browsers hold back live updates; asking now and then
    // keeps the page current until the live connection is back.
    this.poll = window.setInterval(() => {
      if (!this.live) void this.load();
    }, POLL_MS);
    document.addEventListener("visibilitychange", this.onVisible);
    this.stop = follow({
      finding: (finding) => this.upsert(finding),
      event: (event) => {
        this.events = [event, ...this.events].slice(0, 500);
        this.scheduleOverview();
      },
      status: (live) => {
        this.live = live;
        window.clearTimeout(this.offlineTimer);
        if (live) this.offline = false;
        else this.offlineTimer = window.setTimeout(() => (this.offline = true), 15000);
      },
    });
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.stop?.();
    window.clearInterval(this.poll);
    document.removeEventListener("visibilitychange", this.onVisible);
  }

  /** A page coming back from the background may have missed updates. */
  private onVisible = () => {
    if (document.visibilityState === "visible") void this.load();
  };

  private async load(): Promise<void> {
    try {
      const [overview, findings, events, topology] = await Promise.all([
        api.overview(),
        api.findings(),
        api.events(),
        api.topology(),
      ]);
      this.overview = overview;
      this.topology = topology;
      this.findings = findings;
      this.events = events;
      this.failed = false;
    } catch {
      this.failed = true;
    }
  }

  private scheduleOverview(): void {
    window.clearTimeout(this.refresh);
    this.refresh = window.setTimeout(async () => {
      [this.overview, this.topology] = await Promise.all([api.overview(), api.topology()]);
    }, 1500);
  }

  private async dismiss(event: CustomEvent<{ keys: string[]; dismissed: boolean }>) {
    const { keys, dismissed } = event.detail;
    if (!keys.length) return;
    await api.dismiss(keys, dismissed);
    this.findings = this.findings.map((f) =>
      keys.includes(f.key) ? { ...f, dismissed } : f,
    );
    this.overview = await api.overview();
  }

  private async habit(
    event: CustomEvent<{ subject: string; comesAndGoes: boolean | null }>,
  ) {
    const { subject, comesAndGoes } = event.detail;
    await api.habit(subject, comesAndGoes);
    await this.load();
  }

  private acknowledgeAll(findings: Finding[]): void {
    this.dispatchEvent(
      new CustomEvent("mh-dismiss", {
        detail: { keys: findings.map((f) => f.key), dismissed: true },
      }),
    );
  }

  private upsert(finding: Finding): void {
    const before = this.findings.find((f) => f.key === finding.key);
    // Live updates do not carry the dismissal; it holds for the same occurrence.
    const dismissed = before?.dismissed && before.started_at === finding.started_at;
    this.findings = [
      { ...before, ...finding, dismissed },
      ...this.findings.filter((f) => f.key !== finding.key),
    ];
    this.scheduleOverview();
    // Which story a finding belongs to is decided when findings are read.
    window.clearTimeout(this.regroup);
    this.regroup = window.setTimeout(async () => {
      this.findings = await api.findings();
    }, 1500);
  }

  private verdict(): TemplateResult {
    const current = this.findings.filter((f) => !f.ended_at && !f.dismissed);
    const problems = current.filter((f) => f.severity === "problem");
    const warnings = current.filter((f) => f.severity === "warning");
    const sourcesDown = Object.values(this.overview?.sources ?? {}).some(
      (s) => s.ok === false,
    );
    const tone = problems.length ? "problem" : warnings.length ? "warning" : "ok";
    const headline = problems.length
      ? t("status.problems", { count: problems.length })
      : warnings.length
        ? t("status.warnings", { count: warnings.length })
        : t("status.all_good");
    return html`<section class="card hero ${tone}">
      <span class="big"
        >${icon(
          tone === "problem"
            ? mdiAlertCircleOutline
            : tone === "warning"
              ? mdiAlertOutline
              : mdiShieldCheckOutline,
        )}</span
      >
      <div>
        <h2>${headline}</h2>
        ${sourcesDown ? html`<p>${t("status.sources_down")}</p>` : nothing}
      </div>
    </section>`;
  }

  private tiles(): TemplateResult {
    const overview = this.overview;
    if (!overview) return html``;
    // Devices away as usual, or whose absence the user knows, are no news.
    const unreachable = overview.devices.unavailable.filter(
      (d) => !d.usual && !d.known,
    ).length;
    return html`<div class="tiles">
      <div class="card tile">
        <span class="ic">${icon(mdiDevices)}</span>
        <div>
          <div class="v">${overview.devices.total ?? "–"}</div>
          <div class="l">${t("summary.devices")}</div>
          ${unreachable
            ? html`<div class="l bad">
                <span class="long"
                  >${t("summary.devices_unreachable", { count: unreachable })}</span
                ><span class="short"
                  >${t("summary.devices_unreachable_short", { count: unreachable })}</span
                >
              </div>`
            : nothing}
        </div>
      </div>
      ${overview.transports.map(
        (transport) => html`<div class="card tile">
          <span class="ic">${icon(TRANSPORT_ICON[transport.name] ?? mdiLan)}</span>
          <div>
            <div class="v">${transport.devices}</div>
            <div class="l">${t(`transport.${transport.name}.name`)}</div>
            ${transport.connected === false
              ? html`<div class="l bad">${t("summary.disconnected")}</div>`
              : transport.gateways !== null
                ? html`<div class="l">
                    ${t(`transport.${transport.name}.gateways`, { count: transport.gateways })}
                  </div>`
                : nothing}
          </div>
        </div>`,
      )}
    </div>`;
  }

  private findingList(): TemplateResult {
    const related = new Map<string, Finding[]>();
    for (const f of this.findings) {
      if (f.part_of) related.set(f.part_of, [...(related.get(f.part_of) ?? []), f]);
    }
    const shown = this.findings.filter(
      (f) => !f.part_of || !this.findings.some((p) => p.key === f.part_of),
    );
    const rank = (f: Finding) =>
      Math.min(SEVERITY_RANK[f.severity], ...(related.get(f.key) ?? []).map((r) => SEVERITY_RANK[r.severity]));
    const sorted = [...shown].sort(
      (a, b) => rank(a) - rank(b) || b.started_at.localeCompare(a.started_at),
    );
    const open = sorted.filter((f) => !f.ended_at && !f.dismissed);
    const newestFirst = (a: Finding, b: Finding) => b.started_at.localeCompare(a.started_at);
    const earlier = shown.filter((f) => f.ended_at && !f.dismissed).sort(newestFirst);
    const done = shown.filter((f) => f.dismissed).sort(newestFirst);
    if (!open.length && !earlier.length && !done.length) {
      return html`<div class="card empty">
        <span class="ic">${icon(mdiShieldCheckOutline)}</span>
        <h3>${t("finding.none_title")}</h3>
      </div>`;
    }
    return html`
      ${open.length
        ? html`<div class="group">${t("finding.open")}</div>
            <div class="list">
              ${open.map(
                (f, index) =>
                  html`<mh-finding
                    .finding=${f}
                    .related=${related.get(f.key) ?? []}
                    ?open=${index === 0}
                  ></mh-finding>`,
              )}
            </div>`
        : nothing}
      ${earlier.length
        ? html`<div class="group">
              <span>${t("finding.earlier")}</span>
              <button class="link" @click=${() => this.acknowledgeAll(earlier)}>
                ${t("finding.acknowledge_all")}
              </button>
            </div>
            <div class="list">
              ${earlier.map(
                (f, index) =>
                  html`<mh-finding
                    .finding=${f}
                    .related=${related.get(f.key) ?? []}
                    ?open=${!open.length && index === 0 && f.severity !== "info"}
                  ></mh-finding>`,
              )}
            </div>`
        : nothing}
      ${done.length
        ? html`<div class="group">
              <button
                class="link"
                aria-expanded=${this.showDone}
                @click=${() => (this.showDone = !this.showDone)}
              >
                ${t("finding.done", { count: done.length })}
              </button>
            </div>
            ${this.showDone
              ? html`<div class="list">
                  ${done.map(
                    (f) =>
                      html`<mh-finding
                        .finding=${f}
                        .related=${related.get(f.key) ?? []}
                      ></mh-finding>`,
                  )}
                </div>`
              : nothing}`
        : nothing}
    `;
  }

  override render(): TemplateResult {
    const tabs: Tab[] = ["findings", "timeline", "network"];
    return html`<div class="page">
      ${this.failed
        ? html`<section class="card empty"><p>${t("status.unavailable")}</p></section>`
        : nothing}
      ${this.verdict()} ${this.tiles()}
      <div class="bar">
      <nav role="tablist">
        ${tabs.map(
          (tab) =>
            html`<button
              role="tab"
              aria-selected=${this.tab === tab}
              @click=${() => (this.tab = tab)}
            >
              ${t(`nav.${tab}`)}
            </button>`,
        )}
      </nav>
      ${this.offline
        ? html`<span class="pill offline" title=${t("status.reconnecting")}
            >${t("status.offline")}</span
          >`
        : nothing}
      </div>
      ${this.tab === "findings"
        ? this.findingList()
        : this.tab === "timeline"
          ? html`<mh-timeline .events=${this.events}></mh-timeline>`
          : html`<mh-network
              .overview=${this.overview}
              .topology=${this.topology}
              .findings=${this.findings}
            ></mh-network>`}
    </div>`;
  }
}

customElements.define("mh-app", MhApp);
