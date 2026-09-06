"""Pure-function tests for api.py - no live panel or HA test harness
needed.
"""

from custom_components.bentel_absoluta.api import parse_longpoll_chunk


def test_single_event():
    text = '{"eventType":"pageChanged","pageData":{"globalArmingStatus":true},"pageName":"main"}'
    events = parse_longpoll_chunk(text)
    assert len(events) == 1
    assert events[0]["pageName"] == "main"
    assert events[0]["pageData"]["globalArmingStatus"] is True


def test_multiple_events_in_one_chunk():
    """A single long-poll response can contain multiple newline-separated
    JSON objects at once (observed up to 3)."""
    text = (
        '{"eventType":"pageChanged","pageData":{"globalArmingStatus":true},"pageName":"main"}\n'
        '{"eventType":"pageChanged","pageData":{"armingStatus":[2,0,0]},"pageName":"status"}\n'
    )
    events = parse_longpoll_chunk(text)
    assert len(events) == 2
    assert events[0]["pageName"] == "main"
    assert events[1]["pageName"] == "status"
    assert events[1]["pageData"]["armingStatus"] == [2, 0, 0]


def test_device_error_wrong_pin():
    text = '{"errorType":"wrongPin","eventType":"deviceError","extra":"ShutdownHandler 42: Negative acknowledge received: 6"}'
    events = parse_longpoll_chunk(text)
    assert len(events) == 1
    assert events[0]["eventType"] == "deviceError"
    assert events[0]["errorType"] == "wrongPin"


def test_blank_lines_are_skipped():
    text = "\n\n{\"eventType\":\"heartBeat\"}\n\n"
    events = parse_longpoll_chunk(text)
    assert len(events) == 1
    assert events[0]["eventType"] == "heartBeat"


def test_unparseable_line_is_skipped_not_fatal():
    text = 'not json\n{"eventType":"heartBeat"}'
    events = parse_longpoll_chunk(text)
    assert len(events) == 1
