// The network right now: bridges, devices that don't respond, and whether
// Matter Health itself can see everything it needs.

import {
  mdiCheckCircleOutline,
  mdiCloseCircleOutline,
  mdiLinkVariantOff,
  mdiRouterWireless,
} from "@mdi/js";
import { LitElement, css, html, nothing, type TemplateResult } from "lit";

import type { Overview } from "../api";
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
        font-size: 14px;
        color: var(--mh-muted);
      }
      p.facts {
        margin: 12px 0 0;
        font-size: 14px;
        color: var(--mh-muted);
      }
    `,
  ];

  override render(): TemplateResult {
    const overview = this.overview;
    if (!overview) return html``;
    const thread = overview.thread;
    return html`
      <section class="card">
        <h2>${t("summary.border_routers")}</h2>
        <p class="hint muted">${t("summary.border_routers_hint")}</p>
        ${overview.border_routers.length
          ? html`<ul class="grid">
              ${overview.border_routers.map(
                (router) =>
                  html`<li>
                    <span class="router">${icon(mdiRouterWireless)}</span>
                    <span>${router.name}</span>
                    <span class="sub">${router.vendor ?? ""}</span>
                  </li>`,
              )}
            </ul>`
          : html`<p class="empty">${t("network.border_routers_empty")}</p>`}
        ${thread?.role
          ? html`<p class="facts">
              ${t("network.role", {
                role: t(`thread_role.${thread.role}`),
              })}
              ${thread.router_count
                ? t("network.routers", { count: thread.router_count })
                : nothing}
            </p>`
          : nothing}
      </section>

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

      <section class="card">
        <h2>${t("network.sources_title")}</h2>
        <ul>
          ${Object.entries(overview.sources).map(
            ([name, status]) =>
              html`<li>
                <span class=${status.ok ? "ok" : status.ok === false ? "down" : "muted"}
                  >${icon(status.ok === false ? mdiCloseCircleOutline : mdiCheckCircleOutline)}</span
                >
                <span>${t(`source.${name}`)}</span>
                <span class="sub"
                  >${status.ok
                    ? t("source.ok")
                    : status.ok === false
                      ? t("source.down")
                      : t("source.waiting")}</span
                >
              </li>`,
          )}
        </ul>
      </section>
    `;
  }
}

customElements.define("mh-network", MhNetwork);
