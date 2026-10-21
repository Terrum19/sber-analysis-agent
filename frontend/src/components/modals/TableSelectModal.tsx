"use client";

import { useEffect, useState } from "react";
import Icon from "@/components/ui/Icon";
import { useT } from "@/i18n/useT";

interface TableSelectModalProps {
  isOpen: boolean;
  onClose: () => void;
  databaseTitle: string;
  tables: string[];
  onSelect: (table: string) => void;
}

export default function TableSelectModal({
  isOpen,
  onClose,
  databaseTitle,
  tables,
  onSelect,
}: TableSelectModalProps) {
  const t = useT();
  const [selectedTable, setSelectedTable] = useState<string>("");

  useEffect(() => {
    if (isOpen) {
      setSelectedTable(tables[0] || "");
    }
  }, [isOpen, tables]);

  if (!isOpen) return null;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!selectedTable) return;
    onSelect(selectedTable);
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-md rounded-xl border border-graphite-700 bg-graphite-900 p-5 shadow-2xl text-graphite-100 space-y-4 animate-in fade-in zoom-in-95 duration-150">
        <div className="flex items-center justify-between border-b border-graphite-800 pb-3">
          <div>
            <h3 className="font-semibold text-sm">{t("modal.selectTableTitle")}</h3>
            <p className="text-xs text-graphite-400 mt-0.5 truncate max-w-[340px]">{databaseTitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-graphite-400 hover:text-graphite-50 transition"
            title={t("common.close")}
          >
            <Icon name="x" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="max-h-60 overflow-y-auto space-y-1.5 pr-1">
            {tables.map((table) => {
              const isSelected = selectedTable === table;
              return (
                <label
                  key={table}
                  className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 cursor-pointer transition text-xs ${
                    isSelected
                      ? "border-graphite-400 bg-graphite-800/90 text-graphite-50 shadow-sm"
                      : "border-graphite-800 bg-graphite-950/50 text-graphite-300 hover:bg-graphite-850 hover:text-graphite-100"
                  }`}
                >
                  <input
                    type="radio"
                    name="table_choice"
                    value={table}
                    checked={isSelected}
                    onChange={() => setSelectedTable(table)}
                    className="accent-graphite-100 cursor-pointer"
                  />
                  <span className="font-mono flex-1 truncate">{table}</span>
                </label>
              );
            })}
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t border-graphite-800">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-graphite-700 px-3.5 py-1.5 text-xs text-graphite-300 hover:bg-graphite-800 transition"
            >
              {t("modal.cancel")}
            </button>
            <button
              type="submit"
              disabled={!selectedTable}
              className="rounded-lg bg-graphite-100 px-4 py-1.5 text-xs font-medium text-graphite-950 hover:bg-graphite-50 disabled:opacity-50 disabled:cursor-not-allowed transition"
            >
              {t("modal.selectTableBtn")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
