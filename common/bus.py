import copy
import json
import threading
from collections import deque
from typing import Callable, Protocol

Handler = Callable[[str, dict], None]


def topic_matches(pattern: str, topic: str) -> bool:
    p_levels = pattern.split("/")
    t_levels = topic.split("/")
    if "#" in p_levels[:-1]:
        return False
    for i, p in enumerate(p_levels):
        if p == "#":
            return True
        if i >= len(t_levels):
            return False
        if p != "+" and p != t_levels[i]:
            return False
    return len(p_levels) == len(t_levels)


class Bus(Protocol):
    def publish(self, topic: str, payload: dict, sender: str) -> bool: ...

    def subscribe(self, pattern: str, handler: Handler, client_id: str) -> None: ...

    def set_link(self, client_id: str, up: bool) -> None: ...

    def is_link_up(self, client_id: str) -> bool: ...


class InMemoryBus:
    def __init__(self) -> None:
        self._subs: list[tuple[str, Handler, str]] = []
        self._links: dict[str, bool] = {}
        self._queue: deque[tuple[str, dict]] = deque()
        self._delivering = False

    def set_link(self, client_id: str, up: bool) -> None:
        self._links[client_id] = up

    def is_link_up(self, client_id: str) -> bool:
        return self._links.get(client_id, True)

    def subscribe(self, pattern: str, handler: Handler, client_id: str) -> None:
        self._subs.append((pattern, handler, client_id))

    def publish(self, topic: str, payload: dict, sender: str) -> bool:
        if not self.is_link_up(sender):
            return False
        self._queue.append((topic, payload))
        if not self._delivering:
            self._drain()
        return True

    def _drain(self) -> None:
        self._delivering = True
        try:
            while self._queue:
                topic, payload = self._queue.popleft()
                for pattern, handler, client_id in list(self._subs):
                    if self.is_link_up(client_id) and topic_matches(pattern, topic):
                        handler(topic, copy.deepcopy(payload))
        finally:
            self._delivering = False


class MqttBus:
    def __init__(self, host: str = "localhost", port: int = 1883, client_name: str = "aiot") -> None:
        import paho.mqtt.client as mqtt

        self._host = host
        self._port = port
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_name)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._subs: list[tuple[str, Handler, str]] = []
        self._links: dict[str, bool] = {}
        # 回调运行在 paho 网络线程，订阅表与链路标志需加锁
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._client.connect(self._host, self._port)
        self._client.loop_start()

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def set_link(self, client_id: str, up: bool) -> None:
        with self._lock:
            self._links[client_id] = up

    def is_link_up(self, client_id: str) -> bool:
        with self._lock:
            return self._links.get(client_id, True)

    def subscribe(self, pattern: str, handler: Handler, client_id: str) -> None:
        with self._lock:
            self._subs.append((pattern, handler, client_id))
        if self._client.is_connected():
            self._client.subscribe(pattern)

    def publish(self, topic: str, payload: dict, sender: str) -> bool:
        if not self.is_link_up(sender):
            return False
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        self._client.publish(topic, data.encode("utf-8"))
        return True

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        with self._lock:
            patterns = list(dict.fromkeys(p for p, _, _ in self._subs))
        for pattern in patterns:
            client.subscribe(pattern)

    def _on_message(self, client, userdata, message) -> None:
        data = message.payload.decode("utf-8")
        with self._lock:
            targets = [
                handler
                for pattern, handler, client_id in self._subs
                if self._links.get(client_id, True) and topic_matches(pattern, message.topic)
            ]
        for handler in targets:
            handler(message.topic, json.loads(data))
