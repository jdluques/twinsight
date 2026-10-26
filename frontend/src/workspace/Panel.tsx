import type { ReactNode } from "react";

export function Panel({ title, badge, children }: { title: string; badge?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col rounded-lg border border-slate-700 bg-white overflow-hidden shadow-sm">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-slate-200 bg-slate-50 text-sm font-semibold text-slate-700">
        <span className="truncate">{title}</span>
        {badge && <span className="ml-auto text-xs font-normal text-slate-500">{badge}</span>}
      </div>
      <div className="flex-1 min-h-0 relative">{children}</div>
    </div>
  );
}
