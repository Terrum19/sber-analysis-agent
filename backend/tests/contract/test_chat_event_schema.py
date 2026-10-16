"""Contract-тесты: ChatEvent схема."""

import pytest

from app.api.schemas.chat import ChatEvent


@pytest.mark.parametrize("event_type", ["text", "chart", "dashboard", "error", "done"])
def test_chat_event_all_types(event_type):
    event = ChatEvent(type=event_type, data={})
    assert event.type == event_type
    assert event.data == {}


def test_chat_event_with_data():
    event = ChatEvent(type="chart", data={"spec": {"mark": "bar"}, "caption": "Тест"})
    assert event.type == "chart"
    assert event.data["spec"]["mark"] == "bar"
    assert event.data["caption"] == "Тест"


def test_chat_event_serialization_compact():
    event = ChatEvent(type="text", data={"headline": "Тест"})
    json_str = event.model_dump_json()
    assert '"type":"text"' in json_str
    assert '"headline":"Тест"' in json_str
