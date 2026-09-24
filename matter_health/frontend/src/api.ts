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

export interface UnavailableDevice {
  subject: string;
  name: string | null;
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
  overview: () => get<Overview>("api/overview"),
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
