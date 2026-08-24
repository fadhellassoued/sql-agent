from __future__ import annotations

import json
from typing import Any

from .redis_client import RedisClientProvider


class ConversationStore:
    def __init__(
        self,
        provider: RedisClientProvider,
        max_messages: int = 40,
    ):
        self.provider = provider
        self.redis = provider.client
        self.max_messages = max_messages
        self.ttl_seconds = (
            provider.config.conversation_ttl_seconds
        )

    def _key(
        self,
        session_id: str,
    ) -> str:
        return self.provider.key(
            "conversation",
            session_id,
        )

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        if not content:
            return

        key = self._key(
            session_id
        )

        payload = json.dumps(
            {
                "role": role,
                "content": content,
            },
            separators=(",", ":"),
        )

        pipe = self.redis.pipeline()

        pipe.rpush(
            key,
            payload,
        )

        pipe.ltrim(
            key,
            -self.max_messages,
            -1,
        )

        pipe.expire(
            key,
            self.ttl_seconds,
        )

        pipe.execute()

    def list_messages(
        self,
        session_id: str,
    ) -> list[dict[str, Any]]:
        key = self._key(
            session_id
        )

        raw_items = self.redis.lrange(
            key,
            0,
            -1,
        )

        if raw_items:
            self.redis.expire(
                key,
                self.ttl_seconds,
            )

        return [
            json.loads(item)
            for item in raw_items
        ]

    def clear(
        self,
        session_id: str,
    ) -> None:
        self.redis.delete(
            self._key(
                session_id
            )
        )