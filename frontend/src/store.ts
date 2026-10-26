import { create } from "zustand";
import { api } from "./api";
import type { EventItem, Latest, Mapping, Severity, StateMachine, WsMessage } from "./types";

interface State {
  projectId: string | null;
  latest: Latest;
  activeState: Record<string, string>; // machineId -> nombre de estado activo
  severities: Record<string, Severity>; // id (element/visual) -> severidad
  mappings: Mapping[];
  resolution: Map<string, Set<string>>; // id -> contrapartes mapeadas
  machines: StateMachine[];
  events: EventItem[];
  hoverId: string | null;
  impactSet: Set<string>; // resaltado persistente del último impacto
  impactSeverity: Severity;
  hotSet: Set<string>; // conjunto resaltado efectivo (hover ∪ impacto)

  init: (pid: string) => Promise<void>;
  onWs: (msg: WsMessage) => void;
  setHover: (id: string | null) => void;
  channelFor: (visualOrElementId: string) => string | null;
}

function buildResolution(mappings: Mapping[]): Map<string, Set<string>> {
  const m = new Map<string, Set<string>>();
  for (const map of mappings) {
    const ids = map.endpoints.map((e) => e.id);
    for (const id of ids) {
      const set = m.get(id) ?? new Set<string>();
      for (const other of ids) if (other !== id) set.add(other);
      m.set(id, set);
    }
  }
  return m;
}

function computeHot(hoverId: string | null, resolution: Map<string, Set<string>>, impactSet: Set<string>): Set<string> {
  const hot = new Set<string>(impactSet);
  if (hoverId) {
    hot.add(hoverId);
    for (const c of resolution.get(hoverId) ?? []) hot.add(c);
  }
  return hot;
}

export const useStore = create<State>((set, get) => ({
  projectId: null,
  latest: {},
  activeState: {},
  severities: {},
  mappings: [],
  resolution: new Map(),
  machines: [],
  events: [],
  hoverId: null,
  impactSet: new Set(),
  impactSeverity: "warning",
  hotSet: new Set(),

  init: async (pid) => {
    const [mappings, latest, events, machines] = await Promise.all([
      api.mappings(pid).catch(() => []),
      api.latest(pid).catch(() => ({})),
      api.events(pid).catch(() => []),
      api.stateMachines(pid).catch(() => []),
    ]);
    const resolution = buildResolution(mappings);
    const activeState: Record<string, string> = {};
    // Resuelve el nombre del estado activo por máquina (carga inicial).
    await Promise.all(
      machines.map(async (m) => {
        try {
          const full = await api.stateMachine(pid, m.origin_id);
          const active = full.states.find((s) => s.origin_id === full.active_state_id);
          if (active) activeState[m.origin_id] = active.name;
        } catch { /* ignora */ }
      }),
    );
    set({ projectId: pid, mappings, resolution, latest, events, machines, activeState,
      hotSet: computeHot(null, resolution, new Set()) });
  },

  onWs: (msg) => {
    const s = get();
    if (msg.type === "telemetry") {
      set({ latest: { ...s.latest, [msg.channel_id]: { ts: msg.ts, value: msg.value } } });
    } else if (msg.type === "state_change") {
      set({ activeState: { ...s.activeState, [msg.machine_id]: msg.to } });
    } else if (msg.type === "rule_event") {
      set({ events: [msg.event, ...s.events].slice(0, 200) });
    } else if (msg.type === "impact_highlight") {
      const ids = new Set<string>([...msg.elements, ...msg.visual_objects, ...msg.channels, ...msg.states]);
      const severities = { ...s.severities };
      for (const id of [...msg.elements, ...msg.visual_objects]) severities[id] = msg.severity;
      set({ impactSet: ids, impactSeverity: msg.severity, severities,
        hotSet: computeHot(s.hoverId, s.resolution, ids) });
    }
  },

  setHover: (id) => {
    const s = get();
    set({ hoverId: id, hotSet: computeHot(id, s.resolution, s.impactSet) });
  },

  channelFor: (id) => {
    const counterparts = get().resolution.get(id);
    if (!counterparts) return null;
    // Un canal es un endpoint cuyo id no está en el grafo de elementos/visuales:
    // en la práctica, el canal es el UUID que también está en `latest`.
    const latest = get().latest;
    for (const c of counterparts) if (c in latest) return c;
    return null;
  },
}));
