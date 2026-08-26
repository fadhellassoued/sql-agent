from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from infrastructure.memory.redis_client import (
    RedisClientProvider,
)


class KnowledgeCatalogAlternativeCache:
    """
    Redis-backed cache for Knowledge Catalog alternative discovery.

    Key shape:
        sqlagent:catalog:alternatives:{project}:{dataset}:{source_table}

    The payload is semantic/catalog metadata only. It must never contain:
    - OAuth credentials or access/refresh tokens;
    - BigQuery client objects;
    - SQLAlchemy engines;
    - executable SQL candidates.

    Cache failures are expected to be handled as soft failures by callers.
    """

    def __init__(
        self,
        provider: RedisClientProvider | None = None,
    ):
        self.provider = (
            provider
            or RedisClientProvider()
        )

        self.redis = (
            self.provider.client
        )

        self.ttl_seconds = (
            self.provider.config
            .catalog_ttl_seconds
        )

    def _key(
        self,
        *,
        project_id: str,
        dataset_id: str,
        source_table_id: str,
    ) -> str:
        return self.provider.key(
            "catalog",
            "alternatives",
            project_id,
            dataset_id,
            source_table_id,
        )

    def load(
        self,
        *,
        project_id: str,
        dataset_id: str,
        source_table_id: str,
    ) -> dict[str, Any] | None:
        raw = self.redis.get(
            self._key(
                project_id=project_id,
                dataset_id=dataset_id,
                source_table_id=source_table_id,
            )
        )

        if raw is None:
            return None

        payload = json.loads(
            raw
        )

        if not isinstance(
            payload,
            dict,
        ):
            return None

        # Cache metadata is useful operationally but should not replace
        # Knowledge Catalog evidence fields.
        payload["cache_status"] = "HIT"

        return payload

    def save(
        self,
        *,
        project_id: str,
        dataset_id: str,
        source_table_id: str,
        payload: dict[str, Any],
    ) -> None:
        value = {
            **payload,
            "cached_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),
        }

        # cache_status describes the current request and should not be
        # persisted as if it were Knowledge Catalog evidence.
        value.pop(
            "cache_status",
            None,
        )

        self.redis.setex(
            self._key(
                project_id=project_id,
                dataset_id=dataset_id,
                source_table_id=source_table_id,
            ),
            self.ttl_seconds,
            json.dumps(
                value,
                default=str,
                separators=(",", ":"),
            ),
        )

    def delete(
        self,
        *,
        project_id: str,
        dataset_id: str,
        source_table_id: str,
    ) -> None:
        self.redis.delete(
            self._key(
                project_id=project_id,
                dataset_id=dataset_id,
                source_table_id=source_table_id,
            )
        )