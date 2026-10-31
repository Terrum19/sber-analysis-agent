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
import type { Datasource, Glossary, SessionResponse, TableBrief } from "@/types/datasource";
import { useSessionStore } from "@/store/sessionStore";

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
        x: { field: "region", type: "nominal", title: "Регион", axis: { labelAngle: -35 } },
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

const ELECTRICITY_TILES: DashboardTile[] = [
  {
    id: "tile-kpi-elec-1",
    title: "Средневзвешенная цена РСВ",
    spec: { mark: "text" },
    is_kpi: true,
    snapshot_data: { value: "2 480 ₽/МВт·ч", label: "Динамика +3.1% к пред. суткам" },
    insight: "Индекс цен находится в пределах нормативного диапазона энергозоны.",
    result_table: "temp_elec_summary",
    layout: { x: 0, y: 0, w: 6, h: 2 },
  },
  {
    id: "tile-kpi-elec-2",
    title: "Суммарный объём торговли",
    spec: { mark: "text" },
    is_kpi: true,
    snapshot_data: { value: "142.5 ГВт·ч", label: "Покрытие планового спроса: 99.8%" },
    insight: "Пик потребления зафиксирован в интервале 10:00–18:00 МСК.",
    result_table: "temp_elec_volume",
    layout: { x: 6, y: 0, w: 6, h: 2 },
  },
  {
    id: "tile-chart-elec-1",
    title: "Динамика индексов цен РСВ по часам",
    spec: {
      $schema: "https://vega.github.io/schema/vega-lite/v5.json",
      data: {
        values: [
          { hour: "00:00", price: 1850 },
          { hour: "04:00", price: 1720 },
          { hour: "08:00", price: 2340 },
          { hour: "12:00", price: 2750 },
          { hour: "16:00", price: 2680 },
          { hour: "20:00", price: 2420 },
        ],
      },
      mark: { type: "line", point: true, color: "#f59e0b" },
      encoding: {
        x: { field: "hour", type: "nominal", title: "Час суток", axis: { labelAngle: -35 } },
        y: { field: "price", type: "quantitative", title: "Цена (₽/МВт·ч)" },
      },
    },
    is_kpi: false,
    insight: "Суточный максимум цен зафиксирован в полдень на уровне 2 750 ₽/МВт·ч.",
    result_table: "temp_hourly_price",
    layout: { x: 0, y: 2, w: 6, h: 4 },
  },
  {
    id: "tile-chart-elec-2",
    title: "Объёмы поставки по энергозонам",
    spec: {
      $schema: "https://vega.github.io/schema/vega-lite/v5.json",
      data: {
        values: [
          { zone: "1-я Ценовая зона", volume: 98.4 },
          { zone: "2-я Ценовая зона", volume: 44.1 },
        ],
      },
      mark: { type: "bar", cornerRadiusEnd: 4, color: "#10b981" },
      encoding: {
        x: { field: "zone", type: "nominal", title: "Энергозона", axis: { labelAngle: -35 } },
        y: { field: "volume", type: "quantitative", title: "Объём (ГВт·ч)" },
      },
    },
    is_kpi: false,
    insight: "Первая ценовая зона сформировала 69% суммарного объема торгов.",
    result_table: "temp_zone_volume",
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
 * Эмуляция стриминга ответов агента по контракту S-01 + S-02:
 * 1. progress (planning started -> finished)
 * 2. progress (step 1 started -> finished)
 * 3. progress (insight started)
 * 4. 4-6 последовательных событий text { chunk: "..." } с интервалом ~100-150 мс
 * 5. chart (или dashboard)
 * 6. done
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
  onClose?: (error?: string) => void,
  _options?: { recoveryResponse?: string; replyTo?: string | null },
): () => void {
  let isCancelled = false;
  const store = useSessionStore.getState();
  const activeTable = store.sessionActiveTable[sessionId] || "";
  const dbId = store.sessionDatabase[sessionId];
  const database = store.databases.find((d) => d.id === dbId);
  const context = `${activeTable} ${database?.title || ""} ${question}`.toLowerCase();

  const isDashboardRequest = /дашборд|панел|график|kpi|отчет|показател/i.test(question);
  // turn_id нужен кнопкам «Спросить об этом» и цитате в демо-режиме (F-12).
  const turnId = `mock-turn-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  // Видимая проверка, что reply_to дошёл до мока.
  const replyPrefix = _options?.replyTo ? "Уточняю по прошлому ответу…\n" : "";

  const isElectricity = /electric|price|volume|energy|generation|power|grid|hour|load|мвт|гвт|рсв|электр|генерац/i.test(context);
  const isCustomer = /customer|user|client|audience|subscriber|loyalt|клиент|пользовател/i.test(context);
  const isProduct = /product|item|inventory|good|catalog|stock|товар|номенклатур|склад/i.test(context);

  let tiles = DEFAULT_TILES;
  let chartCaption = "Выручка по ключевым регионам";
  let chartResultTable = "temp_result_v1";
  let dashboardTitle = "Аналитический срез: продажи и KPI";
  let stepPurpose = "Агрегация продаж по регионам";
  let chunks: string[] = [];

  if (isElectricity) {
    tiles = ELECTRICITY_TILES;
    chartCaption = "Динамика индексов цен РСВ по часам";
    chartResultTable = "temp_hourly_price";
    dashboardTitle = "Анализ рынка электроэнергии РСВ";
    stepPurpose = "Расчет средневзвешенных цен и объемов РСВ";
    chunks = isDashboardRequest
      ? [
          `${replyPrefix}Формирую аналитический дашборд `,
          "по рынку электроэнергии РСВ. ",
          "Выделены почасовые индексы цен, торговые объемы ",
          "и зоны пиковой нагрузки. ",
          "Дашборд готов к анализу.",
        ]
      : [
          `${replyPrefix}По итогам анализа рынка электроэнергии РСВ `,
          "средневзвешенная цена составила 2 480 ₽/МВт·ч (+3.1%). ",
          "Суммарный объем торгов достиг 142.5 ГВт·ч. ",
          "Пиковые цены зафиксированы в интервале 10:00–18:00 (до 2 750 ₽/МВт·ч).",
        ];
  } else if (isCustomer) {
    chartCaption = "Распределение активности клиентов";
    chartResultTable = "temp_customer_segments";
    dashboardTitle = "Анализ клиентской базы и когорт";
    stepPurpose = "Расчет сегментов активности аудитории";
    chunks = isDashboardRequest
      ? [
          `${replyPrefix}Формирую дашборд клиентской аналитики `,
          "на основе когортного среза. ",
          "Рассчитаны показатели удержания и LTV по группам, ",
          "выделены целевые сегменты покупателей. ",
          "Панель показателей собрана.",
        ]
      : [
          `${replyPrefix}Анализ клиентской базы завершён: `,
          "активная аудитория выросла на 14.2%, ",
          "доля постоянных клиентов достигла 62%. ",
          "Когорта Q3 демонстрирует наивысший LTV (32 400 ₽).",
        ];
  } else if (isProduct) {
    chartCaption = "Маржинальность товарных категорий";
    chartResultTable = "temp_product_margin";
    dashboardTitle = "Анализ товарного каталога и маржи";
    stepPurpose = "Расчет маржинальности и оборачиваемости номенклатуры";
    chunks = isDashboardRequest
      ? [
          `${replyPrefix}Формирую дашборд товарной аналитики `,
          "по номенклатуре и товарным категориям. ",
          "Сгруппированы метрики маржинальности и оборачиваемости SKU, ",
          "выявлены позиции группы риска. ",
          "Дашборд подготовлен.",
        ]
      : [
          `${replyPrefix}Анализ товарного каталога показал: `,
          "15% позиций генерируют 78% всей маржи. ",
          "Лидер маржинальности — комплектующие (38.5%). ",
          "Выявлено 12 медленно оборачиваемых позиций.",
        ];
  } else if (activeTable && activeTable !== "sales_orders" && !/sale|order|retail|revenue|чек|выручк/i.test(activeTable)) {
    const cleanName = activeTable.replace(/[_-]/g, " ");
    chartCaption = `Распределение показателей таблицы «${cleanName}»`;
    chartResultTable = `temp_analytics_${activeTable}`;
    dashboardTitle = `Сводный дашборд: ${cleanName}`;
    stepPurpose = `Агрегация и расчет метрик таблицы «${cleanName}»`;
    chunks = isDashboardRequest
      ? [
          `${replyPrefix}Формирую аналитический дашборд `,
          `для таблицы «${cleanName}». `,
          "Построены распределения числовых полей и ключевые срезы, ",
          "подготовлена сводная панель данных.",
        ]
      : [
          `${replyPrefix}Анализ таблицы «${cleanName}» успешно выполнен. `,
          "Обработаны строки и рассчитаны ключевые агрегаты. ",
          "Выявлены основные тренды и распределения показателей в DuckDB.",
        ];
  } else {
    chunks = isDashboardRequest
      ? [
          `${replyPrefix}Формирую аналитический дашборд `,
          "на основе структуры таблицы DuckDB. ",
          "Выделены ключевые метрики выручки и среднего чека, ",
          "а также региональная разбивка с долей лидера. ",
          "Дашборд подготовлен для отображения.",
        ]
      : [
          `${replyPrefix}По итогам анализа данных продаж в DuckDB `,
          "общий объём выручки показал устойчивый рост на 18.4%. ",
          "Лидирующую позицию сохраняет Москва с результатом 12.5 млн ₽ ",
          "(более 75% всей региональной выручки). ",
          "Темпы прироста в мае–июне ускорились, тренд остаётся положительным.",
        ];
  }

  const timeouts: NodeJS.Timeout[] = [];
  if (!isDashboardRequest) {
    mockPlanEvents().forEach(({ delay, data }) => {
      timeouts.push(setTimeout(() => !isCancelled && onEvent({ type: "progress", data }), delay));
    });
  }

  const schedule = (delay: number, fn: () => void) => {
    const t = setTimeout(() => {
      if (!isCancelled) fn();
    }, delay);
    timeouts.push(t);
  };

  // 1. Progress: planning started -> finished
  schedule(200, () => {
    onEvent({
      type: "progress",
      data: { stage: "planning", status: "started" },
    });
  });

  schedule(450, () => {
    onEvent({
      type: "progress",
      data: { stage: "planning", status: "finished", total_steps: 1 },
    });
  });

  // 2. Progress: step 1 started -> finished
  schedule(650, () => {
    onEvent({
      type: "progress",
      data: {
        stage: "step",
        status: "started",
        step_id: 1,
        tool: "duckdb_sql",
        purpose: stepPurpose,
        total_steps: 1,
      },
    });
  });

  schedule(1000, () => {
    onEvent({
      type: "progress",
      data: {
        stage: "step",
        status: "finished",
        step_id: 1,
        elapsed_ms: 350,
      },
    });
  });

  // 3. Progress: insight started
  schedule(1150, () => {
    onEvent({
      type: "progress",
      data: { stage: "insight", status: "started" },
    });
  });

  // 4. Последовательные события text { chunk: "..." }
  chunks.forEach((chunk, idx) => {
    schedule(1300 + idx * 130, () => {
      onEvent({
        type: "text",
        data: { chunk },
      });
    });
  });

  const chartDelay = 1300 + chunks.length * 130 + 150;

  // 5. Chart или Dashboard
  schedule(chartDelay, () => {
    if (isDashboardRequest) {
      const dashId = `dash-${Date.now().toString(36)}`;
      onEvent({
        type: "dashboard",
        data: {
          dashboard_id: dashId,
          title: dashboardTitle,
          tiles,
        },
      });
    } else {
      onEvent({
        type: "chart",
        data: {
          caption: chartCaption,
          spec: tiles[2]?.spec || DEFAULT_TILES[2].spec,
          result_table: chartResultTable,
        },
      });
    }
  });

  // 6. Done
  schedule(chartDelay + 250, () => {
    onEvent({ type: "done", data: { turn_id: turnId } });
    onClose?.();
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

const MOCK_GLOSSARIES: Record<string, Glossary> = {
  sales: {
    note: "Бизнес-глоссарий для таблицы sales",
    terms: {
      amount: ["чек", "сумма покупки", "выручка"],
      user_id: ["клиент", "пользователь"],
      event_date: ["дата покупки", "день транзакции"],
    },
  },
};

export function mockGetGlossary(table: string): Glossary {
  return MOCK_GLOSSARIES[table] || { terms: {} };
}

export function mockPutGlossary(table: string, glossary: Glossary): TableBrief {
  MOCK_GLOSSARIES[table] = glossary;
  return {
    table,
    rows: 1200,
    columns: Math.max(Object.keys(glossary.terms).length, 4),
    period_from: "2025-01-01",
    period_to: "2025-12-31",
    description_status: "pending",
    warnings: [],
  };
}
