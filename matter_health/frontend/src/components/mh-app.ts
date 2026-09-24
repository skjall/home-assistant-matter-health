// The whole page: a verdict at the top, then findings, timeline or network.

import {
  mdiAlertCircleOutline,
  mdiAlertOutline,
  mdiDevices,
  mdiHeartPulse,
  mdiRouterWireless,
  mdiShieldCheckOutline,
  mdiSourceBranch,
} from "@mdi/js";
import { LitElement, css, html, nothing, type TemplateResult } from "lit";

import { api, follow, type Finding, type Overview, type TimelineEvent } from "../api";
import { t } from "../i18n";
import { base, tokens } from "../theme";
import { icon } from "./mh-finding";
import "./mh-finding";
import "./mh-network";
import "./mh-timeline";

type Tab = "findings" | "timeline" | "network";

const SEVERITY_RANK = { problem: 0, warning: 1, info: 2 };

export class MhApp extends LitElement {
  static override properties = {
    overview: { state: true },
    findings: { state: true },
    events: { state: true },
    tab: { state: true },
    live: { state: true },
    failed: { state: true },
  };

  overview?: Overview;
  findings: Finding[] = [];
  events: TimelineEvent[] = [];
  tab: Tab = "findings";
  live = false;
  failed = false;
  private stop?: () => void;
  private refresh?: number;

  static override styles = [
    tokens,
    base,
    css`
      :host {
        display: block;
        min-height: 100vh;
        background: var(--mh-bg);
      }
      .page {
        max-width: 980px;
        margin: 0 auto;
        padding: 24px 20px 48px;
      }
      .top {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 20px;
      }
      .logo {
        display: grid;
        place-items: center;
        width: 40px;
        height: 40px;
        border-radius: 12px;
        color: #fff;
        background: linear-gradient(135deg, #0a7ee8, #5aa9f5);
      }
      .top h1 {
        margin: 0;
        font-size: 20px;
        line-height: 1.2;
      }
      .top p {
        margin: 2px 0 0;
        font-size: 13px;
      }
      .live {
        margin-left: auto;
        color: var(--mh-muted);
        background: var(--mh-surface);
        border: 1px solid var(--mh-border);
      }
      .live::before {
        content: "";
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--mh-muted);
      }
      .live.on::before {
        background: var(--mh-ok);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--mh-ok) 25%, transparent);
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
      .tiles {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
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
      .tile .bad {
        color: var(--mh-problem);
        font-weight: 600;
      }
      nav {
        display: inline-flex;
        padding: 4px;
        gap: 4px;
        border-radius: 999px;
        background: var(--mh-surface-2);
        border: 1px solid var(--mh-border);
        margin-bottom: 18px;
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
      .group:first-child {
        margin-top: 0;
      }
      .list {
        display: grid;
        gap: 12px;
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
          grid-template-columns: 1fr;
          gap: 8px;
        }
        nav {
          display: flex;
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
    void this.load();
    this.stop = follow({
      finding: (finding) => this.upsert(finding),
      event: (event) => {
        this.events = [event, ...this.events].slice(0, 500);
        this.scheduleOverview();
      },
      status: (live) => (this.live = live),
    });
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.stop?.();
  }

  private async load(): Promise<void> {
    try {
      const [overview, findings, events] = await Promise.all([
        api.overview(),
        api.findings(),
        api.events(),
      ]);
      this.overview = overview;
      this.findings = findings;
      this.events = events;
    } catch {
      this.failed = true;
    }
  }

  private scheduleOverview(): void {
    window.clearTimeout(this.refresh);
    this.refresh = window.setTimeout(async () => {
      this.overview = await api.overview();
    }, 1500);
  }

  private upsert(finding: Finding): void {
    this.findings = [finding, ...this.findings.filter((f) => f.key !== finding.key)];
    this.scheduleOverview();
  }

  private verdict(): TemplateResult {
    const problems = this.findings.filter((f) => !f.ended_at && f.severity === "problem");
    const warnings = this.findings.filter((f) => !f.ended_at && f.severity === "warning");
    const sourcesDown = Object.values(this.overview?.sources ?? {}).some(
      (s) => s.ok === false,
    );
    const tone = problems.length ? "problem" : warnings.length ? "warning" : "ok";
    const headline = problems.length
      ? t("status.problems", { count: problems.length })
      : warnings.length
        ? t("status.warnings", { count: warnings.length })
        : t("status.all_good");
    // The findings follow right below; repeating one here would say it twice.
    const detail = tone === "ok" ? t("status.all_good_detail") : "";
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
        ${detail ? html`<p>${detail}</p>` : nothing}
        ${sourcesDown ? html`<p>${t("status.sources_down")}</p>` : nothing}
      </div>
    </section>`;
  }

  private tiles(): TemplateResult {
    const overview = this.overview;
    if (!overview) return html``;
    const unreachable = overview.devices.unavailable.length;
    const role = overview.thread?.role;
    return html`<div class="tiles">
      <div class="card tile">
        <span class="ic">${icon(mdiDevices)}</span>
        <div>
          <div class="v">${overview.devices.total ?? "–"}</div>
          <div class="l">
            ${t("summary.devices")} ·
            ${unreachable
              ? html`<span class="bad"
                  >${t("summary.devices_unreachable", { count: unreachable })}</span
                >`
              : t("summary.devices_ok")}
          </div>
        </div>
      </div>
      <div class="card tile">
        <span class="ic">${icon(mdiRouterWireless)}</span>
        <div>
          <div class="v">${overview.border_routers.length}</div>
          <div class="l">${t("summary.border_routers")}</div>
        </div>
      </div>
      <div class="card tile">
        <span class="ic">${icon(mdiSourceBranch)}</span>
        <div>
          <div class="v">
            ${role && role !== "detached" && role !== "disabled"
              ? t("summary.mesh_ok")
              : t("summary.mesh_unknown")}
          </div>
          <div class="l">${t("summary.mesh")}</div>
        </div>
      </div>
    </div>`;
  }

  private findingList(): TemplateResult {
    const sorted = [...this.findings].sort(
      (a, b) =>
        SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] ||
        b.started_at.localeCompare(a.started_at),
    );
    const open = sorted.filter((f) => !f.ended_at);
    const earlier = [...this.findings]
      .filter((f) => f.ended_at)
      .sort((a, b) => b.started_at.localeCompare(a.started_at));
    if (!open.length && !earlier.length) {
      return html`<div class="card empty">
        <span class="ic">${icon(mdiShieldCheckOutline)}</span>
        <h3>${t("finding.none_title")}</h3>
        <p>${t("finding.none_text")}</p>
      </div>`;
    }
    return html`
      ${open.length
        ? html`<div class="group">${t("finding.open")}</div>
            <div class="list">
              ${open.map(
                (f, index) =>
                  html`<mh-finding .finding=${f} ?open=${index === 0}></mh-finding>`,
              )}
            </div>`
        : nothing}
      ${earlier.length
        ? html`<div class="group">${t("finding.earlier")}</div>
            <div class="list">
              ${earlier.map(
                (f, index) =>
                  html`<mh-finding
                    .finding=${f}
                    ?open=${!open.length && index === 0 && f.severity !== "info"}
                  ></mh-finding>`,
              )}
            </div>`
        : nothing}
    `;
  }

  override render(): TemplateResult {
    const tabs: Tab[] = ["findings", "timeline", "network"];
    return html`<div class="page">
      <div class="top">
        <span class="logo">${icon(mdiHeartPulse)}</span>
        <div>
          <h1>${t("app.title")}</h1>
          <p class="muted">${t("app.subtitle")}</p>
        </div>
        <span class="pill live ${this.live ? "on" : ""}"
          >${this.live ? t("status.live") : t("status.reconnecting")}</span
        >
      </div>
      ${this.failed
        ? html`<section class="card empty"><p>${t("status.unavailable")}</p></section>`
        : nothing}
      ${this.verdict()} ${this.tiles()}
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
      ${this.tab === "findings"
        ? this.findingList()
        : this.tab === "timeline"
          ? html`<mh-timeline .events=${this.events}></mh-timeline>`
          : html`<mh-network .overview=${this.overview}></mh-network>`}
    </div>`;
  }
}

customElements.define("mh-app", MhApp);
