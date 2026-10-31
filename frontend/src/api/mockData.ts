/**
 * Заглушки (Mock Data) для автономной работы фронтенда без бэкенда.
 * Включает:
 * - Генерацию сессий
 * - Потоковую эмуляцию SSE-ответов агента с инсайтами, графиками и превью дашбордов
 * - Реестр демонстрационных дашбордов с KPI и графиками VegaLite/ECharts
 * - Пересчёт и сохранение снимков в памяти/localStorage
 */
import type { DashboardConfig, DashboardListItem, DashboardTile } from "@/types/dashboard";
import type { ChatStreamEvent, ProgressPayload } from "@/types/chat";
import type { Datasource, SessionResponse, TableBrief } from "@/types/datasource";

const STORAGE_KEY = "sber_mock_dashboards_db";

const DEFAULT_TILES: DashboardTile[] = [
  {
    id: "tile-kpi-1",
    title: "Выручка за Q3",
    spec: { mark: "text" },
    is_kpi: true,
    snapshot_data: { value: "16.5M ₽", label: "Динамика +18.4% к Q2" },
    insight: "Основной прирост обеспечен расширением присутствия в ЦФО.",
    result_table: "temp_result_v1",
    layout: { x: 0, y: 0, w: 6, h: 2 },
  },
  {
    id: "tile-kpi-2",
    title: "Средний чек покупки",
    spec: { mark: "text" },
    is_kpi: true,
    snapshot_data: { value: "4 820 ₽", label: "Глубина корзины: 3.4 ед." },
    insight: "Средний чек вырос на 5.2% за счёт кросс-категорийных предложений.",
    result_table: "temp_result_v2",
    layout: { x: 6, y: 0, w: 6, h: 2 },
  },
  {
    id: "tile-chart-1",
    title: "Выручка по ключевым регионам (DuckDB)",
    spec: {
      $schema: "https://vega.github.io/schema/vega-lite/v5.json",
      data: {
        values: [
          { region: "Москва", revenue: 12.5 },
          { region: "Санкт-Петербург", revenue: 4.0 },
          { region: "Казань", revenue: 2.1 },
          { region: "Екатеринбург", revenue: 1.8 },
          { region: "Новосибирск", revenue: 1.2 },
        ],
      },
      mark: { type: "bar", cornerRadiusEnd: 4, color: "#3b82f6" },
      encoding: {
        x: { field: "region", type: "nominal", title: "Регион", axis: { labelAngle: 0 } },
        y: { field: "revenue", type: "quantitative", title: "Выручка (млн ₽)" },
      },
    },
    is_kpi: false,
    insight: "Москва лидирует с долей более 75% всей выручки. SQL не пересчитывался.",
    result_table: "temp_result_v1",
    layout: { x: 0, y: 2, w: 6, h: 4 },
  },
  {
    id: "tile-chart-2",
    title: "Динамика продаж по месяцам",
    spec: {
      $schema: "https://vega.github.io/schema/vega-lite/v5.json",
      data: {
        values: [
          { month: "Янв", sales: 8.2 },
          { month: "Фев", sales: 9.1 },
          { month: "Мар", sales: 10.4 },
          { month: "Апр", sales: 12.0 },
          { month: "Май", sales: 14.2 },
          { month: "Июн", sales: 16.5 },
        ],
      },
      mark: { type: "line", point: true, color: "#10b981" },
      encoding: {
        x: { field: "month", type: "nominal", title: "Месяц" },
        y: { field: "sales", type: "quantitative", title: "Объём продаж (млн ₽)" },
      },
    },
    is_kpi: false,
    insight: "Устойчивый восходящий тренд с ускорением темпов прироста в мае–июне.",
    result_table: "temp_result_v3",
    layout: { x: 6, y: 2, w: 6, h: 4 },
  },
];

const INITIAL_MOCK_DASHBOARDS: DashboardConfig[] = [
  {
    id: "demo-sales",
    session_id: "demo-sales",
    title: "Анализ выручки и регионов (Демо DuckDB)",
    tiles: DEFAULT_TILES,
    created_at: Date.now() - 7200000,
    updated_at: Date.now() - 3600000,
    is_snapshot: true,
  },
  {
    id: "demo-kpi",
    session_id: "demo-kpi",
    title: "Ключевые операционные показатели",
    tiles: [DEFAULT_TILES[0], DEFAULT_TILES[1]],
    created_at: Date.now() - 86400000,
    updated_at: Date.now() - 86400000,
    is_snapshot: true,
  },
];

function getStoredDashboards(): DashboardConfig[] {
  if (typeof window === "undefined") return INITIAL_MOCK_DASHBOARDS;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    // fallback
  }
  return INITIAL_MOCK_DASHBOARDS;
}

function saveStoredDashboards(list: DashboardConfig[]) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    // fallback
  }
}

export function mockFetchDashboards(sessionId?: string): DashboardListItem[] {
  const all = getStoredDashboards();
  const filtered = sessionId ? all.filter((d) => d.session_id === sessionId) : all;
  return filtered.map((d) => ({
    id: d.id,
    session_id: d.session_id,
    title: d.title,
    created_at: d.created_at,
    updated_at: d.updated_at,
    tiles_count: d.tiles.length,
  }));
}

export function mockFetchDashboard(id: string): DashboardConfig {
  const all = getStoredDashboards();
  const found = all.find((d) => d.id === id);
  if (found) return found;

  // Если дашборд с таким id не найден, сгенерируем шаблонный
  const created: DashboardConfig = {
    id,
    session_id: id,
    title: `Дашборд ${id}`,
    tiles: DEFAULT_TILES,
    created_at: Date.now(),
    updated_at: Date.now(),
    is_snapshot: true,
  };
  saveStoredDashboards([...all, created]);
  return created;
}

export function mockCreateDashboard(payload: { session_id: string; title?: string; tiles?: DashboardTile[] }): DashboardConfig {
  const all = getStoredDashboards();
  const id = `dash-${Date.now().toString(36)}`;
  const newDash: DashboardConfig = {
    id,
    session_id: payload.session_id,
    title: payload.title || "Новый дашборд",
    tiles: payload.tiles && payload.tiles.length > 0 ? payload.tiles : DEFAULT_TILES,
    created_at: Date.now(),
    updated_at: Date.now(),
    is_snapshot: true,
  };
  saveStoredDashboards([newDash, ...all]);
  return newDash;
}

export function mockUpdateDashboard(id: string, payload: { title?: string; tiles?: DashboardTile[] }): DashboardConfig {
  const all = getStoredDashboards();
  const idx = all.findIndex((d) => d.id === id);
  if (idx === -1) {
    return mockCreateDashboard({ session_id: "demo", title: payload.title, tiles: payload.tiles });
  }
  const updated: DashboardConfig = {
    ...all[idx],
    title: payload.title !== undefined ? payload.title : all[idx].title,
    tiles: payload.tiles !== undefined ? payload.tiles : all[idx].tiles,
    updated_at: Date.now(),
  };
  all[idx] = updated;
  saveStoredDashboards([...all]);
  return updated;
}

export function mockDeleteDashboard(id: string): { ok: boolean } {
  const all = getStoredDashboards();
  saveStoredDashboards(all.filter((d) => d.id !== id));
  return { ok: true };
}

export function mockRecalculateDashboard(id: string): DashboardConfig {
  const all = getStoredDashboards();
  const dash = mockFetchDashboard(id);
  // Эмулируем обновление метрик
  const updatedTiles = dash.tiles.map((t) => {
    if (t.is_kpi) {
      return {
        ...t,
        snapshot_data: {
          ...t.snapshot_data,
          value: t.snapshot_data?.value === "16.5M ₽" ? "17.2M ₽" : t.snapshot_data?.value || "100%",
          label: `Обновлено только что (${new Date().toLocaleTimeString("ru-RU")})`,
        },
      };
    }
    return t;
  });

  const updated: DashboardConfig = {
    ...dash,
    tiles: updatedTiles,
    updated_at: Date.now(),
    is_snapshot: true,
  };

  const idx = all.findIndex((d) => d.id === id);
  if (idx !== -1) {
    all[idx] = updated;
    saveStoredDashboards([...all]);
  }
  return updated;
}

export function mockBrief(table: string): TableBrief {
  return { table, rows: 1200, columns: 8, period_from: "2025-01-01", period_to: "2025-12-31", description_status: "none", warnings: [] };
}

export function mockAttachCsv(files: File[]): Datasource {
  return {
    datasource_id: `csv-${Date.now()}`,
    kind: "csv",
    tables: files.map((f) => f.name.replace(/\.[^/.]+$/, "")),
    profiles: files.map((f) => mockBrief(f.name.replace(/\.[^/.]+$/, ""))),
    profile_summary: `DuckDB CSV: ${files.length} файл(ов) загружено`,
  };
}

export function mockAttachDb(kind: "postgres" | "clickhouse", dsn: string): Datasource {
  return {
    datasource_id: `db-${Date.now()}`,
    kind,
    tables: ["sales_orders", "regional_revenue", "customer_segments", "products"],
    profiles: ["sales_orders", "regional_revenue", "customer_segments", "products"].map(mockBrief),
    profile_summary: `${kind.toUpperCase()} (${dsn.split("@")[1] || "подключено"}): 4 таблицы`,
  };
}

export function mockListTables(): string[] {
  return ["sales_orders", "regional_revenue", "customer_segments", "products"];
}

export function mockListSessions(offset = 0): SessionResponse[] {
  if (offset > 0) return [];
  return [
    {
      session_id: "demo-sales",
      table: "sales_orders",
      datasource_id: "demo-ds-1",
      title: "Анализ выручки и регионов (Демо)",
      created_at: new Date(Date.now() - 7200000).toISOString(),
    },
    {
      session_id: "demo-kpi",
      table: "regional_revenue",
      datasource_id: "demo-ds-1",
      title: "Операционные KPI (Демо)",
      created_at: new Date(Date.now() - 3600000).toISOString(),
    },
  ];
}

export function mockCreateSession(table: string, datasourceId?: string, title?: string): SessionResponse {
  if (!table || table === "unknown_table") {
    const err = new Error("Таблица не найдена") as any;
    err.status = 404;
    err.detail = "Таблица не найдена";
    throw err;
  }
  return {
    session_id: `mock-session-${Date.now().toString(36)}`,
    table,
    datasource_id: datasourceId ?? null,
    title: title ?? null,
  };
}

export function mockPatchSession(sessionId: string, table: string): SessionResponse {
  if (!table || table === "unknown_table") {
    const err = new Error("Таблица не найдена") as any;
    err.status = 404;
    err.detail = "Таблица не найдена";
    throw err;
  }
  return {
    session_id: sessionId,
    table,
  };
}

export function mockRenameSession(sessionId: string, title: string): SessionResponse {
  return {
    session_id: sessionId,
    table: "mock_table",
    title: title.trim(),
  };
}

/**
 * Эмуляция стриминга ответов агента с текстом, инсайтами и карточкой дашборда.
 */
const MOCK_REGIONS = ["Москва", "Санкт-Петербург", "Казань", "Новосибирск", "Екатеринбург", "Самара", "Сочи", "Пермь"];
const MOCK_COLUMNS = [
  { name: "region", type: "VARCHAR" },
  { name: "month", type: "DATE" },
  { name: "revenue", type: "DOUBLE" },
  { name: "orders", type: "BIGINT" },
];
/** Размеры результатов шагов мок-хода: temp_result_v1 — фильтр, v2 — группировка, v3 — топ. */
const MOCK_RESULT_ROWS: Record<string, number> = { temp_result_v1: 1240, temp_result_v2: 96, temp_result_v3: 5 };

function mockResultRow(index: number): Record<string, unknown> {
  const month = new Date(Date.UTC(2025, index % 12, 1)).toISOString().slice(0, 10);
  return {
    region: MOCK_REGIONS[index % MOCK_REGIONS.length],
    month,
    revenue: Math.round(((index * 7919) % 10000) * 1250.5) / 100,
    orders: 40 + ((index * 31) % 260),
  };
}

/** Страница результата шага для мок-режима (#239); неизвестная таблица — как удалённая. */
export async function mockStepResult(table: string, offset: number, limit: number) {
  await new Promise((resolve) => setTimeout(resolve, 400));
  const total = MOCK_RESULT_ROWS[table];
  if (total === undefined) {
    const { ApiError } = await import("./client");
    throw new ApiError(404, "Результат больше недоступен.");
  }
  const rows = Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, i) => mockResultRow(offset + i));
  return { columns: MOCK_COLUMNS, rows, total };
}

/** План и шаги мок-хода: событие planning со всем планом, затем шаги, один — с исправлением. */
function mockPlanEvents(): { delay: number; data: ProgressPayload }[] {
  const plan = [
    { step_id: 1, tool: "filter_rows", purpose: "Оставить продажи 2025 года", input: "$source", arguments: { column: "order_date", operator: ">=", value: "2025-01-01" } },
    { step_id: 2, tool: "group_by", purpose: "Выручка и заказы по регионам и месяцам", input: "$prev", arguments: { group_by: ["region", "month"], aggregations: [{ column: "amount", func: "sum", alias: "revenue" }, { column: "order_id", func: "count", alias: "orders" }] } },
    { step_id: 3, tool: "sort_limit", purpose: "Топ-5 регионов по выручке", input: "$step_2", arguments: { order_by: ["revenue"], descending: true, limit: 5 } },
  ];
  const result = (table: string, rows: number) => ({
    result_table: table,
    rows,
    result_columns: MOCK_COLUMNS,
    preview: Array.from({ length: Math.min(5, rows) }, (_, i) => mockResultRow(i)),
  });
  return [
    { delay: 20, data: { stage: "planning", status: "started" } },
    { delay: 80, data: { stage: "planning", status: "finished", total_steps: plan.length, steps: plan } },
    { delay: 120, data: { stage: "step", status: "started", ...plan[0] } },
    {
      delay: 160,
      data: {
        stage: "step", status: "finished", ...plan[0], elapsed_ms: 42, input_table: "sales", ...result("temp_result_v1", 1240),
        code_language: "sql", code: "SELECT *\nFROM sales\nWHERE order_date >= '2025-01-01' -- только 2025 год",
      },
    },
    { delay: 180, data: { stage: "step", status: "started", ...plan[1] } },
    {
      delay: 200,
      data: { stage: "step", status: "fixing", ...plan[1], arguments: { group_by: ["regoin", "month"] }, attempt: 1, error: 'Binder Error: column "regoin" not found' },
    },
    {
      delay: 230,
      data: {
        stage: "step", status: "finished", ...plan[1], elapsed_ms: 318, input_table: "temp_result_v1", input_step_id: 1, ...result("temp_result_v2", 96),
        code_language: "sql",
        code: "SELECT region, month, SUM(amount) AS revenue, COUNT(order_id) AS orders\nFROM temp_result_v1\nGROUP BY region, month",
      },
    },
    { delay: 250, data: { stage: "step", status: "started", ...plan[2] } },
    {
      delay: 280,
      data: { stage: "step", status: "finished", ...plan[2], elapsed_ms: 12, input_table: "temp_result_v2", input_step_id: 2, ...result("temp_result_v3", 5), code_language: "sql", code: "SELECT * FROM temp_result_v2 ORDER BY revenue DESC LIMIT 5" },
    },
    { delay: 290, data: { stage: "insight", status: "started" } },
  ];
}

export function mockStreamChat(
  sessionId: string,
  question: string,
  onEvent: (event: ChatStreamEvent) => void,
  onClose?: (error?: string, status?: number) => void,
  _options?: { recoveryResponse?: string; replyTo?: string | null },
): () => void {
  let isCancelled = false;
  const isDashboardRequest = /дашборд|панел|график|kpi|отчет|показател/i.test(question);
  // turn_id нужен кнопкам «Спросить об этом» и цитате в демо-режиме (F-12).
  const turnId = `mock-turn-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  // Видимая проверка, что reply_to дошёл до мока.
  const replyPrefix = _options?.replyTo ? "Уточняю по прошлому ответу…\n" : "";

  const steps = [
    { delay: 300, chunk: `${replyPrefix}Изучаю схему данных в DuckDB...\n` },
    { delay: 700, chunk: "Выполняю расчет шага 1: агрегация продаж по регионам (temp_result_v1).\n" },
    {
      delay: 1200,
      headline: isDashboardRequest
        ? "Дашборд успешно сформирован на основе промежуточных результатов DuckDB"
        : "Анализ данных завершён: выявлен рост выручки на 18.4%",
      details: [
        "Москва лидирует с результатом 12.5 млн ₽",
        "Темп роста относительно Q2 составил +18.4%",
        "SQL не генерировался заново: использован кэш шага",
      ],
    },
  ];

  const timeouts: NodeJS.Timeout[] = [];
  if (!isDashboardRequest) {
    mockPlanEvents().forEach(({ delay, data }) => {
      timeouts.push(setTimeout(() => !isCancelled && onEvent({ type: "progress", data }), delay));
    });
  }

  steps.forEach((step, idx) => {
    const t = setTimeout(() => {
      if (isCancelled) return;
      if (step.chunk) {
        onEvent({ type: "text", data: { chunk: step.chunk } });
      }
      if (step.headline) {
        onEvent({
          type: "text",
          data: { headline: step.headline, details: step.details },
        });

        // Если вопрос о графиках/дашборде — отправляем превью дашборда
        if (isDashboardRequest) {
          const dashId = `dash-${Date.now().toString(36)}`;
          onEvent({
            type: "dashboard",
            data: {
              dashboard_id: dashId,
              title: "Аналитический срез: продажи и KPI",
              tiles: DEFAULT_TILES,
            },
          });
        } else {
          onEvent({
            type: "chart",
            data: {
              caption: "Выручка по ключевым регионам",
              spec: DEFAULT_TILES[2].spec,
              result_table: "temp_result_v1",
            },
          });
        }
      }

      if (idx === steps.length - 1) {
        const finishTimer = setTimeout(() => {
          if (!isCancelled) {
            onEvent({ type: "done", data: { turn_id: turnId } });
            onClose?.();
          }
        }, 400);
        timeouts.push(finishTimer);
      }
    }, step.delay);
    timeouts.push(t);
  });

  return () => {
    isCancelled = true;
    timeouts.forEach(clearTimeout);
    onClose?.();
  };
}

/** Варианты дашборда для мок-режима (#224). */
export async function mockDashboardIdeas(exclude: string[] = []) {
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const base = [
    { title: "Выручка по регионам", description: "Какие регионы приносят больше всего и как меняется их доля" },
    { title: "Динамика продаж", description: "Как выручка и число заказов меняются по месяцам" },
    { title: "Товарные категории", description: "Какие категории самые прибыльные и где больше возвратов" },
    { title: "Сегменты покупателей", description: "Кто покупает чаще и на какие суммы" },
  ];
  if (exclude.length === 0) return base.map((idea, i) => ({ ...idea, id: String(i + 1) }));
  // Новая пачка: варианты, которых ещё не было
  const fresh: { id: string; title: string; description: string }[] = [];
  for (let n = 1; fresh.length < 4; n += 1) {
    const title = `Дополнительный срез ${n}`;
    if (!exclude.includes(title)) fresh.push({ id: String(fresh.length + 1), title, description: "Ещё один взгляд на данные" });
  }
  return fresh;
}
