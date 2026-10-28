"use client";

import { useState } from "react";
import { attachDb } from "@/api/client";
import { useSessionStore } from "@/store/sessionStore";
import { useUiStore } from "@/store/uiStore";
import { useStartSession } from "@/hooks/useStartSession";
import { useT } from "@/i18n/useT";
import Icon from "@/components/ui/Icon";
import type { DbKind } from "@/types/datasource";

const DSN_PLACEHOLDER: Record<DbKind, string> = {
  postgres: "postgresql://analyst:pass@host:5432/db",
  clickhouse: "clickhouse://analyst:pass@host:8123/db",
};

const DEMO_DSN = "postgresql://analyst:demo_pass@localhost:5432/demo_analytics";

export default function ConnectDbModal() {
  const t = useT();
  const dbModalOpen = useUiStore((s) => s.dbModalOpen);
  const setDbModalOpen = useUiStore((s) => s.setDbModalOpen);
  const addDatabase = useSessionStore((s) => s.addDatabase);
  const startSession = useStartSession();

  const [dbKind, setDbKind] = useState<DbKind>("postgres");
  const [dsn, setDsn] = useState("");
  const [schema, setSchema] = useState("");
  const [dbDescription, setDbDescription] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [dbErrorMsg, setDbErrorMsg] = useState<string | null>(null);

  if (!dbModalOpen) return null;

  function handleFillDemo() {
    setDbKind("postgres");
    setDsn(DEMO_DSN);
    setSchema("public");
    setDbDescription("Демонстрационная аналитическая база данных (PostgreSQL / DuckDB)");
    setDbErrorMsg(null);
  }

  async function handleDbConnect(e?: React.FormEvent) {
    e?.preventDefault();
    const rawDsn = dsn.trim();
    if (!rawDsn) {
      setDbErrorMsg("Пожалуйста, укажите строку подключения (DSN). Либо используйте кнопку «Заполнить демо-данные».");
      return;
    }

    // Проверка корректности схемы DSN
    if (dbKind === "postgres") {
      if (!rawDsn.startsWith("postgresql://") && !rawDsn.startsWith("postgres://")) {
        setDbErrorMsg("Некорректный формат DSN. Строка подключения PostgreSQL должна начинаться с postgresql:// или postgres://");
        return;
      }
    } else if (dbKind === "clickhouse") {
      if (!rawDsn.startsWith("clickhouse://")) {
        setDbErrorMsg("Некорректный формат DSN. Строка подключения ClickHouse должна начинаться с clickhouse://");
        return;
      }
    }

    // Проверка на явный SQLi мусор
    if (/;\s*drop\s+table/i.test(rawDsn) || /--/.test(rawDsn)) {
      setDbErrorMsg("Строка подключения содержит недопустимые символы или SQL-команды.");
      return;
    }

    setIsBusy(true);
    setDbErrorMsg(null);
    try {
      const ds = await attachDb({
        kind: dbKind,
        dsn: rawDsn,
        schema: dbKind === "postgres" ? schema.trim() || undefined : undefined,
        description: dbDescription.trim() || undefined,
      });
      const host = rawDsn.split("@")[1]?.split("/")[0];
      const dbName = rawDsn.split("/").pop()?.split("?")[0];
      const cleanDbName = (dbName || host || dbKind).replace(/[^a-zA-Z0-9_-]/g, "");
      const folder = addDatabase(
        ds,
        `${cleanDbName || "database"} (${dbKind === "postgres" ? "PostgreSQL" : "ClickHouse"})`
      );
      setDbModalOpen(false);
      setDsn("");
      setSchema("");
      setDbDescription("");
      await startSession(folder);
    } catch (err: any) {
      setDbErrorMsg(err.message || t("sidebar.dbError"));
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-md max-h-[90vh] overflow-y-auto rounded-xl border border-graphite-700 bg-graphite-900 p-5 shadow-2xl text-graphite-100 space-y-4">
        <div className="flex items-center justify-between border-b border-graphite-800 pb-3">
          <div className="flex items-center gap-2">
            <Icon name="database" className="text-graphite-400" />
            <h3 className="font-semibold text-sm">{t("modal.title")}</h3>
          </div>
          <button
            type="button"
            onClick={() => setDbModalOpen(false)}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-graphite-400 hover:bg-graphite-800 hover:text-graphite-50 transition"
            title={t("common.close")}
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        {/* Быстрое подключение демо */}
        <div className="rounded-lg border border-graphite-700/80 bg-graphite-800/50 p-3 flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-graphite-300 font-medium">Нет своей базы для теста?</span>
            <button
              type="button"
              onClick={handleFillDemo}
              className="text-xs font-medium text-graphite-100 hover:text-white bg-graphite-700 hover:bg-graphite-650 px-2.5 py-1 rounded transition"
            >
              Заполнить демо-данные
            </button>
          </div>
          <p className="text-[11px] text-graphite-400 leading-tight">
            Будут подставлены тестовые параметры подключения с примерами таблиц заказов и выручки.
          </p>
        </div>

        <form onSubmit={handleDbConnect} className="space-y-3">
          <div>
            <label className="block text-xs text-graphite-400 mb-2">{t("modal.dbms")}</label>
            <div className="flex items-center gap-4">
              <label className="flex items-center gap-2 cursor-pointer text-xs text-graphite-100">
                <input
                  type="radio"
                  name="dbKind"
                  value="postgres"
                  checked={dbKind === "postgres"}
                  onChange={() => setDbKind("postgres")}
                  className="accent-graphite-100"
                />
                <span>PostgreSQL</span>
              </label>
              <label
                className="flex items-center gap-2 cursor-not-allowed text-xs text-graphite-500"
                title={t("modal.comingSoon")}
              >
                <input
                  type="radio"
                  name="dbKind"
                  value="clickhouse"
                  disabled
                  checked={false}
                  className="accent-graphite-100 cursor-not-allowed opacity-50"
                />
                <span>ClickHouse</span>
                <span className="rounded bg-graphite-800 px-1.5 py-0.5 text-[10px] text-graphite-400 border border-graphite-700">
                  {t("modal.comingSoon")}
                </span>
              </label>
            </div>
          </div>

          <div>
            <label className="block text-xs text-graphite-400 mb-1">{t("modal.dsn")}</label>
            <input
              type="text"
              value={dsn}
              onChange={(e) => {
                setDsn(e.target.value);
                if (dbErrorMsg) setDbErrorMsg(null);
              }}
              placeholder={DSN_PLACEHOLDER[dbKind]}
              className="w-full rounded-lg border border-graphite-700 bg-graphite-950 px-3 py-2 text-xs text-graphite-50 placeholder-graphite-500 outline-none focus:border-graphite-400 transition"
              required
            />
          </div>

          {dbKind === "postgres" && (
            <div>
              <label className="block text-xs text-graphite-400 mb-1">{t("modal.schema")}</label>
              <input
                type="text"
                value={schema}
                onChange={(e) => setSchema(e.target.value)}
                placeholder="public"
                className="w-full rounded-lg border border-graphite-700 bg-graphite-950 px-3 py-2 text-xs text-graphite-50 placeholder-graphite-500 outline-none focus:border-graphite-400 transition"
              />
            </div>
          )}

          <div>
            <label className="block text-xs text-graphite-400 mb-1">{t("modal.description")}</label>
            <textarea
              value={dbDescription}
              onChange={(e) => setDbDescription(e.target.value)}
              rows={2}
              placeholder={t("modal.descriptionHint")}
              className="w-full resize-y rounded-lg border border-graphite-700 bg-graphite-950 px-3 py-2 text-xs text-graphite-50 placeholder-graphite-500 outline-none focus:border-graphite-400 transition"
            />
          </div>

          {dbErrorMsg && (
            <div className="rounded-lg border border-bad/40 bg-bad/10 p-2.5 text-xs text-bad leading-relaxed flex items-start gap-2">
              <Icon name="alertTriangle" size={14} className="shrink-0 mt-0.5" />
              <span>{dbErrorMsg}</span>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2 border-t border-graphite-800">
            <button
              type="button"
              onClick={() => setDbModalOpen(false)}
              className="min-h-[36px] rounded-lg border border-graphite-700 px-3 py-1.5 text-xs text-graphite-300 hover:bg-graphite-800 transition"
            >
              {t("modal.cancel")}
            </button>
            <button
              type="submit"
              disabled={isBusy}
              className="min-h-[36px] rounded-lg bg-graphite-100 px-4 py-1.5 text-xs font-medium text-graphite-950 hover:bg-graphite-50 disabled:opacity-50 transition"
            >
              {isBusy ? "Подключение..." : t("modal.connect")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
