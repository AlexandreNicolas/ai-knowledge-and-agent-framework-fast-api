from abc import ABC, abstractmethod


class MemoryStore(ABC):
    @abstractmethod
    async def get(self, thread_id: str) -> list[dict]:
        """Return the messages list for this thread (empty list if new)."""
        ...

    @abstractmethod
    async def save(self, thread_id: str, messages: list[dict]) -> None:
        """Persist the updated messages list for this thread."""
        ...

    @abstractmethod
    async def delete(self, thread_id: str) -> None:
        """Remove all messages for this thread."""
        ...
