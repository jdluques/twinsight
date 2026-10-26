import { useEffect, useMemo, useState } from "react";
import { Panel } from "../Panel";
import { api } from "../../api";
import { useStore } from "../../store";
import { sevColor } from "../../severity";
import type { Diagram, Element } from "../../types";

export function SysmlDiagramPanel() {
  const pid = useStore((s) => s.projectId);
  const [diagram, setDiagram] = useState<Diagram | null>(null);
  const [elements, setElements] = useState<Record<string, Element>>({});
  const hotSet = useStore((s) => s.hotSet);
  const severities = useStore((s) => s.severities);
  const setHover = useStore((s) => s.setHover);

  useEffect(() => {
    if (!pid) return;
    api.diagrams(pid).then((ds) => {
      const bdd = ds.find((d) => d.diagram_type === "bdd") ?? ds[0];
      setDiagram(bdd ?? null);
    });
    api.elements(pid).then((els) => {
      const map: Record<string, Element> = {};
      for (const e of els) map[e.origin_id] = e;
      setElements(map);
    });
  }, [pid]);

  const bounds = useMemo(() => {
    const items = diagram?.items.filter((i) => i.item_type === "BlockItem") ?? [];
    if (!items.length) return { w: 400, h: 300 };
    const maxX = Math.max(...items.map((i) => i.x + i.width));
    const maxY = Math.max(...items.map((i) => i.y + i.height));
    return { w: maxX + 40, h: maxY + 40 };
  }, [diagram]);

  return (
    <Panel title={diagram?.name ?? "Diagrama SysML"} badge="BDD · modelo.gaphor">
      <svg viewBox={`0 0 ${bounds.w} ${bounds.h}`} className="w-full h-full" preserveAspectRatio="xMidYMid meet">
        {diagram?.items.filter((i) => i.item_type === "BlockItem").map((it, idx) => {
          const sid = it.subject_origin_id ?? "";
          const el = elements[sid];
          const hot = hotSet.has(sid);
          const sev = severities[sid] ?? el?.severity ?? "normal";
          const color = hot ? sevColor(sev) : "#334155";
          return (
            <g key={idx} className={hot ? "tw-hot" : ""} style={{ cursor: "pointer" }}
               onMouseEnter={() => setHover(sid)} onMouseLeave={() => setHover(null)}>
              <rect x={it.x} y={it.y} width={it.width} height={it.height} rx={3}
                    fill="#fff" stroke={color} strokeWidth={hot ? 3 : 1.2} />
              <text x={it.x + it.width / 2} y={it.y + 15} textAnchor="middle"
                    fontSize={9} fill="#64748b">«block»</text>
              <text x={it.x + it.width / 2} y={it.y + 30} textAnchor="middle"
                    fontSize={11} fontWeight="600" fill="#0f172a">
                {(el?.name ?? "").slice(0, 28)}
              </text>
            </g>
          );
        })}
      </svg>
    </Panel>
  );
}
