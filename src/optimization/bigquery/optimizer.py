from optimization.core.query_structure import (
    QueryStructure,
)


GIB = 1024 ** 3

TIB = 1024 ** 4


class BigQueryOptimizer:
    """
    BigQuery-specific optimization analyzer.

    This engine uses:

        QueryStructure
        +
        real BigQuery table metadata

    It does NOT contain PostgreSQL index logic.
    """

    def analyze(
        self,
        structure: QueryStructure,
        metadata: dict | None = None,
    ) -> dict:

        findings = []

        metadata = (
            metadata
            or
            {}
        )

        tables = (
            metadata.get(
                "tables",
                [],
            )
        )

        # =====================================================
        # COLUMN PRUNING
        # =====================================================

        if structure.has_select_star:

            findings.append(
                {
                    "code":
                        "BQ_SELECT_STAR",

                    "severity":
                        "warning",

                    "category":
                        "column_pruning",

                    "message":
                        (
                            "SELECT * projects every column. "
                            "BigQuery may therefore scan columns "
                            "that are not required by the consumer."
                        ),

                    "evidence": {
                        "type":
                            "query_structure",
                    },
                }
            )

        # =====================================================
        # PARTITION ANALYSIS
        # =====================================================

        where_text = (
            " ".join(
                structure.where_predicates
            ).lower()
        )

        for table in tables:

            if not table.get(
                "partitioned"
            ):

                # Important:
                #
                # DO NOT claim partition pruning is possible.
                continue

            partition_field = (
                table.get(
                    "partition_field"
                )
            )

            if not partition_field:

                continue

            field_normalized = (
                partition_field.lower()
            )

            has_partition_filter = (
                field_normalized
                in where_text
            )

            if not has_partition_filter:

                findings.append(
                    {
                        "code":
                            "BQ_MISSING_PARTITION_FILTER",

                        "severity":
                            "warning",

                        "category":
                            "partition_pruning",

                        "table":
                            table[
                                "full_name"
                            ],

                        "message":
                            (
                                f"{table['full_name']} is partitioned "
                                f"by {partition_field}, but the query "
                                "does not appear to filter on that "
                                "partition field."
                            ),

                        "evidence": {
                            "partitioned":
                                True,

                            "partition_field":
                                partition_field,

                            "partition_type":
                                table.get(
                                    "partition_type"
                                ),
                        },
                    }
                )

        # =====================================================
        # LARGE TABLES
        # =====================================================

        for table in tables:

            size_bytes = int(
                table.get(
                    "size_bytes",
                    0,
                )
                or 0
            )

            if size_bytes >= TIB:

                findings.append(
                    {
                        "code":
                            "BQ_VERY_LARGE_TABLE",

                        "severity":
                            "warning",

                        "category":
                            "scan_volume",

                        "table":
                            table[
                                "full_name"
                            ],

                        "message":
                            (
                                f"{table['full_name']} is approximately "
                                f"{size_bytes / TIB:.2f} TiB logically. "
                                "Column projection, partition pruning, "
                                "and early data reduction should be "
                                "evaluated carefully."
                            ),

                        "evidence": {
                            "size_bytes":
                                size_bytes,
                        },
                    }
                )

            elif size_bytes >= (
                10 * GIB
            ):

                findings.append(
                    {
                        "code":
                            "BQ_LARGE_TABLE",

                        "severity":
                            "info",

                        "category":
                            "scan_volume",

                        "table":
                            table[
                                "full_name"
                            ],

                        "message":
                            (
                                f"{table['full_name']} is approximately "
                                f"{size_bytes / GIB:.2f} GiB logically."
                            ),

                        "evidence": {
                            "size_bytes":
                                size_bytes,
                        },
                    }
                )

        # =====================================================
        # JOIN ANALYSIS
        # =====================================================

        if structure.join_count >= 3:

            findings.append(
                {
                    "code":
                        "BQ_COMPLEX_JOIN_GRAPH",

                    "severity":
                        "info",

                    "category":
                        "joins",

                    "message":
                        (
                            f"The query contains "
                            f"{structure.join_count} JOINs. "
                            "This should be evaluated for "
                            "large intermediate results, "
                            "filter placement, and shuffle cost."
                        ),

                    "evidence": {
                        "join_count":
                            structure.join_count,
                    },
                }
            )

        # =====================================================
        # LARGE JOIN INPUTS
        # =====================================================

        joined_table_names = {
            (
                join.right_table
                or
                ""
            ).lower()

            for join
            in structure.joins
        }

        for table in tables:

            if (
                table[
                    "name"
                ].lower()
                not in joined_table_names
            ):

                continue

            size_bytes = int(
                table.get(
                    "size_bytes",
                    0,
                )
                or 0
            )

            if size_bytes >= (
                10 * GIB
            ):

                findings.append(
                    {
                        "code":
                            "BQ_LARGE_JOIN_INPUT",

                        "severity":
                            "warning",

                        "category":
                            "joins",

                        "table":
                            table[
                                "full_name"
                            ],

                        "message":
                            (
                                f"{table['full_name']} participates "
                                "in a JOIN and is approximately "
                                f"{size_bytes / GIB:.2f} GiB. "
                                "Evaluate whether filters, projection, "
                                "or aggregation can safely reduce this "
                                "input before the JOIN."
                            ),

                        "evidence": {
                            "size_bytes":
                                size_bytes,
                        },
                    }
                )

        # =====================================================
        # DISTINCT
        # =====================================================

        if structure.has_distinct:

            findings.append(
                {
                    "code":
                        "BQ_DISTINCT",

                    "severity":
                        "info",

                    "category":
                        "aggregation",

                    "message":
                        (
                            "DISTINCT may require processing "
                            "a large intermediate result. "
                            "Verify that global deduplication "
                            "is required."
                        ),

                    "evidence": {
                        "distinct":
                            True,
                    },
                }
            )

        # =====================================================
        # AGGREGATION
        # =====================================================

        if structure.aggregations:

            findings.append(
                {
                    "code":
                        "BQ_AGGREGATION_PRESENT",

                    "severity":
                        "info",

                    "category":
                        "aggregation",

                    "message":
                        (
                            "The query contains aggregation. "
                            "For frequently repeated workloads, "
                            "pre-aggregation or a materialized "
                            "representation may be worth evaluating."
                        ),

                    "evidence": {
                        "aggregations":
                            structure.aggregations,
                    },
                }
            )

        # =====================================================
        # ACTUAL CLUSTERING INFORMATION
        # =====================================================

        for table in tables:

            clustering_fields = (
                table.get(
                    "clustering_fields"
                )
                or
                []
            )

            if clustering_fields:

                findings.append(
                    {
                        "code":
                            "BQ_TABLE_CLUSTERED",

                        "severity":
                            "info",

                        "category":
                            "clustering",

                        "table":
                            table[
                                "full_name"
                            ],

                        "message":
                            (
                                f"{table['full_name']} is already "
                                "clustered by "
                                f"{', '.join(clustering_fields)}."
                            ),

                        "evidence": {
                            "clustering_fields":
                                clustering_fields,
                        },
                    }
                )

        # =====================================================
        # RESULT
        # =====================================================

        return {
            "database_type":
                "bigquery",

            "findings":
                findings,

            "safe_rewrite_available":
                False,

            "evidence_available":
                bool(
                    tables
                ),
        }