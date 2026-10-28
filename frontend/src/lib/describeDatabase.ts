/**
 * Одно предложение о том, что за данные в базе: описание от бэкенда,
 * переведённый текст для демо-базы или сводка по таблицам.
 */
import { tableTitle, type DatabaseFolder } from "@/store/sessionStore";
import type { Translate } from "@/i18n/useT";

export function describeDatabase(db: DatabaseFolder | undefined, t: Translate): string {
  if (!db) return "";
  if (db.demo) return t("db.demoDescription");
  if (db.description) return db.description;
  const shown = db.tables.slice(0, 3).map((table) => tableTitle(db, table)).join(", ");
  const more = db.tables.length > 3 ? "…" : "";
  return t("db.tablesSummary", { n: db.tables.length, tables: `${shown}${more}` });
}
