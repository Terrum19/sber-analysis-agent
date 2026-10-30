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

/** Бизнес-глоссарий: термины пользователя для колонок таблицы (data/business_dictionary/example.json). */
export interface Glossary {
  note?: string | null;
  terms: Record<string, string[]>;
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

/** Воспоминание сессии (#197): выключенное не идёт в контекст хода. */
export interface MemoryItem {
  memory_id: string;
  text: string;
  /** "user" — просьба в чате или кнопка; позже — результаты исследования. */
  source: string;
  /** ISO 8601 в UTC. */
  created_at: string;
  enabled: boolean;
  /** Ход, в котором запись сделана из чата; у добавленной кнопкой — null. */
  turn_id: string | null;
}

/** Тело POST /api/sessions/{id}/memories (пустой текст — 422). */
export interface MemoryCreatePayload {
  text: string;
}

/** Тело PATCH /api/sessions/{id}/memories/{memory_id}. */
export interface MemoryPatchPayload {
  enabled: boolean;
}

export interface SessionResponse {
  session_id: string;
  table: string | null;
  datasource_id?: string | null;
  title?: string | null;
  /** ISO 8601 в UTC; у старых сессий null. */
  created_at?: string | null;
}
