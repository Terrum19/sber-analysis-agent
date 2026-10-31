"use client";

/**
 * «＋ в дашборды» (F-15): один график — в несколько дашбордов галочками.
 * Отмечены дашборды, где график уже есть (по ключу происхождения, origin_keys
 * из списка). Отметили — копия внизу сетки (или возвращается скрытый оригинал),
 * сняли — копия удаляется, оригинал скрывается. Новые дашборды создаются по «Готово».
 * Сбой одного дашборда не откатывает остальные.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { createDashboard, fetchDashboard, fetchDashboards, updateDashboard } from "@/api/client";
import Icon from "@/components/ui/Icon";
import { useDateLocale, useT } from "@/i18n/useT";
import { copyTile, originKey, withChart, withoutChart } from "@/lib/tileOrigin";
import type { DashboardConfig, DashboardListItem, DashboardTile } from "@/types/dashboard";

interface Props {
  tile: DashboardTile;
  /** Сессия, где открыт график: ей принадлежат новые дашборды. */
  sessionId: string;
  /** Дашборд, где открыт график: его плитки берутся из локального состояния. */
  current?: { id: string; tiles: DashboardTile[] } | null;
  /** Текущий дашборд сохранён — состояние страницы надо заменить ответом сервера. */
  onCurrentSaved?: (dashboard: DashboardConfig) => void;
  /** Итог для тоста. */
  onDone: (message: string) => void;
  onClose: () => void;
}

export default function AddToDashboardsDialog({
  tile,
  sessionId,
  current,
  onCurrentSaved,
  onDone,
  onClose,
}: Props) {
  const t = useT();
  const dateLocale = useDateLocale();
  const key = originKey(tile);
  const [items, setItems] = useState<DashboardListItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [initial, setInitial] = useState<Record<string, boolean>>({});
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [created, setCreated] = useState<string[]>([]);
  const [newTitle, setNewTitle] = useState("");
  const [query, setQuery] = useState("");
  const [applying, setApplying] = useState(false);
  const opened = useRef(current);

  useEffect(() => {
    const here = opened.current;
    let alive = true;
    fetchDashboards()
      .then((list) => {
        if (!alive) return;
        const marks = Object.fromEntries(
          list.map((item) => [item.id, Boolean(key && item.origin_keys?.includes(key))]),
        );
        if (here) marks[here.id] = here.tiles.some((item) => !item.hidden && originKey(item) === key);
        setItems(list);
        setInitial(marks);
        setChecked(marks);
      })
      .catch((err) => alive && setLoadError(err?.detail || err?.message || t("addTo.loadError")));
    return () => {
      alive = false;
    };
  }, [key, t]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !applying) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [applying, onClose]);

  const shown = useMemo(() => {
    const list = items ?? [];
    const q = query.trim().toLowerCase();
    const filtered = q ? list.filter((item) => item.title.toLowerCase().includes(q)) : list;
    return [...filtered].sort((a, b) => Number(b.id === current?.id) - Number(a.id === current?.id));
  }, [items, query, current?.id]);

  function addNew() {
    const title = newTitle.trim();
    if (!title) return;
    setCreated((prev) => [...prev, title]);
    setNewTitle("");
  }

  function formatDate(ts?: number | string) {
    if (!ts) return "";
    const d = typeof ts === "number" && ts < 1e11 ? new Date(ts * 1000) : new Date(ts);
    return isNaN(d.getTime()) ? "" : d.toLocaleDateString(dateLocale, { day: "numeric", month: "short" });
  }

  async function apply() {
    const changed = (items ?? []).filter((item) => checked[item.id] !== initial[item.id]);
    if (changed.length === 0 && created.length === 0) {
      onDone(t("addTo.nothing"));
      onClose();
      return;
    }
    setApplying(true);
    const results = await Promise.allSettled([
      ...changed.map(async (item) => {
        const own = current && item.id === current.id;
        const base = own ? current.tiles : (await fetchDashboard(item.id)).tiles;
        const add = checked[item.id];
        const tiles = add ? withChart(base, tile, sessionId) : withoutChart(base, tile);
        const saved = await updateDashboard(item.id, { tiles });
        if (own) onCurrentSaved?.(saved);
        return add ? "added" : "removed";
      }),
      ...created.map(async (title) => {
        await createDashboard({ session_id: sessionId, title, tiles: [copyTile(tile, [], sessionId)] });
        return "added";
      }),
    ]);
    const titles = [...changed.map((item) => item.title), ...created];
    const added = results.filter((r) => r.status === "fulfilled" && r.value === "added").length;
    const removed = results.filter((r) => r.status === "fulfilled" && r.value === "removed").length;
    const failed = titles.filter((_, i) => results[i].status === "rejected");
    const parts = [
      added > 0 ? t("addTo.added", { n: added }) : null,
      removed > 0 ? t("addTo.removed", { n: removed }) : null,
      failed.length > 0 ? t("addTo.failed", { names: failed.map((name) => `«${name}»`).join(", ") }) : null,
    ].filter(Boolean);
    setApplying(false);
    onDone(parts.join(" · "));
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget && !applying) onClose();
      }}
    >
      <div className="w-full max-w-md space-y-4 rounded-xl border border-graphite-700 bg-graphite-900 p-5 text-graphite-100 shadow-2xl">
        <div className="flex items-center justify-between gap-3 border-b border-graphite-800 pb-3">
          <h3 className="min-w-0 truncate text-sm font-semibold" title={tile.title}>
            {t("addTo.title", { name: tile.title })}
          </h3>
          <button
            type="button"
            onClick={onClose}
            disabled={applying}
            className="shrink-0 text-graphite-400 transition hover:text-graphite-50"
            title={t("common.close")}
          >
            <Icon name="x" />
          </button>
        </div>

        {items !== null && items.length > 0 && (
          <div className="flex items-center gap-2 rounded-lg border border-graphite-700 bg-graphite-950 px-2.5 py-1.5">
            <Icon name="search" size={13} className="text-graphite-500" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("addTo.search")}
              autoFocus
              className="w-full bg-transparent text-xs text-graphite-100 outline-none placeholder:text-graphite-500"
            />
          </div>
        )}

        <div className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
          {loadError ? (
            <p className="rounded-lg border border-bad/30 bg-bad/10 p-2.5 text-xs text-bad">{loadError}</p>
          ) : items === null ? (
            <p className="py-4 text-center text-xs text-graphite-500">{t("addTo.loading")}</p>
          ) : (
            <>
              {items.length === 0 && created.length === 0 && (
                <p className="py-4 text-center text-xs text-graphite-500">{t("addTo.empty")}</p>
              )}
              {items.length > 0 && shown.length === 0 && (
                <p className="py-4 text-center text-xs text-graphite-500">{t("addTo.notFound")}</p>
              )}
              {shown.map((item) => (
                <label
                  key={item.id}
                  className="flex cursor-pointer items-center gap-3 rounded-lg border border-graphite-800 bg-graphite-950/50 px-3 py-2 text-xs text-graphite-200 transition hover:bg-graphite-850"
                >
                  <input
                    type="checkbox"
                    checked={Boolean(checked[item.id])}
                    onChange={(e) => setChecked((prev) => ({ ...prev, [item.id]: e.target.checked }))}
                    className="cursor-pointer accent-graphite-100"
                  />
                  <span className="min-w-0 flex-1 truncate">{item.title}</span>
                  {item.id === current?.id && (
                    <span className="shrink-0 rounded border border-graphite-600 px-1.5 text-[10px] text-graphite-400">
                      {t("addTo.current")}
                    </span>
                  )}
                  <span className="shrink-0 text-[11px] text-graphite-500">
                    {t("addTo.tiles", { n: item.tiles_count ?? 0 })}
                    {formatDate(item.updated_at) && ` · ${formatDate(item.updated_at)}`}
                  </span>
                </label>
              ))}
              {created.map((title, i) => (
                <label
                  key={`new-${i}`}
                  className="flex cursor-pointer items-center gap-3 rounded-lg border border-graphite-700 bg-graphite-950/50 px-3 py-2 text-xs text-graphite-200"
                >
                  <input
                    type="checkbox"
                    checked
                    onChange={() => setCreated((prev) => prev.filter((_, j) => j !== i))}
                    className="cursor-pointer accent-graphite-100"
                  />
                  <span className="min-w-0 flex-1 truncate">{title}</span>
                  <span className="shrink-0 rounded border border-graphite-600 px-1.5 text-[10px] text-graphite-400">
                    {t("addTo.new")}
                  </span>
                </label>
              ))}
            </>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Icon name="plus" size={13} className="shrink-0 text-graphite-400" />
          <input
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") addNew();
            }}
            placeholder={t("addTo.newDashboard")}
            className="min-w-0 flex-1 rounded-lg border border-graphite-700 bg-graphite-950 px-2.5 py-1.5 text-xs text-graphite-100 outline-none placeholder:text-graphite-500 focus:border-graphite-500"
          />
          <button
            type="button"
            onClick={addNew}
            disabled={!newTitle.trim()}
            className="shrink-0 rounded-lg border border-graphite-700 px-2.5 py-1.5 text-xs text-graphite-200 transition hover:bg-graphite-800 disabled:opacity-50"
          >
            {t("addTo.create")}
          </button>
        </div>

        <div className="flex justify-end gap-2 border-t border-graphite-800 pt-3">
          <button
            type="button"
            onClick={onClose}
            disabled={applying}
            className="rounded-lg border border-graphite-700 px-3.5 py-1.5 text-xs text-graphite-300 transition hover:bg-graphite-800 disabled:opacity-50"
          >
            {t("addTo.cancel")}
          </button>
          <button
            type="button"
            onClick={() => void apply()}
            disabled={applying || items === null}
            className="rounded-lg bg-graphite-100 px-4 py-1.5 text-xs font-medium text-graphite-950 transition hover:bg-graphite-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {applying ? "…" : t("addTo.done")}
          </button>
        </div>
      </div>
    </div>
  );
}
