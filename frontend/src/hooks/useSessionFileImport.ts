"use client";

/**
 * Импорт файла `.session` (#251): кнопкой в сайдбаре или перетаскиванием в окно.
 * Бэкенд создаёт новую сессию и загружает таблицы под новыми именами; лента
 * из ответа уже с этими именами — её кладёт в браузер `openImported`.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { importSessionFile } from "@/api/client";
import { openImported } from "@/lib/importSession";
import { expand, type JsonObject } from "@/lib/shareLink";
import { useT } from "@/i18n/useT";

export const SESSION_FILE = /\.session$/i;

export function useSessionFileImport() {
  const t = useT();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function importFile(file: File) {
    setError(null);
    if (!SESSION_FILE.test(file.name)) {
      setError(t("import.notSessionFile", { name: file.name }));
      return;
    }
    setBusy(true);
    try {
      const result = await importSessionFile(file);
      const id = openImported(expand(result.session as JsonObject), result, t);
      router.push(`/session/${id}`);
    } catch (err: any) {
      setError(t("import.failed", { msg: err?.detail || err?.message || String(err) }));
    } finally {
      setBusy(false);
    }
  }

  return { importFile, busy, error, clearError: () => setError(null) };
}
