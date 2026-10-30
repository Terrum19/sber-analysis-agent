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

test("Clicking type applies immediately and popover stays open", () => {
  const tile = createTile();
  const ctrl = new TestChartTypeChangeController("dash-123", [tile]);

  ctrl.openPicker(0, { top: 10, left: 10, bottom: 30, right: 30 });
  assert.ok(ctrl.pickerState);

  // Клик по line
  ctrl.changeChartType(0, "line");

  // Тип применился локально сразу
  assert.equal(chartTypeOf(ctrl.currentTiles[0]), "line");

  // Поповер остаётся открытым!
  assert.ok(ctrl.pickerState);
  assert.equal(ctrl.pickerState.tileIndex, 0);

  // Клик по area
  ctrl.changeChartType(0, "area");
  assert.equal(chartTypeOf(ctrl.currentTiles[0]), "area");
  assert.ok(ctrl.pickerState);
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

  // 4. Проверяем ItemActionMenu.tsx
  const menuCode = await fs.readFile("/app/src/components/layout/ItemActionMenu.tsx", "utf-8");
  assert.ok(
    menuCode.includes("onOpenChartTypePicker"),
    "ItemActionMenu must support onOpenChartTypePicker"
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

