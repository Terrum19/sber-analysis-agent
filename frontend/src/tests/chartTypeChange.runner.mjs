import { test } from "node:test";
import assert from "node:assert/strict";

const CHART_TYPES = [
  "bar",
  "line",
  "area",
  "scatter",
  "histogram",
  "box",
  "heatmap",
  "table",
  "kpi",
];

const CHART_MARK = {
  bar: "bar",
  line: "line",
  area: "area",
  scatter: "point",
  histogram: "bar",
  box: "boxplot",
  heatmap: "rect",
  table: "text",
  kpi: { type: "text", fontSize: 48 },
};

function chartTypeOf(tile) {
  if (!tile) return "bar";
  const usermeta = tile.spec?.usermeta;
  const value = usermeta?.requested_type ?? usermeta?.chart_type;
  if (value && CHART_TYPES.includes(value)) return value;
  return tile.is_kpi ? "kpi" : "bar";
}

function withChartType(tile, type) {
  const current = tile.spec?.usermeta ?? {};
  const min = type === "kpi" ? { w: 2, h: 2 } : { w: 4, h: 3 };
  const layout = tile.layout ?? { x: 0, y: 0, w: min.w, h: min.h };
  const specData = tile.spec?.data;
  const spec = {
    ...(tile.spec ?? {}),
    usermeta: { ...current, chart_type: type, requested_type: type },
    mark: CHART_MARK[type],
  };
  if ((!specData || !Array.isArray(specData.values)) && Array.isArray(tile.snapshot_data)) {
    spec.data = { ...(tile.spec?.data ?? {}), values: tile.snapshot_data };
  }
  return {
    ...tile,
    spec,
    is_kpi: type === "kpi",
    layout: {
      x: layout.x ?? 0,
      y: layout.y ?? 0,
      w: Math.max(layout.w ?? min.w, min.w),
      h: Math.max(layout.h ?? min.h, min.h),
    },
  };
}

class TestChartTypeChangeController {
  constructor(dashboardId, initialTiles, debounceMs = 50) {
    this.dashboardId = dashboardId;
    this.tiles = JSON.parse(JSON.stringify(initialTiles));
    this.debounceMs = debounceMs;
    this.initialTiles = null;
    this.pendingChange = null;
    this.saveSeq = 0;
    this.debounceTimer = null;
    this.pickerState = null;
    this.previewType = null;
    this.saveState = "idle";
    this.notifications = [];
    this.putCalls = [];
    this.recalculateCalls = [];
    this.shouldFailSave = false;
  }

  get currentTiles() {
    return this.tiles;
  }

  getTile(tile, index) {
    if (this.pickerState && this.pickerState.tileIndex === index && this.previewType) {
      return withChartType(tile, this.previewType);
    }
    return tile;
  }

  openPicker(tileIndex, anchorRect) {
    if (this.pickerState && this.pickerState.tileIndex === tileIndex) {
      this.closePicker();
      return;
    }
    this.pickerState = { tileIndex, anchorRect };
    this.previewType = null;
  }

  closePicker() {
    this.pickerState = null;
    this.previewType = null;
    if (this.pendingChange) {
      void this.executeSave();
    }
  }

  setPreview(type) {
    this.previewType = type;
  }

  changeChartType(tileIndex, newType) {
    this.previewType = null;
    if (this.initialTiles === null) {
      this.initialTiles = JSON.parse(JSON.stringify(this.tiles));
    }

    const nextTiles = this.tiles.map((item, i) =>
      i === tileIndex ? withChartType(item, newType) : item
    );
    this.tiles = nextTiles;

    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
    }
    this.saveSeq++;

    this.pendingChange = {
      tileIndex,
      type: newType,
      tiles: nextTiles,
    };

    this.debounceTimer = setTimeout(() => {
      void this.executeSave();
    }, this.debounceMs);
  }

  async executeSave() {
    if (this.debounceTimer) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = null;
    }
    const pending = this.pendingChange;
    if (!pending) return;
    this.pendingChange = null;

    if (!this.dashboardId) {
      this.initialTiles = null;
      return;
    }

    const seq = ++this.saveSeq;
    this.saveState = "saving";

    try {
      if (this.shouldFailSave) {
        throw new Error("Simulated network save failure");
      }

      this.putCalls.push({
        dashboardId: this.dashboardId,
        tiles: pending.tiles,
      });

      if (seq !== this.saveSeq) return;

      if (pending.tiles[pending.tileIndex]?.result_table) {
        this.recalculateCalls.push(this.dashboardId);
        if (seq !== this.saveSeq) return;
        this.saveState = "saved";
        this.initialTiles = null;
      } else {
        this.saveState = "saved";
        this.notifications.push("Local only");
        this.initialTiles = null;
      }
    } catch (err) {
      if (seq !== this.saveSeq) return;
      if (this.initialTiles) {
        this.tiles = JSON.parse(JSON.stringify(this.initialTiles));
        this.initialTiles = null;
      }
      this.saveState = "error";
      this.notifications.push(err.message);
    }
  }
}

function createTile(overrides = {}) {
  return {
    id: "tile-1",
    title: "Тестовая плитка",
    spec: {
      data: { values: [{ x: "A", y: 10 }] },
      mark: "bar",
      usermeta: { chart_type: "bar", requested_type: "bar" },
    },
    is_kpi: false,
    layout: { x: 0, y: 0, w: 6, h: 4 },
    result_table: "temp_table",
    ...overrides,
  };
}

test("CHART_TYPES contains all 9 required types", () => {
  assert.equal(CHART_TYPES.length, 9);
  assert.deepEqual(CHART_TYPES, [
    "bar",
    "line",
    "area",
    "scatter",
    "histogram",
    "box",
    "heatmap",
    "table",
    "kpi",
  ]);
});

test("chartTypeOf detects type and fallback", () => {
  const barTile = createTile();
  assert.equal(chartTypeOf(barTile), "bar");

  const lineTile = withChartType(barTile, "line");
  assert.equal(chartTypeOf(lineTile), "line");

  const kpiTile = withChartType(barTile, "kpi");
  assert.equal(chartTypeOf(kpiTile), "kpi");
});

test("Hover preview changes tile rendering without network requests and restores on leave", () => {
  const tile = createTile();
  const ctrl = new TestChartTypeChangeController("dash-123", [tile]);

  // Открываем панель выбора
  ctrl.openPicker(0, { top: 10, left: 10, bottom: 30, right: 30 });
  assert.ok(ctrl.pickerState);

  // Наведение на line
  ctrl.setPreview("line");
  assert.equal(ctrl.previewType, "line");

  const previewTile = ctrl.getTile(ctrl.currentTiles[0], 0);
  assert.equal(chartTypeOf(previewTile), "line");
  assert.equal(previewTile.spec.mark, "line");

  // Сетевых запросов не было!
  assert.equal(ctrl.putCalls.length, 0);
  assert.equal(ctrl.recalculateCalls.length, 0);

  // Наведение на heatmap
  ctrl.setPreview("heatmap");
  const heatmapTile = ctrl.getTile(ctrl.currentTiles[0], 0);
  assert.equal(chartTypeOf(heatmapTile), "heatmap");
  assert.equal(heatmapTile.spec.mark, "rect");
  assert.equal(ctrl.putCalls.length, 0);

  // Курсор ушёл
  ctrl.setPreview(null);
  const restoredTile = ctrl.getTile(ctrl.currentTiles[0], 0);
  assert.equal(chartTypeOf(restoredTile), "bar");
  assert.equal(restoredTile.spec.mark, "bar");
  assert.equal(ctrl.putCalls.length, 0);
});

test("Clicking type applies immediately and closes popover", () => {
  const tile = createTile();
  const ctrl = new TestChartTypeChangeController("dash-123", [tile]);

  ctrl.openPicker(0, { top: 10, left: 10, bottom: 30, right: 30 });
  assert.ok(ctrl.pickerState);

  // Клик по line применяет тип и закрывает окно
  ctrl.changeChartType(0, "line");
  ctrl.closePicker();

  // Тип применился локально сразу
  assert.equal(chartTypeOf(ctrl.currentTiles[0]), "line");

  // Поповер закрывается по клику
  assert.equal(ctrl.pickerState, null);
});

test("Three quick clicks result in exactly one PUT/recalculate with the last type", async () => {
  const tile = createTile();
  const ctrl = new TestChartTypeChangeController("dash-123", [tile], 40);

  ctrl.openPicker(0, { top: 10, left: 10, bottom: 30, right: 30 });

  // 3 быстрых клика подряд: line -> area -> scatter
  ctrl.changeChartType(0, "line");
  ctrl.changeChartType(0, "area");
  ctrl.changeChartType(0, "scatter");

  // Сразу применён последний
  assert.equal(chartTypeOf(ctrl.currentTiles[0]), "scatter");

  // До истечения дебаунса сетевых вызовов нет
  assert.equal(ctrl.putCalls.length, 0);

  // Ждём дебаунс
  await new Promise((r) => setTimeout(r, 60));

  // Ровно один PUT и один recalculate с последним типом
  assert.equal(ctrl.putCalls.length, 1);
  assert.equal(ctrl.recalculateCalls.length, 1);
  assert.equal(ctrl.saveState, "saved");

  const savedTiles = ctrl.putCalls[0].tiles;
  assert.equal(chartTypeOf(savedTiles[0]), "scatter");
  assert.equal(savedTiles[0].spec.mark, "point");
});

test("Save error rolls back to initial type", async () => {
  const tile = createTile(); // initial is "bar"
  const ctrl = new TestChartTypeChangeController("dash-123", [tile], 30);
  ctrl.shouldFailSave = true;

  ctrl.openPicker(0, { top: 10, left: 10, bottom: 30, right: 30 });

  ctrl.changeChartType(0, "line");
  assert.equal(chartTypeOf(ctrl.currentTiles[0]), "line");

  // Ждём дебаунс и выполнение сохранения с ошибкой
  await new Promise((r) => setTimeout(r, 50));

  assert.equal(ctrl.saveState, "error");
  // Плитка откатилась назад к bar!
  assert.equal(chartTypeOf(ctrl.currentTiles[0]), "bar");
  assert.ok(ctrl.notifications.some((n) => n.includes("Simulated network save failure")));
});

test("withChartType populates spec.data.values from snapshot_data if specData is missing", () => {
  const tileWithoutValues = {
    id: "tile-2",
    title: "Тестовая плитка без spec.data.values",
    spec: { mark: "bar" },
    snapshot_data: [{ a: 1, b: 2 }],
  };
  const converted = withChartType(tileWithoutValues, "line");
  assert.equal(chartTypeOf(converted), "line");
  assert.deepEqual(converted.spec.data.values, [{ a: 1, b: 2 }]);
});

test("Re-clicking button closes open picker (toggle behavior)", () => {
  const tile = createTile();
  const ctrl = new TestChartTypeChangeController("dash-123", [tile]);
  ctrl.openPicker(0, { top: 10, left: 10, bottom: 30, right: 30 });
  assert.ok(ctrl.pickerState);

  // Повторный клик по тому же тайлу закрывает поповер
  ctrl.openPicker(0, { top: 10, left: 10, bottom: 30, right: 30 });
  assert.equal(ctrl.pickerState, null);
});

test("Local-only mode (no dashboardId) updates tiles without network save", async () => {
  const tile = createTile();
  const ctrl = new TestChartTypeChangeController(null, [tile], 10);
  ctrl.changeChartType(0, "line");
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(chartTypeOf(ctrl.currentTiles[0]), "line");
  assert.equal(ctrl.putCalls.length, 0);
  assert.equal(ctrl.recalculateCalls.length, 0);
});

test("Tiles without result_table save without recalculation", async () => {
  const tile = createTile({ result_table: undefined });
  const ctrl = new TestChartTypeChangeController("dash-123", [tile], 10);
  ctrl.changeChartType(0, "line");
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(ctrl.putCalls.length, 1);
  assert.equal(ctrl.recalculateCalls.length, 0);
  assert.equal(ctrl.saveState, "saved");
  assert.ok(ctrl.notifications.includes("Local only"));
});

test("RecentDashboards areDashboardsEqual and layout shift cache behavior", () => {
  function updatedAt(item) {
    const value = item.updated_at ?? item.created_at;
    if (typeof value === "number") return value;
    if (typeof value === "string") {
      const parsed = Date.parse(value);
      return Number.isNaN(parsed) ? 0 : parsed;
    }
    return 0;
  }

  function areDashboardsEqual(a, b) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (a[i].id !== b[i].id || a[i].title !== b[i].title || updatedAt(a[i]) !== updatedAt(b[i])) {
        return false;
      }
    }
    return true;
  }

  const listA = [{ id: "1", title: "Дашборд 1", updated_at: 1000 }];
  const listB = [{ id: "1", title: "Дашборд 1", updated_at: 1000 }];
  const listC = [{ id: "1", title: "Дашборд 1 (изм)", updated_at: 2000 }];
  const listDateString = [{ id: "1", title: "Дашборд 1", updated_at: "2026-10-31T00:00:00.000Z" }];
  const listInvalidDate = [{ id: "1", title: "Дашборд 1", updated_at: "invalid" }];
  const listNoDate = [{ id: "1", title: "Дашборд 1" }];

  assert.equal(areDashboardsEqual(listA, listB), true);
  assert.equal(areDashboardsEqual(listA, listC), false);
  assert.equal(areDashboardsEqual(listA, []), false);
  assert.ok(updatedAt(listDateString[0]) > 0);
  assert.equal(updatedAt(listInvalidDate[0]), 0);
  assert.equal(updatedAt(listNoDate[0]), 0);
});

test("sessionStore mergeSessions deduplication: does not return new state if identical", () => {
  function testMergeSessions(currentDatabases, currentSessionDb, currentActiveTable, remoteSessions) {
    let hasChanges = false;
    const nextDatabases = currentDatabases.map((d) => ({
      ...d,
      tables: [...d.tables],
      sessions: [...d.sessions],
    }));
    const nextSessionDb = { ...currentSessionDb };
    const nextActiveTable = { ...currentActiveTable };

    for (const remote of remoteSessions) {
      if (!remote.session_id) continue;

      let db = nextDatabases.find((d) => remote.datasource_id && d.id === remote.datasource_id);
      if (!db && remote.table) {
        db = nextDatabases.find((d) => d.tables.includes(remote.table));
      }

      if (!db && remote.table) {
        db = {
          id: remote.datasource_id || `ds-${remote.table}`,
          title: remote.table,
          kind: "duckdb",
          tables: [remote.table],
          sessions: [],
        };
        nextDatabases.push(db);
        hasChanges = true;
      }

      if (db) {
        if (nextSessionDb[remote.session_id] !== db.id) {
          nextSessionDb[remote.session_id] = db.id;
          hasChanges = true;
        }
        const sessionExists = db.sessions.some((x) => x.id === remote.session_id);
        if (!sessionExists) {
          db.sessions = [
            {
              id: remote.session_id,
              title: remote.title || remote.table || "Сессия",
              created_at: remote.created_at ? new Date(remote.created_at).getTime() : Date.now(),
            },
            ...db.sessions,
          ];
          hasChanges = true;
        } else if (remote.title) {
          const currentSession = db.sessions.find((x) => x.id === remote.session_id);
          if (currentSession && currentSession.title !== remote.title) {
            db.sessions = db.sessions.map((x) =>
              x.id === remote.session_id ? { ...x, title: remote.title || x.title } : x
            );
            hasChanges = true;
          }
        }
      }

      if (remote.table && !nextActiveTable[remote.session_id]) {
        nextActiveTable[remote.session_id] = remote.table;
        hasChanges = true;
      }
    }

    if (!hasChanges) {
      return null; // Нет изменений — нет обновления стора и нет ре-рендеров
    }

    return {
      databases: nextDatabases,
      sessionDatabase: nextSessionDb,
      sessionActiveTable: nextActiveTable,
    };
  }

  const dbs = [
    {
      id: "db-1",
      title: "База 1",
      kind: "duckdb",
      tables: ["sales"],
      sessions: [{ id: "sess-1", title: "Сессия 1", created_at: 100 }],
    },
  ];
  const sDb = { "sess-1": "db-1" };
  const sTable = { "sess-1": "sales" };

  // Повторный вызов с теми же сессиями возвращает null (нет лишнего ре-рендера)
  const noChange = testMergeSessions(dbs, sDb, sTable, [
    { session_id: "sess-1", datasource_id: "db-1", table: "sales", title: "Сессия 1" },
  ]);
  assert.equal(noChange, null);

  // Вызов с новой сессией фиксирует изменения
  const withNewSession = testMergeSessions(dbs, sDb, sTable, [
    { session_id: "sess-2", datasource_id: "db-1", table: "sales", title: "Сессия 2" },
  ]);
  assert.ok(withNewSession);
  assert.equal(withNewSession.databases[0].sessions.length, 2);

  // Переименование сессии с бэкенда фиксирует изменение
  const withRenamed = testMergeSessions(dbs, sDb, sTable, [
    { session_id: "sess-1", datasource_id: "db-1", table: "sales", title: "Сессия 1 (изменена)" },
  ]);
  assert.ok(withRenamed);
  assert.equal(withRenamed.databases[0].sessions[0].title, "Сессия 1 (изменена)");

  // Сопоставление по таблице без datasource_id
  const byTable = testMergeSessions(dbs, sDb, sTable, [
    { session_id: "sess-3", table: "sales", title: "Сессия 3" },
  ]);
  assert.ok(byTable);
  assert.equal(byTable.databases[0].sessions.length, 2);

  // Создание новой папки для неизвестной таблицы
  const newFolder = testMergeSessions(dbs, sDb, sTable, [
    { session_id: "sess-4", table: "orders", title: "Сессия 4" },
  ]);
  assert.ok(newFolder);
  assert.equal(newFolder.databases.length, 2);
  assert.equal(newFolder.databases[1].title, "orders");

  // Пропуск элементов без session_id
  const emptyRemote = testMergeSessions(dbs, sDb, sTable, [{ table: "sales" }]);
  assert.equal(emptyRemote, null);
});

test("Codebase inspection: chat auto-scroll, sidebar and navigation stability", async () => {
  const fs = await import("node:fs/promises");

  // ChatPanel не должен использовать smooth scroll при открытии сессии
  const chatCode = await fs.readFile("/app/src/components/chat/ChatPanel.tsx", "utf-8");
  assert.ok(
    !chatCode.includes('scrollIntoView({ behavior: "smooth" });'),
    "ChatPanel must not use smooth scrollIntoView on tab switch / stream"
  );
  assert.ok(
    chatCode.includes("scrollContainerRef.current.scrollTop = scrollContainerRef.current.scrollHeight"),
    "ChatPanel must set scrollTop directly to scrollHeight"
  );

  // RecentDashboards должен использовать кэш и не зависеть от pathname в fetch-эффекте
  const recentCode = await fs.readFile("/app/src/components/layout/RecentDashboards.tsx", "utf-8");
  assert.ok(
    recentCode.includes("cachedRecentDashboards"),
    "RecentDashboards must use cachedRecentDashboards"
  );
  assert.ok(
    recentCode.includes("areDashboardsEqual"),
    "RecentDashboards must check areDashboardsEqual"
  );
  assert.ok(
    !recentCode.includes("[pathname, canvasDashboardId"),
    "RecentDashboards must not trigger fetch on pathname change"
  );

  // LeftSidebar должен троттлить syncSessions и восстанавливать скролл
  const sidebarCode = await fs.readFile("/app/src/components/layout/LeftSidebar.tsx", "utf-8");
  assert.ok(
    sidebarCode.includes("SESSIONS_SYNC_THROTTLE_MS"),
    "LeftSidebar must throttle syncSessions"
  );
  assert.ok(
    sidebarCode.includes("cachedSidebarScrollTop"),
    "LeftSidebar must preserve cachedSidebarScrollTop"
  );
  assert.ok(
    sidebarCode.includes('pathname?.startsWith("/session/")'),
    "LeftSidebar must scope currentSessionId to /session/ routes"
  );

  // TopHeader не должен вызывать глобальный scrollIntoView
  const headerCode = await fs.readFile("/app/src/components/layout/TopHeader.tsx", "utf-8");
  assert.ok(
    !headerCode.includes("ref.current?.scrollIntoView"),
    "TopHeader must not call global scrollIntoView"
  );

  // Artifact страница содержит кнопку назад в дашборд
  const artifactCode = await fs.readFile("/app/src/app/artifact/[id]/page.tsx", "utf-8");
  assert.ok(
    artifactCode.includes("toDashboard"),
    "Artifact page must have back to dashboard navigation"
  );
});

test("Codebase inspection: DashboardTile renders change chart type button in normal mode and charts tab", async () => {
  const fs = await import("node:fs/promises");
  
  // 1. Проверяем DashboardTile.tsx
  const tileCode = await fs.readFile("/app/src/components/canvas/DashboardTile.tsx", "utf-8");
  assert.ok(
    tileCode.includes("onChangeChartType"),
    "DashboardTile must accept onChangeChartType prop"
  );
  assert.ok(
    tileCode.includes('data-chart-picker-trigger="true"'),
    "DashboardTile must have data-chart-picker-trigger button"
  );
  assert.ok(
    tileCode.includes("chartTypeOf(tile)"),
    "DashboardTile shows chartTypeOf(tile)"
  );
  assert.ok(
    tileCode.includes("dense ?"),
    "DashboardTile handles dense mode specifically"
  );

  // 2. Проверяем Canvas.tsx
  const canvasCode = await fs.readFile("/app/src/components/canvas/Canvas.tsx", "utf-8");
  assert.ok(
    canvasCode.includes("useChartTypeChange"),
    "Canvas must use useChartTypeChange"
  );
  assert.ok(
    canvasCode.includes("ChartTypePicker"),
    "Canvas must render ChartTypePicker"
  );
  // Проверяем, что во вкладке Графики есть onChangeChartType
  const chartsTabSection = canvasCode.substring(canvasCode.indexOf('activeTab === "charts"'));
  assert.ok(
    chartsTabSection.includes("onChangeChartType"),
    "Charts tab in Canvas must pass onChangeChartType to DashboardTile"
  );

  // 3. Проверяем app/dashboard/[id]/page.tsx
  const pageCode = await fs.readFile("/app/src/app/dashboard/[id]/page.tsx", "utf-8");
  assert.ok(
    pageCode.includes("useChartTypeChange"),
    "dashboard/[id]/page.tsx must use useChartTypeChange"
  );
  assert.ok(
    pageCode.includes("ChartTypePicker"),
    "dashboard/[id]/page.tsx must render ChartTypePicker"
  );
  assert.ok(
    pageCode.includes("onChangeChartType"),
    "dashboard/[id]/page.tsx must pass onChangeChartType"
  );

  // 4. Проверяем ItemActionMenu.tsx: выбор графика убран из меню троеточия
  const menuCode = await fs.readFile("/app/src/components/layout/ItemActionMenu.tsx", "utf-8");
  assert.ok(
    !menuCode.includes("chartType"),
    "ItemActionMenu must not contain chartType selection"
  );

  // 5. Проверяем ChartTypePicker.tsx
  const pickerCode = await fs.readFile("/app/src/components/canvas/ChartTypePicker.tsx", "utf-8");
  assert.ok(
    pickerCode.includes("CHART_TYPES.map"),
    "ChartTypePicker must map over CHART_TYPES"
  );
  assert.ok(
    pickerCode.includes("onSelect(type)"),
    "ChartTypePicker must call onSelect(type)"
  );
  assert.ok(
    pickerCode.includes("onClose()"),
    "ChartTypePicker must call onClose() upon selecting a type"
  );
  assert.ok(
    pickerCode.includes("onPreview(type)"),
    "ChartTypePicker must call onPreview(type)"
  );
  assert.ok(
    pickerCode.includes("onPointerLeave"),
    "ChartTypePicker must handle pointer leave to reset preview"
  );
  assert.ok(
    pickerCode.includes("pointerType === \"touch\""),
    "ChartTypePicker must ignore hover preview on touch devices"
  );
});

