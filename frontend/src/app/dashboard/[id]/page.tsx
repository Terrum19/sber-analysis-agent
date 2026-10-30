"use client";

/**
 * Вкладка отдельного дашборда (/dashboard/[id]):
 * - Загрузка через fetchDashboard(id), скелетон и ошибки (404 / сеть)
 * - Дата снимка, переход в сессию
 * - «Обновить данные» -> recalculateDashboard(id)
 * - Редактирование названий, раскладки и удаление плиток с сохранением (updateDashboard)
 * - «＋ в дашборды» и переход к чату, где построен график (F-15); скрытые плитки не видны
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Layout } from "react-grid-layout";
import {
  exportArtifact,
  fetchDashboard,
  getSession,
  isNotFound,
  recalculateDashboard,
  updateDashboard,
} from "@/api/client";
import WorkspaceShell from "@/components/layout/WorkspaceShell";
import DashboardTile from "@/components/canvas/DashboardTile";
import AddToDashboardsDialog from "@/components/canvas/AddToDashboardsDialog";
import Icon from "@/components/ui/Icon";
import ZoomControl from "@/components/ui/ZoomControl";
import ItemActionMenu from "@/components/layout/ItemActionMenu";
import { MIN_SIZE, STACK_BELOW, applyGridLayout, isKpiTile, tileGridLayout } from "@/lib/tileLayout";
import { CHART_TYPES, chartTypeOf, withChartType, type ChartType } from "@/lib/chartTypes";
import { dashboardSnapshotAt } from "@/lib/dashboard";
import FitGrid from "@/components/canvas/FitGrid";
import { useOpenArtifact } from "@/hooks/useOpenArtifact";
import { useAskAgent } from "@/hooks/useAskAgent";
import { useElementSize } from "@/hooks/useElementSize";
import { drillQuestion, type ChartSelection } from "@/lib/chartSelection";
import { downloadBlob } from "@/lib/download";
import { useSessionStore } from "@/store/sessionStore";
import { useUiStore } from "@/store/uiStore";
import { useDateLocale, useT } from "@/i18n/useT";
import type { DashboardConfig, DashboardTile as DashboardTileType } from "@/types/dashboard";


const iconButton =
  "flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] text-graphite-300 hover:bg-graphite-800 hover:text-graphite-50 transition disabled:opacity-50";

export default function DashboardPage({ params }: { params: { id: string } }) {
  const t = useT();
  const dateLocale = useDateLocale();
  const zoom = useUiStore((s) => s.zoom);
  const openArtifact = useOpenArtifact();
  const askAgent = useAskAgent();
  // На узком экране плитки идут одной колонкой, drag/resize и раскладка отключены.
  const { ref: pageBodyRef, width: pageWidth } = useElementSize<HTMLDivElement>();
  const stack = pageWidth > 0 && pageWidth * zoom < STACK_BELOW;
  const [config, setConfig] = useState<DashboardConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [isExporting, setIsExporting] = useState<"xlsx" | "pdf" | null>(null);
  const [menu, setMenu] = useState<{ index: number; rect: DOMRect } | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [addTo, setAddTo] = useState<number | null>(null);
  const [deletedChats, setDeletedChats] = useState<Set<string>>(new Set());
  const router = useRouter();
  const configRef = useRef<DashboardConfig | null>(null);
  const editSnapshotRef = useRef<DashboardConfig | null>(null);
  const saveSeqRef = useRef(0);
  configRef.current = config;

  function markDirty() {
    setRevision((value) => value + 1);
  }

  const databaseTitle = useSessionStore((s) => {
    const dbId = config ? s.sessionDatabase[config.session_id] : undefined;
    return s.databases.find((d) => d.id === dbId)?.title;
  });

  const load = useCallback(async () => {
    setLoading(true);
    setConfig(null);
    setNotFound(false);
    setError(null);
    setRevision(0);
    setSaveState("idle");
    setSaveError(null);
    try {
      const data = await fetchDashboard(params.id);
      if (!data || !data.id) {
        setNotFound(true);
        return;
      }
      setConfig(data);
    } catch (err: any) {
      if (isNotFound(err)) {
        setNotFound(true);
      } else {
        setError(err?.detail || err?.message || t("dashboard.loadError"));
      }
    } finally {
      setLoading(false);
    }
  }, [params.id, t]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Сессия, где построен график: у старых плиток — сессия дашборда. */
  function chatOf(tile: DashboardTileType): string | undefined {
    return tile.source_session_id || config?.session_id || undefined;
  }

  // Чаты, на которые ссылаются плитки: удалённые — «Открыть чат» неактивен.
  const chatIds = config
    ? Array.from(new Set(config.tiles.map((tile) => tile.source_session_id || config.session_id).filter(Boolean)))
        .sort()
        .join(",")
    : "";
  useEffect(() => {
    if (!chatIds) return;
    let alive = true;
    Promise.all(
      chatIds.split(",").map((id) =>
        getSession(id).then(
          () => null,
          (err) => (isNotFound(err) ? id : null),
        ),
      ),
    ).then((missing) => {
      if (alive) setDeletedChats(new Set(missing.filter((id): id is string => Boolean(id))));
    });
    return () => {
      alive = false;
    };
  }, [chatIds]);

  function handleOpenChat(tile: DashboardTileType) {
    const sessionId = chatOf(tile);
    if (!sessionId) return;
    const turn = tile.source_turn_id ? `?turn=${encodeURIComponent(tile.source_turn_id)}` : "";
    router.push(`/session/${sessionId}${turn}`);
  }

  // Автосохранение правок страницы (раскладка, название плитки/дашборда,
  // удаление и добавление плиток): debounce 800 мс, последний запрос выигрывает.
  // В режиме правки не сохраняем: правки уходят по «Сохранить», «Отмена» их откатывает.
  useEffect(() => {
    if (revision === 0 || isEditing) return;
    const timer = setTimeout(async () => {
      const snapshot = configRef.current;
      if (!snapshot) return;
      const seq = ++saveSeqRef.current;
      setSaveState("saving");
      setSaveError(null);
      try {
        const updated = await updateDashboard(params.id, {
          title: snapshot.title,
          tiles: snapshot.tiles,
        });
        if (seq !== saveSeqRef.current) return;
        // Метаданные сервера; локальные tiles не заменяем — они могли измениться
        // за время запроса и должны попасть в следующий PUT.
        setConfig((prev) =>
          prev
            ? {
                ...prev,
                updated_at: updated.updated_at,
                snapshot_at: updated.snapshot_at ?? prev.snapshot_at,
              }
            : prev,
        );
        setSaveState("saved");
      } catch (err: any) {
        if (seq !== saveSeqRef.current) return;
        if (isNotFound(err)) {
          setConfig(null);
          setNotFound(true);
          return;
        }
        setSaveError(err?.detail || err?.message || String(err));
        setSaveState("error");
      }
    }, 800);
    return () => clearTimeout(timer);
  }, [revision, isEditing, params.id]);

  // «Сохранено» гаснет через 2 секунды; «Ошибка» остаётся до повтора.
  useEffect(() => {
    if (saveState !== "saved") return;
    const timer = setTimeout(() => setSaveState("idle"), 2000);
    return () => clearTimeout(timer);
  }, [saveState]);

  async function handleRetrySave() {
    const snapshot = configRef.current;
    if (!snapshot) return;
    const seq = ++saveSeqRef.current;
    setSaveState("saving");
    setSaveError(null);
    try {
      await updateDashboard(params.id, { title: snapshot.title, tiles: snapshot.tiles });
      if (seq !== saveSeqRef.current) return;
      setSaveState("saved");
    } catch (err: any) {
      if (seq !== saveSeqRef.current) return;
      if (isNotFound(err)) {
        setConfig(null);
        setNotFound(true);
        return;
      }
      setSaveError(err?.detail || err?.message || String(err));
      setSaveState("error");
    }
  }

  function flash(message: string, ms = 3000) {
    setStatusMessage(message);
    setTimeout(() => setStatusMessage(null), ms);
  }

  async function handleRecalculate() {
    setRecalculating(true);
    try {
      const updated = await recalculateDashboard(params.id);
      setConfig(updated);
      setRevision(0);
      flash(t("dashboard.recalculated"));
    } catch (err: any) {
      if (isNotFound(err)) {
        setConfig(null);
        setNotFound(true);
        return;
      }
      const msg = err?.detail || err?.message;
      flash(msg ? `${t("canvas.refreshError")}: ${msg}` : t("canvas.refreshError"), 4000);
    } finally {
      setRecalculating(false);
    }
  }

  async function handleSave() {
    if (!config) return;
    setIsSaving(true);
    setSaveState("saving");
    setSaveError(null);
    try {
      const updated = await updateDashboard(params.id, { title: config.title, tiles: config.tiles });
      setConfig(updated);
      setRevision(0);
      setSaveState("saved");
      setIsEditing(false);
      flash(t("dashboard.saved"));
    } catch (err: any) {
      const msg = err?.detail || err?.message || String(err);
      setSaveError(msg);
      setSaveState(isNotFound(err) ? "idle" : "error");
      if (isNotFound(err)) {
        setConfig(null);
        setNotFound(true);
      }
      flash(t("canvas.saveError", { msg }), 4000);
    } finally {
      setIsSaving(false);
    }
  }

  function handleStartEdit() {
    editSnapshotRef.current = config;
    setIsEditing(true);
  }

  /** Выйти из правки без сохранения: дашборд — как до «Редактировать». */
  function handleCancelEdit() {
    const snapshot = editSnapshotRef.current;
    editSnapshotRef.current = null;
    if (snapshot) setConfig(snapshot);
    setIsEditing(false);
  }

  function handleRenameTile(index: number, newTitle: string) {
    if (!config) return;
    const newTiles = [...config.tiles];
    newTiles[index] = { ...newTiles[index], title: newTitle };
    setConfig({ ...config, tiles: newTiles });
    markDirty();
  }

  function handleDeleteTile(index: number) {
    if (!config) return;
    setConfig({ ...config, tiles: config.tiles.filter((_, i) => i !== index) });
    markDirty();
  }

  /** «Дообследовать»: вопрос агенту в чат сессии дашборда (F-09). */
  function handleElaborate(index: number, selection?: ChartSelection) {
    if (!config?.session_id) {
      flash(t("drill.noSession"));
      return;
    }
    const tile = config.tiles[index];
    if (!tile) return;
    askAgent(config.session_id, drillQuestion(t, tile.title, selection));
  }

  function handleAddKpi() {
    if (!config) return;
    const newKpi: DashboardTileType = {
      id: `kpi-${Date.now()}`,
      title: t("canvas.newKpi"),
      spec: {},
      is_kpi: true,
      snapshot_data: { value: "—", label: t("canvas.newKpiLabel") },
      layout: { x: (config.tiles.length % 2) * 6, y: Math.floor(config.tiles.length / 2) * 4, w: 6, h: 3 },
      insight: t("canvas.newKpiInsight"),
    };
    setConfig({ ...config, tiles: [...config.tiles, newKpi] });
    markDirty();
  }

  // Скрытые плитки (график убран из дашборда, где построен) хранятся, но не рисуются;
  // i — индекс в config.tiles, по нему работают правки.
  const visible = (config?.tiles || []).map((tile, i) => ({ tile, i })).filter(({ tile }) => !tile.hidden);
  const gridLayout: Layout[] = tileGridLayout(visible.map(({ tile }) => tile), isKpiTile, { stack });

  /** committed — пользователь отпустил плитку (drag/resize): раскладка меняется и вне режима правки. */
  function handleLayoutChange(newLayout: Layout[], committed = false) {
    if ((!isEditing && !committed) || !config || stack) return;
    const tiles = applyGridLayout(config.tiles, newLayout);
    const changed = tiles.some((tile, i) => {
      const before = config.tiles[i]?.layout;
      return (
        !before ||
        before.x !== tile.layout.x ||
        before.y !== tile.layout.y ||
        before.w !== tile.layout.w ||
        before.h !== tile.layout.h
      );
    });
    if (changed) {
      setConfig({ ...config, tiles });
      markDirty();
    }
  }

  /** Смена типа графика без LLM: PUT + recalculate (если есть result_table). */
  async function handleChartTypeChange(index: number, type: ChartType) {
    if (!config) return;
    const prevConfig = config;
    const nextTiles = config.tiles.map((tile, i) =>
      i === index ? withChartType(tile, type) : tile,
    );
    setConfig({ ...config, tiles: nextTiles });
    try {
      await updateDashboard(params.id, { tiles: nextTiles });
      if (nextTiles[index].result_table) {
        const updated = await recalculateDashboard(params.id);
        setConfig(updated);
      } else {
        flash(t("canvas.chartTypeLocalOnly"), 4000);
      }
      setRevision(0);
    } catch (err: any) {
      if (isNotFound(err)) {
        setConfig(null);
        setNotFound(true);
        return;
      }
      setConfig(prevConfig);
      flash(t("canvas.saveError", { msg: err?.detail || err?.message || String(err) }), 4000);
    }
  }

  async function handleExport(format: "xlsx" | "pdf") {
    if (!config) return;
    setIsExporting(format);
    setExportMenuOpen(false);
    try {
      const shownTiles = visible.map(({ tile }) => tile);
      const source = [...shownTiles].reverse().find((tile) => tile.result_table);
      const target = source ?? shownTiles[0];

      // Готовим спецификацию с данными (из spec или snapshot_data)
      let spec = target?.spec as Record<string, any> | undefined;
      const specData = spec?.data as Record<string, any> | undefined;
      if (
        spec &&
        Array.isArray(target?.snapshot_data) &&
        (!specData || !Array.isArray(specData.values))
      ) {
        spec = { ...spec, data: { values: target.snapshot_data } };
      } else if (!spec && Array.isArray(target?.snapshot_data)) {
        spec = { data: { values: target.snapshot_data } };
      }

      const { blob, filename } = await exportArtifact(
        {
          session_id: config.session_id || params.id,
          format,
          result_ref: source?.result_table,
          dashboard_id: params.id,
          ...(target
            ? {
                headline: target.title || target.insight,
                details: target.insight ? [target.insight] : undefined,
                spec,
              }
            : {}),
        },
        `${config.title || "dashboard"}.${format}`,
      );
      downloadBlob(blob, filename);
      flash(format === "pdf" ? t("canvas.pdfDone") : t("canvas.xlsxDone"), 3500);
    } catch (err: any) {
      if (err?.status === 501) {
        flash(t("chat.exportPdfInProgress"), 4000);
      } else {
        flash(t("canvas.exportError", { msg: err?.detail || err?.message || String(err) }), 3500);
      }
    } finally {
      setIsExporting(null);
    }
  }

  function formatDate(ts?: number | string) {
    if (!ts) return "—";
    const d = typeof ts === "number" && ts < 1e11 ? new Date(ts * 1000) : new Date(ts);
    return isNaN(d.getTime()) ? String(ts) : d.toLocaleString(dateLocale, { dateStyle: "medium", timeStyle: "short" });
  }

  const tab = { kind: "dashboard" as const, id: params.id, title: config?.title };

  if (loading) {
    return (
      <WorkspaceShell tab={tab}>
        <div className="mx-auto w-full max-w-6xl space-y-5 px-6 py-8">
          <div className="h-8 w-64 animate-pulse rounded-lg bg-graphite-800" />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {[1, 2, 3, 4].map((n) => (
              <div key={n} className="h-48 animate-pulse rounded-xl bg-graphite-850" />
            ))}
          </div>
        </div>
      </WorkspaceShell>
    );
  }

  if (notFound) {
    return (
      <WorkspaceShell tab={tab}>
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <Icon name="search" size={24} className="text-graphite-500" />
          <h1 className="text-lg font-medium text-graphite-100">{t("dashboard.notFound")}</h1>
          <p className="max-w-sm text-[13px] text-graphite-400">{t("dashboard.notFoundText")}</p>
          <Link href="/dashboards" className="rounded-lg bg-graphite-100 px-4 py-2 text-[12px] font-medium text-graphite-950 hover:bg-graphite-50">
            {t("dashboard.back")}
          </Link>
        </div>
      </WorkspaceShell>
    );
  }

  if (error || !config) {
    return (
      <WorkspaceShell tab={tab}>
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <Icon name="alertTriangle" size={24} className="text-bad" />
          <h1 className="text-lg font-medium text-graphite-100">{t("dashboard.loadError")}</h1>
          <p className="max-w-md text-[13px] text-graphite-400">{error || t("dashboard.loadError")}</p>
          <div className="flex items-center gap-2">
            <button
              onClick={() => void load()}
              className="flex items-center gap-1.5 rounded-lg bg-graphite-100 px-4 py-2 text-[12px] font-medium text-graphite-950 hover:bg-graphite-50"
            >
              <Icon name="refresh" size={13} />
              {t("dashboard.retry")}
            </button>
            <Link href="/dashboards" className="rounded-lg border border-graphite-700 px-4 py-2 text-[12px] text-graphite-300 hover:bg-graphite-800">
              {t("dashboard.back")}
            </Link>
          </div>
        </div>
      </WorkspaceShell>
    );
  }

  const snapshotAt = dashboardSnapshotAt(config);
  const staleCount = visible.filter(({ tile }) => tile.stale).length;
  const hasResultTable = visible.some(({ tile }) => Boolean(tile.result_table));

  return (
    <WorkspaceShell tab={tab}>
      <div ref={pageBodyRef} className="h-full overflow-y-auto">
        <div className="mx-auto max-w-6xl space-y-5 px-6 py-6 md:px-8">
          {/* Заголовок и действия */}
          <div className="flex flex-col justify-between gap-4 border-b border-graphite-800 pb-4 md:flex-row md:items-end">
            <div className="min-w-0 space-y-1.5">
              <div className="flex flex-wrap items-center gap-2 text-[12px] text-graphite-400">
                <Link href="/dashboards" className="hover:text-graphite-100">
                  ← {t("dashboard.back")}
                </Link>
                {config.session_id && (
                  <>
                    <span className="text-graphite-600">·</span>
                    <Link href={`/session/${config.session_id}`} className="flex items-center gap-1 hover:text-graphite-100">
                      <Icon name="chat" size={13} />
                      {t("dashboard.toSession")}
                    </Link>
                  </>
                )}
              </div>
              {isEditing ? (
                <input
                  type="text"
                  value={config.title}
                  onChange={(e) => {
                    setConfig({ ...config, title: e.target.value });
                    markDirty();
                  }}
                  className="w-full max-w-xl rounded-lg border border-graphite-600 bg-graphite-950 px-3 py-1 text-xl text-graphite-50 outline-none focus:border-graphite-400"
                />
              ) : (
                <h1 className="truncate text-2xl font-light tracking-tight text-graphite-50">{config.title}</h1>
              )}
              <div className="flex flex-wrap items-center gap-2.5 text-[12px] text-graphite-400">
                {databaseTitle && (
                  <span className="flex items-center gap-1.5">
                    <Icon name="folder" size={13} />
                    {databaseTitle}
                  </span>
                )}
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-1.5 w-1.5 rounded-full bg-good" />
                  {t("dashboard.snapshotAt", {
                    date: snapshotAt ? formatDate(snapshotAt) : t("dashboard.snapshotMissing"),
                  })}
                </span>
                {staleCount > 0 && (
                  <span
                    className="inline-flex items-center gap-1.5 text-amber-400"
                    title={t("tile.staleTooltip")}
                  >
                    <Icon name="alertTriangle" size={13} />
                    {t("dashboard.staleCount", { n: staleCount })}
                  </span>
                )}
                <span className="font-mono text-[11px] text-graphite-500">ID: {config.id}</span>
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-1">
              {/* Статус автосохранения правок */}
              {saveState !== "idle" && (
                <span
                  className={`mr-0.5 inline-flex h-8 items-center gap-1 rounded-md px-1.5 text-[11px] ${
                    saveState === "error" ? "text-bad" : saveState === "saving" ? "text-graphite-400" : "text-good"
                  }`}
                  title={saveState === "error" ? saveError ?? t("canvas.saveStateError") : undefined}
                >
                  {saveState === "saving" && (
                    <>
                      <Icon name="refresh" size={13} className="animate-spin" />
                      <span className="hidden sm:inline">{t("canvas.saveStateSaving")}</span>
                    </>
                  )}
                  {saveState === "saved" && (
                    <>
                      <Icon name="check" size={13} />
                      <span className="hidden sm:inline">{t("canvas.saveStateSaved")}</span>
                    </>
                  )}
                  {saveState === "error" && (
                    <>
                      <Icon name="alertTriangle" size={13} />
                      <span className="hidden sm:inline">{t("canvas.saveStateError")}</span>
                      <button
                        onClick={() => void handleRetrySave()}
                        className="rounded p-0.5 hover:bg-graphite-800"
                        title={t("canvas.saveStateRetry")}
                      >
                        <Icon name="refresh" size={12} />
                      </button>
                    </>
                  )}
                </span>
              )}
              <ZoomControl />
              <span className="mx-1 h-4 w-px bg-graphite-800" />
              <button onClick={handleRecalculate} disabled={recalculating} className={iconButton}>
                <Icon name="refresh" className={recalculating ? "animate-spin" : ""} />
                {recalculating ? t("dashboard.refreshing") : t("dashboard.refresh")}
              </button>

              <div className="relative">
                <button onClick={() => setExportMenuOpen((p) => !p)} disabled={isExporting !== null} className={iconButton}>
                  <Icon name="download" />
                  {isExporting ? t("canvas.exporting") : t("canvas.export")}
                </button>
                {exportMenuOpen && (
                  <div className="absolute right-0 top-full z-50 mt-1 w-40 rounded-lg border border-graphite-700 bg-graphite-850 p-1 shadow-2xl">
                    <button
                      onClick={() => handleExport("xlsx")}
                      disabled={!hasResultTable}
                      title={!hasResultTable ? t("canvas.exportNoTable") : undefined}
                      className="flex w-full rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-200 hover:bg-graphite-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {t("canvas.exportXlsx")}
                    </button>
                    <button
                      disabled
                      title={t("chat.exportPdfSoon")}
                      className="flex w-full rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-400 opacity-50 cursor-not-allowed"
                    >
                      {t("canvas.exportPdf")}
                    </button>
                  </div>
                )}
              </div>

              {isEditing ? (
                <>
                  <button
                    onClick={handleCancelEdit}
                    disabled={isSaving}
                    className="ml-1 flex h-8 items-center rounded-lg border border-graphite-700 px-3 text-[12px] text-graphite-300 hover:bg-graphite-800 hover:text-graphite-50 disabled:opacity-50"
                    title={t("canvas.cancelEditHint")}
                  >
                    {t("canvas.cancelEdit")}
                  </button>
                  <button
                    onClick={handleSave}
                    disabled={isSaving}
                    className="ml-1 flex h-8 items-center gap-1.5 rounded-lg bg-graphite-100 px-3 text-[12px] font-medium text-graphite-950 hover:bg-graphite-50 disabled:opacity-50"
                  >
                    <Icon name="save" size={14} className={isSaving ? "animate-spin" : ""} />
                    {isSaving ? t("canvas.saving") : t("canvas.save")}
                  </button>
                </>
              ) : (
                <div className="flex items-center gap-1">
                  <button onClick={handleSave} disabled={isSaving} className={iconButton}>
                    <Icon name="save" size={14} className={isSaving ? "animate-spin" : ""} />
                    {isSaving ? t("canvas.saving") : t("canvas.save")}
                  </button>
                  <button onClick={handleStartEdit} className={iconButton}>
                    <Icon name="pencil" />
                    {t("canvas.edit")}
                  </button>
                </div>
              )}
            </div>
          </div>

          {statusMessage && (
            <div className="rounded-lg border border-graphite-700 bg-graphite-850 px-3 py-2 text-[12px] text-graphite-200">
              {statusMessage}
            </div>
          )}

          {isEditing && (
            <div className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-graphite-700 px-3 py-2">
              <span className="text-[12px] text-graphite-400">{t("dashboard.editHint")}</span>
              <button
                onClick={handleAddKpi}
                className="flex shrink-0 items-center gap-1 rounded-md bg-graphite-800 px-2.5 py-1 text-[12px] text-graphite-100 hover:bg-graphite-700"
              >
                <Icon name="plus" size={13} />
                {t("canvas.addKpi")}
              </button>
            </div>
          )}

          {visible.length === 0 ? (
            <div className="flex h-56 items-center justify-center rounded-xl border border-graphite-800 text-[13px] text-graphite-500">
              {t("dashboard.noTiles")}
            </div>
          ) : (
            <FitGrid
                  responsive
                  zoom={zoom}
                  className="layout"
                  layout={gridLayout}
                  cols={12}
                  rowHeight={Math.round(60 * zoom)}
                  margin={[16, 16]}
                  containerPadding={[0, 0]}
                  isDraggable={!stack}
                  isResizable={!stack}
                  // Вне режима правки плитку тащат за шапку: клики по графику и кнопкам работают
                  draggableHandle={isEditing ? undefined : ".tile-drag-handle"}
                  draggableCancel="button, input, a, select"
                  onLayoutChange={handleLayoutChange}
                  onDragStop={(layout: Layout[]) => handleLayoutChange(layout, true)}
                  onResizeStop={(layout: Layout[]) => handleLayoutChange(layout, true)}
                  useCSSTransforms
                >
                  {visible.map(({ tile, i }, shownIndex) => {
                    const dense = (gridLayout[shownIndex]?.h ?? 0) <= MIN_SIZE.chart.h;
                    const chat = chatOf(tile);
                    return (
                      <div key={tile.id || `tile-${i}`} className="h-full">
                        <DashboardTile
                          tile={tile}
                          isEditing={isEditing}
                          draggable={!stack}
                          dense={dense}
                          onRename={(newTitle) => handleRenameTile(i, newTitle)}
                          onDelete={() => handleDeleteTile(i)}
                          onOpen={() => openArtifact(tile, config.session_id)}
                          onElaborate={(selection) => handleElaborate(i, selection)}
                          onMenu={(rect) => setMenu({ index: i, rect })}
                          onAddToDashboards={() => setAddTo(i)}
                          onOpenChat={chat ? () => handleOpenChat(tile) : undefined}
                          chatDeleted={Boolean(chat && deletedChats.has(chat))}
                        />
                      </div>
                    );
                  })}
            </FitGrid>
          )}
        </div>
      </div>

      {/* Меню действий плитки */}
      {menu && config.tiles[menu.index] && (
        <ItemActionMenu
          target="tile"
          title={config.tiles[menu.index].title}
          anchorRect={menu.rect}
          chartType={chartTypeOf(config.tiles[menu.index])}
          chartTypes={CHART_TYPES}
          onClose={() => setMenu(null)}
          onRename={(newTitle) => handleRenameTile(menu.index, newTitle)}
          onDelete={() => handleDeleteTile(menu.index)}
          onChartTypeChange={(type) => handleChartTypeChange(menu.index, type as ChartType)}
          onOpen={() => openArtifact(config.tiles[menu.index], config.session_id)}
          onElaborate={() => handleElaborate(menu.index)}
        />
      )}

      {addTo !== null && config.tiles[addTo] && (
        <AddToDashboardsDialog
          tile={config.tiles[addTo]}
          sessionId={config.session_id}
          current={{ id: config.id, tiles: config.tiles }}
          onCurrentSaved={(updated) => setConfig((prev) => (prev ? { ...updated, title: prev.title } : updated))}
          onDone={(message) => flash(message, 4000)}
          onClose={() => setAddTo(null)}
        />
      )}
    </WorkspaceShell>
  );
}
