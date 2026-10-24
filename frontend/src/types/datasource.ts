/**
 * Типы источников данных (дубль контракта backend/app/api/schemas/datasource.py).
 */
export type DbKind = "postgres" | "clickhouse";

export type DescriptionStatus = "none" | "pending" | "ready" | "failed";

/** Сводка паспорта таблицы для карточки (TableBrief). */
export interface TableBrief {
  table: string;
  rows: number;
  columns: number;
  period_from?: string | null;
  period_to?: string | null;
  /** pending — описание колонок переносится в паспорт после ответа. */
  description_status: DescriptionStatus;
  summary?: string | null;
  /** Что загрузчик исправил в данных или не смог исправить. */
  warnings: string[];
}

export interface Datasource {
  datasource_id: string;
  kind: "csv" | DbKind;
  tables: string[];
  profiles?: TableBrief[];
  profile_summary?: string;
}

export interface DatasourceDbRequest {
  kind: DbKind;
  dsn: string;
  schema?: string;
  /** Документ-описание таблиц (README, словарь колонок). */
  description?: string;
}

export interface SessionCreatePayload {
  table: string;
  datasource_id?: string | null;
  title?: string | null;
}

/** Тело PATCH /api/sessions/{id}: хотя бы одно из полей (иначе 422). */
export interface SessionPatchPayload {
  table?: string;
  title?: string;
}

export interface SessionResponse {
  session_id: string;
  table: string | null;
  datasource_id?: string | null;
  title?: string | null;
  /** ISO 8601 в UTC; у старых сессий null. */
  created_at?: string | null;
}
