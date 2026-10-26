import { useStore } from "./store";
import type { WsMessage } from "./types";

// Cliente WebSocket con reconexión y re-suscripción.
export function connectWs(projectId: string): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let backoff = 500;

  const open = () => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/projects/${projectId}`);
    ws.onopen = () => {
      backoff = 500;
      ws?.send(JSON.stringify({ type: "subscribe", channels: "*" }));
    };
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data) as WsMessage;
        useStore.getState().onWs(msg);
      } catch { /* ignora frames no-JSON */ }
    };
    ws.onclose = () => {
      if (closed) return;
      setTimeout(open, backoff);
      backoff = Math.min(backoff * 2, 10000);
    };
    ws.onerror = () => ws?.close();
  };
  open();

  return () => { closed = true; ws?.close(); };
}
