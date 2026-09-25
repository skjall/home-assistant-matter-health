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
  /** Unique across transports. */
  id: string;
  subject: string | null;
  /** The transport this node belongs to: thread, wifi, ethernet. */
  transport: string;
  /** What the node is on its transport, in words every transport shares. */
  kind: "gateway" | "relay" | "device" | "sleepy" | "unknown";
  /** The node this one takes its way in through; "home" for a gateway. */
  parent: string | null;
  link: {
    rssi?: number | null;
    quality?: "strong" | "medium" | "weak" | null;
  };
  /** Other relaying neighbours a relaying device could switch to. */
  alternatives: number;
  vendor: string | null;
  /** What the transport has to add, such as a Wi-Fi channel. */
  detail?: Record<string, string | number | null>;
  name: string | null;
  device_id?: string | null;
  available: boolean;
  /** Away and missing from the latest picture; shown where it was last. */
  missing?: boolean;
  /** Away as it usually is, or as the user knows. */
  resting?: boolean;
  /** A Matter bridge: other networks' devices hang on it. */
  bridge?: boolean;
  /** Behind a bridge, on a network that is not Matter's. */
  bridged?: boolean;
  /** How it reaches the home network, where network equipment tells. */
  uplink?: Uplink | null;
  /** Thread: its role in the mesh (leader, router, child, disabled). */
  role?: string | null;
  /** Thread: in another partition than Home Assistant's border router. */
  apart?: boolean;
  /** Thread: how often it found the channel busy lately, per hour. */
  channel_busy_per_hour?: number;
}

/** A gateway's connection to the home network, as an integration tells it. */
export interface Uplink {
  wired: boolean;
  /** The access point, for a Wi-Fi connection. */
  via: string | null;
  ssid: string | null;
}

export interface Topology {
  nodes: TopologyNode[];
}

/** What the overview says about one transport. */
export interface TransportSummary {
  name: string;
  devices: number;
  /** What connects it to the home network; null where nothing does. */
  gateways: number | null;
  /** Whether it works as a whole; null where there is nothing to tell. */
  connected: boolean | null;
  /** Thread: border routers of other networks nearby. */
  foreign?: BorderRouter[];
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
  devices: {
    total: number | null;
    unavailable: UnavailableDevice[];
  };
  transports: TransportSummary[];
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
