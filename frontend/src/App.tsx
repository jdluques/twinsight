import { useEffect, useState } from "react";
import { api } from "./api";
import { useStore } from "./store";
import { connectWs } from "./ws";
import { Workspace } from "./workspace/Workspace";
import type { Project } from "./types";

export function App() {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [project, setProject] = useState<Project | null>(null);
  const init = useStore((s) => s.init);

  useEffect(() => {
    let disconnect: (() => void) | undefined;
    (async () => {
      try {
        // Demo: autentica como editor con la clave de demostración.
        await api.login("editor-demo").catch(() => undefined);
        const projects = await api.projects();
        if (!projects.length) { setStatus("error"); return; }
        const p = projects[0];
        setProject(p);
        await init(p.id);
        disconnect = connectWs(p.id);
        setStatus("ready");
      } catch {
        setStatus("error");
      }
    })();
    return () => disconnect?.();
  }, [init]);

  if (status === "loading")
    return <Center>Cargando TwinSight…</Center>;
  if (status === "error")
    return <Center>No se pudo conectar al servidor. ¿Está corriendo el backend?</Center>;
  return <Workspace project={project!} />;
}

function Center({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-full flex items-center justify-center bg-slate-900 text-slate-300">
      {children}
    </div>
  );
}
