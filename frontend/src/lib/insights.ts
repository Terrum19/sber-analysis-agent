/**
 * Карточки вкладки «Инсайты» (F-17): вопрос пользователя → вывод агента.
 *
 * Ход агента в ленте — несколько сообщений: прогресс, график, текст; `answer`
 * и `turn_id` получает последнее. Поэтому сообщения собираем по ходам — всё
 * между двумя репликами пользователя. Плитки канваса, чей инсайт не встретился
 * в ленте (лента очищена, дашборд открыт из реестра), идут отдельными карточками.
 */
import type { ChatMessage } from "@/store/sessionStore";
import type { DashboardTile } from "@/types/dashboard";

export interface InsightCardData {
  key: string;
  /** Вопрос пользователя; у плитки без хода — её название. */
  question: string;
  /** Карточка собрана из плитки канваса, а не из ленты. */
  fromTile: boolean;
  headline?: string;
  details: string[];
  /** Инсайты плиток хода-дашборда. */
  tileInsights: { title: string; text: string }[];
  /** Графики хода — плитками, чтобы окно рисовало их как канвас. */
  charts: DashboardTile[];
  turnId?: string;
  time?: string;
  /** Для Excel-экспорта ответа (temp_result_vN). */
  resultTable?: string;
}

interface BuildOptions {
  /** Служебные тексты плиток, которые не выводы (`canvas.newKpiInsight` на обоих языках). */
  ignore?: string[];
  /** Название графика без подписи. */
  chartTitle: string;
}

const norm = (text: string) => text.replace(/\s+/g, " ").trim().toLowerCase();

const hasSpec = (spec: unknown) => Boolean(spec && typeof spec === "object" && Object.keys(spec).length > 0);

function chartOf(data: NonNullable<ChatMessage["chart"]>, fallback: string): DashboardTile {
  return {
    title: data.caption || fallback,
    spec: data.spec,
    result_table: data.result_table,
    layout: { x: 0, y: 0, w: 12, h: 5 },
  };
}

function turnCard(question: string, turn: ChatMessage[], index: number, opts: BuildOptions, ignore: Set<string>) {
  const answer = [...turn].reverse().find((m) => m.answer)?.answer;
  const dashboard = [...turn].reverse().find((m) => m.dashboard)?.dashboard;
  const dashTiles = dashboard?.tiles ?? [];

  const tileInsights = dashTiles
    .filter((tile) => tile.insight && !ignore.has(norm(tile.insight)))
    .map((tile) => ({ title: tile.title, text: tile.insight!.trim() }));
  const headline = answer?.headline?.trim() || (tileInsights.length > 0 ? dashboard?.title : undefined);
  const details = (answer?.details ?? []).map((d) => d.trim()).filter(Boolean);
  if (!headline && details.length === 0 && tileInsights.length === 0) return null;

  const charts: DashboardTile[] = [
    ...dashTiles.filter((tile) => hasSpec(tile.spec)),
    ...turn.flatMap((m) => (m.chart && hasSpec(m.chart.spec) ? [chartOf(m.chart, opts.chartTitle)] : [])),
  ];
  if (charts.length === 0 && answer?.spec && hasSpec(answer.spec)) {
    charts.push({ title: headline || opts.chartTitle, spec: answer.spec, layout: { x: 0, y: 0, w: 12, h: 5 } });
  }

  const last = [...turn].reverse().find((m) => m.timestamp);
  const turnId = [...turn].reverse().find((m) => m.turn_id)?.turn_id;
  const card: InsightCardData = {
    key: turnId ? `turn-${turnId}` : `msg-${index}`,
    question,
    fromTile: false,
    headline,
    details,
    tileInsights,
    charts,
    turnId,
    time: last?.timestamp,
    resultTable: answer?.result_table,
  };
  return card;
}

/** Карточки инсайтов, новые сверху; плитки без хода — в конце. */
export function buildInsights(
  messages: ChatMessage[],
  tiles: DashboardTile[],
  opts: BuildOptions,
): InsightCardData[] {
  const ignore = new Set((opts.ignore ?? []).map(norm));
  const cards: InsightCardData[] = [];
  let question = "";
  let turn: ChatMessage[] = [];
  let turnStart = 0;

  const flush = () => {
    if (turn.length > 0) {
      const card = turnCard(question, turn, turnStart, opts, ignore);
      if (card) cards.push(card);
    }
    turn = [];
  };

  messages.forEach((m, i) => {
    if (m.role === "user") {
      flush();
      question = m.content.trim();
      turnStart = i;
    } else {
      turn.push(m);
    }
  });
  flush();

  // Тексты, уже показанные в карточках ходов: такие плитки не дублируем
  const seen = new Set<string>();
  for (const card of cards) {
    if (card.headline) seen.add(norm(card.headline));
    card.details.forEach((d) => seen.add(norm(d)));
    card.tileInsights.forEach((t) => seen.add(norm(t.text)));
  }

  const orphans: InsightCardData[] = [];
  tiles.forEach((tile, i) => {
    const text = tile.insight?.trim();
    if (!text || ignore.has(norm(text)) || seen.has(norm(text))) return;
    seen.add(norm(text));
    orphans.push({
      key: `tile-${tile.id ?? i}`,
      question: tile.title,
      fromTile: true,
      headline: text,
      details: [],
      tileInsights: [],
      charts: hasSpec(tile.spec) ? [tile] : [],
      resultTable: tile.result_table,
    });
  });

  return [...cards.reverse(), ...orphans];
}

/** Строки текста карточки: вывод, детали, инсайты плиток. */
export function insightLines(card: InsightCardData): string[] {
  return [
    ...(card.headline ? [card.headline] : []),
    ...card.details,
    ...card.tileInsights.map((t) => `${t.title} — ${t.text}`),
  ];
}

// Нечёткий поиск: слово запроса ищется в словах карточки подстрокой, а если
// нет — началом слова с опечатками (окончания русских слов меняются:
// «выручка» находит «выручки», «продажм» — «продажи»).

type Word = { text: string; start: number; end: number };

const WORD = /[\p{L}\p{N}_]+/gu;

const fold = (text: string) => text.toLowerCase().replace(/ё/g, "е");

function wordsOf(text: string): Word[] {
  return [...text.matchAll(WORD)].map((m) => {
    const start = m.index ?? 0;
    return { text: fold(m[0]), start, end: start + m[0].length };
  });
}

function queryTokens(query: string): string[] {
  return [...new Set(wordsOf(query).map((w) => w.text))];
}

/** Сколько опечаток прощаем слову запроса: короткие — только подстрокой. */
const allowedTypos = (length: number) => (length < 4 ? 0 : length <= 6 ? 1 : 2);

/**
 * Лучшее совпадение слова запроса со словом текста: оценка 0..1 и длина
 * совпавшей части от начала слова (для подсветки), или null.
 */
function matchWord(token: string, word: string): { score: number; from: number; to: number } | null {
  const at = word.indexOf(token);
  if (at !== -1) return { score: at === 0 ? 1 : 0.9, from: at, to: at + token.length };
  const typos = allowedTypos(token.length);
  if (typos === 0) return null;

  // Расстояние Дамерау–Левенштейна (с перестановкой соседних букв) от слова
  // запроса до каждого начала слова текста; берём лучшее начало
  const n = token.length;
  const m = Math.min(word.length, n + typos);
  let prev2: number[] = [];
  let prev = Array.from({ length: m + 1 }, (_, j) => j);
  for (let i = 1; i <= n; i++) {
    const row = [i];
    for (let j = 1; j <= m; j++) {
      const cost = token[i - 1] === word[j - 1] ? 0 : 1;
      let d = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && token[i - 1] === word[j - 2] && token[i - 2] === word[j - 1]) {
        d = Math.min(d, prev2[j - 2] + 1);
      }
      row.push(d);
    }
    prev2 = prev;
    prev = row;
  }
  let best: { d: number; j: number } | null = null;
  for (let j = Math.max(1, n - typos); j <= m; j++) {
    if (prev[j] <= typos && (!best || prev[j] < best.d)) best = { d: prev[j], j };
  }
  if (!best) return null;
  return { score: 0.8 - (0.3 * best.d) / typos, from: 0, to: best.j };
}

function bestScore(token: string, words: Word[]): number {
  let best = 0;
  for (const word of words) {
    const hit = matchWord(token, word.text);
    if (hit && hit.score > best) best = hit.score;
    if (best === 1) break;
  }
  return best;
}

/**
 * Нечёткий поиск по вопросу и тексту: каждое слово запроса должно найтись.
 * Карточки идут по убыванию совпадения, при равенстве — в прежнем порядке.
 */
export function filterInsights(cards: InsightCardData[], query: string): InsightCardData[] {
  const tokens = queryTokens(query);
  if (tokens.length === 0) return cards;
  const phrase = norm(query);
  const scored: { card: InsightCardData; score: number }[] = [];
  for (const card of cards) {
    const questionWords = wordsOf(card.question);
    const textWords = insightLines(card).flatMap(wordsOf);
    let score = 0;
    for (const token of tokens) {
      // Совпадение в вопросе весит чуть больше, чем в тексте вывода
      const s = Math.max(bestScore(token, questionWords) * 1.1, bestScore(token, textWords));
      if (s === 0) {
        score = 0;
        break;
      }
      score += s;
    }
    if (score === 0) continue;
    if (norm([card.question, ...insightLines(card)].join(" ")).includes(phrase)) score += 0.5;
    scored.push({ card, score });
  }
  return scored.sort((a, b) => b.score - a.score).map((item) => item.card);
}

/** Куски текста, совпавшие со словами запроса, — для подсветки. */
function matchRanges(text: string, query: string): [number, number][] {
  const tokens = queryTokens(query);
  if (tokens.length === 0) return [];
  const ranges: [number, number][] = [];
  for (const word of wordsOf(text)) {
    let best: { score: number; from: number; to: number } | null = null;
    for (const token of tokens) {
      const hit = matchWord(token, word.text);
      if (hit && (!best || hit.score > best.score)) best = hit;
    }
    if (best) ranges.push([word.start + best.from, Math.min(word.end, word.start + best.to)]);
  }
  return ranges;
}

export type TextPart = { text: string; kind: "plain" | "num" | "match" };

// Числа с разрядами через пробел («1 234 567»), дробью и процентом
const NUMBER = /[−-]?\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?(?:\s?%)?|[−-]?\d+(?:[.,]\d+)?(?:\s?%)?/g;

function splitNumbers(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let pos = 0;
  for (const m of text.matchAll(NUMBER)) {
    const start = m.index ?? 0;
    // Цифры внутри слова (temp_result_v3, Q4) — не значения
    if (start > 0 && /[\p{L}_]/u.test(text[start - 1])) continue;
    if (start > pos) parts.push({ text: text.slice(pos, start), kind: "plain" });
    parts.push({ text: m[0], kind: "num" });
    pos = start + m[0].length;
  }
  if (pos < text.length) parts.push({ text: text.slice(pos), kind: "plain" });
  return parts;
}

/** Текст на куски: совпадения поиска и числа подсвечиваются при просмотре. */
export function markText(text: string, query = ""): TextPart[] {
  const ranges = matchRanges(text, query);
  if (ranges.length === 0) return splitNumbers(text);
  const parts: TextPart[] = [];
  let pos = 0;
  for (const [from, to] of ranges) {
    if (from > pos) parts.push(...splitNumbers(text.slice(pos, from)));
    parts.push({ text: text.slice(from, to), kind: "match" });
    pos = to;
  }
  if (pos < text.length) parts.push(...splitNumbers(text.slice(pos)));
  return parts;
}

/** Выгрузка карточек в Markdown в том же порядке, что на вкладке. */
export function insightsMarkdown(
  cards: InsightCardData[],
  labels: { title: string; chart: string },
): string {
  const out = [`# ${labels.title}`, ""];
  for (const card of cards) {
    out.push(`## ${card.question.replace(/\s+/g, " ")}`, "");
    if (card.time) out.push(`_${card.time}_`, "");
    if (card.headline) out.push(card.headline, "");
    const bullets = [...card.details, ...card.tileInsights.map((t) => `**${t.title}** — ${t.text}`)];
    if (bullets.length > 0) out.push(...bullets.map((b) => `- ${b}`), "");
    if (card.charts.length > 0) out.push(`${labels.chart}: ${card.charts.map((c) => c.title).join(", ")}`, "");
  }
  return out.join("\n").trimEnd() + "\n";
}
