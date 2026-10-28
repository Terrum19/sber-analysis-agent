/**
 * Типы источников данных (дубль контракта backend/app/api/schemas/datasource.py).
 */
export type DbKind = "postgres" | "clickhouse";

export type DescriptionStatus = "none" | "pending" | "ready" | "failed";

/** Сводка паспорта таблицы для карточки (TableBrief). */
export interface TableBrief {
  /** Имя таблицы в DuckDB: для SQL и запросов к API. */
  table: string;
  /** Название для пользователя (его или от LLM); нет — показываем table. */
  display_name?: string | null;
  rows: number;
  columns: number;
  period_from?: string | null;
  period_to?: string | null;
  /** pending — описание колонок переносится в паспорт после ответа. */
  description_status: DescriptionStatus;
  summary?: string | null;
  /** Замечания о данных (исправления при загрузке и предупреждения профилировщика). */
  warnings: string[];
  /** Коды и категории — по названиям колонок из профиля. */
  entities?: string[];
  /** Числовые колонки. */
  metrics?: string[];
  /** Подсказки к первому вопросу по колонкам таблицы. */
  suggested_questions?: string[];
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
