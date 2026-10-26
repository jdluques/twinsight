// Tipos del dominio de TwinSight (alineados con la API del backend).

export type Severity = "normal" | "info" | "warning" | "alarm" | "stale";

export interface Project {
  id: string;
  name: string;
  description: string;
  summary?: { sources: number; channels: number; unacknowledged: number };
}

export interface Element {
  origin_id: string;
  name: string;
  kind: string;
  severity?: Severity;
  value_properties?: string; // JSON string
  labels?: string[];
  note?: string;
}

export interface DiagramItem {
  subject_origin_id: string | null;
  item_type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  waypoints: number[][];
  label?: string | null;
}

export interface Diagram {
  origin_id: string;
  name: string;
  diagram_type: string; // bdd | ibd | stm | req | pkg
  items: DiagramItem[];
}

export interface StateNode {
  origin_id: string;
  name: string;
  kind?: string;
  is_failure_mode?: boolean;
}
export interface TransitionEdge {
  id: string;
  from: string;
  to: string;
  guard_text?: string | null;
  guard_status?: string;
}
export interface StateMachine {
  origin_id: string;
  name: string;
  element_id?: string | null;
  active_state_id?: string | null;
  states: StateNode[];
  transitions: TransitionEdge[];
}

export interface MappingEndpoint { id: string; label: string; name: string | null }
export interface Mapping { mapping_id: string; endpoints: MappingEndpoint[]; size: number; complete: boolean }

export interface Source {
  id: string;
  kind: string;
  filename: string;
  report?: { elements?: number; diagrams?: number; visual_objects?: number };
}

export interface EventItem {
  id: string;
  ts: string;
  origin_kind: string; // rule | transition
  origin_id: string;
  status: string; // fired | cleared | transitioned
  severity: Severity;
  payload?: Record<string, unknown>;
  acknowledged_at?: string | null;
}

export type Latest = Record<string, { ts: string; value: number }>;

// --- Mensajes WebSocket (servidor → cliente) ---
export type WsMessage =
  | { type: "telemetry"; channel_id: string; ts: string; value: number }
  | { type: "state_change"; machine_id: string; element_id: string; from: string; to: string; guard: string; ts: string; severity?: Severity }
  | { type: "rule_event"; event: EventItem }
  | { type: "impact_highlight"; cause: { kind: string; ref: string }; severity: Severity; elements: string[]; visual_objects: string[]; channels: string[]; states: string[] }
  | { type: "source_status"; datasource_id: string; connected: boolean };
