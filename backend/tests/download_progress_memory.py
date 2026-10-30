"""The Redis progress edge, including atomic snapshots and transactions."""

from threading import RLock


class MemoryProgressRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.lock = RLock()

    def get(self, key: str) -> str | None:
        with self.lock:
            return self.store.get(key)

    def delete(self, key: str) -> None:
        with self.lock:
            self.store.pop(key, None)

    def mget(self, keys: list[str]) -> list[str | None]:
        with self.lock:
            return [self.store.get(key) for key in keys]

    def pipeline(self) -> "ProgressTransaction":
        return ProgressTransaction(self)


class ProgressTransaction:
    def __init__(self, client: MemoryProgressRedis) -> None:
        self.client = client
        self.pending: dict[str, str | None] = {}

    def __enter__(self) -> "ProgressTransaction":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def delete(self, key: str) -> None:
        self.pending[key] = None

    def set(self, key: str, value: str, **_options: object) -> None:
        self.pending[key] = value

    def execute(self) -> None:
        with self.client.lock:
            for key, value in self.pending.items():
                if value is None:
                    self.client.store.pop(key, None)
                else:
                    self.client.store[key] = value
