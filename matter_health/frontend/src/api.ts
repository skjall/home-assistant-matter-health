// Talks to the add-on. Every URL is relative: behind ingress the page lives
// under a per-session path prefix that the page cannot know in advance.

export type Role = "cause" | "effect" | "impact" | "fix";
export type Confidence = "certain" | "likely" | "possible";
export type Severity = "info" | "warning" | "problem";

export interface Link {
  role: Role;
  key: string;
  params: Record<string, string | number | null>;
  at: string | null;
  confidence: Confidence;
  evidence: number[];
}

export interface Finding {
  key: string;
  rule: string;
  severity: Severity;
  title: string;
  params: Record<string, unknown>;
  started_at: string;
  ended_at: string | null;
  subjects: string[];
  names: Record<string, string>;
  /** Home Assistant device ids of the subjects, where known. */
  devices?: Record<string, string>;
  chain: Link[];
  /** Key of the finding whose story this one is part of. */
  part_of?: string | null;
  /** The user marked this occurrence as dealt with. */
  dismissed?: boolean;
}

export interface TimelineEvent {
  id: number;
  kind: string;
  at: string;
  source: string;
  subject: string | null;
  name: string | null;
  data: Record<string, unknown>;
}

export interface SourceStatus {
  ok: boolean | null;
  since: string | null;
  detail: string | null;
}

export interface BorderRouter {
  subject: string;
  name: string;
  vendor: string | null;
  model: string | null;
  /** Name of the Thread network the router belongs to. */
  network?: string | null;
  /** Whether that is the network Home Assistant uses. */
  own?: boolean;
}

export interface TopologyNode {
  id: string;
  subject: string | null;
  kind: "border_router" | "router" | "end_device" | "sleepy" | "unknown";
  /** The node this one takes its way in through; "home" for a border router. */
  parent: string | null;
  link: { rssi?: number | null; lqi?: number | null; strength?: string | null };
  /** Other relaying neighbours a relaying device could switch to. */
  alternatives: number;
  vendor: string | null;
  name: string | null;
  device_id?: string | null;
  available: boolean;
  /** Away and missing from the latest picture; shown where it was last. */
  missing?: boolean;
  /** Away as it usually is, or as the user knows. */
  resting?: boolean;
}

export interface Topology {
  at: string | null;
  nodes: TopologyNode[];
}

export interface UnavailableDevice {
  subject: string;
  name: string | null;
  device_id?: string | null;
  /** Away, but no longer than this device usually is. */
  usual?: boolean;
  /** The user acknowledged this absence. */
  known?: boolean;
  /** What the user said: comes and goes (true), always report (false). */
  comes_and_goes?: boolean | null;
}

export interface Overview {
  sources: Record<string, SourceStatus>;
  thread: {
    role: string | null;
    router_count: number | null;
    network_name: string | null;
  } | null;
  border_routers: BorderRouter[];
  devices: {
    total: number | null;
    unavailable: UnavailableDevice[];
  };
  open: Record<Severity, number>;
  now: string;
  /** Version of the page the add-on serves now. */
  build?: string | null;
}

/** The version of this page, as the add-on named it when serving it. */
const BUILD = new URL(import.meta.url).searchParams.get("v");

/**
 * Reload once when the add-on serves a newer page than this one.
 *
 * An app that keeps the page open across an update of the add-on would go on
 * running the old code against the new API and translations.
 */
function reloadIfOutdated(served: string | null | undefined): void {
  if (!served || !BUILD || served === BUILD) return;
  try {
    if (sessionStorage.getItem("mh-reloaded-for") === served) return;
    sessionStorage.setItem("mh-reloaded-for", served);
  } catch {
    // Without storage a reload loop cannot be ruled out; better stay put.
    return;
  }
  location.reload();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return (await response.json()) as T;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return (await response.json()) as T;
}

export const api = {
  overview: async () => {
    const overview = await get<Overview>("api/overview");
    reloadIfOutdated(overview.build);
    return overview;
  },
  topology: () => get<Topology>("api/topology"),
  findings: (days = 7) => get<Finding[]>(`api/findings?days=${days}`),
  events: (limit = 300) => get<TimelineEvent[]>(`api/events?limit=${limit}`),
  dismiss: (keys: string[], dismissed: boolean) =>
    post<{ keys: string[]; dismissed: boolean }>("api/dismiss", { keys, dismissed }),
  habit: (subject: string, comesAndGoes: boolean | null) =>
    post<{ subject: string }>("api/habit", { subject, comes_and_goes: comesAndGoes }),
};

export type StreamHandlers = {
  finding: (finding: Finding) => void;
  event: (event: TimelineEvent) => void;
  status: (live: boolean) => void;
};

/** Follow new findings and events; reconnects by itself. */
export function follow(handlers: StreamHandlers): () => void {
  const source = new EventSource("api/stream");
  source.addEventListener("open", () => handlers.status(true));
  source.addEventListener("error", () => handlers.status(false));
  source.addEventListener("finding", (message) =>
    handlers.finding(JSON.parse((message as MessageEvent).data) as Finding),
  );
  source.addEventListener("event", (message) =>
    handlers.event(JSON.parse((message as MessageEvent).data) as TimelineEvent),
  );
  return () => source.close();
}
