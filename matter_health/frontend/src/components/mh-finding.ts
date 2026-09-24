// One finding, told as a chain: why - what happened - what it means - what to
// do. The headline and the chain are in everyday words; the technical detail
// is one click away for whoever wants it.

import {
  mdiAlertCircleOutline,
  mdiAlertOutline,
  mdiCheckCircleOutline,
  mdiChevronDown,
  mdiHelpCircleOutline,
  mdiInformationOutline,
  mdiLightbulbOnOutline,
  mdiLightningBoltOutline,
} from "@mdi/js";
import { LitElement, css, html, nothing, svg, type TemplateResult } from "lit";

import type { Finding, Link, Role, Severity } from "../api";
import { clock, duration, relative, t } from "../i18n";
import { base } from "../theme";
import { sentence, title } from "../words";

const ROLE_ICON: Record<Role, string> = {
  cause: mdiHelpCircleOutline,
  effect: mdiLightningBoltOutline,
  impact: mdiAlertCircleOutline,
  fix: mdiLightbulbOnOutline,
};

const SEVERITY_ICON = {
  problem: mdiAlertCircleOutline,
  warning: mdiAlertOutline,
  info: mdiInformationOutline,
};

export function icon(path: string): TemplateResult {
  return html`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">
    ${svg`<path d=${path}></path>`}
  </svg>`;
}

const ORDER: Role[] = ["cause", "effect", "impact", "fix"];

export class MhFinding extends LitElement {
  static override properties = {
    finding: { attribute: false },
    related: { attribute: false },
    nested: { type: Boolean, reflect: true },
    open: { type: Boolean, reflect: true },
    details: { state: true },
  };

  finding!: Finding;
  /** Findings that are consequences of this one, shown inside it. */
  related: Finding[] = [];
  nested = false;
  open = false;
  details = false;

  static override styles = [
    base,
    css`
      :host {
        display: block;
      }
      article {
        overflow: hidden;
        border-left: 5px solid var(--accent);
      }
      :host([severity="problem"]) {
        --accent: var(--mh-problem);
      }
      :host([severity="warning"]) {
        --accent: var(--mh-warning);
      }
      :host([severity="info"]) {
        --accent: var(--mh-info);
      }
      header {
        all: unset;
        display: grid;
        grid-template-columns: auto 1fr auto;
        gap: 14px;
        align-items: start;
        width: 100%;
        padding: 18px 20px;
        cursor: pointer;
        box-sizing: border-box;
      }
      header:focus-visible {
        outline: 2px solid var(--mh-primary);
        outline-offset: -2px;
      }
      .badge {
        display: grid;
        place-items: center;
        width: 40px;
        height: 40px;
        border-radius: 12px;
        color: var(--accent);
        background: color-mix(in srgb, var(--accent) 12%, transparent);
      }
      .badge svg.icon {
        width: 24px;
        height: 24px;
      }
      h3 {
        margin: 0 0 4px;
        font-size: 17px;
        line-height: 1.35;
        font-weight: 650;
      }
      .meta {
        display: flex;
        flex-wrap: wrap;
        gap: 6px 12px;
        align-items: center;
        font-size: 13px;
      }
      .ongoing {
        color: var(--accent);
        background: color-mix(in srgb, var(--accent) 12%, transparent);
      }
      .chevron {
        color: var(--mh-muted);
        transition: transform 0.2s ease;
        margin-top: 8px;
      }
      :host([open]) .chevron {
        transform: rotate(180deg);
      }
      .teaser {
        margin: 6px 0 0;
        font-size: 14px;
        line-height: 1.5;
        color: var(--mh-muted);
      }
      .chain {
        padding: 4px 20px 20px 20px;
      }
      ol {
        list-style: none;
        margin: 0;
        padding: 0;
      }
      li.step {
        position: relative;
        display: grid;
        grid-template-columns: 36px 1fr;
        gap: 14px;
        padding-bottom: 18px;
      }
      li.step:not(:last-child)::before {
        content: "";
        position: absolute;
        left: 17px;
        top: 38px;
        bottom: 2px;
        width: 2px;
        background: linear-gradient(
          var(--dot) 0%,
          color-mix(in srgb, var(--dot) 25%, transparent) 100%
        );
        border-radius: 2px;
      }
      .node {
        display: grid;
        place-items: center;
        width: 36px;
        height: 36px;
        border-radius: 50%;
        color: var(--ink);
        background: var(--soft);
        border: 2px solid var(--dot);
      }
      .role-cause {
        --ink: var(--mh-cause);
        --soft: var(--mh-cause-soft);
        --dot: var(--mh-cause-dot);
      }
      .role-effect {
        --ink: var(--mh-effect);
        --soft: var(--mh-effect-soft);
        --dot: var(--mh-effect-dot);
      }
      .role-impact {
        --ink: var(--mh-impact);
        --soft: var(--mh-impact-soft);
        --dot: var(--mh-impact-dot);
      }
      .role-fix {
        --ink: var(--mh-fix);
        --soft: var(--mh-fix-soft);
        --dot: var(--mh-fix-dot);
      }
      .label {
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--ink);
        margin: 6px 0 6px;
      }
      .texts {
        display: grid;
        gap: 8px;
      }
      .text {
        margin: 0;
        font-size: 15px;
        line-height: 1.55;
      }
      .fix .text {
        padding: 12px 14px;
        border-radius: 12px;
        background: var(--soft);
      }
      .guess {
        margin-right: 6px;
        color: var(--ink);
        background: var(--soft);
        vertical-align: 1px;
      }
      .when {
        font-size: 12px;
        color: var(--mh-muted);
        margin-left: 6px;
        white-space: nowrap;
      }
      .details-toggle {
        margin: 4px 0 0 50px;
        padding: 6px 12px;
        border: 1px solid var(--mh-border);
        border-radius: 999px;
        background: var(--mh-surface-2);
        color: var(--mh-muted);
        font-size: 13px;
        cursor: pointer;
      }
      .details {
        margin: 12px 0 0 50px;
        padding: 12px 14px;
        border-radius: 12px;
        background: var(--mh-surface-2);
        font-size: 13px;
        color: var(--mh-muted);
      }
      .details h4 {
        margin: 0 0 8px;
        font-size: 13px;
        color: var(--mh-text);
      }
      .details code {
        font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
        font-size: 12px;
        word-break: break-word;
      }
      dl {
        display: grid;
        grid-template-columns: max-content 1fr;
        gap: 4px 12px;
        margin: 0;
      }
      dt {
        font-weight: 600;
      }
      dd {
        margin: 0;
      }
      .consequences {
        color: var(--mh-muted);
        background: var(--mh-surface-2);
      }
      .related {
        display: grid;
        gap: 8px;
        margin: 0 0 14px 50px;
      }
      .related .label {
        color: var(--mh-muted);
        margin: 0 0 2px;
      }
      :host([nested]) article {
        box-shadow: none;
        border: 1px solid var(--mh-border);
        border-left: 4px solid var(--accent);
        background: var(--mh-surface-2);
      }
      :host([nested]) header {
        padding: 12px 14px;
      }
      :host([nested]) h3 {
        font-size: 15px;
      }
      :host([nested]) .badge {
        width: 32px;
        height: 32px;
        border-radius: 10px;
      }
      :host([nested]) .badge svg.icon {
        width: 20px;
        height: 20px;
      }
      @media (max-width: 600px) {
        .related {
          margin-left: 0;
        }
        header {
          padding: 16px;
          gap: 12px;
        }
        .chain {
          padding: 0 16px 16px;
        }
        .details-toggle,
        .details {
          margin-left: 0;
        }
      }
    `,
  ];

  /** The most serious of this finding and its consequences. */
  private severity(): Severity {
    const all = [this.finding, ...this.related].map((f) => f.severity);
    return all.includes("problem") ? "problem" : all.includes("warning") ? "warning" : "info";
  }

  override updated(): void {
    this.setAttribute("severity", this.severity());
  }

  private toggle(): void {
    this.open = !this.open;
  }

  private teaser(): string | undefined {
    const first = this.finding.chain.find((link) => link.role === "effect");
    return first ? sentence(first, this.finding) : undefined;
  }

  private step(role: Role, links: Link[]): TemplateResult {
    return html`<li class="step role-${role} ${role}">
      <span class="node">${icon(ROLE_ICON[role])}</span>
      <div>
        <div class="label">${t(`role.${role}`)}</div>
        <div class="texts">
          ${links.map(
            (link) =>
              html`<p class="text">
                ${link.confidence !== "certain"
                  ? html`<span class="pill guess">${t(`confidence.${link.confidence}`)}</span>`
                  : nothing}${sentence(link, this.finding)}${link.at && role !== "fix"
                  ? html`<span class="when">${clock(link.at)}</span>`
                  : nothing}
              </p>`,
          )}
        </div>
      </div>
    </li>`;
  }

  private technical(): TemplateResult {
    const params = this.finding.params;
    const rows = Object.entries(params).filter(
      ([, value]) =>
        value !== null &&
        value !== undefined &&
        value !== "" &&
        !(Array.isArray(value) && value.length === 0),
    );
    return html`<div class="details">
      <h4>${t("finding.technical")}</h4>
      <dl>
        ${rows.map(
          ([name, value]) =>
            html`<dt>${name}</dt>
              <dd><code>${Array.isArray(value) ? value.join(" · ") : String(value)}</code></dd>`,
        )}
        <dt>rule</dt>
        <dd><code>${this.finding.rule}</code></dd>
        <dt>${t("finding.evidence")}</dt>
        <dd>
          <code>${this.finding.chain.flatMap((link) => link.evidence).join(", ") || "–"}</code>
        </dd>
      </dl>
    </div>`;
  }

  override render(): TemplateResult {
    const finding = this.finding;
    const ended = finding.ended_at;
    const span = ended
      ? (new Date(ended).getTime() - new Date(finding.started_at).getTime()) / 1000
      : null;
    const groups = ORDER.map(
      (role) => [role, finding.chain.filter((link) => link.role === role)] as const,
    ).filter(([, links]) => links.length > 0);
    const teaser = this.teaser();
    return html`<article class="card">
      <header
        role="button"
        tabindex="0"
        aria-expanded=${this.open}
        @click=${this.toggle}
        @keydown=${(e: KeyboardEvent) =>
          (e.key === "Enter" || e.key === " ") && (e.preventDefault(), this.toggle())}
      >
        <span class="badge"
          >${icon(
            this.severity() === "info" && ended
              ? mdiCheckCircleOutline
              : SEVERITY_ICON[this.severity()],
          )}</span
        >
        <div>
          <h3>${title(finding)}</h3>
          <div class="meta muted">
            <span title=${clock(finding.started_at)}>${relative(finding.started_at)}</span>
            ${ended
              ? span && span >= 30
                ? html`<span>${t("finding.lasted", { duration: duration(span) })}</span>`
                : nothing
              : html`<span class="pill ongoing">${t("finding.ongoing")}</span>`}
            ${this.related.length
              ? html`<span class="pill consequences"
                  >${t("finding.related_count", { count: this.related.length })}</span
                >`
              : nothing}
          </div>
          ${!this.open && teaser ? html`<p class="teaser">${teaser}</p>` : nothing}
        </div>
        <span class="chevron">${icon(mdiChevronDown)}</span>
      </header>
      ${this.open
        ? html`<div class="chain">
            <ol>
              ${groups.map(([role, links]) => this.step(role, links))}
            </ol>
            ${this.related.length
              ? html`<div class="related">
                  <div class="label">${t("finding.related_title")}</div>
                  ${this.related.map(
                    (f) => html`<mh-finding nested .finding=${f}></mh-finding>`,
                  )}
                </div>`
              : nothing}
            <button class="details-toggle" @click=${() => (this.details = !this.details)}>
              ${this.details ? t("finding.hide_details") : t("finding.details")}
            </button>
            ${this.details ? this.technical() : nothing}
          </div>`
        : nothing}
    </article>`;
  }
}

customElements.define("mh-finding", MhFinding);
