import { useEffect, useState } from "react";
import { Panel } from "../Panel";
import { api } from "../../api";
import { useStore } from "../../store";
import type { StateMachine } from "../../types";

// Normaliza nombres de estado para comparar (sin acentos, minúsculas).
const norm = (s: string) =>
  s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();

export function StateMachinePanel() {
  const pid = useStore((s) => s.projectId);
  const [sm, setSm] = useState<StateMachine | null>(null);
  const activeState = useStore((s) => (sm ? s.activeState[sm.origin_id] : undefined));

  useEffect(() => {
    if (!pid) return;
    api.stateMachines(pid).then(async (list) => {
      if (!list.length) return;
      const full = await api.stateMachine(pid, list[0].origin_id);
      setSm(full);
    });
  }, [pid]);

  if (!sm) return <Panel title="Máquina de estados"><Empty /></Panel>;

  // Estados reales (sin pseudoestados) dispuestos en columna.
  const states = sm.states.filter((s) => norm(s.kind ?? "state") !== "initial");
  const activeName = activeState ?? states[0]?.name;
  const yOf = (i: number) => 40 + i * 78;
  const posByName: Record<string, { x: number; y: number }> = {};
  states.forEach((s, i) => (posByName[s.origin_id] = { x: 150, y: yOf(i) }));

  const severityOf = (name: string) =>
    norm(name) === "alarma" ? "#dc2626" : norm(name) === "forzada" ? "#d97706" : "#16a34a";

  return (
    <Panel title={`STM — ${sm.name}`} badge="interpretada en vivo">
      <svg viewBox="0 0 300 280" className="w-full h-full" preserveAspectRatio="xMidYMid meet">
        {/* Transiciones */}
        {sm.transitions.map((t, i) => {
          const a = posByName[t.from], b = posByName[t.to];
          if (!a || !b) return null;
          const midY = (a.y + b.y) / 2;
          const offset = a.y < b.y ? 60 : -60;
          return (
            <g key={i} stroke="#94a3b8" fill="none">
              <path d={`M${a.x + offset / 2},${a.y} C${a.x + offset},${midY} ${b.x + offset},${midY} ${b.x + offset / 2},${b.y}`}
                    markerEnd="url(#arrow)" />
              {t.guard_text && (
                <text x={a.x + offset + 6} y={midY} fontSize={8}
                      fill={t.guard_status === "inert" ? "#dc2626" : "#475569"}>
                  [{t.guard_text}]
                </text>
              )}
            </g>
          );
        })}
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
            <path d="M0,0 L7,3 L0,6 Z" fill="#94a3b8" />
          </marker>
        </defs>
        {/* Estados */}
        {states.map((s) => {
          const p = posByName[s.origin_id];
          const active = norm(s.name) === norm(activeName ?? "");
          const c = severityOf(s.name);
          return (
            <g key={s.origin_id} className={active ? "tw-hot" : ""}>
              <rect x={p.x - 55} y={p.y - 20} width={110} height={40} rx={8}
                    fill={active ? c : "#fff"} stroke={c} strokeWidth={active ? 3 : 1.5} />
              <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize={12} fontWeight="600"
                    fill={active ? "#fff" : "#0f172a"}>{s.name}</text>
              {active && <text x={p.x} y={p.y + 34} textAnchor="middle" fontSize={8} fill={c}>estado activo</text>}
            </g>
          );
        })}
      </svg>
    </Panel>
  );
}

function Empty() {
  return <div className="h-full flex items-center justify-center text-slate-400 text-sm">Sin máquinas de estado</div>;
}
