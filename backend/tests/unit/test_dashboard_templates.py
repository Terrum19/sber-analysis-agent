import pytest

from app.core.dashboards.templates import TEMPLATES, match, role_columns

HK_HEADERS = {
    "01_Продажи.csv": "Код позиции;Код заказа;Номер позиции;Дата операции (МСК);Код покупателя;Уровень программы лояльности;Город;Регион;Федеральный округ;Канал продажи;Код магазина;Артикул;Категория товара;Товар;Количество, шт.;Цена до скидки за единицу, руб.;Скидка, %;Себестоимость единицы, руб.;Способ оплаты;Тип покупателя;Тип операции;Код исходной позиции",
    "02_Техническая_поддержка.csv": "Код обращения;Создано (МСК);Первый ответ (МСК);Закрыто (МСК);Код клиента;Город;Регион;Канал обращения;Тема;Заголовок;Приоритет;Группа поддержки;Код оператора;Статус;Норматив решения, ч;Повторные открытия;Оценка клиента;Код предыдущего обращения",
    "03_Задачи_команд.csv": "Код задачи;Название задачи;Направление;Тип задачи;Команда;Город;Код исполнителя;Приоритет;Статус;Создано (МСК);Начало работы (МСК);Срок выполнения (МСК);Завершено (МСК);Обновлено (МСК);Плановые трудозатраты, ч;Фактические трудозатраты, ч",
    "04_Доставка.csv": "Код отправления;Принято (МСК);Передано перевозчику (МСК);Обещанный срок (МСК);Доставлено (МСК);Возвращено отправителю (МСК);Город отправления;Регион отправления;Город получения;Регион получения;Перевозчик;Способ доставки;Вес, кг;Стоимость доставки, руб.;Статус;Попытки вручения;Причина отмены или возврата",
    "05_События_приложения.csv": "Код события;Время события (МСК);Код пользователя;Код сессии;Номер события в сессии;Город;Регион;Платформа;Версия приложения;Источник сессии;Событие;Экран;Время обработки, мс;Товаров в корзине, шт.;Сумма оплаты, руб.;Текст ошибки",
    "06_Рекламные_кампании.csv": "Код записи;Дата;Код кампании;Название кампании;Площадка;Категория товаров;Аудитория;Город;Регион;Показы;Клики;Расходы, руб.;Заказы по рекламе;Выручка по рекламе, руб.",
    "07_Заявки_на_закупку.csv": "Код заявки;Подана (МСК);Подразделение;Город;Регион;Категория закупки;Предмет закупки;Код инициатора;Код поставщика;Срочность;Запрошенная сумма, руб.;Согласованная сумма, руб.;Оплаченная сумма, руб.;Требуется к дате;Согласована (МСК);Отклонена (МСК);Отменена (МСК);Исполнена (МСК);Оплачена (МСК);Статус;Причина отклонения",
    "08_Фоновые_задания.csv": "Код запуска;Код цепочки запусков;Задание;Сервис;Окружение;Способ запуска;Номер попытки;Начало (МСК);Окончание (МСК);Статус;Записей к обработке;Обработано записей;Код ошибки;Описание ошибки;Узел выполнения",
    "09_Лекарственные_препараты.csv": "Дата;ID_Аптеки;ID_Лекарства;Тип_Лекарства;Действующее_Вещество;Сколько_Купили",
}
EXPECTED = {
    "01_Продажи.csv": "sales_quarterly",
    "02_Техническая_поддержка.csv": "support_tickets",
    "03_Задачи_команд.csv": None,
    "04_Доставка.csv": "delivery",
    "05_События_приложения.csv": "app_events",
    "06_Рекламные_кампании.csv": None,
    "07_Заявки_на_закупку.csv": None,
    "08_Фоновые_задания.csv": None,
    "09_Лекарственные_препараты.csv": None,
}


def columns(file: str) -> list[str]:
    return HK_HEADERS[file].split(";")


def test_sales_columns_match_sales_template() -> None:
    assert match(["order_date", "order_id", "product", "quantity", "price"]).id == "sales_quarterly"


def test_passport_alias_replaces_column_name() -> None:
    names = ["Дата", "Номер заказа", "Наименование", "Штук", "Цена за шт"]
    aliases = {
        "Дата": ["дата операции"],
        "Номер заказа": ["order_id"],
        "Наименование": ["товар"],
        "Штук": ["количество"],
        "Цена за шт": ["price"],
    }
    assert match(names) is None
    assert match(names, aliases).id == "sales_quarterly"


def test_foreign_columns_match_nothing() -> None:
    assert match(["planet", "orbit", "mass"]) is None


def test_matching_ignores_case_and_whitespace() -> None:
    assert (
        match(["  ORDER_DATE", "Order_Id ", "PRODUCT", "Quantity", "PRICE"]).id == "sales_quarterly"
    )


@pytest.mark.parametrize("file", sorted(HK_HEADERS))
def test_real_hackathon_headers(file: str) -> None:
    template = match(columns(file))
    assert (template.id if template else None) == EXPECTED[file]


def test_byte_order_mark_on_first_column_is_ignored() -> None:
    raw = [
        "\ufeff" + column if index == 0 else column
        for index, column in enumerate(columns("04_Доставка.csv"))
    ]
    assert match(raw).id == "delivery"


def test_role_columns_name_the_real_columns() -> None:
    template = next(template for template in TEMPLATES if template.id == "delivery")
    assert role_columns(template, columns("04_Доставка.csv")) == {
        "shipment": "Код отправления",
        "accepted": "Принято (МСК)",
        "promised": "Обещанный срок (МСК)",
        "delivered": "Доставлено (МСК)",
        "carrier": "Перевозчик",
    }


def test_template_with_more_roles_wins() -> None:
    sales = ["order_date", "order_id", "product", "quantity", "price"]
    events = ["event_time", "user_id", "session_id", "event_name"]
    assert match(events + sales).id == "sales_quarterly"
    assert match(events).id == "app_events"


def test_templates_are_well_formed() -> None:
    assert len({template.id for template in TEMPLATES}) == len(TEMPLATES) == 4
    for template in TEMPLATES:
        assert template.tiles
        for tile in template.tiles:
            assert tile.is_kpi == (tile.chart_type == "kpi")
