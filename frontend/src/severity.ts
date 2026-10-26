import type { Severity } from "./types";

export const SEV_COLOR: Record<Severity, string> = {
  normal: "#16a34a",
  info: "#2563eb",
  warning: "#d97706",
  alarm: "#dc2626",
  stale: "#6b7280",
};

export const SEV_LABEL: Record<Severity, string> = {
  normal: "Normal",
  info: "Info",
  warning: "Advertencia",
  alarm: "Alarma",
  stale: "Sin datos",
};

export function sevColor(s: Severity | undefined): string {
  return SEV_COLOR[s ?? "normal"];
}
