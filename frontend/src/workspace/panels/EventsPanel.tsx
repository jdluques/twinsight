import { Panel } from "../Panel";
import { useStore } from "../../store";
import { sevColor, SEV_LABEL } from "../../severity";
import type { Severity } from "../../types";

export function EventsPanel() {
  const events = useStore((s) => s.events);

  return (
    <Panel title="Eventos" badge="en vivo">
      <div className="h-full overflow-auto text-sm divide-y divide-slate-100">
        {events.length === 0 && (
          <div className="p-3 text-slate-400">Sin eventos todavía.</div>
        )}
        {events.map((e) => {
          const sev = (e.severity ?? "info") as Severity;
          const kind = e.origin_kind === "transition" ? "Transición" : "Regla";
          const to = (e.payload as any)?.to;
          return (
            <div key={e.id} className="flex items-start gap-2 px-3 py-2">
              <span className="mt-1 w-2 h-2 rounded-full shrink-0" style={{ background: sevColor(sev) }} />
              <div className="min-w-0">
                <div className="flex gap-2">
                  <span className="text-xs font-semibold px-1.5 rounded"
                        style={{ background: sevColor(sev) + "22", color: sevColor(sev) }}>
                    {SEV_LABEL[sev]}
                  </span>
                  <span className="text-slate-700">{kind} · {e.status}</span>
                </div>
                <div className="text-xs text-slate-500 truncate">
                  {to ? `→ ${to}` : ""} {new Date(e.ts).toLocaleTimeString()}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
