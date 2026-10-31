"use client";

/**
 * Перетаскивание файла `.session` в окно (#251): пока файл над окном — подсказка
 * поверх рабочей области, отпущенный файл импортируется в новую сессию.
 * Браузер не открывает отпущенный файл сам: drop в окне перехватывается.
 */
import { useEffect, useRef, useState } from "react";
import { useSessionFileImport } from "@/hooks/useSessionFileImport";
import { useT } from "@/i18n/useT";
import Icon from "@/components/ui/Icon";

function hasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer?.types ?? []).includes("Files");
}

export default function SessionFileDrop() {
  const t = useT();
  const { importFile, busy, error, clearError } = useSessionFileImport();
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  const latest = useRef(importFile);
  latest.current = importFile;

  useEffect(() => {
    function enter(e: DragEvent) {
      if (!hasFiles(e)) return;
      depth.current += 1;
      setOver(true);
    }
    function leave(e: DragEvent) {
      if (!hasFiles(e)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setOver(false);
    }
    function overWindow(e: DragEvent) {
      if (hasFiles(e)) e.preventDefault();
    }
    function drop(e: DragEvent) {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current = 0;
      setOver(false);
      const file = e.dataTransfer?.files[0];
      if (file) latest.current(file);
    }
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", overWindow);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", overWindow);
      window.removeEventListener("drop", drop);
    };
  }, []);

  if (over) {
    return (
      <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-sm">
        <div className="rounded-xl border border-dashed border-graphite-500 bg-graphite-900 px-8 py-6 text-[14px] text-graphite-100">
          {t("import.drop")}
        </div>
      </div>
    );
  }
  if (!busy && !error) return null;
  return (
    <div className="fixed bottom-4 left-1/2 z-50 flex max-w-lg -translate-x-1/2 items-start gap-3 rounded-lg border border-graphite-700 bg-graphite-900 px-4 py-2.5 text-[13px] shadow-2xl">
      {busy ? (
        <span className="animate-pulse text-graphite-300">{t("import.working")}</span>
      ) : (
        <>
          <span className="text-bad">{error}</span>
          <button onClick={clearError} className="text-graphite-400 hover:text-graphite-50" title={t("common.close")}>
            <Icon name="x" size={14} />
          </button>
        </>
      )}
    </div>
  );
}
