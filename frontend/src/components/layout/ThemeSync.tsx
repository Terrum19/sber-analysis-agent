"use client";

/** Переносит выбранные тему и язык на <html data-theme lang>. */
import { useEffect } from "react";
import { useUiStore } from "@/store/uiStore";

export default function ThemeSync() {
  const theme = useUiStore((s) => s.theme);
  const locale = useUiStore((s) => s.locale);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  return null;
}
