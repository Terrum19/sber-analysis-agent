"use client";

/**
 * Подключение источника данных к диалогу: загрузка сырых CSV
 * (POST /api/datasources/csv) или подключение PostgreSQL / ClickHouse
 * по DSN (POST /api/datasources/db). Данные грузятся без предобработки.
 */
import { useRef, useState } from "react";
import { attachCsv, attachDb } from "@/api/client";
import { useSessionStore } from "@/store/sessionStore";
import type { Datasource, DbKind } from "@/types/datasource";

const DSN_PLACEHOLDER: Record<DbKind, string> = {
  postgres: "postgresql://analyst:***@db.local:5432/shop",
  clickhouse: "clickhouse://analyst:***@db.local:8123/shop",
};

export default function DatasourcePanel() {
  const datasource = useSessionStore((s) => s.datasource);
  const setDatasource = useSessionStore((s) => s.setDatasource);
  const addMessage = useSessionStore((s) => s.addMessage);
  const fileInput = useRef<HTMLInputElement>(null);
  const [dbFormOpen, setDbFormOpen] = useState(false);
  const [kind, setKind] = useState<DbKind>("postgres");
  const [dsn, setDsn] = useState("");
  const [schema, setSchema] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function attach(request: () => Promise<Datasource>) {
    setBusy(true);
    setError(null);
    try {
      const attached = await request();
      setDatasource(attached);
      setDbFormOpen(false);
      addMessage({ role: "agent", content: `Источник подключён: ${attached.tables.join(", ") || "таблиц нет"}` });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  function onFiles(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length) attach(() => attachCsv(files));
  }

  function onDbSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (dsn.trim()) attach(() => attachDb({ kind, dsn: dsn.trim(), schema: kind === "postgres" ? schema.trim() : undefined }));
  }

  return (
    <div className="mb-4 space-y-2 border-b border-slate-200 pb-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <input ref={fileInput} type="file" accept=".csv,text/csv" multiple hidden onChange={onFiles} />
        <button
          onClick={() => fileInput.current?.click()}
          disabled={busy}
          className="rounded-lg border border-slate-300 px-3 py-1.5 hover:bg-slate-50 disabled:opacity-50"
        >
          Загрузить CSV
        </button>
        <button
          onClick={() => setDbFormOpen((open) => !open)}
          disabled={busy}
          className="rounded-lg border border-slate-300 px-3 py-1.5 hover:bg-slate-50 disabled:opacity-50"
        >
          Подключить БД
        </button>
        {busy && <span className="text-slate-500">Подключение...</span>}
        {!busy && datasource && (
          <span className="text-slate-600">
            {datasource.kind}: {datasource.tables.join(", ") || "таблиц нет"}
          </span>
        )}
      </div>
      {dbFormOpen && (
        <form onSubmit={onDbSubmit} className="flex flex-wrap gap-2">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as DbKind)}
            className="rounded-lg border border-slate-300 px-2 py-1.5"
          >
            <option value="postgres">PostgreSQL</option>
            <option value="clickhouse">ClickHouse</option>
          </select>
          <input
            value={dsn}
            onChange={(e) => setDsn(e.target.value)}
            placeholder={DSN_PLACEHOLDER[kind]}
            className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-1.5"
          />
          {kind === "postgres" && (
            <input
              value={schema}
              onChange={(e) => setSchema(e.target.value)}
              placeholder="схема (необязательно)"
              className="w-40 rounded-lg border border-slate-300 px-3 py-1.5"
            />
          )}
          <button type="submit" disabled={busy || !dsn.trim()} className="rounded-lg bg-blue-600 px-3 py-1.5 text-white disabled:opacity-50">
            Подключить
          </button>
        </form>
      )}
      {error && <p className="text-red-600">Не удалось подключить источник: {error}</p>}
    </div>
  );
}
