import { useEffect, useRef, useState } from "react";
import { Panel } from "../Panel";
import { api } from "../../api";
import { useStore } from "../../store";

const THRESHOLD = 1000; // umbral de CO2 (ppm)
const MAX_POINTS = 120;

export function TelemetryPanel() {
  const pid = useStore((s) => s.projectId);
  const [channel, setChannel] = useState<string | null>(null);
  const [points, setPoints] = useState<number[]>([]);
  const lastTs = useRef<string>("");

  // Encuentra el canal de CO2 de aula-A vía el mapeo del sensor.
  useEffect(() => {
    if (!pid) return;
    const ch = useStore.getState().channelFor("sensor-co2-aulaA");
    if (ch) {
      setChannel(ch);
      api.telemetry(ch, MAX_POINTS).then((r) => {
        const pts = Array.isArray(r) ? r : (r.points ?? []);
        setPoints(pts.map((p) => p.value));
      });
    }
  }, [pid]);

  const latestVal = useStore((s) => (channel ? s.latest[channel] : undefined));
  useEffect(() => {
    if (latestVal && latestVal.ts !== lastTs.current) {
      lastTs.current = latestVal.ts;
      setPoints((p) => [...p, latestVal.value].slice(-MAX_POINTS));
    }
  }, [latestVal]);

  const W = 300, H = 160, pad = 24;
  const max = Math.max(1800, ...points, THRESHOLD + 200);
  const min = Math.min(400, ...points);
  const xy = (v: number, i: number) => {
    const x = pad + (i / Math.max(1, MAX_POINTS - 1)) * (W - pad * 2);
    const y = H - pad - ((v - min) / (max - min)) * (H - pad * 2);
    return [x, y];
  };
  const path = points.map((v, i) => `${i === 0 ? "M" : "L"}${xy(v, i).join(",")}`).join(" ");
  const [, thY] = xy(THRESHOLD, 0);
  const current = points.length ? points[points.length - 1] : undefined;
  const over = current !== undefined && current > THRESHOLD;

  return (
    <Panel title="Telemetría — CO₂ Aula A" badge={current !== undefined ? `${Math.round(current)} ppm` : "co2_ppm"}>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-full" preserveAspectRatio="xMidYMid meet">
        <line x1={pad} y1={thY} x2={W - pad} y2={thY} stroke="#d97706" strokeDasharray="4 3" strokeWidth={1} />
        <text x={W - pad} y={thY - 3} textAnchor="end" fontSize={8} fill="#d97706">umbral {THRESHOLD}</text>
        <path d={path} fill="none" stroke={over ? "#dc2626" : "#2563eb"} strokeWidth={2} />
        {current !== undefined && (
          <circle {...pos(xy(current, points.length - 1))} r={3} fill={over ? "#dc2626" : "#2563eb"} />
        )}
      </svg>
    </Panel>
  );
}

function pos([cx, cy]: number[]) { return { cx, cy }; }
