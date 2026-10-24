"use client";

import { useCallback } from "react";
import { useUiStore, type Locale } from "@/store/uiStore";
import { dictionaries, type MessageKey } from "./dictionary";

export type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string;

export function translate(locale: Locale, key: MessageKey, vars?: Record<string, string | number>): string {
  const template: string = dictionaries[locale][key] ?? dictionaries.ru[key] ?? key;
  return vars ? template.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? `{${name}}`)) : template;
}

/** Перевод строк интерфейса на текущий язык. */
export function useT(): Translate {
  const locale = useUiStore((s) => s.locale);
  return useCallback((key, vars) => translate(locale, key, vars), [locale]);
}

/** Локаль для дат и чисел. */
export function useDateLocale(): string {
  return useUiStore((s) => (s.locale === "en" ? "en-GB" : "ru-RU"));
}

/** «N сессий» / «N sessions» с правильным склонением. */
export function sessionsLabel(locale: Locale, n: number): string {
  if (locale === "en") return `${n} ${n === 1 ? "session" : "sessions"}`;
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} сессия`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} сессии`;
  return `${n} сессий`;
}
