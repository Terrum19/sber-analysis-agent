/**
 * Скачивание файлов, которые отдаёт бэкенд: имя берём из `Content-Disposition`
 * (там может быть кириллица в RFC 5987), иначе — запасное имя.
 */

/** `filename*=UTF-8''…` или `filename="…"` из заголовка. */
export function filenameFromDisposition(
  header: string | null | undefined,
  fallback: string,
): string {
  if (!header) return fallback;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded?.[1]) {
    const value = encoded[1].trim().replace(/^["']|["']$/g, "");
    try {
      return decodeURIComponent(value);
    } catch {
      return value;
    }
  }
  const simple = /filename="?([^";]+)"?/i.exec(header);
  if (simple?.[1]) return simple[1].trim();
  return fallback;
}

/** Запрещённые в именах файлов символы заменяем подчёркиванием. */
export function safeFilename(name: string, extension: string): string {
  const base = name
    .trim()
    .replace(/[\\/:*?"<>|]+/g, "_")
    .replace(/\s+/g, " ")
    .slice(0, 120);
  return `${base || "export"}.${extension}`;
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Safari дочитывает blob асинхронно — отзываем URL после клика.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
