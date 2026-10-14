import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SberAnalyticalAgent",
  description: "AI-Native BI: диалог с данными, визуализации и дашборды",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="bg-slate-50 text-slate-900">{children}</body>
    </html>
  );
}
