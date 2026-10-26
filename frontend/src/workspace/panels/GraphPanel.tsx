import { useEffect, useState } from "react";
import { Panel } from "../Panel";
import { api } from "../../api";
import { useStore } from "../../store";

interface GNode { id: string; label: string; name?: string }
interface GEdge { source: string; target: string; type: string }

const COLOR: Record<string, string> = {
  Element: "#2563eb", Block: "#2563eb", StateMachine: "#7c3aed",
  State: "#0891b2", Transition: "#64748b", VisualObject: "#0d9488", Channel: "#d97706",
};

export function GraphPanel() {
  const pid = useStore((s) => s.projectId);
  const [data, setData] = useState<{ nodes: GNode[]; edges: GEdge[] }>({ nodes: [], edges: [] });
  const hotSet = useStore((s) => s.hotSet);
  const setHover = useStore((s) => s.setHover);

  useEffect(() => {
    if (!pid) return;
    api.elements(pid, "MH-Z19B").then((els) => {
      const center = els.find((e) => e.name.includes("Aula A"));
      if (!center) return;
      api.neighborhood(pid, center.origin_id, 2).then((d) => setData({
        nodes: d.nodes.slice(0, 14), edges: d.edges,
      }));
    });
  }, [pid]);

  const W = 300, H = 280, cx = W / 2, cy = H / 2;
  const n = data.nodes.length || 1;
  const pos = (i: number): [number, number] =>
    i === 0 ? [cx, cy] : [cx + 105 * Math.cos((2 * Math.PI * i) / n), cy + 105 * Math.sin((2 * Math.PI * i) / n)];
  const idx: Record<string, number> = {};
  data.nodes.forEach((nd, i) => (idx[nd.id] = i));

  return (
    <Panel title="Grafo" badge="vecindario">
      {data.nodes.length === 0
        ? <div className="h-full flex items-center justify-center text-slate-400 text-sm">Cargando grafo…</div>
        : (
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full" preserveAspectRatio="xMidYMid meet">
          {data.edges.map((e, i) => {
            const a = idx[e.source], b = idx[e.target];
            if (a === undefined || b === undefined) return null;
            const [x1, y1] = pos(a), [x2, y2] = pos(b);
            return <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#cbd5e1" strokeWidth={1} />;
          })}
          {data.nodes.map((nd, i) => {
            const [x, y] = pos(i);
            const hot = hotSet.has(nd.id);
            const label = nd.label?.split(":")[0] ?? "Element";
            return (
              <g key={nd.id} className={hot ? "tw-hot" : ""} style={{ cursor: "pointer" }}
                 onMouseEnter={() => setHover(nd.id)} onMouseLeave={() => setHover(null)}>
                <circle cx={x} cy={y} r={hot ? 11 : 8} fill={COLOR[label] ?? "#64748b"}
                        stroke={hot ? "#d97706" : "#fff"} strokeWidth={hot ? 3 : 1.5} />
                <text x={x} y={y - 12} textAnchor="middle" fontSize={7} fill="#334155">
                  {(nd.name ?? label).slice(0, 14)}
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </Panel>
  );
}
