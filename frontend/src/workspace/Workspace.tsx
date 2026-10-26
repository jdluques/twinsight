import type { Project } from "../types";
import { SysmlDiagramPanel } from "./panels/SysmlDiagramPanel";
import { StateMachinePanel } from "./panels/StateMachinePanel";
import { FloorPlanPanel } from "./panels/FloorPlanPanel";
import { TelemetryPanel } from "./panels/TelemetryPanel";
import { EventsPanel } from "./panels/EventsPanel";
import { GraphPanel } from "./panels/GraphPanel";

export function Workspace({ project }: { project: Project }) {
  return (
    <div className="h-full flex flex-col bg-slate-200">
      {/* Barra superior mínima */}
      <header className="flex items-center gap-3 px-4 h-12 bg-slate-900 text-slate-100 shrink-0">
        <div className="flex items-center gap-2 font-bold">
          <span className="inline-block w-5 h-5 rounded bg-blue-500" />
          TwinSight
        </div>
        <span className="text-slate-400">/</span>
        <span className="font-medium">{project.name}</span>
        <span className="ml-3 text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-200">Layout: Operación</span>
        <div className="ml-auto flex items-center gap-3 text-xs">
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-green-400" /> En vivo
          </span>
          <span className="px-2 py-0.5 rounded border border-slate-600">Editor</span>
        </div>
      </header>

      {/* Grilla de paneles del workspace */}
      <main className="flex-1 min-h-0 grid grid-cols-3 grid-rows-2 gap-2 p-2">
        <SysmlDiagramPanel />
        <StateMachinePanel />
        <GraphPanel />
        <FloorPlanPanel />
        <TelemetryPanel />
        <EventsPanel />
      </main>
    </div>
  );
}
