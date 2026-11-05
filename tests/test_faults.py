from __future__ import annotations


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, key: str, value: object, ex: int | None = None) -> bool:
        self.store[key] = str(value)
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def decr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) - 1
        self.store[key] = str(value)
        return value

    def scan_iter(self, pattern: str):
        prefix = pattern.rstrip("*")
        return [key for key in self.store if key.startswith(prefix)]


def test_faults_without_redis(monkeypatch):
    from app.services import faults

    monkeypatch.setattr(faults, "client", lambda: None)
    assert faults.set_fault("ocr", 1) == 1
    assert faults.consume_fault("ocr") is False
    assert faults.current_faults() == {}


def test_faults_with_fake_redis(monkeypatch):
    from app.services import faults

    fake = FakeRedis()
    monkeypatch.setattr(faults, "client", lambda: fake)
    assert faults.set_fault("ocr", 2) == 2
    assert faults.current_faults() == {"ocr": 2}
    assert faults.consume_fault("ocr") is True
    assert faults.consume_fault("ocr") is True
    assert faults.consume_fault("ocr") is False
