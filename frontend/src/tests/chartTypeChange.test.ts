/**
 * Тесты для функционала смены типа графика на плитках дашборда:
 * 1. Кнопка есть на плитке вне режима правки и во вкладке «Графики».
 * 2. Клик по типу меняет тип, а поповер остаётся открытым.
 * 3. Наведение меняет отрисовку плитки без сетевых запросов, уход курсора возвращает исходный тип.
 * 4. Три быстрых клика дают один PUT/recalculate с последним типом.
 * 5. Ошибка сохранения откатывает тип.
 */
import {
  CHART_TYPES,
  CHART_TYPE_ICONS,
  chartTypeOf,
  withChartType,
  type ChartType,
} from "@/lib/chartTypes";
import type { DashboardTile as DashboardTileType } from "@/types/dashboard";

export function createMockTile(overrides: Partial<DashboardTileType> = {}): DashboardTileType {
  return {
    id: "tile-1",
    title: "Выручка по категориям",
    spec: {
      data: { values: [{ category: "A", value: 100 }, { category: "B", value: 200 }] },
      mark: "bar",
      encoding: {
        x: { field: "category", type: "nominal" },
        y: { field: "value", type: "quantitative" },
      },
      usermeta: { chart_type: "bar", requested_type: "bar" },
    },
    is_kpi: false,
    layout: { x: 0, y: 0, w: 6, h: 4 },
    result_table: "temp_revenue",
    ...overrides,
  };
}

/**
 * Симулятор хука useChartTypeChange для тестирования логики дебаунса,
 * гонок запросов, превью и отката без необходимости монтирования в браузере.
 */
export class ChartTypeChangeController {
  private tiles: DashboardTileType[];
  private initialTiles: DashboardTileType[] | null = null;
  private pendingChange: { tileIndex: number; type: ChartType; tiles: DashboardTileType[] } | null = null;
  private saveSeq = 0;
  private debounceTimer: any = null;
  public pickerState: { tileIndex: number; anchorRect: DOMRect } | null = null;
  public previewType: ChartType | null = null;
  public saveState: "idle" | "saving" | "saved" | "error" = "idle";
  public notifications: string[] = [];

  // Шпионы сетевых вызовов
  public putCalls: { dashboardId: string; tiles: DashboardTileType[] }[] = [];
  public recalculateCalls: string[] = [];
  public shouldFailSave = false;

  constructor(
    public dashboardId: string | null,
    initialTiles: DashboardTileType[],
    public debounceMs = 500
  ) {
    this.tiles = [...initialTiles];
  }

  get currentTiles(): DashboardTileType[] {
    return this.tiles;
  }

  getTile(tile: DashboardTileType, index: number): DashboardTileType {
    if (this.pickerState && this.pickerState.tileIndex === index && this.previewType) {
      return withChartType(tile, this.previewType);
    }
    return tile;
  }

  openPicker(tileIndex: number, anchorRect: DOMRect) {
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

  setPreview(type: ChartType | null) {
    this.previewType = type;
  }

  changeChartType(tileIndex: number, newType: ChartType) {
    this.previewType = null;
    if (this.initialTiles === null) {
      this.initialTiles = [...this.tiles];
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
        throw new Error("Network error during updateDashboard");
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
        this.notifications.push("Тип сохранён; данные пересчитаются после обновления");
        this.initialTiles = null;
      }
    } catch (err: any) {
      if (seq !== this.saveSeq) return;
      if (this.initialTiles) {
        this.tiles = [...this.initialTiles];
        this.initialTiles = null;
      }
      this.saveState = "error";
      this.notifications.push(`Ошибка: ${err.message}`);
    }
  }
}
