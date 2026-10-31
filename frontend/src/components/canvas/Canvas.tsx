"use client";

/**
 * Канвас артефактов и дашборда (правая панель):
 * - Вкладки: Дашборд, Графики, Инсайты (согласно разметке макета)
 * - Отображение плиток с layout, KPI, графиками и инсайтами
 * - Кнопка «Обновить данные» (пересчёт снимка)
 * - Кнопка «Сохранить» (PUT /api/dashboards/{id})
 * - Режим редактирования (переименование, удаление плиток)
 * - «＋ в дашборды» на плитке (F-15); скрытые плитки не видны
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import type { Layout } from "react-grid-layout";
import { useCanvasStore } from "@/store/canvasStore";
import { useSessionStore } from "@/store/sessionStore";
import {
  createDashboard,
  exportArtifact,
  fetchDashboard,
  fetchDashboards,
  recalculateDashboard,
  updateDashboard,
} from "@/api/client";
import DashboardTile, { isKpiTile } from "@/components/canvas/DashboardTile";
import AddToDashboardsDialog from "@/components/canvas/AddToDashboardsDialog";
import AnswerExport from "@/components/chat/AnswerExport";
import Icon from "@/components/ui/Icon";
import ZoomControl from "@/components/ui/ZoomControl";
import { MIN_SIZE, STACK_BELOW, applyGridLayout, tileGridLayout } from "@/lib/tileLayout";
import { CHART_TYPES, chartTypeOf, withRebuiltChartType, type ChartType } from "@/lib/chartTypes";
import { useElementSize } from "@/hooks/useElementSize";
import FitGrid from "@/components/canvas/FitGrid";
import InsightsView from "@/components/insights/InsightsView";
import { buildInsights } from "@/lib/insights";
import { translate } from "@/i18n/useT";
import ChartTypePicker from "@/components/canvas/ChartTypePicker";
import ItemActionMenu from "@/components/layout/ItemActionMenu";
import { useChartTypeChange } from "@/hooks/useChartTypeChange";
import { useOpenArtifact } from "@/hooks/useOpenArtifact";
import { useAskAgent } from "@/hooks/useAskAgent";
import { drillQuestion, type ChartSelection } from "@/lib/chartSelection";
import { downloadBlob, safeFilename } from "@/lib/download";
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
  const [addTo, setAddTo] = useState<DashboardTileType | null>(null);
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
  const editSnapshotRef = useRef<{ title: string; tiles: DashboardTileType[] } | null>(null);

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
  // Скрытые плитки (график убран из дашборда, где построен) хранятся, но не рисуются;
  // i — индекс в displayTiles, по нему работают правки.
  const visible = useMemo(
    () => displayTiles.map((tile, i) => ({ tile, i })).filter(({ tile }) => !tile.hidden),
    [displayTiles],
  );
  const shownTiles = useMemo(() => visible.map(({ tile }) => tile), [visible]);
  const hasResultTable = shownTiles.some((tile) => Boolean(tile.result_table));

  // Инсайты: ходы ленты и плитки панели без хода (F-17)
  const messages = useSessionStore((s) => s.messages);
  const insights = useMemo(
    () =>
      buildInsights(messages, shownTiles, {
        ignore: [translate("ru", "canvas.newKpiInsight"), translate("en", "canvas.newKpiInsight")],
        chartTitle: tr("chat.chartDefault"),
      }),
    [messages, shownTiles, tr],
  );

  // Координаты сетки react-grid-layout
  // В узкой панели график по умолчанию во всю ширину, KPI — в половину
  const { ref: bodyRef, width: bodyWidth } = useElementSize<HTMLDivElement>();
  const stack = bodyWidth > 0 && bodyWidth * zoom < STACK_BELOW;
  const gridLayout: Layout[] = tileGridLayout(shownTiles, isKpiTile, { stack });

  // Запись плиток: сохранённый дашборд живёт в canvasStore, черновик сессии —
  // в sessionStore (он же синхронизирует canvasStore, пока dashboardId === null).
  function writeTiles(next: DashboardTileType[]) {
    if (dashboardId) setTiles(next);
    else useSessionStore.getState().setTiles(next);
  }

  /** committed — пользователь отпустил плитку (drag/resize): раскладка меняется и вне режима правки. */
  function handleLayoutChange(newLayout: Layout[], committed = false) {
    // Одна колонка в узкой панели — только отображение, сохранённую раскладку не трогаем
    if (stack || (!committed && (!isEditing || !layoutReadyRef.current))) return;
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
  // В режиме правки не сохраняем: правки уходят по «Сохранить», «Отмена» их откатывает.
  useEffect(() => {
    if (!dashboardId || layoutRevision === 0 || stack || isEditing) return;
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
  }, [layoutRevision, dashboardId, stack, isEditing, setSaveState]);

  // «Сохранено» гаснет через 2 секунды; «Ошибка» остаётся до повтора.
  useEffect(() => {
    if (saveState !== "saved") return;
    const timer = setTimeout(() => setSaveState("idle"), 2000);
    return () => clearTimeout(timer);
  }, [saveState, setSaveState]);

  // Возврат в чат: панель снова работает с последним сохранённым дашбордом сессии,
  // иначе «Сохранить» и «Обновить данные» создают его копию с исходным названием.
  useEffect(() => {
    let alive = true;
    fetchDashboards(sessionId)
      .then(async (list) => {
        const latest = [...list].sort((a, b) => Number(b.updated_at ?? 0) - Number(a.updated_at ?? 0))[0];
        if (!latest || !alive) return;
        const dashboard = await fetchDashboard(latest.id);
        const canvas = useCanvasStore.getState();
        if (!alive || canvas.dashboardId || useSessionStore.getState().sessionId !== sessionId) return;
        canvas.setDashboard(dashboard.id, dashboard.title, dashboard.tiles, dashboard.updated_at);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [sessionId]);

  useEffect(() => {
    mountedRef.current = true;
    function onToast(e: Event) {
      const msg = (e as CustomEvent)?.detail?.message;
      if (msg) {
        setSaveStatus(msg);
        setTimeout(() => setSaveStatus(null), 3500);
      }
    }
    window.addEventListener("app:toast", onToast);
    return () => {
      mountedRef.current = false;
      window.removeEventListener("app:toast", onToast);
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

  const {
    pickerState,
    openPicker,
    closePicker,
    setPreview,
    changeChartType,
    getTile,
  } = useChartTypeChange({
    dashboardId,
    tiles: displayTiles,
    onTilesChange: (nextTiles) => writeTiles(nextTiles),
    onDashboardUpdated: (updated) => {
      if (updated?.tiles) setTiles(updated.tiles);
    },
    setSaveState: (state, err) => setSaveState(state, err),
    onNotify: (msg) => {
      setSaveStatus(msg);
      setTimeout(() => setSaveStatus(null), 3500);
    },
  });

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

  function handleRemoveTile(index: number) {
    if (dashboardId) {
      removeTile(index);
    } else {
      removeTile(index);
      const next = sessionTiles.filter((_, i) => i !== index);
      useSessionStore.getState().setTiles(next);
    }
  }

  function handleStartEdit() {
    editSnapshotRef.current = { title: useCanvasStore.getState().dashboardTitle, tiles: displayTiles };
    if (tiles.length === 0 && sessionTiles.length > 0) {
      setTiles(sessionTiles);
    }
    setIsEditing(true);
  }

  /** Выйти из правки без сохранения: название и плитки — как до «карандаша». */
  function handleCancelEdit() {
    const snapshot = editSnapshotRef.current;
    editSnapshotRef.current = null;
    if (snapshot) {
      setDashboardTitle(snapshot.title);
      writeTiles(snapshot.tiles);
    }
    setIsEditing(false);
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
    if (shownTiles.length === 0) {
      setSaveStatus(tr("canvas.exportNoTable"));
      setTimeout(() => setSaveStatus(null), 3500);
      return;
    }
    setIsExporting(format);
    setExportMenuOpen(false);
    try {
      let targetDashboardId = dashboardId;
      const exportTitle = (dashboardTitle || tr("canvas.tabDashboard")).trim();

      if (!targetDashboardId) {
        const created = await createDashboard({
          session_id: sessionId,
          title: exportTitle,
          tiles: displayTiles,
        });
        targetDashboardId = created.id;
        useCanvasStore.getState().setDashboard(created.id, created.title, created.tiles, created.updated_at);
      } else {
        try {
          await updateDashboard(targetDashboardId, {
            title: exportTitle,
            tiles: displayTiles,
          });
        } catch (updateErr: any) {
          if (updateErr?.status === 404) {
            const created = await createDashboard({
              session_id: sessionId,
              title: exportTitle,
              tiles: displayTiles,
            });
            targetDashboardId = created.id;
            useCanvasStore.getState().setDashboard(created.id, created.title, created.tiles, created.updated_at);
          }
        }
      }
      const sState = useSessionStore.getState();
      const sessionDatabase = sState.databases.find(
        (d) => d.id === sState.sessionDatabase[sessionId]
      );
      const sessionObj = sState.databases
        .flatMap((d) => d.sessions)
        .find((s) => s.id === sessionId);
      const sessionTitle = sessionObj?.title;
      const databaseName = sessionDatabase?.title || sessionDatabase?.fileName;
      const sourceName =
        sessionDatabase?.fileName && sessionDatabase?.fileName !== databaseName
          ? sessionDatabase.fileName
          : undefined;

      const { blob, filename } = await exportArtifact(
        {
          session_id: sessionId,
          format,
          dashboard_id: targetDashboardId,
          title: exportTitle,
          session_title: sessionTitle,
          database_name: databaseName,
          source_name: sourceName,
        },
        safeFilename(exportTitle, format),
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
            <h2
              className="truncate text-[13px] font-medium text-graphite-100 cursor-text"
              onDoubleClick={() => setIsEditing(true)}
              title={tr("tile.rename")}
            >
              {dashboardTitle}
            </h2>
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
                  disabled={!hasResultTable || isExporting !== null}
                  title={!hasResultTable ? tr("canvas.exportNoTable") : undefined}
                  className="flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-200 hover:bg-graphite-700 transition disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span>{tr("canvas.exportXlsx")}</span>
                  {isExporting === "xlsx" && <Icon name="refresh" size={12} className="animate-spin text-graphite-400" />}
                </button>
                <button
                  onClick={() => handleExport("pdf")}
                  disabled={isExporting !== null || displayTiles.length === 0}
                  className="flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-200 hover:bg-graphite-700 transition disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span>{tr("canvas.exportPdf")}</span>
                  {isExporting === "pdf" && <Icon name="refresh" size={12} className="animate-spin text-graphite-400" />}
                </button>
              </div>
            )}
          </div>

          {isEditing ? (
            <>
              <button
                onClick={handleCancelEdit}
                disabled={isSaving}
                className="ml-1 flex items-center rounded-md border border-graphite-700 px-2.5 py-1 text-[12px] text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100 transition disabled:opacity-50"
                title={tr("canvas.cancelEditHint")}
              >
                {tr("canvas.cancelEdit")}
              </button>
              <button
                onClick={handleSave}
                disabled={isSaving}
                className="ml-1 flex items-center gap-1.5 rounded-md bg-graphite-100 px-2.5 py-1 text-[12px] font-medium text-graphite-950 hover:bg-graphite-50 transition disabled:opacity-50"
              >
                <Icon name="save" size={14} className={isSaving ? "animate-spin" : ""} />
                {isSaving ? tr("canvas.saving") : tr("canvas.save")}
              </button>
            </>
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
              ["charts", tr("canvas.tabCharts", { n: shownTiles.filter((x) => !isKpiTile(x)).length })],
              ["insights", insights.length > 0 ? tr("canvas.tabInsightsCount", { n: insights.length }) : tr("canvas.tabInsights")],
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
        {activeTab === "insights" ? (
          // Ответы чата есть и без плиток на панели
          <InsightsView sessionId={sessionId} cards={insights} />
        ) : shownTiles.length === 0 ? (
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
                  isDraggable={!stack}
                  isResizable={!stack}
                  // Вне режима правки плитку тащат за шапку: клики по графику и кнопкам работают
                  draggableHandle=".tile-drag-handle"
                  draggableCancel="button, input, a, select"
                  onLayoutChange={handleLayoutChange}
                  onDragStop={(layout: Layout[]) => handleLayoutChange(layout, true)}
                  onResizeStop={(layout: Layout[]) => handleLayoutChange(layout, true)}
                  useCSSTransforms={true}
                >
                  {visible.map(({ tile, i }, shownIndex) => {
                    const tileId = tile.id || `tile-${i}`;
                    const dense = (gridLayout[shownIndex]?.h ?? 0) <= MIN_SIZE.chart.h;
                    const effectiveTile = getTile(tile, i);
                    return (
                      <div key={tileId} className="h-full">
                        <DashboardTile
                          tile={effectiveTile}
                          isEditing={isEditing}
                          draggable={!stack}
                          dense={dense}
                          onRename={(newTitle) => updateTile(i, { title: newTitle })}
                          onDelete={() => handleRemoveTile(i)}
                          onOpen={() => openArtifact(tile, sessionId, dashboardId ?? undefined)}
                          onElaborate={(selection) => handleElaborate(tile, selection)}
                          onChangeChartType={(rect) => openPicker(i, rect)}
                          onMenu={(rect) => setMenu({ index: i, rect })}
                          onAddToDashboards={() => setAddTo(tile)}
                        />
                      </div>
                    );
                  })}
            </FitGrid>
          </div>
        ) : activeTab === "charts" ? (
          shownTiles.filter((t) => !t.is_kpi).length === 0 ? (
            <div className="flex h-48 flex-col items-center justify-center rounded-xl border border-graphite-800 bg-graphite-950/40 text-center text-graphite-500 space-y-2">
              <Icon name="layout" size={20} className="text-graphite-500" />
              <p className="text-xs">{tr("canvas.noCharts")}</p>
            </div>
          ) : (
            <div className="space-y-4">
              {shownTiles
                .filter((t) => !isKpiTile(t))
                .map((tile, i) => {
                  const origIdx = displayTiles.findIndex(
                    (t) => t === tile || (tile.id && t.id === tile.id),
                  );
                  const effectiveTile = getTile(tile, origIdx);
                  return (
                    <div key={tile.id || origIdx || i} style={{ height: Math.round(340 * zoom) }}>
                      <DashboardTile
                        tile={effectiveTile}
                        isEditing={isEditing}
                        onRename={(newTitle) => {
                          if (origIdx >= 0) updateTile(origIdx, { title: newTitle });
                        }}
                        onMenu={(rect) => {
                          if (origIdx >= 0) setMenu({ index: origIdx, rect });
                        }}
                        actions={
                          <AnswerExport
                            sessionId={sessionId}
                            title={tile.title}
                            headline={tile.insight || undefined}
                            details={tile.insight ? [tile.insight] : undefined}
                            spec={
                              tile.snapshot_data &&
                              Array.isArray(tile.snapshot_data) &&
                              !(tile.spec?.data as any)?.values
                                ? { ...tile.spec, data: { values: tile.snapshot_data } }
                                : tile.spec
                            }
                            resultTable={tile.result_table}
                            compact
                            inline
                          />
                        }
                        onOpen={() => openArtifact(tile, sessionId, dashboardId ?? undefined)}
                        onElaborate={(selection) => handleElaborate(tile, selection)}
                        onAddToDashboards={() => setAddTo(tile)}
                        onChangeChartType={(rect) => openPicker(origIdx, rect)}
                      />
                    </div>
                  );
                })}
            </div>
          )
        ) : null}
      </div>

      {/* Меню действий плитки (fixed, вне скролла канваса) */}
      {menu && displayTiles[menu.index] && (
        <ItemActionMenu
          target="tile"
          title={displayTiles[menu.index].title}
          anchorRect={menu.rect}
          onClose={() => setMenu(null)}
          onRename={(newTitle) => updateTile(menu.index, { title: newTitle })}
          onDelete={() => handleRemoveTile(menu.index)}
          onOpen={() => openArtifact(displayTiles[menu.index], sessionId, dashboardId ?? undefined)}
          onElaborate={() => handleElaborate(displayTiles[menu.index])}
        />
      )}

      {addTo && (
        <AddToDashboardsDialog
          tile={addTo}
          sessionId={sessionId}
          current={dashboardId ? { id: dashboardId, tiles } : null}
          onCurrentSaved={(updated) => setTiles(updated.tiles)}
          onDone={(message) => {
            setSaveStatus(message);
            setTimeout(() => setSaveStatus(null), 4000);
          }}
          onClose={() => setAddTo(null)}
        />
      )}

      {/* Поповер выбора типа графика */}
      {pickerState && displayTiles[pickerState.tileIndex] && (
        <ChartTypePicker
          anchorRect={pickerState.anchorRect}
          currentType={chartTypeOf(displayTiles[pickerState.tileIndex])}
          onSelect={(type) => changeChartType(pickerState.tileIndex, type)}
          onPreview={setPreview}
          onClose={closePicker}
        />
      )}
    </div>
  );
}
