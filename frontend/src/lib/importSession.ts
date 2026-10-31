/**
 * Завершение импорта сессии — по ссылке (#249) или из файла `.session` (#251):
 * лента кладётся в кеш браузера, сессия — в папку «Импортированные».
 * Последним сообщением ленты — нашлась ли у получателя таблица автора (CSV —
 * по хэшу, БД — в той же базе); нашлась — сессия к ней привязана.
 * У ответов — шаги хода из бандла. У рецепта (#250) графики без точек ждут
 * пересчёта: их запускает плашка в чате, не импорт.
 */
import { hasValues, type SharedSession } from "@/lib/shareLink";
import { chartTile } from "@/store/chatRunStore";
import { IMPORTED_FOLDER, useSessionStore, type ChatMessage, type ImportedStep } from "@/store/sessionStore";
import type { ImportedSession, TableCheck } from "@/types/datasource";
import type { DashboardTile } from "@/types/dashboard";
import type { Translate } from "@/i18n/useT";

/**
 * Сообщение ленты с шагами хода из бандла; у рецепта (#250) график без точек
 * ждёт пересчёта, с точками — едет с данными автора.
 */
function withRecipe(m: ChatMessage, shared: SharedSession): ChatMessage {
  const turn = m.role === "agent" && m.turn_id ? shared.bundle.turns.find((x) => x.turn_id === m.turn_id) : undefined;
  const steps = Array.isArray(turn?.steps) && turn.steps.length ? (turn.steps as unknown as ImportedStep[]) : undefined;
  const recipe = shared.recipe && m.chart ? (hasValues(m.chart.spec) ? "author" : "pending") : undefined;
  return { ...m, ...(steps ? { steps } : {}), ...(recipe ? { recipe } : {}) };
}

/** Лента получателя: карточки дашбордов — на новые id, плитки превью — из бандла. */
function importedMessages(shared: SharedSession, result: ImportedSession): ChatMessage[] {
  return shared.messages.map((message) => {
    const m = withRecipe(message, shared);
    if (!m.dashboard) return m;
    const { dashboard, ...rest } = m;
    const id = result.dashboards[dashboard.dashboard_id];
    if (!id) return rest;
    const source = shared.bundle.dashboards.find((d) => d.id === dashboard.dashboard_id);
    const tiles = (source?.tiles ?? []).map((tile, i) => ({ ...tile, id: `${id}-${i}` }) as unknown as DashboardTile);
    return { ...rest, dashboard: { dashboard_id: id, title: dashboard.title, tiles } };
  });
}

/** Сообщение ленты: нашлась ли у получателя таблица автора. */
function tableNotice(check: TableCheck, t: Translate): string {
  const vars = { table: check.table, bound: check.bound ?? "", dsn: check.dsn ?? "", file: check.file ?? check.table };
  switch (check.status) {
    case "found":
      return check.kind === "csv" ? t("import.foundCsv", vars) : t("import.foundDb", vars);
    case "different":
      return t("import.differentCsv", vars);
    case "missing":
      return t("import.missingCsv", vars);
    case "no_table":
      return t("import.noTable", vars);
    case "not_connected":
      return t("import.notConnected", vars);
  }
}

/** Положить импортированную бэкендом сессию в стор браузера; вернуть её id. */
export function openImported(shared: SharedSession, result: ImportedSession, t: Translate): string {
  const messages = importedMessages(shared, result);
  const tiles = messages
    .filter((m) => m.chart && m.recipe !== "pending")
    .map((m) => chartTile(m.chart, t("chat.chartDefault")));
  if (result.check) messages.push({ id: "msg-imported-table", role: "agent", content: tableNotice(result.check, t) });
  const table = result.table || shared.table || IMPORTED_FOLDER;
  useSessionStore.getState().addImportedSession(result.session_id, result.title, table, { messages, tiles });
  return result.session_id;
}
