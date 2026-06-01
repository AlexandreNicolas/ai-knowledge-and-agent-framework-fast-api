from .store import MemoryStore


class InMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = {}

    async def get(self, thread_id: str) -> list[dict]:
        return list(self._data.get(thread_id, []))

    async def save(self, thread_id: str, messages: list[dict]) -> None:
        self._data[thread_id] = list(messages)

    async def delete(self, thread_id: str) -> None:
        self._data.pop(thread_id, None)
