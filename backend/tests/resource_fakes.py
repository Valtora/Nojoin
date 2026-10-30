"""External Redis/Docker/NVML edges for resource monitor tests, never services."""

import fnmatch
import threading
from types import SimpleNamespace


class MemoryRedis:
    def __init__(self) -> None:
        self.now = 0
        self.entries: dict = {}
        self.lengths = {"gpu": 2, "gpu\x06\x163": 3, "gpu\x06\x169": 1}
        self.mutex = threading.Lock()

    def set(self, key: str, value: str, ex: int) -> None:
        self.entries[key] = (value, self.now + ex)

    def get(self, key: str) -> str | None:
        value, expiry = self.entries.get(key, (None, 0))
        return value if self.now < expiry else None

    def scan_iter(self, match: str) -> list[str]:
        return [k for k in self.entries if fnmatch.fnmatch(k, match) and self.get(k)]

    def llen(self, key: str) -> int:
        return self.lengths.get(key, 0)

    def lock(self, *args: object, **kwargs: object) -> threading.Lock:
        return self.mutex


class DockerContainer:
    def __init__(self, project: str) -> None:
        self.labels = {"com.docker.compose.project": project}
        self.reads = 0

    def stats(self, stream: bool) -> dict:
        self.reads += 1
        return {
            "cpu_stats": {"cpu_usage": {"total_usage": 500}, "system_cpu_usage": 4000},
            "precpu_stats": {
                "cpu_usage": {"total_usage": 100},
                "system_cpu_usage": 2000,
            },
            "memory_stats": {"usage": 1024**3},
        }


class DockerContainers:
    def __init__(self) -> None:
        self.containers = [
            DockerContainer("nojoin"),
            DockerContainer("nojoin"),
            DockerContainer("other"),
        ]

    def get(self, name: str) -> DockerContainer:
        return self.containers[0]

    def list(self, filters: dict) -> list[DockerContainer]:
        expected = filters["label"].split("=", 1)[1]
        return [
            c
            for c in self.containers
            if c.labels["com.docker.compose.project"] == expected
        ]


def nvml() -> SimpleNamespace:
    return SimpleNamespace(
        NVMLError=RuntimeError,
        nvmlInit=lambda: None,
        nvmlShutdown=lambda: None,
        # Duplicate handles must produce one UUID, not two GPU graphs.
        nvmlDeviceGetCount=lambda: 2,
        nvmlDeviceGetHandleByIndex=lambda index: index,
        nvmlDeviceGetUUID=lambda handle: "GPU-example",
        nvmlDeviceGetName=lambda handle: "Example GPU",
        nvmlDeviceGetMemoryInfo=lambda handle: SimpleNamespace(
            used=2 * 1024**3, total=8 * 1024**3
        ),
        nvmlDeviceGetUtilizationRates=lambda handle: SimpleNamespace(gpu=75),
    )
