/**
 * Типы источников данных (дубль контракта backend/app/api/schemas/datasource.py).
 */
export type DbKind = "postgres" | "clickhouse";

export interface Datasource {
  datasource_id: string;
  kind: "csv" | DbKind;
  tables: string[];
  profile_summary?: string;
  warnings?: string[];
}

export interface DatasourceDbRequest {
  kind: DbKind;
  dsn: string;
  schema?: string;
}
