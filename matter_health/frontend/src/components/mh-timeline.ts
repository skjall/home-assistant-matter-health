// Who came, who went, what was switched - grouped by day, newest first.

import {
  mdiDevices,
  mdiLanDisconnect,
  mdiLinkVariant,
  mdiLinkVariantOff,
  mdiMinusCircleOutline,
  mdiPlusCircleOutline,
  mdiPowerPlugOffOutline,
  mdiPowerPlugOutline,
  mdiRouterWireless,
  mdiSourceBranch,
} from "@mdi/js";
import { LitElement, css, html, type TemplateResult } from "lit";

import type { TimelineEvent } from "../api";
import { day, t, time } from "../i18n";
import { base } from "../theme";
import { happening } from "../words";
import { icon } from "./mh-finding";

type Tone = "good" | "bad" | "neutral";

const LOOK: Record<string, [string, Tone]> = {
  "border_router.appeared": [mdiRouterWireless, "good"],
  "border_router.gone": [mdiLanDisconnect, "bad"],
  "matter.node_added": [mdiPlusCircleOutline, "good"],
  "matter.node_removed": [mdiMinusCircleOutline, "neutral"],
  "matter.node_available": [mdiLinkVariant, "good"],
  "matter.node_unavailable": [mdiLinkVariantOff, "bad"],
  "ha.power_off": [mdiPowerPlugOffOutline, "neutral"],
  "ha.power_on": [mdiPowerPlugOutline, "neutral"],
  "thread.leader_lost": [mdiSourceBranch, "bad"],
  "thread.leader_changed": [mdiSourceBranch, "neutral"],
  "thread.foreign_partition": [mdiSourceBranch, "bad"],
  "thread.partition_changed": [mdiSourceBranch, "neutral"],
  "commissioning.contact": [mdiDevices, "neutral"],
  "commissioning.completed": [mdiDevices, "good"],
  "commissioning.failed": [mdiDevices, "bad"],
};

export class MhTimeline extends LitElement {
  static override properties = { events: { attribute: false } };
  events: TimelineEvent[] = [];

  static override styles = [
    base,
    css`
      :host {
        display: block;
      }
      h2 {
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--mh-muted);
        margin: 24px 4px 10px;
      }
      h2:first-child {
        margin-top: 4px;
      }
      ul {
        list-style: none;
        margin: 0;
        padding: 6px 0;
      }
      li {
        display: grid;
        grid-template-columns: 36px 1fr auto;
        gap: 12px;
        align-items: center;
        padding: 10px 16px;
      }
      li + li {
        border-top: 1px solid var(--mh-border);
      }
      .dot {
        display: grid;
        place-items: center;
        width: 36px;
        height: 36px;
        border-radius: 10px;
        background: var(--mh-surface-2);
        color: var(--mh-muted);
      }
      .good .dot {
        color: var(--mh-ok);
        background: color-mix(in srgb, var(--mh-ok) 12%, transparent);
      }
      .bad .dot {
        color: var(--mh-problem);
        background: color-mix(in srgb, var(--mh-problem) 12%, transparent);
      }
      .what {
        font-size: 15px;
        line-height: 1.4;
      }
      time {
        font-size: 13px;
        color: var(--mh-muted);
        font-variant-numeric: tabular-nums;
      }
      .empty {
        padding: 32px 20px;
        text-align: center;
        color: var(--mh-muted);
      }
    `,
  ];

  override render(): TemplateResult {
    if (this.events.length === 0) {
      return html`<div class="card empty">${t("timeline.empty")}</div>`;
    }
    const days = new Map<string, TimelineEvent[]>();
    for (const event of this.events) {
      const key = new Date(event.at).toDateString();
      days.set(key, [...(days.get(key) ?? []), event]);
    }
    return html`${[...days.values()].map(
      (events) =>
        html`<h2>${day(events[0].at)}</h2>
          <ul class="card">
            ${events.map((event) => {
              const [path, tone] =
                event.kind === "source.status"
                  ? [mdiLinkVariantOff, (event.data.ok ? "good" : "bad") as Tone]
                  : (LOOK[event.kind] ?? [mdiDevices, "neutral" as Tone]);
              return html`<li class=${tone}>
                <span class="dot">${icon(path)}</span>
                <span class="what">${happening(event)}</span>
                <time datetime=${event.at}>${time(event.at)}</time>
              </li>`;
            })}
          </ul>`,
    )}`;
  }
}

customElements.define("mh-timeline", MhTimeline);
