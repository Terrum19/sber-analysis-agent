/**
 * Копирование текста в буфер обмена.
 *
 * Сначала пробуем современный Clipboard API (работает только в secure context),
 * затем — скрытую textarea и execCommand("copy") для http и старых браузеров.
 * Возвращает false, если скопировать не удалось: вызывающий покажет ошибку.
 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;

  if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // Нет разрешения или API недоступен — пробуем фолбэк ниже
    }
  }

  if (typeof document === "undefined") return false;

  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "-1000px";
  area.style.left = "-1000px";
  area.style.opacity = "0";
  document.body.appendChild(area);
  try {
    area.select();
    area.setSelectionRange(0, area.value.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    document.body.removeChild(area);
  }
}
