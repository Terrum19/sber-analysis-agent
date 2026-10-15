"use client";

/**
 * Повторный доступ к дашборду из интерфейса платформы (без чата).
 * Конфиг (плитки + Vega-спеки) приходит с бэкенда по dashboard_id.
 */
import { useEffect, useState } from "react";
import DashboardTile from "@/components/canvas/DashboardTile";
import type { DashboardConfig } from "@/types/dashboard";

export default function DashboardPage({ params }: { params: { id: string } }) {
  const [config, setConfig] = useState<Pick<DashboardConfig, "title" | "tiles"> | null>(null);

  useEffect(() => {
    fetch(`/api/dashboards/${params.id}`)
      .then((r) => r.json() as Promise<DashboardConfig>)
      .then(setConfig)
      .catch(() => setConfig({ title: "Ошибка загрузки", tiles: [] }));
  }, [params.id]);

  return (
    <main className="p-6">
      <h1 className="mb-4 text-2xl font-bold">{config?.title ?? "Загрузка..."}</h1>
      <div className="grid grid-cols-2 gap-4">
        {(config?.tiles ?? []).map((tile, i) => (
          <DashboardTile key={i} tile={tile} />
        ))}
      </div>
    </main>
  );
}
