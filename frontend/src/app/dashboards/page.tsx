"use client";

/**
 * Вкладка «Дашборды» (/dashboards) — реестр всех сохранённых дашбордов:
 * - Поиск по названию, ID или дате
 * - Карточка: ID, база данных, небольшое превью плиток
 * - Клик открывает дашборд во вкладке, «✕» удаляет
 */
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { deleteDashboard, fetchDashboard, fetchDashboards, isNotFound } from "@/api/client";
import WorkspaceShell from "@/components/layout/WorkspaceShell";
import DashboardTile, { isKpiTile } from "@/components/canvas/DashboardTile";
import Icon from "@/components/ui/Icon";
import { useSessionStore } from "@/store/sessionStore";
import { useDateLocale, useT } from "@/i18n/useT";
import { describeDatabase } from "@/lib/describeDatabase";
import type { DashboardConfig, DashboardListItem } from "@/types/dashboard";

function toDate(ts?: number | string): Date | null {
  if (ts === undefined || ts === null || ts === "") return null;
  const d = typeof ts === "number" && ts < 1e11 ? new Date(ts * 1000) : new Date(ts);
  return isNaN(d.getTime()) ? null : d;
}

/** Превью: до четырёх плиток, KPI первыми. */
function DashboardPreview({ config }: { config: DashboardConfig | undefined }) {
  const t = useT();
  if (!config) return <div className="h-full animate-pulse rounded-lg bg-graphite-800/60" />;
  const tiles = [...config.tiles].sort((a, b) => Number(isKpiTile(b)) - Number(isKpiTile(a))).slice(0, 4);
  if (tiles.length === 0) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-graphite-700 text-[11px] text-graphite-500">
        {t("registry.noPreview")}
      </div>
    );
  }
  return (
    <div className="grid h-full grid-cols-2 grid-rows-2 gap-1.5">
      {tiles.map((tile, i) => (
        <DashboardTile key={tile.id || i} tile={tile} mini />
      ))}
    </div>
  );
}

export default function DashboardsListPage() {
  const t = useT();
  const dateLocale = useDateLocale();
  const router = useRouter();
  const databases = useSessionStore((s) => s.databases);
  const sessionDatabase = useSessionStore((s) => s.sessionDatabase);

  const [dashboards, setDashboards] = useState<DashboardListItem[]>([]);
  const [configs, setConfigs] = useState<Record<string, DashboardConfig>>({});
  const [searchQuery, setSearchQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<{ id: string; message: string } | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadList(controller.signal);
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Превью грузим пачками по 4: 20 дашбордов не открывают 20 запросов сразу. */
  async function loadPreviews(list: DashboardListItem[], signal?: AbortSignal) {
    const concurrency = 4;
    for (let start = 0; start < list.length; start += concurrency) {
      if (signal?.aborted) return;
      const chunk = list.slice(start, start + concurrency);
      const results = await Promise.allSettled(chunk.map((item) => fetchDashboard(item.id)));
      if (signal?.aborted || !mountedRef.current) return;
      const next: Record<string, DashboardConfig> = {};
      results.forEach((result, i) => {
        if (result.status === "fulfilled") next[chunk[i].id] = result.value;
      });
      if (Object.keys(next).length > 0) {
        setConfigs((prev) => ({ ...prev, ...next }));
      }
    }
  }

  async function loadList(signal?: AbortSignal) {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchDashboards(undefined, signal);
      if (signal?.aborted || !mountedRef.current) return;
      setDashboards(data);
      setConfigs({});
      void loadPreviews(data, signal);
    } catch (err: any) {
      if (signal?.aborted || !mountedRef.current) return;
      setError(err.message || t("registry.loadError"));
    } finally {
      if (!signal?.aborted && mountedRef.current) setLoading(false);
    }
  }

  async function handleDelete(id: string, e?: React.MouseEvent) {
    e?.stopPropagation();
    if (!confirm(t("registry.deleteConfirm"))) return;
    setDeleteError(null);
    try {
      await deleteDashboard(id);
    } catch (err) {
      // 404 — дашборд уже удалён: убираем его из списка, не пугаем пользователя
      if (!isNotFound(err)) {
        setDeleteError({
          id,
          message: (err as any)?.detail || (err as any)?.message || String(err),
        });
        return;
      }
    }
    setDashboards((prev) => prev.filter((d) => d.id !== id));
    setConfigs((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    window.dispatchEvent(new Event("dashboards:changed"));
  }

  function databaseOf(d: DashboardListItem) {
    const dbId = sessionDatabase[d.session_id || ""];
    return databases.find((x) => x.id === dbId);
  }

  function databaseTitle(d: DashboardListItem): string {
    return databaseOf(d)?.title ?? t("registry.unknownDb");
  }

  function formatDate(d: DashboardListItem): string {
    const date = toDate(d.updated_at ?? d.created_at);
    return date ? date.toLocaleString(dateLocale, { dateStyle: "medium", timeStyle: "short" }) : "—";
  }

  const visibleDashboards = useMemo(() => {
    return dashboards.filter((d) => {
      if (!d.session_id) return false;
      const dbId = sessionDatabase[d.session_id];
      if (dbId) {
        return databases.some((x) => x.id === dbId);
      }
      return databases.some((x) => x.sessions?.some((s) => s.id === d.session_id));
    });
  }, [dashboards, sessionDatabase, databases]);

  const filtered = useMemo(() => {
    const q = searchQuery.toLowerCase().trim();
    if (!q) return visibleDashboards;
    return visibleDashboards.filter((d) => {
      const date = toDate(d.updated_at ?? d.created_at);
      // Дата ищется в нескольких записях: 17.10.2026, 2026-10-17, «17 окт. 2026 г.»
      const dateTexts = date
        ? [
            date.toLocaleDateString("ru-RU"),
            date.toISOString().slice(0, 10),
            date.toLocaleDateString(dateLocale, { dateStyle: "medium" }),
          ]
        : [];
      return [d.title, d.id, d.session_id ?? "", databaseTitle(d), ...dateTexts].some((s) =>
        s.toLowerCase().includes(q),
      );
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleDashboards, searchQuery, databases, sessionDatabase, dateLocale]);

  return (
    <WorkspaceShell tab={{ kind: "dashboards" }}>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-6xl space-y-6 px-6 py-8 md:px-8">
          <div>
            <h1 className="text-2xl font-light tracking-tight text-graphite-50">{t("registry.title")}</h1>
            <p className="mt-1 text-[13px] text-graphite-400">{t("registry.subtitle")}</p>
          </div>

          {/* Поиск */}
          <div className="flex items-center gap-3">
            <label className="flex w-full items-center gap-2 rounded-lg border border-graphite-700 bg-graphite-850 px-3 py-2 transition focus-within:border-graphite-500 sm:w-96">
              <Icon name="search" className="shrink-0 text-graphite-500" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t("registry.search")}
                className="w-full bg-transparent text-[13px] text-graphite-100 placeholder-graphite-500 outline-none"
              />
            </label>
            {searchQuery && (
              <button onClick={() => setSearchQuery("")} className="text-[12px] text-graphite-400 hover:text-graphite-100">
                {t("registry.reset")}
              </button>
            )}
          </div>

          {deleteError && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-bad/30 bg-bad/10 px-4 py-2.5 text-[12px] text-bad">
              <span>{t("registry.deleteError", { msg: deleteError.message })}</span>
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => void handleDelete(deleteError.id)}
                  className="rounded-md border border-bad/40 px-2.5 py-1 text-[11px] hover:bg-bad/20 transition"
                >
                  {t("registry.retry")}
                </button>
                <button
                  onClick={() => setDeleteError(null)}
                  className="rounded-md px-2 py-1 text-[11px] text-graphite-300 hover:bg-graphite-800 transition"
                >
                  {t("sidebar.cancel")}
                </button>
              </div>
            </div>
          )}

          {loading ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-72 animate-pulse rounded-xl border border-graphite-800 bg-graphite-850" />
              ))}
            </div>
          ) : error ? (
            <div className="space-y-2 rounded-xl border border-bad/30 bg-bad/10 p-5 text-center text-[13px] text-bad">
              <p className="font-medium">{t("registry.loadError")}</p>
              <p className="text-graphite-400">{error}</p>
              <button onClick={() => void loadList()} className="rounded-lg bg-graphite-800 px-3 py-1.5 text-graphite-100 hover:bg-graphite-700">
                {t("registry.retry")}
              </button>
            </div>
          ) : visibleDashboards.length === 0 ? (
            <div className="flex h-56 flex-col items-center justify-center gap-3 rounded-xl border border-graphite-800 text-center">
              <Icon name="layout" size={22} className="text-graphite-500" />
              <div>
                <p className="text-[14px] text-graphite-200">{t("registry.emptyTitle")}</p>
                <p className="mt-1 max-w-sm text-[12px] text-graphite-500">{t("registry.emptyText")}</p>
              </div>
              <Link
                href="/"
                className="rounded-lg bg-graphite-100 px-3.5 py-1.5 text-[12px] font-medium text-graphite-950 hover:bg-graphite-50 transition"
              >
                {t("registry.emptyAction")}
              </Link>
            </div>
          ) : filtered.length === 0 ? (
            <p className="py-10 text-center text-[13px] text-graphite-400">{t("registry.notFound", { q: searchQuery })}</p>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {filtered.map((dash) => (
                <div
                  key={dash.id}
                  role="link"
                  tabIndex={0}
                  onClick={() => router.push(`/dashboard/${dash.id}`)}
                  onKeyDown={(e) => e.key === "Enter" && router.push(`/dashboard/${dash.id}`)}
                  className="group flex cursor-pointer flex-col gap-3 rounded-xl border border-graphite-800 bg-graphite-850 p-4 transition hover:border-graphite-600"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <span className="font-mono text-[11px] text-graphite-500">ID: {dash.id}</span>
                      <h2 className="mt-1 flex items-center gap-1.5 text-[15px] font-medium text-graphite-50">
                        <Icon name="folder" className="shrink-0 text-graphite-400" />
                        <span className="truncate">{databaseTitle(dash)}</span>
                      </h2>
                      {databaseOf(dash) && (
                        <p className="mt-1 line-clamp-2 text-[12px] leading-relaxed text-graphite-400">
                          {describeDatabase(databaseOf(dash), t)}
                        </p>
                      )}
                    </div>
                    <button
                      onClick={(e) => handleDelete(dash.id, e)}
                      className="rounded-md p-1 text-graphite-500 opacity-0 transition hover:bg-graphite-800 hover:text-bad group-hover:opacity-100 focus:opacity-100"
                      title={t("registry.delete")}
                    >
                      <Icon name="x" size={14} />
                    </button>
                  </div>

                  <div className="h-44">
                    <DashboardPreview config={configs[dash.id]} />
                  </div>

                  <div className="flex items-center justify-between gap-2 text-[11px] text-graphite-500">
                    <span className="min-w-0 truncate text-graphite-300">{dash.title}</span>
                    <span className="shrink-0">{t("registry.tilesCount", { n: dash.tiles_count ?? 0 })}</span>
                    <span className="shrink-0">{formatDate(dash)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </WorkspaceShell>
  );
}
