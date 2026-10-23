/**
 * Типы источников данных (дубль контракта backend/app/api/schemas/datasource.py).
 */
export type DbKind = "postgres" | "clickhouse";

export interface Datasource {
  datasource_id: string;
  kind: "csv" | DbKind;
  tables: string[];
  profile_summary?: string;
}

export interface DatasourceDbRequest {
  kind: DbKind;
  dsn: string;
  schema?: string;
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
}
