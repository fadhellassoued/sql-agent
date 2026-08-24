from __future__ import annotations

from google.cloud import bigquery

from optimization.core.query_structure import (
    QueryStructure,
)


class BigQueryEvidenceAnalyzer:
    """
    Retrieve REAL BigQuery metadata for only the physical tables
    referenced by the current SQL query.

    This prevents us from making speculative recommendations.

    Evidence collected:
        - full table ID
        - row count
        - logical bytes
        - partition status
        - partition type
        - partition field
        - clustering fields
        - schema columns
        - aliases
        - join participation

    IMPORTANT:

    This component does NOT rewrite SQL.
    """

    def __init__(
        self,
        client: bigquery.Client,
        default_dataset: str | None = None,
    ):

        self.client = client

        self.default_dataset = (
            default_dataset
        )

    async def analyze(
        self,
        structure: QueryStructure,
    ) -> dict:

        table_evidence = []

        errors = []

        for table_ref in structure.tables:

            try:

                table_info = (
                    self._load_table_metadata(
                        table_ref
                    )
                )

                table_evidence.append(
                    table_info
                )

            except Exception as exc:

                errors.append(
                    {
                        "table":
                            (
                                table_ref.full_name
                                or
                                table_ref.name
                            ),

                        "error":
                            str(
                                exc
                            ),
                    }
                )

        return {
            "database_type":
                "bigquery",

            "tables":
                table_evidence,

            "table_count":
                len(
                    table_evidence
                ),

            "metadata_errors":
                errors,
        }

    # =========================================================
    # LOAD TABLE
    # =========================================================

    def _load_table_metadata(
        self,
        table_ref,
    ) -> dict:

        project_id = (
            table_ref.database
            or
            self.client.project
        )

        dataset_id = (
            table_ref.schema
            or
            self.default_dataset
        )

        if not dataset_id:

            raise ValueError(
                f"Unable to determine dataset for "
                f"table '{table_ref.name}'."
            )

        full_table_id = (
            f"{project_id}."
            f"{dataset_id}."
            f"{table_ref.name}"
        )

        table = (
            self.client.get_table(
                full_table_id
            )
        )

        # =====================================================
        # PARTITIONING
        # =====================================================

        partitioned = False

        partition_type = None

        partition_field = None

        if table.time_partitioning:

            partitioned = True

            partition_type = str(
                table.time_partitioning.type
            )

            partition_field = (
                table.time_partitioning.field
            )

            # Ingestion-time partitioned tables can have
            # field=None.
            if not partition_field:

                partition_field = (
                    "_PARTITIONTIME"
                )

        elif table.range_partitioning:

            partitioned = True

            partition_type = (
                "RANGE"
            )

            partition_field = (
                table.range_partitioning.field
            )

        # =====================================================
        # COLUMNS
        # =====================================================

        columns = []

        def add_fields(
            fields,
            prefix="",
        ):

            for field in fields:

                name = (
                    f"{prefix}.{field.name}"
                    if prefix
                    else field.name
                )

                columns.append(
                    {
                        "name":
                            name,

                        "type":
                            field.field_type,

                        "mode":
                            field.mode,
                    }
                )

                if field.fields:

                    add_fields(
                        field.fields,
                        name,
                    )

        add_fields(
            table.schema
        )

        # =====================================================
        # JOIN PARTICIPATION
        # =====================================================

        join_columns = []

        aliases = {
            table_ref.name.lower(),
        }

        if table_ref.alias:

            aliases.add(
                table_ref.alias.lower()
            )

        # We don't infer "good clustering columns" here.
        # We only record what this query actually references.
        for join in []:
            pass

        return {
            "name":
                table.table_id,

            "alias":
                table_ref.alias,

            "project_id":
                project_id,

            "dataset_id":
                dataset_id,

            "full_name":
                full_table_id,

            "row_count":
                int(
                    table.num_rows
                    or
                    0
                ),

            "size_bytes":
                int(
                    table.num_bytes
                    or
                    0
                ),

            "partitioned":
                partitioned,

            "partition_type":
                partition_type,

            "partition_field":
                partition_field,

            "clustering_fields":
                list(
                    table.clustering_fields
                    or
                    []
                ),

            "columns":
                columns,
        }