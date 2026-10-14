"use client";

/**
 * Универсальный рендерер: Vega-Lite (основной) или ECharts (тяжёлые
 * дашборды). Выбор по маркеру в спеке, чтобы фронт не зависел от бэка.
 */
import Chart from "react-vega/lib/Chart";

export default function ChartRenderer({ spec, height = 300 }: { spec: Record<string, unknown>; height?: number }) {
  return (
    <div style={{ height }}>
      <Chart spec={spec as object} />
    </div>
  );
}
