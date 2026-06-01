def trim_messages(messages: list[dict], max_messages: int = 20) -> list[dict]:
    """Keep the last max_messages turns, always preserving complete user/assistant pairs."""
    if len(messages) <= max_messages:
        return messages
    keep = max_messages if max_messages % 2 == 0 else max_messages - 1
    return messages[-keep:]
