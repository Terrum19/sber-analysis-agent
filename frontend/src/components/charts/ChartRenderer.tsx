"use client";

/**
 * Универсальный рендерер: Vega-Lite (основной) или ECharts (тяжёлые
 * дашборды). Выбор по маркеру в спеке, чтобы фронт не зависел от бэка.
 */
import { VegaLite, type VisualizationSpec } from "react-vega";

export default function ChartRenderer({ spec, height = 300 }: { spec: Record<string, unknown>; height?: number }) {
  return (
    <div style={{ height }}>
      <VegaLite spec={spec as VisualizationSpec} />
    </div>
  );
}
