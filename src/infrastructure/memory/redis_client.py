from __future__ import annotations

import os
from dataclasses import dataclass

import redis


@dataclass(frozen=True)
class RedisRuntimeConfig:
    url: str
    key_prefix: str
    session_ttl_seconds: int
    conversation_ttl_seconds: int
    state_ttl_seconds: int
    metadata_ttl_seconds: int
    catalog_ttl_seconds: int

    @classmethod
    def from_env(cls) -> "RedisRuntimeConfig":
        return cls(
            url=os.getenv(
                "REDIS_URL",
                "redis://localhost:6379/0",
            ),
            key_prefix=os.getenv(
                "REDIS_KEY_PREFIX",
                "sqlagent",
            ).strip(": "),
            session_ttl_seconds=int(
                os.getenv(
                    "REDIS_SESSION_TTL_SECONDS",
                    "28800",
                )
            ),
            conversation_ttl_seconds=int(
                os.getenv(
                    "REDIS_CONVERSATION_TTL_SECONDS",
                    "28800",
                )
            ),
            state_ttl_seconds=int(
                os.getenv(
                    "REDIS_STATE_TTL_SECONDS",
                    "3600",
                )
            ),
            metadata_ttl_seconds=int(
                os.getenv(
                    "REDIS_METADATA_TTL_SECONDS",
                    "300",
                )
            ),
            # Knowledge Catalog descriptions / curated-asset relationships
            # change far less frequently than per-query state. Fifteen minutes
            # keeps the UI responsive while still allowing edits to propagate
            # reasonably quickly during development.
            catalog_ttl_seconds=int(
                os.getenv(
                    "REDIS_CATALOG_TTL_SECONDS",
                    "900",
                )
            ),
        )


class RedisClientProvider:
    def __init__(
        self,
        config: RedisRuntimeConfig | None = None,
    ):
        self.config = config or RedisRuntimeConfig.from_env()

        self.client = redis.Redis.from_url(
            self.config.url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )

    def ping(self) -> bool:
        return bool(self.client.ping())

    def key(self, *parts: str) -> str:
        clean = [
            str(part).strip(": ")
            for part in parts
            if part is not None
            and str(part).strip(": ")
        ]
        return ":".join(
            [self.config.key_prefix, *clean]
        )