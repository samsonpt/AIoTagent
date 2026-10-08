import os
import threading

import pytest

from common.bus import InMemoryBus, MqttBus, topic_matches


@pytest.mark.parametrize(
    ("pattern", "topic", "expected"),
    [
        ("plant/+/DRL-01/telemetry", "plant/drill/DRL-01/telemetry", True),
        ("plant/+/DRL-01/telemetry", "plant/drill/x/DRL-01/telemetry", False),
        ("plant/+/DRL-01/telemetry", "plant/drill/PLT-01/telemetry", False),
        ("plant/#", "plant/drill/DRL-01/telemetry", True),
        ("plant/#", "plant", True),
        ("plant/#", "other/drill", False),
        ("plant/events/#", "plant/events", True),
        ("plant/events/#", "plant/events/etch", True),
        ("plant/events/#", "plant/intents/etch", False),
        ("#", "plant/events/etch", True),
        ("plant/#/etch", "plant/events/etch", False),
        ("plant/events/etch", "plant/events/etch", True),
        ("plant/events/etch", "plant/events/drill", False),
        ("plant/events", "plant/events/etch", False),
    ],
)
def test_topic_matches(pattern, topic, expected):
    assert topic_matches(pattern, topic) is expected


def test_delivery_follows_subscription_order():
    bus = InMemoryBus()
    log = []
    bus.subscribe("plant/#", lambda t, p: log.append(("a", t, p["n"])), "a")
    bus.subscribe("plant/events/+", lambda t, p: log.append(("b", t, p["n"])), "b")
    bus.subscribe("other/#", lambda t, p: log.append(("c", t, p["n"])), "c")

    assert bus.publish("plant/events/etch", {"n": 1}, "src") is True
    assert bus.publish("plant/drill/DRL-01/telemetry", {"n": 2}, "src") is True

    assert log == [
        ("a", "plant/events/etch", 1),
        ("b", "plant/events/etch", 1),
        ("a", "plant/drill/DRL-01/telemetry", 2),
    ]


def test_republish_in_handler_is_queued_not_recursive():
    bus = InMemoryBus()
    log = []

    def first(topic, payload):
        log.append(("first", topic))
        if topic == "x/1":
            bus.publish("x/2", {}, "first")
            log.append(("first-after-publish", topic))

    def second(topic, payload):
        log.append(("second", topic))

    bus.subscribe("x/+", first, "first")
    bus.subscribe("x/+", second, "second")
    bus.publish("x/1", {}, "src")

    assert log == [
        ("first", "x/1"),
        ("first-after-publish", "x/1"),
        ("second", "x/1"),
        ("first", "x/2"),
        ("second", "x/2"),
    ]


def test_sender_link_down_returns_false_and_nobody_receives():
    bus = InMemoryBus()
    received = []
    bus.subscribe("#", lambda t, p: received.append(t), "rx")

    assert bus.is_link_up("tx") is True
    bus.set_link("tx", False)
    assert bus.is_link_up("tx") is False
    assert bus.publish("a/b", {}, "tx") is False
    assert received == []


def test_receiver_link_down_drops_then_recovers():
    bus = InMemoryBus()
    received = []
    bus.subscribe("#", lambda t, p: received.append(p["n"]), "rx")

    bus.set_link("rx", False)
    assert bus.publish("a/b", {"n": 1}, "tx") is True
    assert received == []

    bus.set_link("rx", True)
    bus.publish("a/b", {"n": 2}, "tx")
    assert received == [2]


def test_handlers_receive_deep_copies():
    bus = InMemoryBus()
    payload = {"nested": {"values": [1, 2]}}
    seen = []

    def mutator(topic, p):
        p["nested"]["values"].append(99)

    bus.subscribe("t", mutator, "m")
    bus.subscribe("t", lambda t, p: seen.append(p), "s")
    bus.publish("t", payload, "src")

    assert seen == [{"nested": {"values": [1, 2]}}]
    assert payload == {"nested": {"values": [1, 2]}}


def test_handler_exception_propagates():
    bus = InMemoryBus()

    def boom(topic, payload):
        raise RuntimeError("boom")

    bus.subscribe("t", boom, "x")
    with pytest.raises(RuntimeError, match="boom"):
        bus.publish("t", {}, "src")


@pytest.mark.skipif("AIOT_MQTT_HOST" not in os.environ, reason="需要设置 AIOT_MQTT_HOST 指向 MQTT broker")
def test_mqtt_bus_roundtrip():
    bus = MqttBus(host=os.environ["AIOT_MQTT_HOST"], client_name="aiot-test")
    got = threading.Event()
    received = []

    def handler(topic, payload):
        received.append((topic, payload))
        got.set()

    bus.connect()
    try:
        bus.subscribe("aiot-test/+/ping", handler, "rx")
        threading.Event().wait(0.5)
        assert bus.publish("aiot-test/x/ping", {"n": 1, "s": "中文"}, "tx") is True
        assert got.wait(5.0)
        assert received == [("aiot-test/x/ping", {"n": 1, "s": "中文"})]

        bus.set_link("tx", False)
        assert bus.publish("aiot-test/x/ping", {"n": 2}, "tx") is False
    finally:
        bus.close()
