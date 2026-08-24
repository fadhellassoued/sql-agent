from optimization.core.query_structure import (
    QueryStructure,
)


class PostgreSQLOptimizer:
    """
    PostgreSQL-specific optimization engine.

    This engine will eventually use:

        - EXPLAIN
        - EXPLAIN ANALYZE
        - sequential scans
        - index scans
        - nested loops
        - hash joins
        - merge joins
        - sort operations
        - row estimates
        - indexes
        - composite indexes
        - partial indexes
        - statistics
        - ANALYZE evidence

    It MUST NOT contain BigQuery partition /
    clustering / bytes-scanned logic.
    """

    def analyze(
        self,
        structure: QueryStructure,
        metadata: dict | None = None,
    ) -> dict:

        findings = []

        if structure.join_count > 0:

            findings.append(
                {
                    "code":
                        "PG_JOIN_ANALYSIS_REQUIRED",

                    "severity":
                        "info",

                    "category":
                        "joins",

                    "message":
                        (
                            "JOIN performance should be "
                            "evaluated using PostgreSQL "
                            "EXPLAIN evidence and available "
                            "indexes."
                        ),
                }
            )

        return {
            "database_type":
                "postgresql",

            "findings":
                findings,

            "safe_rewrite_available":
                False,
        }