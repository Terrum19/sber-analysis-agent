#!/usr/bin/env python3
"""Слепые зоны семантического отбора колонок (select_columns) — офлайн, без LLM.

Синтетическая витрина продаж на 26 колонок и вопросы с разметкой нужных
колонок. Вопрос «покрыт», если каждая требуемая колонка (или одна из
альтернатив) попала в то, что видит планировщик.

    python3 scripts/column_recall.py            # из корня репозитория
    python3 scripts/column_recall.py --verbose  # промахи по вопросам

Режимы:
  top5           — как сейчас: только топ-K колонок с выжимкой паспорта;
  top5+titles    — то же, но в паспорте есть русские названия колонок;
  prompt         — что реально видит планировщик в текущем коде
                   (describe_columns + список остальных колонок, если он есть);
  prompt+titles  — то же с названиями колонок из паспорта.
"""

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import settings
from app.core.data.duckdb_manager import DuckDBManager
from app.core.data.profiling import profile_table
from app.core.data.semantic_select import describe_columns, select_columns

try:  # есть только в ветке с исправлением слепых зон
    from app.core.data.semantic_select import describe_other_columns
except ImportError:
    describe_other_columns = None

TITLES = {
    "order_id": "Номер заказа",
    "customer_id": "ID клиента",
    "order_date": "Дата заказа",
    "ship_date": "Дата отгрузки",
    "region": "Регион",
    "city": "Город",
    "channel": "Канал продаж",
    "category": "Категория",
    "subcategory": "Подкатегория",
    "product_name": "Товар",
    "quantity": "Количество",
    "unit_price": "Цена за единицу",
    "discount": "Скидка",
    "revenue": "Выручка",
    "cost": "Себестоимость",
    "profit": "Прибыль",
    "payment_method": "Способ оплаты",
    "is_returned": "Возврат",
    "rating": "Оценка",
    "delivery_days": "Срок доставки, дней",
    "manager": "Менеджер",
    "segment": "Сегмент клиента",
    "customer_age": "Возраст клиента",
    "gender": "Пол",
    "promo_code": "Промокод",
    "warehouse": "Склад",
}

MONEY = {"revenue", "profit"}
# (вопрос, вид, требования): требование — множество допустимых колонок.
# Виды: L — имя колонки в вопросе, R — русский синоним, V — размытая
# формулировка («деньги», «кто», «когда»), X — значение категории, D — производная метрика.
QUESTIONS = [
    ("Средний revenue по region", "L", [{"revenue"}, {"region"}]),
    (
        "Распределение delivery_days по warehouse",
        "L",
        [{"delivery_days"}, {"warehouse"}],
    ),
    ("Какая выручка по регионам?", "R", [{"revenue"}, {"region"}]),
    ("Как меняется себестоимость по месяцам?", "R", [{"cost"}, {"order_date"}]),
    ("Сколько заказов оплачено через СБП?", "X", [{"payment_method"}]),
    ("Сколько денег мы заработали в Казани?", "V", [MONEY, {"city"}]),
    ("Кто наши самые ценные клиенты?", "V", [{"customer_id"}, MONEY]),
    ("Когда было больше всего заказов?", "V", [{"order_date"}]),
    ("Какая доля возвратов по категориям?", "R", [{"is_returned"}, {"category"}]),
    ("Как скидка влияет на прибыль?", "R", [{"discount"}, {"profit"}]),
    ("Какой средний чек в онлайне?", "V", [{"revenue"}, {"channel"}]),
    ("Насколько быстро доставляем в Новосибирск?", "V", [{"delivery_days"}, {"city"}]),
    ("Довольны ли покупатели товарами Электроники?", "V", [{"rating"}, {"category"}]),
    ("Какой менеджер продаёт лучше всех?", "R", [{"manager"}, MONEY]),
    (
        "Какая маржинальность по подкатегориям?",
        "D",
        [{"profit", "cost"}, {"subcategory"}],
    ),
    (
        "Отличается ли возраст покупателей B2B и B2C?",
        "X",
        [{"customer_age"}, {"segment"}],
    ),
    (
        "Мужчины или женщины покупают больше?",
        "V",
        [{"gender"}, {"revenue", "quantity"}],
    ),
    (
        "Какие промокоды самые эффективные?",
        "R",
        [{"promo_code"}, {"revenue", "discount"}],
    ),
    ("Сколько штук iPhone 15 продали?", "X", [{"quantity"}, {"product_name"}]),
    ("С какого склада чаще всего возвраты?", "R", [{"warehouse"}, {"is_returned"}]),
    (
        "Сколько дней в среднем между заказом и отгрузкой?",
        "D",
        [{"order_date"}, {"ship_date"}],
    ),
    (
        "Какая цена за единицу у самых дорогих товаров?",
        "R",
        [{"unit_price"}, {"product_name"}],
    ),
    ("Есть ли сезонность продаж?", "V", [{"order_date"}, {"revenue", "quantity"}]),
    ("Покажи топ городов по обороту", "V", [{"city"}, {"revenue"}]),
    ("Где больше всего недовольных клиентов?", "V", [{"rating"}, {"city", "region"}]),
    (
        "Сколько уникальных покупателей в каждом канале?",
        "R",
        [{"customer_id"}, {"channel"}],
    ),
    ("Влияет ли время доставки на оценку?", "R", [{"delivery_days"}, {"rating"}]),
    ("Сравни маркетплейс и магазин по прибыли", "X", [{"channel"}, {"profit"}]),
]

CITIES = {
    "Москва": "Центр",
    "Санкт-Петербург": "Северо-Запад",
    "Казань": "Поволжье",
    "Новосибирск": "Сибирь",
    "Екатеринбург": "Урал",
}
CATALOG = {
    "Электроника": {
        "Смартфоны": ["iPhone 15", "Galaxy S24"],
        "Ноутбуки": ["MacBook Air"],
    },
    "Одежда": {"Куртки": ["Пуховик"], "Обувь": ["Кроссовки"]},
    "Дом": {"Кухня": ["Чайник", "Сковорода"], "Текстиль": ["Плед"]},
}


def build_table(db: DuckDBManager, rows: int = 2000) -> None:
    rnd = random.Random(0)
    values = []
    for i in range(rows):
        city = rnd.choice(list(CITIES))
        category = rnd.choice(list(CATALOG))
        subcategory = rnd.choice(list(CATALOG[category]))
        product = rnd.choice(CATALOG[category][subcategory])
        ordered = date(2024, 1, 1) + timedelta(days=rnd.randrange(366))
        days = rnd.randrange(1, 9)
        qty = rnd.randrange(1, 5)
        price = rnd.choice([990, 2490, 5990, 49990, 89990])
        discount = rnd.choice([0, 0, 0.05, 0.1, 0.2])
        revenue = round(qty * price * (1 - discount), 2)
        cost = round(qty * price * 0.6, 2)
        values.append(
            (
                f"o{i:05d}",
                f"c{rnd.randrange(400):04d}",
                ordered,
                ordered + timedelta(days=rnd.randrange(0, 3)),
                CITIES[city],
                city,
                rnd.choice(["онлайн", "магазин", "маркетплейс"]),
                category,
                subcategory,
                product,
                qty,
                price,
                discount,
                revenue,
                cost,
                round(revenue - cost, 2),
                rnd.choice(["карта", "наличные", "СБП"]),
                rnd.random() < 0.07,
                rnd.randrange(1, 6),
                days,
                rnd.choice(["Иванов", "Петрова", "Сидоров", "Ким"]),
                rnd.choice(["B2B", "B2C"]),
                rnd.randrange(18, 70),
                rnd.choice(["М", "Ж"]),
                rnd.choice(["", "", "SALE10", "NEWYEAR", "VIP"]),
                rnd.choice(["Подольск", "Шушары", "Казань-1"]),
            )
        )
    names = list(TITLES)
    db.execute(
        "CREATE TABLE sales ("
        + ", ".join(
            f"{name} {kind}"
            for name, kind in zip(
                names,
                ["VARCHAR", "VARCHAR", "DATE", "DATE"]
                + ["VARCHAR"] * 6
                + ["INTEGER", "INTEGER", "DOUBLE", "DOUBLE", "DOUBLE", "DOUBLE"]
                + ["VARCHAR", "BOOLEAN", "INTEGER", "INTEGER", "VARCHAR", "VARCHAR"]
                + ["INTEGER", "VARCHAR", "VARCHAR", "VARCHAR"],
                strict=True,
            )
        )
        + ")"
    )
    con = db._con
    con.executemany(f"INSERT INTO sales VALUES ({', '.join('?' * len(names))})", values)


def covered(visible: set[str], needs: list[set[str]]) -> bool:
    return all(need & visible for need in needs)


def visible_in_prompt(text: str, columns: list[str]) -> set[str]:
    return {name for name in columns if name in text}


def evaluate(profile, columns, verbose: bool) -> None:
    def prompt_text(question: str, titles: dict | None = None) -> str:
        top = select_columns(question, columns, profile=profile, titles=titles)
        text = describe_columns(top, profile, titles=titles)
        if describe_other_columns is not None:
            rest = [name for name in columns if name not in top]
            text += "\n" + describe_other_columns(rest, profile, titles)
        return text

    modes = {
        "top5": lambda q: set(select_columns(q, columns, profile=profile)),
        "top5+titles": lambda q: set(
            select_columns(q, columns, profile=profile, titles=TITLES)
        ),
        "prompt": lambda q: visible_in_prompt(prompt_text(q), columns),
        "prompt+titles": lambda q: visible_in_prompt(prompt_text(q, TITLES), columns),
    }
    sizes = [len(prompt_text(q)) for q, _, _ in QUESTIONS]
    print(f"Блок колонок в промпте: в среднем {sum(sizes) // len(sizes)} символов")
    kinds = sorted({kind for _, kind, _ in QUESTIONS})
    print(f"Колонок в таблице: {len(columns)}, top_k={settings.semantic_top_k}")
    header = f"{'режим':<13}" + "".join(f"{k:>7}" for k in kinds) + f"{'всего':>9}"
    print(header)
    for mode, pick in modes.items():
        hits = {k: [0, 0] for k in kinds}
        misses = []
        for question, kind, needs in QUESTIONS:
            ok = covered(pick(question), needs)
            hits[kind][0] += ok
            hits[kind][1] += 1
            if not ok:
                misses.append((question, sorted(pick(question))[:8]))
        total = sum(h[0] for h in hits.values())
        print(
            f"{mode:<13}"
            + "".join(f"{h[0]:>4}/{h[1]:<2}" for h in hits.values())
            + f"{total:>6}/{len(QUESTIONS)}"
        )
        if verbose:
            for question, picked in misses:
                print(f"    ✗ {question}  -> {picked}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    db = DuckDBManager(":memory:")
    build_table(db)
    profile = profile_table(db, "sales")
    columns = [column.name for column in profile.columns]
    evaluate(profile, columns, args.verbose)


if __name__ == "__main__":
    main()
