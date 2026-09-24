// Turns what the add-on stores - translation keys and raw values - into the
// sentences the user reads.

import type { Finding, Link, TimelineEvent } from "./api";
import { duration, t, type Params } from "./i18n";

/** Values that stand for a thing the user knows by name. */
const NAMED: Record<string, string> = {
  device: "generic.device",
  neighbour: "generic.device",
  border_router: "generic.border_router",
  switch: "generic.switch",
};

function subjectName(finding: Finding): string | undefined {
  for (const subject of finding.subjects) {
    if (finding.names[subject]) return finding.names[subject];
  }
  return undefined;
}

/** Prepare a link's or finding's values for the sentence they go into. */
export function prepare(raw: Record<string, unknown>, finding?: Finding): Params {
  const params: Params = {};
  for (const [name, value] of Object.entries(raw)) {
    if (value === null || value === undefined || typeof value === "object") continue;
    params[name] = value as string | number;
  }
  if (finding && !params.device) {
    const known = subjectName(finding);
    if (known) params.device = known;
  }
  for (const [name, fallback] of Object.entries(NAMED)) {
    if (name in raw || name === "device") {
      if (!params[name]) params[name] = t(fallback);
    }
  }
  if ("origin" in raw) {
    const origin = String(raw.origin ?? "unknown");
    const by = raw.by ? String(raw.by) : null;
    params.origin =
      origin === "unknown"
        ? t("origin.unknown")
        : by
          ? t(`origin.${origin}`, { by })
          : t(`origin.${origin}_unknown`);
  }
  if (typeof raw.phase === "string") params.phase = t(`phase.${raw.phase}.name`);
  if (typeof raw.duration === "number") params.duration = duration(raw.duration);
  return params;
}

export function title(finding: Finding): string {
  return t(finding.title, prepare(finding.params, finding));
}

export function sentence(link: Link, finding: Finding): string {
  return t(link.key, prepare(link.params, finding));
}

/** The line a timeline entry shows. */
export function happening(event: TimelineEvent): string {
  if (event.kind === "source.status") {
    const source = (event.subject ?? "").replace(/^source:/, "");
    const state = event.data.ok ? "ok" : "down";
    return t(`timeline.source.status.${state}`, { source: t(`source.${source}`) });
  }
  const name = event.name ?? fallbackName(event.subject);
  return t(`timeline.${event.kind}`, { name });
}

function fallbackName(subject: string | null): string {
  if (subject?.startsWith("br:")) return t("generic.border_router");
  if (subject?.startsWith("entity:")) return t("generic.switch");
  return t("generic.device");
}
