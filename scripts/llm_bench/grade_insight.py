#!/usr/bin/env python3
"""Оценка заголовков вывода по таблицам результата: grade_insight.py a.json [b.json ...].

Ожидаемый ответ берётся из таблицы хода (replay_insight.py), а не из
заранее записанного эталона, поэтому оценка не зависит от того, какой
план построила модель:
- одна строка — число из колонки, о которой вопрос (среднее, медиана, …),
  а на «какой / кто / лучший» — ещё и её период или категория;
- «сколько …» по таблице строк — число строк;
- ряд — названы максимум или минимум со своей строкой, либо диапазон
  «от мин до макс».
✅ — верно, ⚠️ — верно, но голое число или дата без слов, ❌ — неверно.
"""

import json
import re
import sys

MONTHS = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр",
          "октябр", "ноябр", "декабр"]  # fmt: skip


def mentions(headline: str, value: object) -> bool:
    """Число value есть в headline (пробелы в разрядах и запятая допустимы)."""
    if not isinstance(value, (int, float)):
        return False
    text = re.sub(r"(?<=\d)[\s  ](?=\d{3}\b)", "", headline).replace(",", ".")
    tolerance = max(0.6, abs(value) * 0.002)
    return any(
        abs(float(n) - value) <= tolerance for n in re.findall(r"\d+(?:\.\d+)?", text)
    )


def names(headline: str, label: object) -> bool:
    """Период (как месяц или ГГГГ-ММ) или категория есть в headline."""
    text, value = headline.lower(), str(label)
    date = re.match(r"(\d{4})-(\d{2})", value)
    if date:
        return MONTHS[int(date.group(2)) - 1] in text or date.group(0) in text
    return value.lower() in text


def metric(question: str, row: dict) -> str | None:
    numeric = [k for k, v in row.items() if isinstance(v, (int, float))]
    for word, keys in (("медиан", ("median",)), ("средн", ("mean", "avg"))):
        if word in question:
            match = [k for k in numeric if any(key in k for key in keys)]
            if match:
                return match[0]
    return numeric[-1] if numeric else None


def grade(item: dict, headline: str) -> str:
    question, rows = item["question"].lower(), item["rows"]
    if headline.startswith("ERROR") or not rows:
        return "❌"
    labels = [k for k, v in rows[0].items() if not isinstance(v, (int, float))]
    label = labels[0] if labels else None
    column = metric(question, rows[0])
    asks_label = re.search(r"какой|кто|лучш|худш", question)
    if item["row_count"] == 1:
        ok = column is not None and mentions(headline, rows[0][column])
        if asks_label and label is not None:
            ok = ok and names(headline, rows[0][label])
    elif "сколько" in question and not re.search(r"\bпо \w|у первого", question):
        ok = mentions(headline, item["row_count"])
    else:
        top = max(rows, key=lambda r: r[column] if column else 0)
        low = min(rows, key=lambda r: r[column] if column else 0)
        named = [
            r for r in (top, low) if label is not None and names(headline, r[label])
        ]
        if asks_label:
            ok = bool(named)
        else:
            ok = any(mentions(headline, r[column]) for r in named) or (
                mentions(headline, top[column]) and mentions(headline, low[column])
            )
    if not ok:
        return "❌"
    return "⚠️" if len(re.findall(r"[А-Яа-яЁёA-Za-z]{3,}", headline)) < 2 else "✅"


def main() -> None:
    runs = []
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as fh:
            runs.append(json.load(fh))
    totals = [{"✅": 0, "⚠️": 0, "❌": 0} for _ in runs]
    for items in zip(*runs, strict=True):
        print(f"## {items[0]['question']}  (строк: {items[0]['row_count']})")
        for number, item in enumerate(items):
            for headline in item["headlines"]:
                mark = grade(item, headline)
                totals[number][mark] += 1
                print(f"   {number + 1} {mark} {headline[:120]}")
    for path, total in zip(sys.argv[1:], totals, strict=True):
        print(f"{path}: ✅ {total['✅']}  ⚠️ {total['⚠️']}  ❌ {total['❌']}")


if __name__ == "__main__":
    main()
