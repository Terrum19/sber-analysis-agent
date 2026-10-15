"use client";

/**
 * Универсальный рендерер: Vega-Lite (основной), ECharts (тяжёлые
 * дашборды) или HTML-таблица. Выбор по маркеру в спеке, чтобы фронт
 * не зависел от бэка:
 *  - usermeta.chart_type === "table" -> таблица из data.values;
 *  - renderer === "echarts" (опция в option) или series без $schema -> ECharts;
 *  - иначе -> Vega-Lite.
 */
import dynamic from "next/dynamic";
import type { EChartsOption } from "echarts";
import { VegaLite, type VisualizationSpec } from "react-vega";

const ReactECharts = dynamic(() => import("echarts-for-react"), { ssr: false });

type Spec = Record<string, unknown>;
type Row = Record<string, unknown>;

interface ColumnMeta {
  field: string;
  title?: string;
  type?: string;
}

const TABLE_ROW_LIMIT = 500;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function usermetaOf(spec: Spec): Record<string, unknown> {
  return isRecord(spec.usermeta) ? spec.usermeta : {};
}

export function chartKind(spec: Spec): "table" | "echarts" | "vega-lite" {
  if (usermetaOf(spec).chart_type === "table") return "table";
  if (spec.renderer === "echarts") return "echarts";
  if ("series" in spec && !("$schema" in spec)) return "echarts";
  return "vega-lite";
}

function rowsOf(spec: Spec): Row[] {
  const data = spec.data;
  if (!isRecord(data) || !Array.isArray(data.values)) return [];
  return data.values.filter(isRecord);
}

function columnsOf(spec: Spec, rows: Row[]): ColumnMeta[] {
  const declared = usermetaOf(spec).columns;
  if (Array.isArray(declared)) {
    const columns = declared.filter(
      (column): column is ColumnMeta => isRecord(column) && typeof column.field === "string",
    );
    if (columns.length) return columns;
  }
  const fields = new Set<string>();
  rows.forEach((row) => Object.keys(row).forEach((field) => fields.add(field)));
  return Array.from(fields, (field) => ({ field }));
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function DataTable({ spec, height }: { spec: Spec; height: number }) {
  const rows = rowsOf(spec).slice(0, TABLE_ROW_LIMIT);
  const columns = columnsOf(spec, rows);
  if (!rows.length) {
    return <p className="p-3 text-sm text-slate-500">Нет данных для отображения</p>;
  }
  return (
    <div className="overflow-auto rounded border border-slate-200" style={{ maxHeight: height }}>
      <table className="min-w-full border-collapse text-sm">
        <thead className="sticky top-0 bg-slate-50">
          <tr>
            {columns.map((column) => (
              <th key={column.field} className="border-b border-slate-200 px-3 py-2 text-left font-medium text-slate-700">
                {column.title ?? column.field}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="odd:bg-white even:bg-slate-50">
              {columns.map((column) => (
                <td
                  key={column.field}
                  className={`border-b border-slate-100 px-3 py-1.5 ${column.type === "quantitative" ? "text-right tabular-nums" : ""}`}
                >
                  {formatCell(row[column.field])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ChartRenderer({ spec, height = 300 }: { spec: Spec; height?: number }) {
  const kind = chartKind(spec);
  if (kind === "table") {
    return <DataTable spec={spec} height={height} />;
  }
  if (kind === "echarts") {
    const option = (isRecord(spec.option) ? spec.option : spec) as EChartsOption;
    return <ReactECharts option={option} notMerge style={{ height, width: "100%" }} />;
  }
  return (
    <div style={{ height, width: "100%" }}>
      <VegaLite
        spec={{ height: Math.max(height - 60, 120), ...spec } as VisualizationSpec}
        actions={false}
        style={{ width: "100%" }}
      />
    </div>
  );
}
