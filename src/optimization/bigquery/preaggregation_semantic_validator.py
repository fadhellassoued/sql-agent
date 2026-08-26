from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp


_NUMERIC_BIGQUERY_TYPES = {
    "INTEGER",
    "INT64",
    "FLOAT",
    "FLOAT64",
    "NUMERIC",
    "BIGNUMERIC",
    "DECIMAL",
    "BIGDECIMAL",
}


@dataclass(frozen=True)
class _ResolvedTable:
    project_id: str
    dataset_id: str
    table_id: str

    @property
    def full_name(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{self.table_id}"


class BigQueryPreaggregationSemanticValidator:
    """
    Deterministic semantic-compatibility checker for a narrow, supported
    BigQuery pre-aggregation rewrite pattern.

    Supported v1 shape:
      - exactly one SELECT scope and one base table;
      - no JOIN/WHERE/HAVING/QUALIFY/LIMIT/DISTINCT/CTE;
      - dimensions: DATE(source_column) AS output_alias;
      - measures: SUM(source_numeric_column) AS output_alias;
      - GROUP BY only the supported dimensions;
      - ORDER BY only output aliases;
      - Catalog candidate explicitly references the source;
      - Catalog description documents daily/day grain and required measures;
      - candidate BigQuery schema contains compatible output columns;
      - candidate table is not older than the source table.

    Unsupported shapes fail closed.
    """

    PROVEN_STATUS = "PROVEN_FOR_SUPPORTED_PREAGGREGATION_PATTERN"

    async def analyze(
        self,
        *,
        sql: str,
        catalog_alternatives: dict | None,
        client,
        default_project: str | None = None,
        default_dataset: str | None = None,
    ) -> dict[str, Any]:
        try:
            parsed = sqlglot.parse_one(sql, read="bigquery")
        except Exception as exc:
            return self._not_proven(
                "SQL_PARSE_FAILED",
                f"The original SQL could not be parsed: {exc}",
            )

        select_scopes = list(parsed.find_all(exp.Select))
        if len(select_scopes) != 1:
            return self._not_proven(
                "UNSUPPORTED_QUERY_SCOPE",
                "Pre-aggregation proof currently requires exactly one SELECT scope.",
            )

        select = select_scopes[0]

        if (
            select.args.get("joins")
            or select.args.get("where") is not None
            or select.args.get("having") is not None
            or select.args.get("qualify") is not None
            or select.args.get("limit") is not None
            or select.args.get("distinct") is not None
        ):
            return self._not_proven(
                "UNSUPPORTED_QUERY_CLAUSES",
                "JOIN, WHERE, HAVING, QUALIFY, LIMIT, and DISTINCT are not yet supported.",
            )

        if select.args.get("with") is not None or select.args.get("with_") is not None:
            return self._not_proven(
                "UNSUPPORTED_CTE",
                "CTEs are not supported by the current pre-aggregation proof.",
            )

        tables = list(parsed.find_all(exp.Table))
        if len(tables) != 1:
            return self._not_proven(
                "UNSUPPORTED_SOURCE_SHAPE",
                "Exactly one physical source table is required.",
            )

        source = self._resolve_sqlglot_table(
            tables[0],
            default_project=default_project,
            default_dataset=default_dataset,
        )
        if source is None:
            return self._not_proven(
                "UNRESOLVED_SOURCE",
                "The source table could not be resolved to project.dataset.table.",
            )

        group = select.args.get("group")
        if group is None:
            return self._not_proven(
                "GROUP_BY_REQUIRED",
                "The supported pre-aggregation pattern requires GROUP BY.",
            )

        dimensions: list[dict[str, Any]] = []
        measures: list[dict[str, Any]] = []
        output_aliases: list[str] = []

        for projection in select.expressions:
            if not isinstance(projection, exp.Alias):
                return self._not_proven(
                    "EXPLICIT_OUTPUT_ALIASES_REQUIRED",
                    "Every selected dimension/measure must have an explicit output alias.",
                )

            alias = projection.alias
            expression = projection.this
            if not alias:
                return self._not_proven("MISSING_OUTPUT_ALIAS", "A selected expression is missing its alias.")
            if alias in output_aliases:
                return self._not_proven("DUPLICATE_OUTPUT_ALIAS", f"Duplicate output alias: {alias}")
            output_aliases.append(alias)

            if isinstance(expression, exp.Date):
                source_column = self._single_column_name(expression)
                if source_column is None:
                    return self._not_proven(
                        "UNSUPPORTED_DATE_DIMENSION",
                        "DATE dimensions must reference exactly one source column.",
                    )
                dimensions.append(
                    {
                        "kind": "DATE",
                        "source_column": source_column,
                        "output_alias": alias,
                        "expression_sql": expression.sql(dialect="bigquery"),
                    }
                )
                continue

            if isinstance(expression, exp.Sum):
                source_column = self._single_column_name(expression)
                if source_column is None:
                    return self._not_proven(
                        "UNSUPPORTED_SUM_MEASURE",
                        "SUM measures must reference exactly one source column.",
                    )
                measures.append(
                    {
                        "kind": "SUM",
                        "source_column": source_column,
                        "output_alias": alias,
                        "expression_sql": expression.sql(dialect="bigquery"),
                    }
                )
                continue

            return self._not_proven(
                "UNSUPPORTED_PROJECTION",
                "Only DATE(source_column) dimensions and SUM(source_column) measures are supported.",
            )

        if not dimensions or not measures:
            return self._not_proven(
                "DIMENSION_AND_MEASURE_REQUIRED",
                "At least one DATE dimension and one SUM measure are required.",
            )

        dimension_aliases = {item["output_alias"].lower() for item in dimensions}
        dimension_expression_sql = {item["expression_sql"].lower() for item in dimensions}

        for group_expression in group.expressions:
            group_sql = group_expression.sql(dialect="bigquery").strip().lower()
            group_name = (
                group_expression.name.lower()
                if isinstance(group_expression, exp.Column) and group_expression.name
                else None
            )
            if group_name not in dimension_aliases and group_sql not in dimension_expression_sql:
                return self._not_proven(
                    "GROUP_BY_MISMATCH",
                    "GROUP BY contains an expression outside the supported dimensions.",
                )

        order = select.args.get("order")
        order_by: list[dict[str, Any]] = []
        if order is not None:
            allowed_outputs = {alias.lower() for alias in output_aliases}
            for ordered in order.expressions:
                ordered_expression = ordered.this if isinstance(ordered, exp.Ordered) else ordered
                if not isinstance(ordered_expression, exp.Column):
                    return self._not_proven("UNSUPPORTED_ORDER_BY", "ORDER BY must use output aliases only.")
                order_name = ordered_expression.name or ""
                if order_name.lower() not in allowed_outputs:
                    return self._not_proven(
                        "ORDER_BY_OUTPUT_MISMATCH",
                        "ORDER BY references a column that is not an output alias.",
                    )
                order_by.append(
                    {
                        "column": order_name,
                        "descending": bool(getattr(ordered, "args", {}).get("desc", False)),
                    }
                )

        catalog_result = catalog_alternatives or {}
        candidates = catalog_result.get("candidates", []) or []
        if not candidates:
            return self._not_proven("NO_CATALOG_CANDIDATES", "Knowledge Catalog returned no candidate asset.")

        eligible_candidates = [candidate for candidate in candidates if candidate.get("references_source", False)]
        if not eligible_candidates:
            return self._not_proven(
                "NO_SOURCE_REFERENCING_CANDIDATE",
                "No Catalog alternative explicitly references the source table.",
            )

        source_catalog_name = (catalog_result.get("source_table") or "").lower()
        source_tokens = {
            source.table_id.lower(),
            f"{source.dataset_id}.{source.table_id}".lower(),
            source.full_name.lower(),
            source_catalog_name.replace("bigquery:", "").lower(),
        }

        try:
            source_table = await asyncio.to_thread(client.get_table, source.full_name)
        except Exception as exc:
            return self._not_proven(
                "SOURCE_METADATA_UNAVAILABLE",
                f"BigQuery source metadata could not be loaded: {exc}",
            )

        source_schema = self._schema_map(source_table)

        for candidate in eligible_candidates:
            candidate_table = self._resolve_catalog_candidate(candidate)
            if candidate_table is None:
                continue

            description = (candidate.get("description") or "").strip()
            description_lower = description.lower()

            if not any(token and token in description_lower for token in source_tokens):
                continue

            if dimensions and not (
                "daily" in description_lower
                or "calendar day" in description_lower
                or "one day" in description_lower
                or "per day" in description_lower
            ):
                continue

            if any(measure["output_alias"].lower() not in description_lower for measure in measures):
                continue

            try:
                candidate_bq_table = await asyncio.to_thread(client.get_table, candidate_table.full_name)
            except Exception:
                continue

            candidate_schema = self._schema_map(candidate_bq_table)
            missing_outputs = [
                alias for alias in output_aliases if alias.lower() not in candidate_schema
            ]
            if missing_outputs:
                continue

            type_checks: list[dict[str, Any]] = []
            type_compatible = True

            for dimension in dimensions:
                source_field = source_schema.get(dimension["source_column"].lower())
                candidate_field = candidate_schema.get(dimension["output_alias"].lower())
                source_type = self._field_type(source_field)
                candidate_type = self._field_type(candidate_field)
                dimension_ok = source_field is not None and candidate_type == "DATE"
                type_compatible = type_compatible and dimension_ok
                type_checks.append(
                    {
                        "output_alias": dimension["output_alias"],
                        "source_column": dimension["source_column"],
                        "source_type": source_type,
                        "candidate_type": candidate_type,
                        "compatible": dimension_ok,
                    }
                )

            for measure in measures:
                source_field = source_schema.get(measure["source_column"].lower())
                candidate_field = candidate_schema.get(measure["output_alias"].lower())
                source_type = self._field_type(source_field)
                candidate_type = self._field_type(candidate_field)
                measure_ok = source_type in _NUMERIC_BIGQUERY_TYPES and candidate_type in _NUMERIC_BIGQUERY_TYPES
                type_compatible = type_compatible and measure_ok
                type_checks.append(
                    {
                        "output_alias": measure["output_alias"],
                        "source_column": measure["source_column"],
                        "source_type": source_type,
                        "candidate_type": candidate_type,
                        "compatible": measure_ok,
                    }
                )

            if not type_compatible:
                continue

            source_modified = getattr(source_table, "modified", None)
            candidate_modified = getattr(candidate_bq_table, "modified", None)
            freshness_ok = (
                source_modified is not None
                and candidate_modified is not None
                and candidate_modified >= source_modified
            )
            if not freshness_ok:
                continue

            return {
                "status": self.PROVEN_STATUS,
                "supported": True,
                "source_table": source.full_name,
                "candidate_table": candidate_table.full_name,
                "catalog_candidate": {
                    "fully_qualified_name": candidate.get("fully_qualified_name"),
                    "display_name": candidate.get("display_name"),
                    "description": description,
                    "references_source": True,
                    "matched_keywords": candidate.get("matched_keywords", []),
                    "relevance_score": candidate.get("relevance_score"),
                },
                "dimensions": dimensions,
                "measures": measures,
                "output_columns": output_aliases,
                "order_by": order_by,
                "checks": {
                    "single_select_scope": True,
                    "single_source_table": True,
                    "no_joins_filters_having_qualify_limit_distinct": True,
                    "supported_group_by": True,
                    "catalog_references_source": True,
                    "documented_daily_grain": True,
                    "documented_measure_outputs": True,
                    "candidate_output_columns_present": True,
                    "physical_types_compatible": True,
                    "candidate_not_older_than_source": True,
                    "type_checks": type_checks,
                    "source_modified": str(source_modified),
                    "candidate_modified": str(candidate_modified),
                },
                "proof_basis": (
                    "Deterministic supported-pattern proof: single-table daily DATE + SUM aggregation, "
                    "Catalog documents the candidate as derived from the source with daily/day grain "
                    "and the required measure output, BigQuery schema/type checks pass, and the "
                    "candidate table is not older than the source table."
                ),
                "limitations": [
                    "Proof is limited to this narrow documented pre-aggregation pattern.",
                    "The Knowledge Catalog description is treated as the maintained semantic contract for the aggregate measure definition.",
                    "Filters, joins, HAVING, DISTINCT, nested scopes, non-SUM measures, and different grains fail closed.",
                ],
            }

        return self._not_proven(
            "NO_PROVEN_COMPATIBLE_CANDIDATE",
            "Catalog alternatives were found, but none satisfied every semantic, schema, type, and freshness check.",
        )

    @staticmethod
    def _schema_map(table) -> dict[str, Any]:
        return {field.name.lower(): field for field in (getattr(table, "schema", None) or [])}

    @staticmethod
    def _field_type(field) -> str | None:
        if field is None:
            return None
        value = getattr(field, "field_type", None)
        return str(value).upper() if value else None

    @staticmethod
    def _single_column_name(expression: exp.Expression) -> str | None:
        columns = list(expression.find_all(exp.Column))
        if len(columns) != 1:
            return None
        return columns[0].name or None

    @staticmethod
    def _resolve_sqlglot_table(
        table: exp.Table,
        *,
        default_project: str | None,
        default_dataset: str | None,
    ) -> _ResolvedTable | None:
        project_id = table.catalog or default_project
        dataset_id = table.db or default_dataset
        table_id = table.name
        if not (project_id and dataset_id and table_id):
            return None
        return _ResolvedTable(str(project_id), str(dataset_id), str(table_id))

    @staticmethod
    def _resolve_catalog_candidate(candidate: dict[str, Any]) -> _ResolvedTable | None:
        fqn = (candidate.get("fully_qualified_name") or "").strip()
        if fqn.startswith("bigquery:"):
            fqn = fqn[len("bigquery:"):]
        parts = [part.strip("` ") for part in fqn.split(".") if part.strip("` ")]
        if len(parts) != 3:
            return None
        return _ResolvedTable(parts[0], parts[1], parts[2])

    @staticmethod
    def _not_proven(code: str, reason: str) -> dict[str, Any]:
        return {
            "status": "NOT_PROVEN",
            "supported": False,
            "code": code,
            "reason": reason,
            "source_table": None,
            "candidate_table": None,
            "dimensions": [],
            "measures": [],
            "output_columns": [],
            "order_by": [],
            "checks": {},
            "proof_basis": None,
            "limitations": [
                "No executable pre-aggregation candidate may be generated unless deterministic semantic proof succeeds."
            ],
        }