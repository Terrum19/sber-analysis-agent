/**
 * Состояние LLM (синхронизировано с backend/app/api/schemas/llm.py).
 * Модель готовит бэкенд сам (#167): connecting — Ollama не отвечает, бэкенд
 * переподключается; pulling — модель скачивается (progress 0..1); preparing —
 * создаётся вариант модели с нужным контекстом; ok — можно спрашивать.
 */
export interface LlmStatus {
  state: "connecting" | "pulling" | "preparing" | "ok";
  model: string;
  progress?: number | null;
  detail?: string | null;
}
