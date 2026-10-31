"""Реплики без запроса к данным отвечаются без планировщика."""

import pytest

from app.core.agent.triage import ACK, GREETING, HELP, THANKS, small_talk_reply


@pytest.mark.parametrize(
    ("text", "reply"),
    [
        ("привет", GREETING),
        ("Привет!", GREETING),
        ("ну привет", GREETING),
        ("Здравствуйте", GREETING),
        ("добрый день", GREETING),
        ("спасибо", THANKS),
        ("Спасибо большое!", THANKS),
        ("ок", ACK),
        ("Понятно, спасибо", THANKS),
        ("отлично!", ACK),
        ("что ты умеешь?", HELP),
        ("Помощь", HELP),
    ],
)
def test_small_talk_gets_a_ready_reply(text: str, reply: str) -> None:
    assert small_talk_reply(text) == reply


@pytest.mark.parametrize(
    "text",
    [
        "привет, какая выручка по месяцам?",
        "спасибо, а теперь по регионам",
        "что ты можешь сказать о выбросах?",
        "скока всего денег",
        "есть аномалии?",
        "что за таблица вообще",
        "ок, а медиана?",
        "ок, а по месяцам?",
        "привет, сколько продаж?",
        "отлично, теперь топ-5 клиентов",
        "",
    ],
)
def test_questions_about_data_go_to_the_planner(text: str) -> None:
    assert small_talk_reply(text) is None
