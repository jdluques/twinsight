// Cliente REST de TwinSight. Las cookies de sesión viajan con credentials.
import type {
  Diagram, Element, EventItem, Latest, Mapping, Project, Source, StateMachine,
} from "./types";

const BASE = "/api/v1";

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const r = await fetch(BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("application/json") ? r.json() : r.text()) as Promise<T>;
}

export const api = {
  login: (key: string) => req<{ level: string }>("/auth/login", { method: "POST", body: JSON.stringify({ key }) }),
  projects: () => req<Project[]>("/projects"),
  elements: (pid: string, q?: string) =>
    req<Element[]>(`/projects/${pid}/elements${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  diagrams: (pid: string) => req<Diagram[]>(`/projects/${pid}/diagrams`),
  diagram: (pid: string, did: string) => req<Diagram>(`/diagrams/${did}?project_id=${pid}`),
  stateMachines: (pid: string) => req<StateMachine[]>(`/projects/${pid}/state-machines`),
  stateMachine: (pid: string, mid: string) => req<StateMachine>(`/state-machines/${mid}?project_id=${pid}`),
  mappings: (pid: string) => req<Mapping[]>(`/projects/${pid}/mappings`),
  sources: (pid: string) => req<Source[]>(`/projects/${pid}/sources`),
  representationSvg: (repId: string) => req<string>(`/representations/${repId}`),
  latest: (pid: string) => req<Latest>(`/projects/${pid}/telemetry/latest`),
  telemetry: (chid: string, limit = 120) =>
    req<{ points?: { ts: string; value: number }[] } | { ts: string; value: number }[]>(
      `/channels/${chid}/telemetry?limit=${limit}`,
    ),
  events: (pid: string) => req<EventItem[]>(`/projects/${pid}/events`),
  impact: (pid: string, elementId: string, maxDepth = 4) =>
    req<{ elements: string[]; visual_objects: string[]; channels: string[]; states: string[] }>(
      `/projects/${pid}/impact?element_id=${elementId}&max_depth=${maxDepth}`,
    ),
  neighborhood: (pid: string, nodeId: string, depth = 2) =>
    req<{ nodes: { id: string; label: string; name?: string }[]; edges: { source: string; target: string; type: string }[] }>(
      `/projects/${pid}/graph/neighborhood?node_id=${nodeId}&depth=${depth}`,
    ),
};
