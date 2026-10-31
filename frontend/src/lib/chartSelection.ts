/**
 * Выбор точки на графике для сценария «дообследуй» (F-09).
 *
 * Vega-Lite и ECharts отдают разный формат клика, поэтому оба приводятся
 * к одному `ChartSelection`: поле из `usermeta.columns`, сырое значение и
 * исходная строка данных. Вопрос агенту собирается здесь же, чтобы текст
 * был одинаковым на канвасе, странице дашборда и вкладке артефакта.
 */
import type { Translate } from "@/i18n/useT";

export interface ChartSelection {
  /** Поле данных, по которому кликнули (field из usermeta.columns). */
  field: string;
  /** Сырое значение: без локализованных разделителей, чтобы не путать планировщик. */
  value: string | number | boolean;
  /** Строка данных, из которой взято значение. */
  datum: Record<string, unknown>;
  /** Подпись поля из паспорта таблицы (title колонки). */
  label?: string;
}

interface ColumnMeta {
  field: string;
  title?: string;
  type?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPrimitive(value: unknown): value is string | number | boolean {
  return (
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
  );
}

/** Колонки из `spec.usermeta.columns` (см. docs/visualization.md). */
export function specColumns(spec: Record<string, unknown> | undefined): ColumnMeta[] {
  const meta = isRecord(spec?.usermeta) ? spec.usermeta : {};
  const declared = meta.columns;
  if (!Array.isArray(declared)) return [];
  return declared
    .filter((column): column is Record<string, unknown> => isRecord(column))
    .filter((column) => typeof column.field === "string")
    .map((column) => ({
      field: column.field as string,
      title: typeof column.title === "string" ? column.title : undefined,
      type: typeof column.type === "string" ? column.type : undefined,
    }));
}

/** Клик по марке Vega-Lite: `item.datum` — строка данных. */
export function selectionFromVega(
  datum: unknown,
  spec: Record<string, unknown> | undefined,
): ChartSelection | null {
  if (!isRecord(datum)) return null;
  const columns = specColumns(spec);
  const keys = Object.keys(datum).filter((key) => isPrimitive(datum[key]));
  if (keys.length === 0) return null;

  // Сначала категория/дата (именно она объясняет «почему июль»), потом метрика.
  const dimension = columns.find(
    (column) => column.type !== "quantitative" && keys.includes(column.field),
  );
  const metric = columns.find((column) => keys.includes(column.field));
  const field =
    dimension?.field ?? metric?.field ?? keys.find((key) => !key.startsWith("_")) ?? keys[0];
  const value = datum[field];
  if (!isPrimitive(value)) return null;

  const label = columns.find((column) => column.field === field)?.title ?? field;
  return { field, value, datum, label };
}

/**
 * Клик в ECharts. Для scatter/графиков с объектными данными приходит `params.data`,
 * для категориальных столбцов — `params.name` (значение категории).
 */
export function selectionFromECharts(
  params: unknown,
  spec: Record<string, unknown> | undefined,
): ChartSelection | null {
  if (!isRecord(params)) return null;
  if (isRecord(params.data)) {
    return selectionFromVega(params.data, spec);
  }

  const rawValue = Array.isArray(params.value) ? params.value[0] : params.value;
  const value = isPrimitive(params.name) ? params.name : rawValue;
  if (!isPrimitive(value)) return null;

  const columns = specColumns(spec);
  const column =
    columns.find((item) => item.type !== "quantitative") ?? columns[0];
  const seriesName = typeof params.seriesName === "string" ? params.seriesName : undefined;
  const field = column?.field ?? seriesName ?? "value";
  return {
    field,
    value,
    datum: { [field]: value },
    label: column?.title ?? seriesName ?? field,
  };
}

/** Значение для вопроса: числа не локализуем, даты оставляем ISO-строкой. */
export function formatSelectionValue(value: string | number | boolean): string {
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return value;
}

/** Вопрос агенту: по конкретной точке или по всей плитке (пункт меню). */
export function drillQuestion(
  t: Translate,
  title: string,
  selection?: ChartSelection | null,
): string {
  if (!selection) {
    return t("drill.tileQuestion", { title });
  }
  return t("drill.question", {
    title,
    field: selection.label || selection.field,
    value: formatSelectionValue(selection.value),
  });
}
