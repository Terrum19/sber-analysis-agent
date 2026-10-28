"use client";

/**
 * Канвас артефактов и дашборда (правая панель):
 * - Вкладки: Дашборд, Графики, Инсайты (согласно разметке макета)
 * - Отображение плиток с layout, KPI, графиками и инсайтами
 * - Кнопка «Обновить данные» (пересчёт снимка)
 * - Кнопка «Сохранить» (PUT /api/dashboards/{id})
 * - Режим редактирования (переименование, удаление плиток)
 */
import React, { useEffect, useRef, useState } from "react";
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
import AnswerExport from "@/components/chat/AnswerExport";
import Icon from "@/components/ui/Icon";
import ZoomControl from "@/components/ui/ZoomControl";
import { MIN_SIZE, STACK_BELOW, applyGridLayout, tileGridLayout } from "@/lib/tileLayout";
import { CHART_TYPES, chartTypeOf, withChartType, type ChartType } from "@/lib/chartTypes";
import { useElementSize } from "@/hooks/useElementSize";
import FitGrid from "@/components/canvas/FitGrid";
import ItemActionMenu from "@/components/layout/ItemActionMenu";
import { useOpenArtifact } from "@/hooks/useOpenArtifact";
import { useAskAgent } from "@/hooks/useAskAgent";
import { drillQuestion, type ChartSelection } from "@/lib/chartSelection";
import { downloadBlob } from "@/lib/download";
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
  const [menu, setMenu] = useState<{ index: number; rect: DOMRect } | null>(null);
  const setCanvasOpen = useUiStore((s) => s.setCanvasOpen);
  const tr = useT();
  const zoom = useUiStore((s) => s.zoom);
  const openArtifact = useOpenArtifact();
  const askAgent = useAskAgent();

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
  const updateTileLayouts = useCanvasStore((s) => s.updateTileLayouts);
  const layoutRevision = useCanvasStore((s) => s.layoutRevision);
  const saveState = useCanvasStore((s) => s.saveState);
  const saveError = useCanvasStore((s) => s.saveError);
  const setSaveState = useCanvasStore((s) => s.setSaveState);
  const mountedRef = useRef(true);
  const saveSeqRef = useRef(0);
  const layoutReadyRef = useRef(false);

  // Первый onLayoutChange после (пере)монтирования сетки — служебный: RGL
  // нормализует координаты, это не действие пользователя и не повод для PUT.
  useEffect(() => {
    layoutReadyRef.current = false;
    const timer = setTimeout(() => {
      layoutReadyRef.current = true;
    }, 250);
    return () => clearTimeout(timer);
  }, [dashboardId]);

  // Session store tiles (backup if canvas store has no active dashboard yet)
  const sessionTiles = useSessionStore((s) => s.tiles);

  const displayTiles = dashboardId ? tiles : (sessionTiles.length > 0 ? sessionTiles : tiles);
  const hasResultTable = displayTiles.some((tile) => Boolean(tile.result_table));

  // Координаты сетки react-grid-layout
  // В узкой панели график по умолчанию во всю ширину, KPI — в половину
  const { ref: bodyRef, width: bodyWidth } = useElementSize<HTMLDivElement>();
  const stack = bodyWidth > 0 && bodyWidth * zoom < STACK_BELOW;
  const gridLayout: Layout[] = tileGridLayout(displayTiles, isKpiTile, { stack });

  // Запись плиток: сохранённый дашборд живёт в canvasStore, черновик сессии —
  // в sessionStore (он же синхронизирует canvasStore, пока dashboardId === null).
  function writeTiles(next: DashboardTileType[]) {
    if (dashboardId) setTiles(next);
    else useSessionStore.getState().setTiles(next);
  }

  function handleLayoutChange(newLayout: Layout[]) {
    // Одна колонка в узкой панели — только отображение, сохранённую раскладку не трогаем
    if (!isEditing || stack || !layoutReadyRef.current) return;
    const next = applyGridLayout(displayTiles, newLayout);
    const changed: Record<number, DashboardTileType["layout"]> = {};
    next.forEach((tile, i) => {
      const before = displayTiles[i]?.layout;
      const after = tile.layout;
      if (
        !before ||
        before.x !== after.x ||
        before.y !== after.y ||
        before.w !== after.w ||
        before.h !== after.h
      ) {
        changed[i] = after;
      }
    });
    if (Object.keys(changed).length === 0) return;
    if (dashboardId) updateTileLayouts(changed);
    else useSessionStore.getState().setTiles(next);
  }

  // Автосохранение раскладки: только для сохранённого дашборда, debounce 800 мс.
  useEffect(() => {
    if (!dashboardId || layoutRevision === 0 || stack) return;
    const timer = setTimeout(async () => {
      const snapshot = useCanvasStore.getState().tiles;
      if (!snapshot.length) return;
      const seq = ++saveSeqRef.current;
      setSaveState("saving");
      try {
        await updateDashboard(dashboardId, { tiles: snapshot });
        if (!mountedRef.current || seq !== saveSeqRef.current) return;
        setSaveState("saved");
      } catch (err) {
        if (!mountedRef.current || seq !== saveSeqRef.current) return;
        setSaveState("error", err);
      }
    }, 800);
    return () => clearTimeout(timer);
  }, [layoutRevision, dashboardId, stack, setSaveState]);

  // «Сохранено» гаснет через 2 секунды; «Ошибка» остаётся до повтора.
  useEffect(() => {
    if (saveState !== "saved") return;
    const timer = setTimeout(() => setSaveState("idle"), 2000);
    return () => clearTimeout(timer);
  }, [saveState, setSaveState]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function handleRetrySave() {
    if (!dashboardId) return;
    const snapshot = useCanvasStore.getState().tiles;
    if (!snapshot.length) return;
    const seq = ++saveSeqRef.current;
    setSaveState("saving");
    try {
      await updateDashboard(dashboardId, { tiles: snapshot });
      if (!mountedRef.current || seq !== saveSeqRef.current) return;
      setSaveState("saved");
    } catch (err) {
      if (!mountedRef.current || seq !== saveSeqRef.current) return;
      setSaveState("error", err);
    }
  }

  // Смена типа графика без LLM: локальный spec, PUT и (если есть данные) recalculate.
  async function handleChartTypeChange(index: number, type: ChartType) {
    const tile = displayTiles[index];
    if (!tile) return;
    const prevTiles = displayTiles;
    const nextTiles = displayTiles.map((item, i) =>
      i === index ? withChartType(item, type) : item,
    );
    writeTiles(nextTiles);

    if (!dashboardId) return;

    try {
      setSaveState("saving");
      await updateDashboard(dashboardId, { tiles: nextTiles });
      if (nextTiles[index].result_table) {
        const updated = await recalculateDashboard(dashboardId);
        setTiles(updated.tiles);
        setSaveState("saved");
      } else {
        setSaveState("saved");
        setSaveStatus(tr("canvas.chartTypeLocalOnly"));
        setTimeout(() => setSaveStatus(null), 3500);
      }
    } catch (err) {
      writeTiles(prevTiles);
      setSaveState("error", err);
    }
  }

  // Пересчёт данных
  async function handleRecalculate() {
    try {
      setIsRecalculating(true);
      let id = dashboardId;
      if (!id) {
        if (displayTiles.length === 0) {
          setSaveStatus(tr("canvas.nothingToSave"));
          setTimeout(() => setSaveStatus(null), 3000);
          return;
        }
        const created = await createDashboard({
          session_id: sessionId,
          title: dashboardTitle,
          tiles: displayTiles,
        });
        id = created.id;
        useCanvasStore.getState().setDashboard(created.id, created.title, created.tiles, created.updated_at);
      }
      const updated = await recalculateDashboard(id);
      if (updated && updated.tiles) {
        setTiles(updated.tiles);
      }
      setLastSnapshotTime(new Date().toLocaleTimeString("ru-RU"));
      setSaveStatus(tr("canvas.snapshotUpdated"));
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err: any) {
      const msg = err?.detail || err?.message;
      setSaveStatus(msg ? `${tr("canvas.refreshError")}: ${msg}` : tr("canvas.refreshError"));
      setTimeout(() => setSaveStatus(null), 3500);
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
      setSaveState("saving");
      if (dashboardId) {
        const updated = await updateDashboard(dashboardId, {
          title: dashboardTitle,
          tiles: displayTiles,
        });
        if (updated && updated.tiles) {
          setTiles(updated.tiles);
        }
        if (updated && updated.title) {
          setDashboardTitle(updated.title);
        }
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
      setSaveState("saved");
      setIsEditing(false);
      setTimeout(() => setSaveStatus(null), 3500);
    } catch (err: any) {
      setSaveState("error", err);
      setSaveStatus(tr("canvas.saveError", { msg: err?.detail || err?.message || String(err) }));
      setTimeout(() => setSaveStatus(null), 4000);
    } finally {
      setIsSaving(false);
    }
  }

  async function handleExport(format: "xlsx" | "pdf") {
    if (format === "xlsx") {
      const source = [...displayTiles].reverse().find((tile) => tile.result_table);
      if (!source?.result_table) {
        setSaveStatus(tr("canvas.exportNoTable"));
        setTimeout(() => setSaveStatus(null), 3500);
        return;
      }
    }
    setIsExporting(format);
    setExportMenuOpen(false);
    try {
      // Excel — по таблице результата последнего графика с ней (temp_result_vN);
      // PDF дополнительно получает заголовок, инсайт и спеку этой плитки (E-01).
      const source = [...displayTiles].reverse().find((tile) => tile.result_table);
      const target = source ?? displayTiles[0];
      const { blob, filename } = await exportArtifact(
        {
          session_id: sessionId,
          format,
          result_ref: source?.result_table,
          dashboard_id: dashboardId ?? undefined,
          ...(format === "pdf" && target
            ? {
                headline: target.title || target.insight,
                details: target.insight ? [target.insight] : undefined,
                spec: target.spec,
              }
            : {}),
        },
        `${dashboardTitle || "dashboard"}.${format}`,
      );
      downloadBlob(blob, filename);
      setSaveStatus(format === "pdf" ? tr("canvas.pdfDone") : tr("canvas.xlsxDone"));
    } catch (err: any) {
      if (err?.status === 501) {
        setSaveStatus(tr("chat.exportPdfInProgress"));
      } else {
        setSaveStatus(tr("canvas.exportError", { msg: err?.detail || err?.message || String(err) }));
      }
    } finally {
      setIsExporting(null);
      setTimeout(() => setSaveStatus(null), 3500);
    }
  }

  /** Клик по точке/плитке: вопрос агенту в чат этой сессии (F-09). */
  function handleElaborate(tile: DashboardTileType, selection?: ChartSelection) {
    askAgent(sessionId, drillQuestion(tr, tile.title, selection));
  }

  function handleAddCustomKpi() {
    // Черновик сессии (dashboardId === null) живёт в sessionStore; сохранённый
    // дашборд — в canvasStore. Раньше KPI добавлялся только в canvasStore и
    // терялся, если в сессии уже были плитки.
    const base = dashboardId ? tiles : sessionTiles.length > 0 ? sessionTiles : tiles;
    const index = base.length;
    const newKpi: DashboardTileType = {
      id: `kpi-${Date.now()}`,
      title: tr("canvas.newKpi"),
      spec: {},
      is_kpi: true,
      snapshot_data: { value: "—", label: tr("canvas.newKpiLabel") },
      layout: { x: (index % 2) * 6, y: Math.floor(index / 2) * 4, w: 6, h: 3 },
      insight: tr("canvas.newKpiInsight"),
    };
    if (dashboardId) addTile(newKpi);
    else useSessionStore.getState().addTile(newKpi);
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
          {/* Статус автосохранения раскладки */}
          {saveState !== "idle" && (
            <span
              className={`mr-0.5 inline-flex h-7 items-center gap-1 rounded-md px-1.5 text-[11px] ${
                saveState === "error"
                  ? "text-bad"
                  : saveState === "saving"
                    ? "text-graphite-400"
                    : "text-good"
              }`}
              title={saveState === "error" ? saveError ?? tr("canvas.saveStateError") : undefined}
            >
              {saveState === "saving" && (
                <>
                  <Icon name="refresh" size={13} className="animate-spin" />
                  <span className="hidden sm:inline">{tr("canvas.saveStateSaving")}</span>
                </>
              )}
              {saveState === "saved" && (
                <>
                  <Icon name="check" size={13} />
                  <span className="hidden sm:inline">{tr("canvas.saveStateSaved")}</span>
                </>
              )}
              {saveState === "error" && (
                <>
                  <Icon name="alertTriangle" size={13} />
                  <span className="hidden sm:inline">{tr("canvas.saveStateError")}</span>
                  <button
                    onClick={handleRetrySave}
                    className="rounded p-0.5 hover:bg-graphite-800"
                    title={tr("canvas.saveStateRetry")}
                  >
                    <Icon name="refresh" size={12} />
                  </button>
                </>
              )}
            </span>
          )}
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
                  disabled={!hasResultTable}
                  title={!hasResultTable ? tr("canvas.exportNoTable") : undefined}
                  className="flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-200 hover:bg-graphite-700 transition disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {tr("canvas.exportXlsx")}
                </button>
                <button
                  disabled
                  title={tr("chat.exportPdfSoon")}
                  className="flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-400 opacity-50 cursor-not-allowed"
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
              <Icon name="save" size={14} className={isSaving ? "animate-spin" : ""} />
              {isSaving ? tr("canvas.saving") : tr("canvas.save")}
            </button>
          ) : (
            <div className="flex items-center gap-0.5">
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="flex h-7 items-center gap-1 rounded-md px-2 text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100 transition disabled:opacity-50 text-[12px]"
                title={tr("canvas.save")}
              >
                <Icon name="save" size={13} className={isSaving ? "animate-spin" : ""} />
                <span>{isSaving ? tr("canvas.saving") : tr("canvas.save")}</span>
              </button>
              <button
                onClick={handleStartEdit}
                className="flex h-7 w-7 items-center justify-center rounded-md text-graphite-400 hover:bg-graphite-800 hover:text-graphite-100 transition"
                title={tr("canvas.edit")}
              >
                <Icon name="pencil" />
              </button>
            </div>
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
                  responsive
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
                    const dense = (gridLayout[i]?.h ?? 0) <= MIN_SIZE.chart.h;
                    return (
                      <div key={tileId} className="h-full">
                        <DashboardTile
                          tile={tile}
                          isEditing={isEditing}
                          dense={dense}
                          onRename={(newTitle) => updateTile(i, { title: newTitle })}
                          onDelete={() => removeTile(i)}
                          onOpen={() => openArtifact(tile, sessionId)}
                          onElaborate={(selection) => handleElaborate(tile, selection)}
                          onMenu={(rect) => setMenu({ index: i, rect })}
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
                    <DashboardTile
                      tile={tile}
                      actions={
                        <AnswerExport
                          sessionId={sessionId}
                          headline={tile.title}
                          spec={tile.spec}
                          resultTable={tile.result_table}
                          compact
                          inline
                        />
                      }
                      onOpen={() => openArtifact(tile, sessionId)}
                      onElaborate={(selection) => handleElaborate(tile, selection)}
                    />
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

      {/* Меню действий плитки (fixed, вне скролла канваса) */}
      {menu && displayTiles[menu.index] && (
        <ItemActionMenu
          target="tile"
          title={displayTiles[menu.index].title}
          anchorRect={menu.rect}
          chartType={chartTypeOf(displayTiles[menu.index])}
          chartTypes={CHART_TYPES}
          onClose={() => setMenu(null)}
          onRename={(newTitle) => updateTile(menu.index, { title: newTitle })}
          onDelete={() => removeTile(menu.index)}
          onChartTypeChange={(type) => handleChartTypeChange(menu.index, type as ChartType)}
          onOpen={() => openArtifact(displayTiles[menu.index], sessionId)}
          onElaborate={() => handleElaborate(displayTiles[menu.index])}
        />
      )}
    </div>
  );
}
