"use client";

import { useEffect, useRef, useState } from "react";
import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";
import { getGlossary, putGlossary } from "@/api/client";
import { useSessionStore } from "@/store/sessionStore";
import type { Glossary, TableBrief } from "@/types/datasource";

interface GlossaryRow {
  id: string;
  column: string;
  terms: string;
}

interface GlossaryModalProps {
  isOpen: boolean;
  onClose: () => void;
  table: string;
  onSaved?: (brief: TableBrief) => void;
}

export default function GlossaryModal({
  isOpen,
  onClose,
  table,
  onSaved,
}: GlossaryModalProps) {
  const t = useT();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [rows, setRows] = useState<GlossaryRow[]>([]);
  const [note, setNote] = useState<string>("");
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [infoMessage, setInfoMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen) return;

    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setInfoMessage(null);

    getGlossary(table)
      .then((data) => {
        if (cancelled) return;
        setNote(data.note || "");
        const termsEntries = Object.entries(data.terms || {});
        if (termsEntries.length > 0) {
          setRows(
            termsEntries.map(([col, termList]) => ({
              id: crypto.randomUUID(),
              column: col,
              terms: Array.isArray(termList) ? termList.join(", ") : String(termList),
            })),
          );
        } else {
          setRows([{ id: crypto.randomUUID(), column: "", terms: "" }]);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        // Если глоссария ещё нет (например, 404), открываем форму с пустой строкой
        setRows([{ id: crypto.randomUUID(), column: "", terms: "" }]);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isOpen, table]);

  if (!isOpen) return null;

  function handleAddRow() {
    setRows((prev) => [...prev, { id: crypto.randomUUID(), column: "", terms: "" }]);
  }

  function handleRemoveRow(id: string) {
    setRows((prev) => (prev.length > 1 ? prev.filter((r) => r.id !== id) : [{ id: crypto.randomUUID(), column: "", terms: "" }]));
  }

  function handleUpdateRow(id: string, field: "column" | "terms", value: string) {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, [field]: value } : r)));
  }

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;

    setError(null);
    setInfoMessage(null);

    try {
      const text = await file.text();
      const json = JSON.parse(text);

      let termsMap: Record<string, any> | null = null;
      let fileNote = "";

      if (json && typeof json === "object") {
        if ("terms" in json && typeof json.terms === "object" && json.terms !== null) {
          termsMap = json.terms;
          fileNote = typeof json.note === "string" ? json.note : "";
        } else {
          // Если передан словарь верхнего уровня { "col": ["term1"] }
          termsMap = json;
        }
      }

      if (!termsMap || Object.keys(termsMap).length === 0) {
        throw new Error(t("glossary.uploadJsonError"));
      }

      const newRows: GlossaryRow[] = Object.entries(termsMap).map(([col, termsVal]) => ({
        id: crypto.randomUUID(),
        column: col.trim(),
        terms: Array.isArray(termsVal) ? termsVal.join(", ") : String(termsVal || ""),
      }));

      setRows(newRows);
      if (fileNote) setNote(fileNote);
      setInfoMessage(t("glossary.uploadJsonSuccess", { count: newRows.length }));
    } catch (err: any) {
      setError(err?.message || t("glossary.uploadJsonError"));
    }
  }

  function handleExportJson() {
    const terms: Record<string, string[]> = {};
    for (const r of rows) {
      const col = r.column.trim();
      if (!col) continue;
      const termItems = r.terms
        .split(/[,;\n]/)
        .map((x) => x.trim())
        .filter(Boolean);
      if (termItems.length > 0) {
        terms[col] = termItems;
      }
    }
    const exportData: Glossary = {
      ...(note.trim() ? { note: note.trim() } : {}),
      terms,
    };
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `glossary-${table}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (isSaving) return;

    setError(null);
    setInfoMessage(null);
    setIsSaving(true);

    try {
      const terms: Record<string, string[]> = {};
      for (const r of rows) {
        const col = r.column.trim();
        if (!col) continue;
        const termItems = r.terms
          .split(/[,;\n]/)
          .map((x) => x.trim())
          .filter(Boolean);
        if (termItems.length > 0) {
          terms[col] = termItems;
        }
      }

      const payload: Glossary = {
        terms,
        ...(note.trim() ? { note: note.trim() } : {}),
      };

      const updatedBrief = await putGlossary(table, payload);
      useSessionStore.getState().updateTableBrief(table, updatedBrief);
      onSaved?.(updatedBrief);
      onClose();
    } catch (err: any) {
      const message = err?.detail || err?.message || String(err);
      setError(t("glossary.saveError", { error: message }));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-2xl rounded-xl border border-graphite-700 bg-graphite-900 p-5 shadow-2xl text-graphite-100 space-y-4 animate-in fade-in zoom-in-95 duration-150 max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-start justify-between border-b border-graphite-800 pb-3">
          <div>
            <div className="flex items-center gap-2">
              <Icon name="book" size={16} className="text-graphite-400" />
              <h3 className="font-semibold text-sm">{t("glossary.modalTitle", { table })}</h3>
            </div>
            <p className="text-xs text-graphite-400 mt-1">{t("glossary.modalSubtitle")}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-graphite-400 hover:text-graphite-50 transition p-1"
            title={t("common.close")}
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        {/* Action bar: Upload JSON, Export JSON, Add row */}
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              onChange={handleFileUpload}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-1.5 rounded-lg border border-graphite-700 bg-graphite-800 px-2.5 py-1.5 text-graphite-200 hover:bg-graphite-700 hover:text-graphite-50 transition"
              title={t("glossary.uploadJson")}
            >
              <Icon name="upload" size={13} />
              <span>{t("glossary.uploadJson")}</span>
            </button>
            <button
              type="button"
              onClick={handleExportJson}
              className="flex items-center gap-1.5 rounded-lg border border-graphite-700 bg-graphite-800 px-2.5 py-1.5 text-graphite-200 hover:bg-graphite-700 hover:text-graphite-50 transition"
              title="Экспорт в JSON"
            >
              <Icon name="download" size={13} />
              <span>Экспорт JSON</span>
            </button>
          </div>
          <button
            type="button"
            onClick={handleAddRow}
            className="flex items-center gap-1.5 rounded-lg border border-graphite-700 bg-graphite-800 px-2.5 py-1.5 text-graphite-200 hover:bg-graphite-700 hover:text-graphite-50 transition"
          >
            <Icon name="plus" size={13} />
            <span>{t("glossary.addColumn")}</span>
          </button>
        </div>

        {infoMessage && (
          <div className="rounded-lg border border-green-500/30 bg-green-500/10 p-2.5 text-xs text-green-300 flex items-center justify-between">
            <span>{infoMessage}</span>
            <button type="button" onClick={() => setInfoMessage(null)} className="text-green-400 hover:text-green-200 ml-2">
              <Icon name="x" size={12} />
            </button>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-bad/30 bg-bad/10 p-2.5 text-xs text-bad flex items-center justify-between">
            <span>{error}</span>
            <button type="button" onClick={() => setError(null)} className="text-bad hover:text-red-300 ml-2">
              <Icon name="x" size={12} />
            </button>
          </div>
        )}

        {/* Form content */}
        <form onSubmit={handleSave} className="flex-1 overflow-y-auto space-y-3 pr-1 min-h-0">
          {isLoading ? (
            <div className="py-12 text-center text-xs text-graphite-400">
              <Icon name="refresh" size={18} className="animate-spin mx-auto mb-2 text-graphite-500" />
              <span>Загрузка глоссария...</span>
            </div>
          ) : (
            <div className="space-y-2">
              {rows.map((row) => (
                <div key={row.id} className="flex items-center gap-2">
                  <input
                    type="text"
                    value={row.column}
                    onChange={(e) => handleUpdateRow(row.id, "column", e.target.value)}
                    placeholder={t("glossary.columnName")}
                    className="w-1/3 rounded-lg border border-graphite-700 bg-graphite-950/80 px-2.5 py-1.5 font-mono text-xs text-graphite-100 placeholder:text-graphite-500 focus:border-graphite-500 focus:outline-none"
                  />
                  <span className="text-graphite-500 select-none">→</span>
                  <input
                    type="text"
                    value={row.terms}
                    onChange={(e) => handleUpdateRow(row.id, "terms", e.target.value)}
                    placeholder={t("glossary.columnTerms")}
                    className="flex-1 rounded-lg border border-graphite-700 bg-graphite-950/80 px-2.5 py-1.5 text-xs text-graphite-100 placeholder:text-graphite-500 focus:border-graphite-500 focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={() => handleRemoveRow(row.id)}
                    className="text-graphite-500 hover:text-bad transition p-1.5 rounded-md hover:bg-graphite-800"
                    title={t("common.delete")}
                  >
                    <Icon name="trash" size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Optional Note */}
          <div className="pt-2">
            <label className="block text-[11px] text-graphite-400 mb-1">
              Примечание к глоссарию (опционально):
            </label>
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Например: Термины для таблицы продаж"
              className="w-full rounded-lg border border-graphite-700 bg-graphite-950/80 px-2.5 py-1.5 text-xs text-graphite-200 placeholder:text-graphite-500 focus:border-graphite-500 focus:outline-none"
            />
          </div>

          {/* Footer actions */}
          <div className="flex justify-end gap-2 pt-3 border-t border-graphite-800">
            <button
              type="button"
              onClick={onClose}
              disabled={isSaving}
              className="rounded-lg border border-graphite-700 bg-graphite-800 px-3 py-1.5 text-xs text-graphite-200 hover:bg-graphite-700 hover:text-graphite-50 transition"
            >
              {t("sidebar.cancel")}
            </button>
            <button
              type="submit"
              disabled={isSaving || isLoading}
              className="rounded-lg bg-graphite-100 px-3.5 py-1.5 text-xs font-medium text-graphite-950 hover:bg-white transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5"
            >
              {isSaving && <Icon name="refresh" size={13} className="animate-spin" />}
              <span>{isSaving ? t("glossary.saving") : t("glossary.save")}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
