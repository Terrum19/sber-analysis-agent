"use client";

/**
 * Главная: выбор/создание сессии и подключение датасорса.
 * План-минимум: чат. План-максимум: чат слева, канвас справа.
 */
import { useRouter } from "next/navigation";

export default function HomePage() {
  const router = useRouter();

  async function createSession() {
    const res = await fetch("/api/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const data = await res.json();
    router.push(`/session/${data.session_id}`);
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-3xl flex-col items-center justify-center gap-6 p-8">
      <h1 className="text-4xl font-bold">SberAnalyticalAgent</h1>
      <p className="text-center text-slate-600">
        Задавайте вопросы данным на естественном языке — получайте ответы, графики и дашборды.
      </p>
      <button
        onClick={createSession}
        className="rounded-lg bg-blue-600 px-6 py-3 font-medium text-white hover:bg-blue-700"
      >
        Начать диалог
      </button>
    </main>
  );
}
