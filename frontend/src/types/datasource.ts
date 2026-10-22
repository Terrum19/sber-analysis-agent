/**
 * Типы источников данных (дубль контракта backend/app/api/schemas/datasource.py).
 * profiles и TableBrief — из D-07 (#96); пока бэкенд их не отдаёт, поля необязательны.
 */
export type DbKind = "postgres" | "clickhouse";

export type DescriptionStatus = "none" | "pending" | "ready" | "failed";

/** Краткий паспорт таблицы после подключения (D-07, #96). */
export interface TableBrief {
  table: string;
  rows?: number;
  columns?: number;
  period_from?: string | null;
  period_to?: string | null;
  description_status?: DescriptionStatus;
  summary?: string | null;
  warnings?: string[];
}

export interface Datasource {
  datasource_id: string;
  kind: "csv" | DbKind;
  tables: string[];
  profile_summary?: string;
  profiles?: TableBrief[];
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

export interface SessionPatchPayload {
  table: string;
}

export interface SessionResponse {
  session_id: string;
  table: string;
  datasource_id?: string | null;
  title?: string | null;
  /** ISO 8601 в UTC; у старых сессий null. */
  created_at?: string | null;
}
