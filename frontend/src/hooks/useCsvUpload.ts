"use client";

import { useRef, useState } from "react";
import { attachCsv } from "@/api/client";
import { useSessionStore } from "@/store/sessionStore";
import { useStartSession } from "@/hooks/useStartSession";
import { useT } from "@/i18n/useT";

export function useCsvUpload() {
  const t = useT();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const addDatabase = useSessionStore((s) => s.addDatabase);
  const startSession = useStartSession();

  async function handleCsvUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    e.target.value = "";
    // Не-CSV файл среди выбранных — документ-описание данных (multipart-поле description)
    const files = picked.filter((f) => /\.csv$/i.test(f.name));
    const description = picked.find((f) => !/\.csv$/i.test(f.name)) ?? null;
    if (!files.length) return;

    setIsBusy(true);
    setErrorMsg(null);
    try {
      const ds = await attachCsv(files, description);
      const title = files.map((f) => f.name.replace(/\.csv$/i, "")).join(", ");
      const folder = addDatabase(ds, title);
      await startSession(folder);
    } catch (err: any) {
      setErrorMsg(err.message || t("sidebar.csvError"));
    } finally {
      setIsBusy(false);
    }
  }

  function openFilePicker() {
    fileInputRef.current?.click();
  }

  return {
    fileInputRef,
    isBusy,
    errorMsg,
    setErrorMsg,
    handleCsvUpload,
    openFilePicker,
  };
}
