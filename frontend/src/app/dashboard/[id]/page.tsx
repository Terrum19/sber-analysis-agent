"use client";

/**
 * Вкладка отдельного дашборда (/dashboard/[id]):
 * - Загрузка через fetchDashboard(id), скелетон и ошибки (404 / сеть)
 * - Дата снимка, переход в сессию
 * - «Обновить данные» -> recalculateDashboard(id)
 * - Редактирование названий, раскладки и удаление плиток с сохранением (updateDashboard)
 */
import { useEffect, useState } from "react";
import Link from "next/link";
import type { Layout } from "react-grid-layout";
import { exportArtifact, fetchDashboard, recalculateDashboard, updateDashboard } from "@/api/client";
import WorkspaceShell from "@/components/layout/WorkspaceShell";
import DashboardTile, { isKpiTile } from "@/components/canvas/DashboardTile";
import Icon from "@/components/ui/Icon";
import ZoomControl from "@/components/ui/ZoomControl";
import { tileGridLayout } from "@/lib/tileLayout";
import FitGrid from "@/components/canvas/FitGrid";
import { useOpenArtifact } from "@/hooks/useOpenArtifact";
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
  const [config, setConfig] = useState<DashboardConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [recalculating, setRecalculating] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [exportMenuOpen, setExportMenuOpen] = useState(false);
  const [isExporting, setIsExporting] = useState<"xlsx" | "pdf" | null>(null);

  const databaseTitle = useSessionStore((s) => {
    const dbId = config ? s.sessionDatabase[config.session_id] : undefined;
    return s.databases.find((d) => d.id === dbId)?.title;
  });

  useEffect(() => {
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchDashboard(params.id);
        if (!data || !data.id) {
          setError(t("dashboard.notFound"));
        } else {
          setConfig(data);
        }
      } catch (err: any) {
        setError(err.message || t("dashboard.notFound"));
      } finally {
        setLoading(false);
      }
    }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.id]);

  function flash(message: string, ms = 3000) {
    setStatusMessage(message);
    setTimeout(() => setStatusMessage(null), ms);
  }

  async function handleRecalculate() {
    setRecalculating(true);
    try {
      setConfig(await recalculateDashboard(params.id));
      flash(t("dashboard.recalculated"));
    } catch (err: any) {
      flash(t("canvas.refreshError"), 4000);
    } finally {
      setRecalculating(false);
    }
  }

  async function handleSave() {
    if (!config) return;
    setIsSaving(true);
    try {
      setConfig(await updateDashboard(params.id, { title: config.title, tiles: config.tiles }));
      setIsEditing(false);
      flash(t("dashboard.saved"));
    } catch (err: any) {
      flash(t("canvas.saveError", { msg: err.message }), 4000);
    } finally {
      setIsSaving(false);
    }
  }

  function handleRenameTile(index: number, newTitle: string) {
    if (!config) return;
    const newTiles = [...config.tiles];
    newTiles[index] = { ...newTiles[index], title: newTitle };
    setConfig({ ...config, tiles: newTiles });
  }

  function handleDeleteTile(index: number) {
    if (!config) return;
    setConfig({ ...config, tiles: config.tiles.filter((_, i) => i !== index) });
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
  }

  const gridLayout: Layout[] = tileGridLayout(config?.tiles || [], isKpiTile);

  function handleLayoutChange(newLayout: Layout[]) {
    if (!isEditing || !config) return;
    const updatedTiles = config.tiles.map((tile, i) => {
      const item = newLayout.find((l) => l.i === (tile.id || `tile-${i}`));
      return item ? { ...tile, layout: { x: item.x, y: item.y, w: item.w, h: item.h } } : tile;
    });
    setConfig({ ...config, tiles: updatedTiles });
  }

  function exportClientData(title: string, tileList: DashboardTileType[]) {
    const bom = "﻿";
    let csv = `Дашборд:;${title}\nДата выгрузки:;${new Date().toLocaleString("ru-RU")}\n\n`;
    csv += "№;Название плитки;Тип;Значение / Метрика;Инсайт;Таблица DuckDB\n";
    tileList.forEach((tile, i) => {
      const isKpi = isKpiTile(tile);
      let val = "";
      if (isKpi) {
        val = tile.snapshot_data?.value ?? "—";
      } else if (tile.spec?.data && typeof tile.spec.data === "object" && "values" in tile.spec.data) {
        val = `Записей: ${(tile.spec.data as any).values?.length || 0}`;
      }
      const cleanInsight = (tile.insight || "").replace(/[\r\n]+/g, " ");
      csv += `${i + 1};"${tile.title.replace(/"/g, '""')}";${isKpi ? "KPI" : "График"};"${String(val).replace(/"/g, '""')}";"${cleanInsight.replace(/"/g, '""')}";${tile.result_table || "—"}\n`;
    });

    tileList.forEach((tile, i) => {
      const rows = tile.snapshot_data?.records || (tile.spec?.data as any)?.values;
      if (Array.isArray(rows) && rows.length > 0) {
        csv += `\n\n--- Данные плитки ${i + 1}: ${tile.title} ---\n`;
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
    if (!config) return;
    setIsExporting(format);
    setExportMenuOpen(false);
    try {
      try {
        const blob = await exportArtifact(config.session_id || params.id, format);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${config.title || "dashboard"}.${format}`;
        a.click();
        URL.revokeObjectURL(url);
        flash(format === "pdf" ? t("canvas.pdfDone") : t("canvas.xlsxDone"), 3500);
      } catch {
        if (format === "pdf") {
          window.print();
          flash(t("canvas.printOpened"), 3500);
        } else {
          exportClientData(config.title, config.tiles);
          flash(t("canvas.csvDone"), 3500);
        }
      }
    } catch (err: any) {
      flash(t("canvas.exportError", { msg: err.message || String(err) }), 3500);
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

  if (error || !config) {
    return (
      <WorkspaceShell tab={tab}>
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <Icon name="search" size={24} className="text-graphite-500" />
          <h1 className="text-lg font-medium text-graphite-100">{t("dashboard.notFound")}</h1>
          <p className="max-w-sm text-[13px] text-graphite-400">{error || t("dashboard.notFoundText")}</p>
          <Link href="/dashboards" className="rounded-lg bg-graphite-100 px-4 py-2 text-[12px] font-medium text-graphite-950 hover:bg-graphite-50">
            {t("dashboard.back")}
          </Link>
        </div>
      </WorkspaceShell>
    );
  }

  return (
    <WorkspaceShell tab={tab}>
      <div className="h-full overflow-y-auto">
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
                  onChange={(e) => setConfig({ ...config, title: e.target.value })}
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
                  {t("dashboard.snapshotAt", { date: formatDate(config.updated_at || config.created_at) })}
                </span>
                <span className="font-mono text-[11px] text-graphite-500">ID: {config.id}</span>
              </div>
            </div>

            <div className="flex shrink-0 items-center gap-1">
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
                    {(["xlsx", "pdf"] as const).map((f) => (
                      <button
                        key={f}
                        onClick={() => handleExport(f)}
                        className="flex w-full rounded-md px-2.5 py-1.5 text-left text-[12px] text-graphite-200 hover:bg-graphite-700"
                      >
                        {f === "xlsx" ? t("canvas.exportXlsx") : t("canvas.exportPdf")}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {isEditing ? (
                <button
                  onClick={handleSave}
                  disabled={isSaving}
                  className="ml-1 flex h-8 items-center gap-1.5 rounded-lg bg-graphite-100 px-3 text-[12px] font-medium text-graphite-950 hover:bg-graphite-50 disabled:opacity-50"
                >
                  <Icon name="save" size={14} />
                  {isSaving ? t("canvas.saving") : t("canvas.save")}
                </button>
              ) : (
                <button onClick={() => setIsEditing(true)} className={iconButton}>
                  <Icon name="pencil" />
                  {t("canvas.edit")}
                </button>
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

          {config.tiles.length === 0 ? (
            <div className="flex h-56 items-center justify-center rounded-xl border border-graphite-800 text-[13px] text-graphite-500">
              {t("dashboard.noTiles")}
            </div>
          ) : (
            <FitGrid
                  zoom={zoom}
                  className="layout"
                  layout={gridLayout}
                  cols={12}
                  rowHeight={Math.round(65 * zoom)}
                  margin={[16, 16]}
                  containerPadding={[0, 0]}
                  isDraggable={isEditing}
                  isResizable={isEditing}
                  onLayoutChange={handleLayoutChange}
                  useCSSTransforms
                >
                  {config.tiles.map((tile, i) => (
                    <div key={tile.id || `tile-${i}`} className="h-full">
                      <DashboardTile
                        tile={tile}
                        isEditing={isEditing}
                        onRename={(newTitle) => handleRenameTile(i, newTitle)}
                        onDelete={() => handleDeleteTile(i)}
                        onOpen={() => openArtifact(tile, config.session_id)}
                      />
                    </div>
                  ))}
            </FitGrid>
          )}
        </div>
      </div>
    </WorkspaceShell>
  );
}
