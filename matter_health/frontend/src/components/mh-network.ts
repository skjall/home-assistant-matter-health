// The network right now: bridges, devices that don't respond, and whether
// Matter Health itself can see everything it needs.

import {
  mdiCloseCircleOutline,
  mdiLinkVariantOff,
  mdiRouterWireless,
} from "@mdi/js";
import { LitElement, css, html, nothing, type TemplateResult } from "lit";

import type { BorderRouter, Overview } from "../api";
import { t } from "../i18n";
import { base } from "../theme";
import { icon } from "./mh-finding";

export class MhNetwork extends LitElement {
  static override properties = { overview: { attribute: false } };
  overview?: Overview;

  static override styles = [
    base,
    css`
      :host {
        display: grid;
        gap: 16px;
      }
      section {
        padding: 18px 20px;
      }
      h2 {
        margin: 0 0 4px;
        font-size: 16px;
      }
      .hint {
        margin: 0 0 14px;
        font-size: 14px;
      }
      ul {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        gap: 8px;
      }
      li {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 10px 12px;
        border-radius: 12px;
        background: var(--mh-surface-2);
        font-size: 15px;
      }
      li .sub {
        margin-left: auto;
        font-size: 13px;
        color: var(--mh-muted);
        text-align: right;
      }
      .ok {
        color: var(--mh-ok);
      }
      .down {
        color: var(--mh-problem);
      }
      .router {
        color: var(--mh-primary);
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
        gap: 8px;
      }
      .empty {
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 0;
        font-size: 14px;
        color: var(--mh-muted);
      }
      h3 {
        margin: 12px 0 8px;
        font-size: 14px;
        font-weight: 600;
      }
      p.facts {
        margin: 12px 0 0;
        font-size: 14px;
        color: var(--mh-muted);
      }
    `,
  ];

  private router(router: BorderRouter): TemplateResult {
    return html`<li>
      <span class="router">${icon(mdiRouterWireless)}</span>
      <span>${router.name}</span>
      <span class="sub">${router.vendor ?? ""}</span>
    </li>`;
  }

  override render(): TemplateResult {
    const overview = this.overview;
    if (!overview) return html``;
    const thread = overview.thread;
    // Only what is missing is worth a line; all present is the normal case.
    const missing = Object.entries(overview.sources).filter(([, status]) => !status.ok);
    const own = overview.border_routers.filter((r) => r.own !== false);
    const foreign = new Map<string, BorderRouter[]>();
    for (const router of overview.border_routers.filter((r) => r.own === false)) {
      const network = router.network ?? "?";
      foreign.set(network, [...(foreign.get(network) ?? []), router]);
    }
    return html`
      <section class="card">
        <h2>${t("summary.border_routers")}</h2>
        ${own.length
          ? html`<ul class="grid">
              ${own.map((router) => this.router(router))}
            </ul>`
          : html`<p class="empty">${t("network.border_routers_empty")}</p>`}
        ${thread?.role
          ? html`<p class="facts">
              ${t("network.facts", {
                role: t(`thread_role.${thread.role}`),
                count: thread.router_count ?? "–",
              })}
            </p>`
          : nothing}
      </section>

      ${foreign.size
        ? html`<section class="card">
            <h2>${t("network.foreign_title")}</h2>
            <p class="hint muted">${t("network.foreign_hint")}</p>
            ${[...foreign].map(
              ([network, routers]) =>
                html`<h3>${t("network.foreign_network", { network })}</h3>
                  <ul class="grid">
                    ${routers.map((router) => this.router(router))}
                  </ul>`,
            )}
          </section>`
        : nothing}

      <section class="card">
        <h2>${t("network.unreachable_title")}</h2>
        ${overview.devices.unavailable.length
          ? html`<ul>
              ${overview.devices.unavailable.map(
                (device) =>
                  html`<li>
                    <span class="down">${icon(mdiLinkVariantOff)}</span>
                    <span>${device.name ?? t("generic.device")}</span>
                  </li>`,
              )}
            </ul>`
          : html`<p class="empty">${t("network.unreachable_empty")}</p>`}
      </section>

      ${missing.length
        ? html`<section class="card">
            <h2>${t("network.sources_missing")}</h2>
            <ul>
              ${missing.map(
                ([name, status]) =>
                  html`<li>
                    <span class=${status.ok === false ? "down" : "muted"}
                      >${icon(mdiCloseCircleOutline)}</span
                    >
                    <span>${t(`source.${name}`)}</span>
                    <span class="sub"
                      >${status.ok === false ? t("source.down") : t("source.waiting")}</span
                    >
                  </li>`,
              )}
            </ul>
          </section>`
        : nothing}
    `;
  }
}

customElements.define("mh-network", MhNetwork);
