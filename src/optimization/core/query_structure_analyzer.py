from __future__ import annotations

from typing import Optional

import sqlglot
from sqlglot import exp

from optimization.core.query_structure import (
    QueryStructure,
    TableReference,
    ColumnReference,
    JoinInfo,
    CTEInfo,
)


class QueryStructureAnalyzer:
    """
    Database-independent SQL structural analyzer.

    Responsibilities:
        - parse SQL
        - extract physical tables
        - extract columns
        - extract JOINs
        - extract CTEs
        - extract predicates
        - extract GROUP BY / ORDER BY
        - detect aggregates
        - detect subqueries
        - detect projection SELECT *
        - detect DISTINCT
        - detect LIMIT

    IMPORTANT:

    This class contains NO BigQuery-specific optimization logic
    and NO PostgreSQL-specific optimization logic.
    """

    def analyze(
        self,
        sql: str,
        dialect: Optional[str] = None,
    ) -> QueryStructure:

        if (
            not sql
            or not sql.strip()
        ):
            raise ValueError(
                "SQL query cannot be empty."
            )

        try:

            expression = sqlglot.parse_one(
                sql,
                read=dialect,
            )

        except Exception as exc:

            raise ValueError(
                f"Unable to parse SQL: {exc}"
            ) from exc

        structure = QueryStructure(
            original_sql=sql
        )

        # =====================================================
        # CTE NAMES
        # =====================================================

        cte_names = (
            self._extract_cte_names(
                expression
            )
        )

        # =====================================================
        # PHYSICAL TABLES
        # =====================================================

        structure.tables = (
            self._extract_tables(
                expression,
                cte_names,
            )
        )

        # =====================================================
        # COLUMNS
        # =====================================================

        structure.columns = (
            self._extract_columns(
                expression
            )
        )

        # =====================================================
        # SELECT EXPRESSIONS
        # =====================================================

        structure.select_expressions = (
            self._extract_select_expressions(
                expression
            )
        )

        # =====================================================
        # SELECT *
        # =====================================================

        structure.has_select_star = (
            self._has_select_star(
                expression
            )
        )

        # =====================================================
        # DISTINCT
        # =====================================================

        structure.has_distinct = (
            self._has_distinct(
                expression
            )
        )

        # =====================================================
        # PREDICATES
        # =====================================================

        structure.where_predicates = (
            self._extract_where_predicates(
                expression
            )
        )

        structure.having_predicates = (
            self._extract_having_predicates(
                expression
            )
        )

        # =====================================================
        # GROUP / ORDER
        # =====================================================

        structure.group_by = (
            self._extract_group_by(
                expression
            )
        )

        structure.order_by = (
            self._extract_order_by(
                expression
            )
        )

        # =====================================================
        # AGGREGATIONS
        # =====================================================

        structure.aggregations = (
            self._extract_aggregations(
                expression
            )
        )

        # =====================================================
        # SUBQUERIES
        # =====================================================

        structure.subqueries = (
            self._extract_subqueries(
                expression
            )
        )

        # =====================================================
        # CTEs
        # =====================================================

        structure.ctes = (
            self._extract_ctes(
                expression
            )
        )

        # =====================================================
        # JOINS
        # =====================================================

        structure.joins = (
            self._extract_joins(
                expression
            )
        )

        # =====================================================
        # LIMIT
        # =====================================================

        (
            structure.has_limit,
            structure.limit_value,
        ) = self._extract_limit(
            expression
        )

        # =====================================================
        # COUNTERS
        # =====================================================

        structure.join_count = len(
            structure.joins
        )

        structure.cte_count = len(
            structure.ctes
        )

        structure.subquery_count = len(
            structure.subqueries
        )

        return structure

    # =========================================================
    # CTE NAMES
    # =========================================================

    def _extract_cte_names(
        self,
        expression: exp.Expression,
    ) -> set[str]:

        names: set[str] = set()

        for cte in expression.find_all(
            exp.CTE
        ):

            alias = cte.alias_or_name

            if alias:

                names.add(
                    alias.lower()
                )

        return names

    # =========================================================
    # TABLES
    # =========================================================

    def _extract_tables(
        self,
        expression: exp.Expression,
        cte_names: set[str],
    ) -> list[TableReference]:

        tables: list[TableReference] = []

        seen = set()

        for table in expression.find_all(
            exp.Table
        ):

            table_name = table.name

            if not table_name:

                continue

            # CTE references are logical sources,
            # not physical BigQuery/PostgreSQL tables.
            if (
                table_name.lower()
                in cte_names
            ):
                continue

            catalog = (
                table.catalog
                or None
            )

            database = (
                table.db
                or None
            )

            alias = (
                table.alias
                or None
            )

            full_name_parts = [
                part
                for part in (
                    catalog,
                    database,
                    table_name,
                )
                if part
            ]

            full_name = ".".join(
                full_name_parts
            )

            key = (
                catalog,
                database,
                table_name,
                alias,
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            tables.append(
                TableReference(
                    name=
                        table_name,

                    database=
                        catalog,

                    schema=
                        database,

                    alias=
                        alias,

                    full_name=
                        full_name,
                )
            )

        return tables

    # =========================================================
    # COLUMNS
    # =========================================================

    def _extract_columns(
        self,
        expression: exp.Expression,
    ) -> list[ColumnReference]:

        columns = []

        seen = set()

        for column in expression.find_all(
            exp.Column
        ):

            name = column.name

            if not name:

                continue

            table = (
                column.table
                or None
            )

            key = (
                table,
                name,
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            columns.append(
                ColumnReference(
                    name=name,
                    table=table,
                )
            )

        return columns

    # =========================================================
    # SELECT EXPRESSIONS
    # =========================================================

    def _extract_select_expressions(
        self,
        expression: exp.Expression,
    ) -> list[str]:

        expressions = []

        # We inspect every SELECT because enterprise SQL can
        # contain CTEs and nested SELECT statements.
        for select in expression.find_all(
            exp.Select
        ):

            for item in select.expressions:

                sql = item.sql()

                if sql not in expressions:

                    expressions.append(
                        sql
                    )

        return expressions

    # =========================================================
    # SELECT *
    # =========================================================

    def _has_select_star(
        self,
        expression: exp.Expression,
    ) -> bool:
        """
        Detect wildcard projection only.

        VALID detections:

            SELECT *
            SELECT t.*

        MUST NOT trigger for:

            COUNT(*)
            COUNT(DISTINCT ...)
            function(*)

        The old implementation recursively searched for Star
        inside each projection expression, which incorrectly
        detected COUNT(*) as SELECT *.
        """

        for select in expression.find_all(
            exp.Select
        ):

            for item in select.expressions:

                # ---------------------------------------------
                # SELECT *
                # ---------------------------------------------

                if isinstance(
                    item,
                    exp.Star,
                ):

                    return True

                # ---------------------------------------------
                # SELECT table.*
                #
                # SQLGlot normally represents this as a Column
                # whose `this` expression is Star.
                # ---------------------------------------------

                if isinstance(
                    item,
                    exp.Column,
                ):

                    if isinstance(
                        item.this,
                        exp.Star,
                    ):

                        return True

        return False

    # =========================================================
    # DISTINCT
    # =========================================================

    def _has_distinct(
        self,
        expression: exp.Expression,
    ) -> bool:

        for select in expression.find_all(
            exp.Select
        ):

            if select.args.get(
                "distinct"
            ):

                return True

        return False

    # =========================================================
    # WHERE
    # =========================================================

    def _extract_where_predicates(
        self,
        expression: exp.Expression,
    ) -> list[str]:

        predicates = []

        for where in expression.find_all(
            exp.Where
        ):

            condition = where.this

            if condition:

                predicate = (
                    condition.sql()
                )

                if predicate not in predicates:

                    predicates.append(
                        predicate
                    )

        return predicates

    # =========================================================
    # HAVING
    # =========================================================

    def _extract_having_predicates(
        self,
        expression: exp.Expression,
    ) -> list[str]:

        predicates = []

        for having in expression.find_all(
            exp.Having
        ):

            if having.this:

                predicate = (
                    having.this.sql()
                )

                if predicate not in predicates:

                    predicates.append(
                        predicate
                    )

        return predicates

    # =========================================================
    # GROUP BY
    # =========================================================

    def _extract_group_by(
        self,
        expression: exp.Expression,
    ) -> list[str]:

        items = []

        for group in expression.find_all(
            exp.Group
        ):

            for item in group.expressions:

                sql = item.sql()

                if sql not in items:

                    items.append(
                        sql
                    )

        return items

    # =========================================================
    # ORDER BY
    # =========================================================

    def _extract_order_by(
        self,
        expression: exp.Expression,
    ) -> list[str]:

        items = []

        for order in expression.find_all(
            exp.Order
        ):

            for item in order.expressions:

                sql = item.sql()

                if sql not in items:

                    items.append(
                        sql
                    )

        return items

    # =========================================================
    # AGGREGATIONS
    # =========================================================

    def _extract_aggregations(
        self,
        expression: exp.Expression,
    ) -> list[str]:

        aggregations = []

        seen = set()

        for node in expression.walk():

            if isinstance(
                node,
                exp.AggFunc,
            ):

                sql = node.sql()

                if sql in seen:
                    continue

                seen.add(
                    sql
                )

                aggregations.append(
                    sql
                )

        return aggregations

    # =========================================================
    # SUBQUERIES
    # =========================================================

    def _extract_subqueries(
        self,
        expression: exp.Expression,
    ) -> list[str]:

        subqueries = []

        seen = set()

        for subquery in expression.find_all(
            exp.Subquery
        ):

            sql = subquery.sql()

            if sql in seen:
                continue

            seen.add(
                sql
            )

            subqueries.append(
                sql
            )

        return subqueries

    # =========================================================
    # CTEs
    # =========================================================

    def _extract_ctes(
        self,
        expression: exp.Expression,
    ) -> list[CTEInfo]:

        ctes = []

        cte_names = {
            cte.alias_or_name.lower()
            for cte in expression.find_all(
                exp.CTE
            )
            if cte.alias_or_name
        }

        for cte in expression.find_all(
            exp.CTE
        ):

            name = (
                cte.alias_or_name
            )

            if not name:
                continue

            cte_expression = (
                cte.this
            )

            referenced_tables = []

            if cte_expression:

                for table in (
                    cte_expression.find_all(
                        exp.Table
                    )
                ):

                    table_name = (
                        table.name
                    )

                    if not table_name:
                        continue

                    if (
                        table_name.lower()
                        in cte_names
                    ):
                        continue

                    referenced_tables.append(
                        table_name
                    )

            ctes.append(
                CTEInfo(
                    name=name,

                    sql=(
                        cte_expression.sql()
                        if cte_expression
                        else ""
                    ),

                    referenced_tables=
                        list(
                            dict.fromkeys(
                                referenced_tables
                            )
                        ),
                )
            )

        return ctes

    # =========================================================
    # JOINS
    # =========================================================

    def _extract_joins(
        self,
        expression: exp.Expression,
    ) -> list[JoinInfo]:

        joins = []

        for join in expression.find_all(
            exp.Join
        ):

            right_expression = (
                join.this
            )

            right_table = None
            right_alias = None

            if isinstance(
                right_expression,
                exp.Table,
            ):

                right_table = (
                    right_expression.name
                )

                right_alias = (
                    right_expression.alias
                    or None
                )

            elif right_expression:

                right_table = (
                    right_expression.sql()
                )

            # ---------------------------------------------
            # JOIN TYPE
            # ---------------------------------------------

            side = (
                join.args.get(
                    "side"
                )
            )

            kind = (
                join.args.get(
                    "kind"
                )
            )

            if side:

                join_type = (
                    str(
                        side
                    ).upper()
                )

            elif kind:

                join_type = (
                    str(
                        kind
                    ).upper()
                )

            else:

                join_type = (
                    "INNER"
                )

            # ---------------------------------------------
            # ON CONDITION
            # ---------------------------------------------

            condition_expression = (
                join.args.get(
                    "on"
                )
            )

            condition = (
                condition_expression.sql()
                if condition_expression
                else None
            )

            join_columns = []

            if condition_expression:

                for column in (
                    condition_expression.find_all(
                        exp.Column
                    )
                ):

                    if column.table:

                        column_name = (
                            f"{column.table}."
                            f"{column.name}"
                        )

                    else:

                        column_name = (
                            column.name
                        )

                    join_columns.append(
                        column_name
                    )

            joins.append(
                JoinInfo(
                    join_type=
                        join_type,

                    right_table=
                        right_table,

                    right_alias=
                        right_alias,

                    condition=
                        condition,

                    columns=
                        list(
                            dict.fromkeys(
                                join_columns
                            )
                        ),
                )
            )

        return joins

    # =========================================================
    # LIMIT
    # =========================================================

    def _extract_limit(
        self,
        expression: exp.Expression,
    ) -> tuple[
        bool,
        Optional[int],
    ]:

        limit = expression.find(
            exp.Limit
        )

        if not limit:

            return (
                False,
                None,
            )

        limit_expression = (
            limit.expression
        )

        if limit_expression is None:

            return (
                True,
                None,
            )

        try:

            value = int(
                limit_expression.name
            )

        except (
            ValueError,
            TypeError,
            AttributeError,
        ):

            value = None

        return (
            True,
            value,
        )