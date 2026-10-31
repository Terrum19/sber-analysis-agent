import type { Config } from "tailwindcss";

// Цвета темы берутся из CSS-переменных (globals.css): у тёмной и светлой темы
// свои значения, а классы компонентов (bg-graphite-900, text-good…) одни и те же.
const themed = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // Нейтральная шкала интерфейса: 950 — фон сайдбара, 900 — рабочая область,
        // 50 — самый контрастный текст. В светлой теме шкала зеркальна.
        graphite: Object.fromEntries(
          ["50", "100", "200", "300", "400", "500", "600", "700", "800", "850", "900", "950"].map((step) => [
            step,
            themed(`g-${step}`),
          ]),
        ),
        // Статусы: текст и метки (good/warn/bad), KPI-бейдж, заливка опасных кнопок
        good: themed("good"),
        warn: themed("warn"),
        bad: themed("bad"),
        kpi: themed("kpi"),
        danger: themed("danger"),
      },
    },
  },
  plugins: [],
};
export default config;
