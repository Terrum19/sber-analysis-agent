"use client";

/**
 * Канвас артефактов и дашборда (правая панель):
 * - Вкладки: Дашборд, Графики, Инсайты (согласно разметке макета)
 * - Отображение плиток с layout, KPI, графиками и инсайтами
 * - Кнопка «Обновить данные» (пересчёт снимка)
 * - Кнопка «Сохранить» (PUT /api/dashboards/{id})
 * - Режим редактирования (переименование, удаление плиток)
 */
import React, { useState } from "react";
import type { Layout } from "react-grid-layout";
import { useCanvasStore } from "@/store/canvasStore";
import { useSessionStore } from "@/store/sessionStore";
import {
  createDashboard,
  exportArtifact,
  recalculateDashboard,
  updateDashboard,
} from "@/api/client";
import DashboardTile, { isKpiTile } from "@/components/canvas/DashboardTile";
import Icon from "@/components/ui/Icon";
import ZoomControl from "@/components/ui/ZoomControl";
import { STACK_BELOW, tileGridLayout } from "@/lib/tileLayout";
import { useElementSize } from "@/hooks/useElementSize";
import FitGrid from "@/components/canvas/FitGrid";
import { useOpenArtifact } from "@/hooks/useOpenArtifact";
import { useT } from "@/i18n/useT";
import { useUiStore } from "@/store/uiStore";
import type { DashboardTile as DashboardTileType } from "@/types/dashboard";


interface Props {
  sessionId: string;
}

export default function Canvas({ sessionId }: Props) {
  const [activeTab, setActiveTab] = useState<"dashboard" | "charts" | "insights">("dashboard");
  const [saveStatus, setSaveStatus] = useState<string | null>(null);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [isExporting, setIsExporting] = useState<"xlsx" | "pdf" | null>(null);
  const setCanvasOpen = useUiStore((s) => s.setCanvasOpen);
  const tr = useT();
  const zoom = useUiStore((s) => s.zoom);
  const openArtifact = useOpenArtifact();

  // Canvas store
  const dashboardId = useCanvasStore((s) => s.dashboardId);
  // Пустое название — дашборд по умолчанию, подпись на языке интерфейса
  const dashboardTitle = useCanvasStore((s) => s.dashboardTitle) || tr("chat.dashboardDefault");
  const tiles = useCanvasStore((s) => s.tiles);
  const isEditing = useCanvasStore((s) => s.isEditing);
  const isSaving = useCanvasStore((s) => s.isSaving);
  const isRecalculating = useCanvasStore((s) => s.isRecalculating);
  const lastSnapshotTime = useCanvasStore((s) => s.lastSnapshotTime);

  const setDashboardTitle = useCanvasStore((s) => s.setDashboardTitle);
  const setIsEditing = useCanvasStore((s) => s.setIsEditing);
  const setIsSaving = useCanvasStore((s) => s.setIsSaving);
  const setIsRecalculating = useCanvasStore((s) => s.setIsRecalculating);
  const setLastSnapshotTime = useCanvasStore((s) => s.setLastSnapshotTime);
  const removeTile = useCanvasStore((s) => s.removeTile);
  const updateTile = useCanvasStore((s) => s.updateTile);
  const addTile = useCanvasStore((s) => s.addTile);
  const setTiles = useCanvasStore((s) => s.setTiles);

  // Session store tiles (backup if canvas store has no active dashboard yet)
  const sessionTiles = useSessionStore((s) => s.tiles);

  const displayTiles = tiles.length > 0 ? tiles : sessionTiles;

  // Координаты сетки react-grid-layout
  // В узкой панели график по умолчанию во всю ширину, KPI — в половину
  const { ref: bodyRef, width: bodyWidth } = useElementSize<HTMLDivElement>();
  const stack = bodyWidth > 0 && bodyWidth * zoom < STACK_BELOW;
  const gridLayout: Layout[] = tileGridLayout(displayTiles, isKpiTile, { stack });

  function handleLayoutChange(newLayout: Layout[]) {
    // Одна колонка в узкой панели — только отображение, сохранённую раскладку не трогаем
    if (!isEditing || stack) return;
    const updatedTiles = displayTiles.map((tile, i) => {
      const tileId = tile.id || `tile-${i}`;
      const item = newLayout.find((l) => l.i === tileId);
      if (!item) return tile;
      return {
        ...tile,
        layout: {
          x: item.x,
          y: item.y,
          w: item.w,
          h: item.h,
        },
      };
    });
    setTiles(updatedTiles);
  }

  // Пересчёт данных
  async function handleRecalculate() {
    if (!dashboardId) {
      // Имитируем локальный пересчёт для несохранённого дашборда
      setIsRecalculating(true);
      setTimeout(() => {
        setIsRecalculating(false);
        setLastSnapshotTime(new Date().toLocaleTimeString("ru-RU"));
        setSaveStatus(tr("canvas.recalculated"));
        setTimeout(() => setSaveStatus(null), 3000);
      }, 600);
      return;
    }

    try {
      setIsRecalculating(true);
      const updated = await recalculateDashboard(dashboardId);
      setLastSnapshotTime(new Date().toLocaleTimeString("ru-RU"));
      setSaveStatus(tr("canvas.snapshotUpdated"));
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      setSaveStatus(tr("canvas.refreshError"));
      setTimeout(() => setSaveStatus(null), 3000);
    } finally {
      setIsRecalculating(false);
    }
  }

  function handleStartEdit() {
    if (tiles.length === 0 && sessionTiles.length > 0) {
      setTiles(sessionTiles);
    }
    setIsEditing(true);
  }

  // Сохранение изменений в бэкенд
  async function handleSave() {
    if (displayTiles.length === 0) {
      setSaveStatus(tr("canvas.nothingToSave"));
      setTimeout(() => setSaveStatus(null), 3000);
      return;
    }

    try {
      setIsSaving(true);
      if (dashboardId) {
        await updateDashboard(dashboardId, {
          title: dashboardTitle,
          tiles: displayTiles,
        });
        setSaveStatus(tr("canvas.savedServer"));
      } else {
        const created = await createDashboard({
          session_id: sessionId,
          title: dashboardTitle,
          tiles: displayTiles,
        });
        useCanvasStore.getState().setDashboard(created.id, created.title, created.tiles, created.updated_at);
        setSaveStatus(tr("canvas.savedRegistry", { id: created.id }));
      }
      setIsEditing(false);
      setTimeout(() => setSaveStatus(null), 3500);
    } catch (err: any) {
      setSaveStatus(tr("canvas.saveError", { msg: err.message || String(err) }));
      setTimeout(() => setSaveStatus(null), 4000);
    } finally {
      setIsSaving(false);
    }
  }

  function exportClientData(title: string, tileList: DashboardTileType[]) {
    const bom = "\uFEFF";
    let csv = `Дашборд:;${title}\nДата выгрузки:;${new Date().toLocaleString("ru-RU")}\n\n`;
    csv += "№;Название плитки;Тип;Значение / Метрика;Инсайт;Таблица DuckDB\n";
    tileList.forEach((t, i) => {
      const isKpi = Boolean(t.is_kpi);
      let val = "";
      if (isKpi) {
        val = t.snapshot_data?.value ?? "—";
      } else if (t.spec?.data && typeof t.spec.data === "object" && "values" in t.spec.data) {
        val = `Записей: ${(t.spec.data as any).values?.length || 0}`;
      }
      const cleanInsight = (t.insight || "").replace(/[\r\n]+/g, " ");
      csv += `${i + 1};"${t.title.replace(/"/g, '""')}";${isKpi ? "KPI" : "График"};"${String(val).replace(/"/g, '""')}";"${cleanInsight.replace(/"/g, '""')}";${t.result_table || "—"}\n`;
    });

    tileList.forEach((t, i) => {
      const rows = t.snapshot_data?.records || (t.spec?.data as any)?.values;
      if (Array.isArray(rows) && rows.length > 0) {
        csv += `\n\n--- Данные плитки ${i + 1}: ${t.title} ---\n`;
        const cols = Object.keys(rows[0]);
        csv += cols.map((c) => `"${c.replace(/"/g, '""')}"`).join(";") + "\n";
        rows.slice(0, 300).forEach((r: any) => {
          csv += cols.map((c) => `"${String(r[c] ?? "").replace(/"/g, '""')}"`).join(";") + "\n";
        });
      }
    });

    const blob = new Blob([bom + csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${title || "dashboard"}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleExport(format: "xlsx" | "pdf") {
    setIsExporting(format);
    setExportMenuOpen(false);
    try {
      if (format === "pdf") {
        try {
          const blob = await exportArtifact(sessionId, "pdf");
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `${dashboardTitle || "dashboard"}.pdf`;
          a.click();
          URL.revokeObjectURL(url);
          setSaveStatus(tr("canvas.pdfDone"));
        } catch {
          window.print();
          setSaveStatus(tr("canvas.printOpened"));
        }
      } else {
        try {
          const blob = await exportArtifact(sessionId, "xlsx");
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = `${dashboardTitle || "dashboard"}.xlsx`;
          a.click();
          URL.revokeObjectURL(url);
          setSaveStatus(tr("canvas.xlsxDone"));
        } catch {
          exportClientData(dashboardTitle, displayTiles);
          setSaveStatus(tr("canvas.csvDone"));
        }
      }
    } catch (err: any) {
      setSaveStatus(tr("canvas.exportError", { msg: err.message || String(err) }));
    } finally {
      setIsExporting(null);
      setTimeout(() => setSaveStatus(null), 3500);
    }
  }

  function handleAddCustomKpi() {
    if (tiles.length === 0 && sessionTiles.length > 0) {
      setTiles(sessionTiles);
    }
    const newKpi: DashboardTileType = {
      id: `kpi-${Date.now()}`,
      title: tr("canvas.newKpi"),
      spec: {},
      is_kpi: true,
      snapshot_data: { value: "—", label: tr("canvas.newKpiLabel") },
      layout: { x: (displayTiles.length % 2) * 6, y: Math.floor(displayTiles.length / 2) * 4, w: 6, h: 3 },
      insight: tr("canvas.newKpiInsight"),
    };
    addTile(newKpi);
  }

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-xl border border-graphite-800 bg-graphite-900 text-graphite-100">
      {/* Шапка панели (как боковые панели Claude Code): название, действия, закрыть */}
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-graphite-800 pl-3.5 pr-1.5">
        <Icon name="layout" className="shrink-0 text-graphite-400" />
        <div className="min-w-0 flex-1">
          {isEditing ? (
            <input
              type="text"
              value={dashboardTitle}
              onChange={(e) => setDashboardTitle(e.target.value)}
              className="w-full max-w-[260px] rounded-md border border-graphite-600 bg-graphite-950 px-2 py-0.5 text-[13px] text-graphite-50 outline-none focus:border-graphite-400"
            />
          ) : (
            <h2 className="truncate text-[13px] font-medium text-graphite-100">{dashboardTitle}</h2>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-0.5">
          <ZoomControl />
          <span className="mx-1 h-4 w-px bg-graphite-800" />
          <button
            onClick={handleRecalculate}
            disabled={isRecalculating}
            className="flex h-7 w-7 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 transition disabled:opacity-50"
            title={tr("canvas.refresh")}
          >
            <Icon name="refresh" className={isRecalculating ? "animate-spin" : ""} />
          </button>

          {/* Меню экспорта */}
          <div className="relative">
            <button
              onClick={() => setExportMenuOpen((prev) => !prev)}
              disabled={isExporting !== null}
              className="flex h-7 w-7 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 transition disabled:opacity-50"
              title={isExporting ? tr("canvas.exporting") : tr("canvas.export")}
            >
              <Icon name="download" />
            </button>
            {exportMenuOpen && (
              <div className="absolute right-0 top-full z-50 mt-1 w-40 rounded-lg border border-graphite-700 bg-graphite-850 p-1 shadow-2xl">
                <button
                  onClick={() => handleExport("xlsx")}
                  className="flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-200 hover:bg-graphite-700 transition"
                >
                  {tr("canvas.exportXlsx")}
                </button>
                <button
                  onClick={() => handleExport("pdf")}
                  className="flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-200 hover:bg-graphite-700 transition"
                >
                  {tr("canvas.exportPdf")}
                </button>
              </div>
            )}
          </div>

          {isEditing ? (
            <button
              onClick={handleSave}
              disabled={isSaving}
              className="ml-1 flex items-center gap-1.5 rounded-md bg-graphite-100 px-2.5 py-1 text-[12px] font-medium text-graphite-950 hover:bg-graphite-50 transition disabled:opacity-50"
            >
              <Icon name="save" size={14} />
              {isSaving ? tr("canvas.saving") : tr("canvas.save")}
            </button>
          ) : (
            <button
              onClick={handleStartEdit}
              className="flex h-7 w-7 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 transition"
              title={tr("canvas.edit")}
            >
              <Icon name="pencil" />
            </button>
          )}

          <span className="mx-1 h-4 w-px bg-graphite-800" />
          <button
            onClick={() => setCanvasOpen(false)}
            className="flex h-7 w-7 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 transition"
            title={tr("canvas.hide")}
          >
            <Icon name="x" />
          </button>
        </div>
      </div>

      {/* Вкладки и статус снимка */}
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-graphite-800 px-2.5 py-1.5">
        <div className="flex items-center gap-0.5 text-[12px]">
          {(
            [
              ["dashboard", tr("canvas.tabDashboard")],
              ["charts", tr("canvas.tabCharts", { n: displayTiles.filter((x) => !isKpiTile(x)).length })],
              ["insights", tr("canvas.tabInsights")],
            ] as const
          ).map(([tab, label]) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`rounded-md px-2.5 py-1 transition ${
                activeTab === tab ? "bg-graphite-800 text-graphite-50" : "text-graphite-400 hover:text-graphite-200"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="inline-flex shrink-0 items-center gap-1.5 text-[11px] text-graphite-400">
          <span className="h-1.5 w-1.5 rounded-full bg-good" />
          {tr("canvas.snapshot", { time: lastSnapshotTime || tr("canvas.snapshotFresh") })}
        </span>
      </div>

      {/* Уведомление о сохранении */}
      {saveStatus && (
        <div className="mx-3 mt-2 rounded-md border border-graphite-700 bg-graphite-850 px-2.5 py-1 text-[12px] text-graphite-200">
          {saveStatus}
        </div>
      )}

      {/* Основная рабочая область канваса */}
      <div ref={bodyRef} className="flex-1 overflow-y-auto p-4 md:p-5">
        {displayTiles.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center text-graphite-500 space-y-3">
            <div className="h-10 w-10 rounded-lg bg-graphite-800 flex items-center justify-center text-graphite-400">
              <Icon name="layout" size={20} />
            </div>
            <div>
              <p className="text-sm font-medium text-graphite-300">{tr("canvas.emptyTitle")}</p>
              <p className="text-xs text-graphite-500 mt-1 max-w-xs">
                {tr("canvas.emptyText")}
              </p>
            </div>
          </div>
        ) : activeTab === "dashboard" ? (
          <div className="space-y-4">
            {/* Панель добавления в режиме редактирования */}
            {isEditing && (
              <div className="flex items-center justify-between rounded-lg border border-dashed border-graphite-700 p-2.5 bg-graphite-950/40">
                <div className="flex items-center gap-2">
                  <Icon name="pencil" size={14} className="text-graphite-400" />
                  <span className="text-[11px] text-graphite-400">{tr("canvas.editHint")}</span>
                </div>
                <button
                  onClick={handleAddCustomKpi}
                  className="rounded bg-graphite-600/80 hover:bg-graphite-500 px-2.5 py-1 text-xs text-graphite-50 transition shadow-sm"
                >
                  + {tr("canvas.addKpi")}
                </button>
              </div>
            )}

            {/* Сетка плиток: масштаб меняет ширину сетки и высоту рядов */}
            <FitGrid
                  zoom={zoom}
                  className="layout"
                  layout={gridLayout}
                  cols={12}
                  rowHeight={Math.round(60 * zoom)}
                  margin={[14, 14]}
                  containerPadding={[0, 0]}
                  isDraggable={isEditing && !stack}
                  isResizable={isEditing && !stack}
                  onLayoutChange={handleLayoutChange}
                  useCSSTransforms={true}
                >
                  {displayTiles.map((tile, i) => {
                    const tileId = tile.id || `tile-${i}`;
                    return (
                      <div key={tileId} className="h-full">
                        <DashboardTile
                          tile={tile}
                          isEditing={isEditing}
                          onRename={(newTitle) => updateTile(i, { title: newTitle })}
                          onDelete={() => removeTile(i)}
                          onOpen={() => openArtifact(tile, sessionId)}
                        />
                      </div>
                    );
                  })}
            </FitGrid>
          </div>
        ) : activeTab === "charts" ? (
          displayTiles.filter((t) => !t.is_kpi).length === 0 ? (
            <div className="flex h-48 flex-col items-center justify-center rounded-xl border border-graphite-800 bg-graphite-950/40 text-center text-graphite-500 space-y-2">
              <Icon name="layout" size={20} className="text-graphite-500" />
              <p className="text-xs">{tr("canvas.noCharts")}</p>
            </div>
          ) : (
            <div className="space-y-4">
              {displayTiles
                .filter((t) => !isKpiTile(t))
                .map((tile, i) => (
                  <div key={tile.id || i} style={{ height: Math.round(340 * zoom) }}>
                    <DashboardTile tile={tile} onOpen={() => openArtifact(tile, sessionId)} />
                  </div>
                ))}
            </div>
          )
        ) : (
          displayTiles.filter((t) => t.insight).length === 0 ? (
            <div className="flex h-48 flex-col items-center justify-center rounded-xl border border-graphite-800 bg-graphite-950/40 text-center text-graphite-500 space-y-2">
              <Icon name="sparkle" size={20} className="text-graphite-500" />
              <p className="text-xs">{tr("canvas.noInsights")}</p>
            </div>
          ) : (
            <div className="space-y-3">
              {displayTiles
                .filter((t) => t.insight)
                .map((tile, i) => (
                  <div
                    key={i}
                    className="rounded-xl border border-graphite-800 bg-graphite-950/80 p-4 shadow-sm"
                  >
                    <div className="flex items-center justify-between border-b border-graphite-800/80 pb-2 mb-2">
                      <span className="text-xs font-semibold text-graphite-300">{tile.title}</span>
                      {tile.result_table && (
                        <span className="text-[10px] text-graphite-500 font-mono">
                          {tile.result_table}
                        </span>
                      )}
                    </div>
                    <div className="flex items-start gap-2.5 text-xs text-graphite-200">
                      <Icon name="sparkle" size={14} className="mt-0.5 shrink-0 text-graphite-400" />
                      <p className="leading-relaxed">{tile.insight}</p>
                    </div>
                  </div>
                ))}
            </div>
          )
        )}
      </div>
    </div>
  );
}
