"use client";

/**
 * Плитка дашборда: рендер Vega-Lite спеки, полученной с бэкенда.
 * Один конфиг -> один график; перетаскивание — на уровне react-grid-layout.
 */
import Chart from "react-vega/lib/Chart";

export default function DashboardTile({ tile }: { tile: { title: string; spec: Record<string, unknown> } }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
      <h3 className="mb-2 text-sm font-medium text-slate-700">{tile.title}</h3>
      <Chart spec={tile.spec as object} />
    </div>
  );
}
