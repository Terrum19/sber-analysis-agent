import type { Metadata } from "next";
import "./globals.css";
import ThemeSync from "@/components/layout/ThemeSync";

export const metadata: Metadata = {
  title: "SberAnalyticalAgent",
  description: "AI-Native BI: диалог с данными, визуализации и дашборды",
};

// Тема и язык применяются до первой отрисовки, чтобы не было вспышки тёмной темы
const PRELOAD_THEME = `try{var s=JSON.parse(localStorage.getItem("sber_ui")||"{}").state||{};document.documentElement.dataset.theme=s.theme==="light"?"light":"dark";document.documentElement.lang=s.locale==="en"?"en":"ru"}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: PRELOAD_THEME }} />
      </head>
      <body className="bg-graphite-950 text-graphite-100 antialiased font-sans">
        <ThemeSync />
        {children}
      </body>
    </html>
  );
}
