from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .redis_client import RedisClientProvider


class _JsonRedisStore:
    def __init__(
        self,
        provider: RedisClientProvider,
        namespace: str,
        ttl_seconds: int,
    ):
        self.provider = provider
        self.redis = provider.client
        self.namespace = namespace
        self.ttl_seconds = ttl_seconds

    def _key(self, *parts: str) -> str:
        return self.provider.key(
            self.namespace,
            *parts,
        )

    def set(
        self,
        parts: tuple[str, ...],
        payload: dict[str, Any],
        ttl_seconds: int | None = None,
    ) -> None:
        value = {
            **payload,
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

        self.redis.setex(
            self._key(*parts),
            ttl_seconds or self.ttl_seconds,
            json.dumps(
                value,
                default=str,
                separators=(",", ":"),
            ),
        )

    def get(
        self,
        parts: tuple[str, ...],
    ) -> dict[str, Any] | None:
        raw = self.redis.get(
            self._key(*parts)
        )

        if raw is None:
            return None

        return json.loads(raw)

    def delete(
        self,
        parts: tuple[str, ...],
    ) -> None:
        self.redis.delete(
            self._key(*parts)
        )

    def touch(
        self,
        parts: tuple[str, ...],
    ) -> None:
        self.redis.expire(
            self._key(*parts),
            self.ttl_seconds,
        )


class SessionStore(_JsonRedisStore):
    """
    Shared, serializable session metadata only.

    Never store BigQuery client objects, SQLAlchemy engines, passwords,
    OAuth access tokens, refresh tokens, or credential objects here.
    """

    def __init__(
        self,
        provider: RedisClientProvider,
    ):
        super().__init__(
            provider,
            namespace="session",
            ttl_seconds=provider.config.session_ttl_seconds,
        )

    def save(
        self,
        session_id: str,
        payload: dict,
    ) -> None:
        self.set(
            (session_id,),
            payload,
        )

    def load(
        self,
        session_id: str,
    ) -> dict | None:
        return self.get(
            (session_id,)
        )

    def remove(
        self,
        session_id: str,
    ) -> None:
        self.delete(
            (session_id,)
        )


class AgentStateStore(_JsonRedisStore):
    def __init__(
        self,
        provider: RedisClientProvider,
    ):
        super().__init__(
            provider,
            namespace="agent",
            ttl_seconds=provider.config.state_ttl_seconds,
        )

    def save(
        self,
        session_id: str,
        payload: dict,
    ) -> None:
        self.set(
            (session_id, "state"),
            payload,
        )

    def load(
        self,
        session_id: str,
    ) -> dict | None:
        return self.get(
            (session_id, "state")
        )


class IntentStore(_JsonRedisStore):
    def __init__(
        self,
        provider: RedisClientProvider,
    ):
        super().__init__(
            provider,
            namespace="intent",
            ttl_seconds=provider.config.state_ttl_seconds,
        )

    def save(
        self,
        session_id: str,
        request_id: str,
        payload: dict,
    ) -> None:
        self.set(
            (session_id, request_id),
            payload,
        )

    def load(
        self,
        session_id: str,
        request_id: str,
    ) -> dict | None:
        return self.get(
            (session_id, request_id)
        )


class CandidateStore(_JsonRedisStore):
    def __init__(
        self,
        provider: RedisClientProvider,
    ):
        super().__init__(
            provider,
            namespace="candidate",
            ttl_seconds=provider.config.state_ttl_seconds,
        )

    def save(
        self,
        session_id: str,
        candidate_id: str,
        payload: dict,
    ) -> None:
        self.set(
            (session_id, candidate_id),
            payload,
        )

    def load(
        self,
        session_id: str,
        candidate_id: str,
    ) -> dict | None:
        return self.get(
            (session_id, candidate_id)
        )


class MetadataCacheStore(_JsonRedisStore):
    def __init__(
        self,
        provider: RedisClientProvider,
    ):
        super().__init__(
            provider,
            namespace="metadata",
            ttl_seconds=provider.config.metadata_ttl_seconds,
        )

    def save(
        self,
        project: str,
        dataset: str,
        table: str,
        payload: dict,
    ) -> None:
        self.set(
            (project, dataset, table),
            payload,
        )

    def load(
        self,
        project: str,
        dataset: str,
        table: str,
    ) -> dict | None:
        return self.get(
            (project, dataset, table)
        )