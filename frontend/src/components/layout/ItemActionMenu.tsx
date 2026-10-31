"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useT } from "@/i18n/useT";
import Icon from "@/components/ui/Icon";

export type ActionMenuTarget = "session" | "csv" | "db" | "tile";

interface ItemActionMenuProps {
  target: ActionMenuTarget;
  title: string;
  isPinned?: boolean;
  anchorRect: DOMRect;
  onClose: () => void;
  onTogglePin?: () => void;
  onRename: (newTitle: string) => void;
  onDelete: () => void;
  /** Плитка: текущий тип графика и доступные варианты. */
  chartType?: string;
  chartTypes?: readonly string[];
  onChartTypeChange?: (type: string) => void;
  /** Плитка: открыть на весь экран во вкладке. */
  onOpen?: () => void;
  /** Плитка: отправить вопрос агенту по всей плитке (F-09). */
  onElaborate?: () => void;
}

export default function ItemActionMenu({
  target,
  title,
  isPinned = false,
  anchorRect,
  onClose,
  onTogglePin,
  onRename,
  onDelete,
  chartType,
  chartTypes,
  onChartTypeChange,
  onOpen,
  onElaborate,
}: ItemActionMenuProps) {
  const t = useT();
  const menuRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const [view, setView] = useState<"menu" | "rename" | "confirm_delete" | "chart_type">("menu");
  const [nameInput, setNameInput] = useState(title);
  const [pos, setPos] = useState({ top: anchorRect.bottom + 4, left: anchorRect.left });

  // Корректировка положения меню с учетом размеров окна и самого меню
  useLayoutEffect(() => {
    if (!menuRef.current) return;
    const { offsetWidth, offsetHeight } = menuRef.current;
    const padding = 8;

    let left = anchorRect.left;
    if (left + offsetWidth > window.innerWidth - padding) {
      left = Math.max(padding, anchorRect.right - offsetWidth);
    }
    if (left < padding) left = padding;

    let top = anchorRect.bottom + 4;
    if (top + offsetHeight > window.innerHeight - padding) {
      top = Math.max(padding, anchorRect.top - offsetHeight - 4);
    }

    setPos({ top, left });
  }, [anchorRect, view]);

  // Фокус и выделение текста при переходе в режим переименования
  useEffect(() => {
    if (view === "rename") {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [view]);

  // Закрытие при клике снаружи или нажатии Escape
  useEffect(() => {
    function handlePointerDown(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    }

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        if (view === "menu") {
          onClose();
        } else {
          setView("menu");
        }
      }
    }

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, view]);

  const [renameError, setRenameError] = useState<string | null>(null);

  function handleSaveRename(e?: React.FormEvent) {
    e?.preventDefault();
    const trimmed = nameInput.trim();
    if (!trimmed) {
      setRenameError("Название не может быть пустым");
      return;
    }
    if (trimmed.length > 80) {
      setRenameError("Максимальная длина названия — 80 символов");
      return;
    }
    if (trimmed !== title) {
      onRename(trimmed);
    }
    onClose();
  }

  function handleConfirmDelete() {
    onDelete();
    onClose();
  }

  const pinLabel =
    target === "session"
      ? isPinned
        ? t("sidebar.unpinSession")
        : t("sidebar.pinSession")
      : target === "csv"
        ? isPinned
          ? t("sidebar.unpinCsv")
          : t("sidebar.pinCsv")
        : isPinned
          ? t("sidebar.unpinDb")
          : t("sidebar.pinDb");

  const renameLabel =
    target === "session"
      ? t("sidebar.renameSession")
      : target === "csv"
        ? t("sidebar.renameCsv")
        : target === "tile"
          ? t("tile.rename")
          : t("sidebar.renameDb");

  const deleteLabel =
    target === "session"
      ? t("sidebar.deleteSession")
      : target === "csv"
        ? t("sidebar.deleteCsv")
        : target === "tile"
          ? t("tile.delete")
          : t("sidebar.disconnectDb");

  const confirmTitle =
    target === "session"
      ? t("sidebar.confirmDeleteSession")
      : target === "csv"
        ? t("sidebar.confirmDeleteCsv")
        : target === "tile"
          ? t("tile.deleteConfirm")
          : t("sidebar.confirmDisconnectDb");

  const warningText =
    target === "session"
      ? t("sidebar.deleteSessionWarning")
      : target === "csv"
        ? t("sidebar.deleteCsvWarning")
        : target === "tile"
          ? t("tile.deleteWarning")
          : t("sidebar.disconnectDbWarning");

  return (
    <div
      ref={menuRef}
      role="dialog"
      aria-modal="true"
      style={{ top: `${pos.top}px`, left: `${pos.left}px` }}
      onClick={(e) => e.stopPropagation()}
      className="fixed z-50 min-w-[220px] max-w-[280px] rounded-xl border border-graphite-700 bg-graphite-900 p-1.5 shadow-2xl text-graphite-100 select-none animate-in fade-in zoom-in-95 duration-100"
    >
      {view === "menu" && (
        <div className="space-y-0.5">
          {onTogglePin && target !== "tile" && (
            <button
              type="button"
              onClick={() => {
                onTogglePin();
                onClose();
              }}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100 transition"
            >
              <Icon name="pin" size={14} className="shrink-0 text-graphite-400" />
              <span className="truncate">{pinLabel}</span>
            </button>
          )}

          <button
            type="button"
            onClick={() => setView("rename")}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100 transition"
          >
            <Icon name="pencil" size={14} className="shrink-0 text-graphite-400" />
            <span className="truncate">{renameLabel}</span>
          </button>

          {onChartTypeChange && chartTypes && chartTypes.length > 0 && (
            <button
              type="button"
              onClick={() => setView("chart_type")}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100 transition"
            >
              <Icon name="chart" size={14} className="shrink-0 text-graphite-400" />
              <span className="truncate">{t("canvas.chartType")}</span>
            </button>
          )}

          {onOpen && (
            <button
              type="button"
              onClick={() => {
                onOpen();
                onClose();
              }}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100 transition"
            >
              <Icon name="maximize" size={14} className="shrink-0 text-graphite-400" />
              <span className="truncate">{t("artifact.open")}</span>
            </button>
          )}

          {onElaborate && (
            <button
              type="button"
              onClick={() => {
                onElaborate();
                onClose();
              }}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100 transition"
            >
              <Icon name="sparkle" size={14} className="shrink-0 text-graphite-400" />
              <span className="truncate">{t("drill.menu")}</span>
            </button>
          )}

          <div className="my-1 border-t border-graphite-800" />

          <button
            type="button"
            onClick={() => setView("confirm_delete")}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-bad hover:bg-bad/10 hover:text-bad transition"
          >
            <Icon name="trash" size={14} className="shrink-0 text-bad" />
            <span className="truncate">{deleteLabel}</span>
          </button>
        </div>
      )}

      {view === "chart_type" && (
        <div className="p-1 space-y-1">
          <div className="px-1.5 pb-1 text-[12px] font-medium text-graphite-200">
            {t("canvas.chartType")}
          </div>
          {(chartTypes ?? []).map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => {
                onChartTypeChange?.(type);
                onClose();
              }}
              className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition ${
                type === chartType
                  ? "bg-graphite-800 text-graphite-50"
                  : "text-graphite-300 hover:bg-graphite-800 hover:text-graphite-100"
              }`}
            >
              <span className="truncate">{t(`chartType.${type}` as any)}</span>
              {type === chartType && <Icon name="check" size={14} className="shrink-0 text-good" />}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setView("menu")}
            className="mt-1 w-full rounded-md border border-graphite-700 px-2.5 py-1 text-xs text-graphite-300 hover:bg-graphite-800 transition"
          >
            {t("sidebar.cancel")}
          </button>
        </div>
      )}

      {view === "rename" && (
        <form onSubmit={handleSaveRename} className="p-1 space-y-2.5">
          <div className="text-[12px] font-medium text-graphite-200">{renameLabel}</div>
          <input
            ref={inputRef}
            type="text"
            maxLength={80}
            value={nameInput}
            onChange={(e) => {
              setNameInput(e.target.value);
              if (renameError) setRenameError(null);
            }}
            className="w-full rounded-lg border border-graphite-700 bg-graphite-950 px-2.5 py-1.5 text-xs text-graphite-50 placeholder-graphite-500 outline-none focus:border-graphite-400"
            placeholder={t("sidebar.newName")}
          />
          {renameError && (
            <p className="text-[11px] leading-tight text-bad">{renameError}</p>
          )}
          <div className="flex items-center justify-end gap-1.5 pt-1">
            <button
              type="button"
              onClick={() => setView("menu")}
              className="rounded-md border border-graphite-700 px-2.5 py-1 text-xs text-graphite-300 hover:bg-graphite-800 transition"
            >
              {t("sidebar.cancel")}
            </button>
            <button
              type="submit"
              disabled={!nameInput.trim()}
              className="rounded-md bg-graphite-100 px-3 py-1 text-xs font-medium text-graphite-950 hover:bg-graphite-50 disabled:opacity-50 transition"
            >
              {t("sidebar.save")}
            </button>
          </div>
        </form>
      )}

      {view === "confirm_delete" && (
        <div className="p-1 space-y-2.5">
          <div className="text-[12px] font-semibold text-graphite-100">{confirmTitle}</div>
          <div className="rounded-lg border border-bad/30 bg-bad/10 p-2 text-[11px] leading-snug text-bad">
            {warningText}
          </div>
          <div className="flex items-center justify-end gap-1.5 pt-1">
            <button
              type="button"
              onClick={() => setView("menu")}
              className="rounded-md border border-graphite-700 px-2.5 py-1 text-xs text-graphite-300 hover:bg-graphite-800 transition"
            >
              {t("sidebar.cancel")}
            </button>
            <button
              type="button"
              onClick={handleConfirmDelete}
              className="rounded-md bg-bad px-3 py-1 text-xs font-medium text-white hover:bg-bad/90 transition shadow-sm"
            >
              {deleteLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
