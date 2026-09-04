"""Small in-process event stream for household-scale real-time UI updates."""
import queue
import threading
from datetime import datetime, timezone


class EventHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()
        self._next_id = 1

    def subscribe(self) -> queue.Queue:
        subscriber = queue.Queue(maxsize=32)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def publish(self, event_type: str, resource_id: str = "") -> dict:
        with self._lock:
            event = {
                "id": self._next_id,
                "type": event_type,
                "resource_id": resource_id,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            self._next_id += 1
            for subscriber in tuple(self._subscribers):
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    try:
                        subscriber.get_nowait()
                        subscriber.put_nowait(event)
                    except queue.Empty:
                        pass
            return event
