from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
from typing import Any

from google.cloud import dataplex_v1


@dataclass
class CatalogEntryEvidence:
    entry_name: str
    fully_qualified_name: str | None
    entry_type: str | None
    display_name: str | None
    description: str | None
    system: str | None
    location: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class KnowledgeCatalogClient:
    """
    Read-only Knowledge Catalog client.

    Knowledge Catalog was formerly called Dataplex Universal Catalog.
    The Google Cloud Python API still uses `google.cloud.dataplex_v1`.

    Responsibility:
        - search catalog entries;
        - resolve catalog context for BigQuery assets;
        - discover controlled related/curated BigQuery assets;
        - return serializable evidence only.

    It does NOT:
        - generate SQL;
        - approve optimization candidates;
        - replace BigQuery metadata as physical truth;
        - claim semantic equivalence between discovered assets.
    """

    def __init__(
        self,
        *,
        consumer_project_id: str,
    ):
        self.consumer_project_id = (
            consumer_project_id
        )

        self._client = (
            dataplex_v1.CatalogServiceClient()
        )

    @property
    def search_parent(self) -> str:
        return (
            f"projects/"
            f"{self.consumer_project_id}/"
            f"locations/global"
        )

    @property
    def search_scope(self) -> str:
        return (
            f"projects/"
            f"{self.consumer_project_id}"
        )

    # ============================================================
    # PUBLIC API
    # ============================================================

    async def find_bigquery_table(
        self,
        *,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> dict[str, Any]:
        """
        Look for a specific BigQuery table in Knowledge Catalog.

        Search is deliberately narrow because the optimizer already
        knows which table the SQL references.
        """

        return await asyncio.to_thread(
            self._find_bigquery_table_sync,
            project_id,
            dataset_id,
            table_id,
        )

    async def search_bigquery_tables(
        self,
        *,
        project_id: str,
        dataset_id: str,
        search_text: str | None = None,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """
        Search BigQuery table entries inside one project + dataset.

        This is intentionally dataset-scoped so the optimizer does not
        perform broad enterprise-wide discovery.

        It can be used for controlled alternative-asset discovery,
        such as looking for curated, reporting, summary, or
        pre-aggregated tables.
        """

        return await asyncio.to_thread(
            self._search_bigquery_tables_sync,
            project_id,
            dataset_id,
            search_text,
            page_size,
        )

    async def find_preaggregated_candidates(
        self,
        *,
        project_id: str,
        dataset_id: str,
        source_table_id: str,
    ) -> dict[str, Any]:
        """
        Search the current dataset for plausible curated or
        pre-aggregated analytical assets related to a source table.

        Candidate discovery is intentionally conservative.

        A result is only a recommendation candidate. This method does NOT:
            - prove semantic equivalence;
            - generate replacement SQL;
            - approve a rewrite.
        """

        result = await self.search_bigquery_tables(
            project_id=project_id,
            dataset_id=dataset_id,
            search_text=None,
            page_size=30,
        )

        entries = (
            result.get(
                "entries",
                [],
            )
            or []
        )

        source_fqn = (
            f"bigquery:"
            f"{project_id}."
            f"{dataset_id}."
            f"{source_table_id}"
        )

        source_qualified_name = (
            f"{dataset_id}."
            f"{source_table_id}"
        ).lower()

        source_table_lower = (
            source_table_id.lower()
        )

        strong_keywords = {
            "aggregate",
            "aggregated",
            "curated",
            "materialized",
            "pre-aggregated",
            "preaggregated",
            "reporting",
            "summary",
        }

        supporting_keywords = {
            "analytical",
            "daily",
            "preferred",
        }

        candidates = []

        for entry in entries:
            if (
                entry.get(
                    "fully_qualified_name"
                )
                ==
                source_fqn
            ):
                continue

            description = (
                entry.get(
                    "description"
                )
                or ""
            ).strip()

            display_name = (
                entry.get(
                    "display_name"
                )
                or ""
            ).strip()

            searchable_text = (
                f"{display_name} "
                f"{description}"
            ).lower()

            matched_strong = sorted(
                keyword
                for keyword
                in strong_keywords
                if keyword
                in searchable_text
            )

            matched_supporting = sorted(
                keyword
                for keyword
                in supporting_keywords
                if keyword
                in searchable_text
            )

            references_source = (
                source_qualified_name
                in searchable_text
                or
                source_table_lower
                in searchable_text
            )

            if not matched_strong:
                continue

            relevance_score = (
                len(matched_strong) * 3
                + len(matched_supporting)
                + (5 if references_source else 0)
            )

            candidates.append(
                {
                    **entry,
                    "matched_keywords":
                        sorted(
                            set(
                                matched_strong
                                +
                                matched_supporting
                            )
                        ),
                    "references_source":
                        references_source,
                    "relevance_score":
                        relevance_score,
                }
            )

        candidates.sort(
            key=lambda item: (
                item.get(
                    "relevance_score",
                    0,
                ),
                bool(
                    item.get(
                        "references_source"
                    )
                ),
            ),
            reverse=True,
        )

        return {
            "status":
                "FOUND"
                if candidates
                else "NO_MATCHES",
            "source_table":
                source_fqn,
            "project_id":
                project_id,
            "dataset_id":
                dataset_id,
            "candidates":
                candidates,
            "candidate_count":
                len(
                    candidates
                ),
            "source":
                "knowledge_catalog",
        }

    async def health_check(
        self,
    ) -> dict[str, Any]:
        """
        Lightweight API connectivity test.
        """

        return await asyncio.to_thread(
            self._health_check_sync
        )

    # ============================================================
    # SYNCHRONOUS GOOGLE CLIENT OPERATIONS
    # ============================================================

    def _find_bigquery_table_sync(
        self,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> dict[str, Any]:

        expected_fqn = (
            f"bigquery:"
            f"{project_id}."
            f"{dataset_id}."
            f"{table_id}"
        )

        # Keep exact-table lookup highly constrained.
        query = (
            "system=bigquery "
            "type=table "
            f"projectid:{project_id} "
            f"parent:{dataset_id} "
            f"name:{table_id}"
        )

        request = (
            dataplex_v1.SearchEntriesRequest(
                name=self.search_parent,
                scope=self.search_scope,
                query=query,
                page_size=10,
                semantic_search=False,
            )
        )

        response = (
            self._client.search_entries(
                request=request
            )
        )

        candidates = []

        for result in response:
            entry = (
                result.dataplex_entry
            )

            evidence = (
                self._entry_to_evidence(
                    entry
                )
            )

            candidates.append(
                evidence
            )

        exact_match = None

        for candidate in candidates:
            if (
                candidate.get(
                    "fully_qualified_name"
                )
                ==
                expected_fqn
            ):
                exact_match = (
                    candidate
                )
                break

        if exact_match is None:
            return {
                "status":
                    "NOT_FOUND",

                "expected_fqn":
                    expected_fqn,

                "entry":
                    None,

                "candidate_count":
                    len(
                        candidates
                    ),

                "source":
                    "knowledge_catalog",
            }

        return {
            "status":
                "FOUND",

            "expected_fqn":
                expected_fqn,

            "entry":
                exact_match,

            "candidate_count":
                len(
                    candidates
                ),

            "source":
                "knowledge_catalog",
        }

    def _search_bigquery_tables_sync(
        self,
        project_id: str,
        dataset_id: str,
        search_text: str | None,
        page_size: int,
    ) -> dict[str, Any]:
        """
        Synchronous implementation of dataset-scoped BigQuery
        Knowledge Catalog search.
        """

        safe_page_size = max(
            1,
            min(
                int(
                    page_size
                    or 20
                ),
                50,
            ),
        )

        query_parts = [
            "system=bigquery",
            "type=table",
            f"projectid:{project_id}",
            f"parent:{dataset_id}",
        ]

        if search_text:
            cleaned = (
                str(
                    search_text
                )
                .strip()
                .replace(
                    '"',
                    "",
                )
            )

            if cleaned:
                # Keep free-text matching optional.
                query_parts.append(
                    f'"{cleaned}"'
                )

        query = " ".join(
            query_parts
        )

        request = (
            dataplex_v1.SearchEntriesRequest(
                name=self.search_parent,
                scope=self.search_scope,
                query=query,
                page_size=
                    safe_page_size,
                semantic_search=False,
            )
        )

        response = (
            self._client.search_entries(
                request=request
            )
        )

        entries = []

        expected_prefix = (
            f"bigquery:"
            f"{project_id}."
            f"{dataset_id}."
        )

        for result in response:
            entry = (
                result.dataplex_entry
            )

            evidence = (
                self._entry_to_evidence(
                    entry
                )
            )

            fqn = (
                evidence.get(
                    "fully_qualified_name"
                )
                or ""
            )

            # Defense in depth:
            # only keep entries in the exact requested BigQuery dataset.
            if not fqn.startswith(
                expected_prefix
            ):
                continue

            entries.append(
                evidence
            )

            if len(entries) >= safe_page_size:
                break

        return {
            "status":
                "FOUND"
                if entries
                else "NO_MATCHES",

            "project_id":
                project_id,

            "dataset_id":
                dataset_id,

            "search_text":
                search_text,

            "entries":
                entries,

            "candidate_count":
                len(
                    entries
                ),

            "source":
                "knowledge_catalog",
        }

    def _health_check_sync(
        self,
    ) -> dict[str, Any]:

        request = (
            dataplex_v1.SearchEntriesRequest(
                name=self.search_parent,
                scope=self.search_scope,
                query=(
                    "system=bigquery "
                    "type=table"
                ),
                page_size=1,
                semantic_search=False,
            )
        )

        response = (
            self._client.search_entries(
                request=request
            )
        )

        # Trigger the request without loading the catalog.
        iterator = iter(
            response
        )

        try:
            next(
                iterator
            )
            reachable = True

        except StopIteration:
            reachable = True

        return {
            "status":
                "AVAILABLE"
                if reachable
                else "UNAVAILABLE",

            "project_id":
                self.consumer_project_id,
        }

    # ============================================================
    # SERIALIZATION
    # ============================================================

    @staticmethod
    def _entry_to_evidence(
        entry: Any,
    ) -> dict[str, Any]:

        entry_source = getattr(
            entry,
            "entry_source",
            None,
        )

        display_name = (
            getattr(
                entry_source,
                "display_name",
                None,
            )
            if entry_source
            else None
        )

        description = (
            getattr(
                entry_source,
                "description",
                None,
            )
            if entry_source
            else None
        )

        system = (
            getattr(
                entry_source,
                "system",
                None,
            )
            if entry_source
            else None
        )

        location = (
            getattr(
                entry_source,
                "location",
                None,
            )
            if entry_source
            else None
        )

        evidence = (
            CatalogEntryEvidence(
                entry_name=(
                    getattr(
                        entry,
                        "name",
                        "",
                    )
                    or ""
                ),

                fully_qualified_name=(
                    getattr(
                        entry,
                        "fully_qualified_name",
                        None,
                    )
                ),

                entry_type=(
                    getattr(
                        entry,
                        "entry_type",
                        None,
                    )
                ),

                display_name=(
                    display_name
                ),

                description=(
                    description
                ),

                system=(
                    str(
                        system
                    )
                    if system
                    else None
                ),

                location=(
                    location
                ),
            )
        )

        return (
            evidence.to_dict()
        )