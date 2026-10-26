/**
 * Состояние LLM (синхронизировано с backend/app/api/schemas/llm.py).
 * Модель готовит бэкенд сам (#167): connecting — Ollama не отвечает, бэкенд
 * переподключается; pulling — модель скачивается (progress 0..1); preparing —
 * создаётся вариант модели с нужным контекстом; missing — модели LLM_MODEL нет
 * в реестре Ollama (опечатка), бэкенд её больше не скачивает; ok — можно спрашивать.
 */
export interface LlmStatus {
  state: "connecting" | "pulling" | "preparing" | "missing" | "ok";
  model: string;
  progress?: number | null;
  detail?: string | null;
}
