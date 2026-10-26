import { useEffect, useRef, useState } from "react";
import { Panel } from "../Panel";
import { api } from "../../api";
import { useStore } from "../../store";
import { sevColor } from "../../severity";

const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();

export function FloorPlanPanel() {
  const pid = useStore((s) => s.projectId);
  const [svg, setSvg] = useState<string>("");
  const ref = useRef<HTMLDivElement>(null);
  const latest = useStore((s) => s.latest);
  const hotSet = useStore((s) => s.hotSet);
  const activeState = useStore((s) => s.activeState);
  const severities = useStore((s) => s.severities);

  useEffect(() => {
    if (!pid) return;
    api.sources(pid).then(async (sources) => {
      const svgSrc = sources.find((s) => s.kind === "svg");
      if (!svgSrc) return;
      const text = await api.representationSvg(svgSrc.id);
      setSvg(text);
    });
  }, [pid]);

  // Ajusta el SVG inyectado y conecta el hover a los objetos mapeables.
  useEffect(() => {
    const root = ref.current?.querySelector("svg");
    if (!root) return;
    root.setAttribute("width", "100%");
    root.setAttribute("height", "100%");
    root.setAttribute("preserveAspectRatio", "xMidYMid meet");
    const setHover = useStore.getState().setHover;
    const mapped = Array.from(root.querySelectorAll<SVGGElement>("g[id]"));
    const cleanups: (() => void)[] = [];
    for (const g of mapped) {
      const id = g.getAttribute("id")!;
      const enter = () => setHover(id);
      const leave = () => setHover(null);
      g.style.cursor = "pointer";
      g.addEventListener("mouseenter", enter);
      g.addEventListener("mouseleave", leave);
      cleanups.push(() => { g.removeEventListener("mouseenter", enter); g.removeEventListener("mouseleave", leave); });
    }
    return () => cleanups.forEach((c) => c());
  }, [svg]);

  // Superpone valores en vivo, capas por estado y resaltado.
  useEffect(() => {
    const root = ref.current?.querySelector("svg");
    if (!root) return;
    const st = useStore.getState();
    let overlay = root.querySelector("#tw-overlay") as SVGGElement | null;
    if (!overlay) {
      overlay = document.createElementNS("http://www.w3.org/2000/svg", "g");
      overlay.setAttribute("id", "tw-overlay");
      root.appendChild(overlay);
    }
    overlay.innerHTML = "";

    const activeName = Object.values(activeState)[0];

    for (const g of Array.from(root.querySelectorAll<SVGGElement>("g[id]"))) {
      const id = g.getAttribute("id")!;
      if (id === "tw-overlay") continue;
      let bbox: DOMRect;
      try { bbox = (g as unknown as SVGGraphicsElement).getBBox(); } catch { continue; }

      // Valor en vivo del canal mapeado.
      const ch = st.channelFor(id);
      if (ch && latest[ch]) {
        const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
        t.setAttribute("x", String(bbox.x + bbox.width / 2));
        t.setAttribute("y", String(bbox.y + bbox.height + 12));
        t.setAttribute("text-anchor", "middle");
        t.setAttribute("font-size", "10");
        t.setAttribute("font-weight", "600");
        t.setAttribute("fill", "#0f172a");
        t.textContent = String(Math.round(latest[ch].value * 10) / 10);
        overlay.appendChild(t);
      }

      // Resaltado cruzado.
      if (hotSet.has(id)) {
        const r = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        r.setAttribute("x", String(bbox.x - 4)); r.setAttribute("y", String(bbox.y - 4));
        r.setAttribute("width", String(bbox.width + 8)); r.setAttribute("height", String(bbox.height + 8));
        r.setAttribute("rx", "4"); r.setAttribute("fill", "none");
        r.setAttribute("stroke", sevColor(severities[id] ?? "warning"));
        r.setAttribute("stroke-width", "3");
        r.setAttribute("class", "tw-hot");
        overlay.appendChild(r);
      }

      // Capas por estado (rejilla de ventilación).
      if (id.includes("rejilla") && activeName) {
        for (const layer of Array.from(g.querySelectorAll<SVGGElement>("g"))) {
          const label = layer.getAttribute("inkscape:label") || layer.getAttribute("id") || "";
          if (label.startsWith("estado:")) {
            const name = norm(label.slice("estado:".length));
            layer.style.display = name === norm(activeName) ? "" : "none";
          }
        }
      }
    }
  }, [svg, latest, hotSet, activeState, severities]);

  return (
    <Panel title="Plano — Piso 3" badge="piso.svg · importado">
      {svg
        ? <div ref={ref} className="w-full h-full p-1" dangerouslySetInnerHTML={{ __html: svg }} />
        : <div className="h-full flex items-center justify-center text-slate-400 text-sm">Cargando plano…</div>}
    </Panel>
  );
}
