// The network right now: bridges, devices that don't respond - split into
// news, devices away as they often are, and absences the user already knows -
// and whether Matter Health itself can see everything it needs.

import {
  mdiCloseCircleOutline,
  mdiEyeOffOutline,
  mdiLinkVariantOff,
  mdiPowerPlugOffOutline,
  mdiRouterWireless,
} from "@mdi/js";
import { LitElement, css, html, nothing, type TemplateResult } from "lit";

import type {
  BorderRouter,
  TransportSummary,
  Finding,
  Overview,
  Topology,
  UnavailableDevice,
} from "../api";
import { deviceName } from "../ha";
import { t } from "../i18n";
import { base } from "../theme";
import { icon } from "./mh-finding";
import "./mh-topology";

export class MhNetwork extends LitElement {
  static override properties = {
    overview: { attribute: false },
    topology: { attribute: false },
    findings: { attribute: false },
  };
  overview?: Overview;
  topology?: Topology;
  findings: Finding[] = [];

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
      ul {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        /* Side by side where there is room, one below the other on a phone. */
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 340px), 1fr));
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
      button.link {
        border: 0;
        background: none;
        padding: 0;
        font: inherit;
        font-size: 13px;
        color: var(--mh-primary);
        cursor: pointer;
      }
    `,
  ];

  /** Networks of the same transport nearby that carry nothing for this one. */
  private foreign(transport: TransportSummary): TemplateResult | typeof nothing {
    const networks = new Map<string, BorderRouter[]>();
    for (const router of transport.foreign ?? []) {
      const network = router.network ?? "?";
      networks.set(network, [...(networks.get(network) ?? []), router]);
    }
    if (!networks.size) return nothing;
    const name = transport.name;
    return html`<section class="card">
      <h2>${t(`transport.${name}.foreign_title`)}</h2>
      ${[...networks].map(
        ([network, routers]) =>
          html`<h3>${t(`transport.${name}.foreign_network`, { network })}</h3>
            <ul class="grid">
              ${routers.map((router) => this.router(router))}
            </ul>`,
      )}
    </section>`;
  }

  private router(router: BorderRouter): TemplateResult {
    return html`<li>
      <span class="router">${icon(mdiRouterWireless)}</span>
      <span>${router.name}</span>
      <span class="sub">${router.vendor ?? ""}</span>
    </li>`;
  }

  private device(
    device: UnavailableDevice,
    symbol: string,
    tone: string,
    action: TemplateResult | typeof nothing = nothing,
  ): TemplateResult {
    return html`<li>
      <span class=${tone}>${icon(symbol)}</span>
      <span>${deviceName(device.name ?? t("generic.device"), device.device_id)}</span>
      ${action !== nothing ? html`<span class="sub">${action}</span>` : nothing}
    </li>`;
  }

  private habit(device: UnavailableDevice, comesAndGoes: boolean | null): void {
    this.dispatchEvent(
      new CustomEvent("mh-habit", {
        detail: { subject: device.subject, comesAndGoes },
        bubbles: true,
        composed: true,
      }),
    );
  }

  override render(): TemplateResult {
    const overview = this.overview;
    if (!overview) return html``;
    // Only what is missing is worth a line; all present is the normal case.
    const missing = Object.entries(overview.sources).filter(([, status]) => !status.ok);
    const away = overview.devices.unavailable;
    const known = away.filter((d) => d.known);
    const usual = away.filter((d) => !d.known && (d.usual || d.comes_and_goes === true));
    const surprising = away.filter((d) => !known.includes(d) && !usual.includes(d));
    return html`
      <section class="card">
        <mh-topology .topology=${this.topology} .findings=${this.findings}></mh-topology>
      </section>

      ${overview.transports.map((transport) => this.foreign(transport))}

      <section class="card">
        <h2>${t("network.unreachable_title")}</h2>
        ${surprising.length
          ? html`<ul>
              ${surprising.map((device) => this.device(device, mdiLinkVariantOff, "down"))}
            </ul>`
          : html`<p class="empty">${t("network.unreachable_empty")}</p>`}
        ${usual.length
          ? html`<h3>${t("network.usual_title")}</h3>
              <ul>
                ${usual.map((device) =>
                  this.device(
                    device,
                    mdiPowerPlugOffOutline,
                    "muted",
                    html`<button class="link" @click=${() => this.habit(device, false)}>
                      ${t("network.always_report")}
                    </button>`,
                  ),
                )}
              </ul>`
          : nothing}
        ${known.length
          ? html`<h3>${t("network.known_title")}</h3>
              <ul>
                ${known.map((device) => this.device(device, mdiEyeOffOutline, "muted"))}
              </ul>`
          : nothing}
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
