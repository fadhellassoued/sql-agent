from __future__ import annotations

from dataclasses import dataclass, asdict

from optimization.core.query_structure import (
    QueryStructure,
)


@dataclass
class RequiredUserInput:
    """
    Describes information that the optimizer needs from the user
    before it can safely generate a candidate query.
    """

    type: str

    reason: str

    table: str | None = None

    table_alias: str | None = None

    columns: list[dict] | None = None

    required: bool = True

    def to_dict(
        self,
    ) -> dict:

        return asdict(
            self
        )


class BigQueryUserIntentAnalyzer:
    """
    Determines whether a BigQuery optimization requires
    business/user intent.

    Example:

        SELECT *
        FROM orders

    The optimizer cannot safely decide whether the caller wants:

        order_id
        customer_id
        amount

    or every available column.

    Therefore this becomes NEEDS_USER_INPUT.
    """

    def analyze(
        self,
        structure: QueryStructure,
        evidence: dict | None,
    ) -> RequiredUserInput | None:

        evidence = (
            evidence
            or
            {}
        )

        # =====================================================
        # SELECT *
        # =====================================================

        if structure.has_select_star:

            return self._build_column_selection_request(
                structure=
                    structure,

                evidence=
                    evidence,
            )

        return None

    # =========================================================
    # COLUMN SELECTION
    # =========================================================

    def _build_column_selection_request(
        self,
        structure: QueryStructure,
        evidence: dict,
    ) -> RequiredUserInput | None:

        tables = (
            evidence.get(
                "tables",
                [],
            )
        )

        # -----------------------------------------------------
        # First implementation:
        #
        # automatically support the common case where SELECT *
        # targets one physical table.
        #
        # Multi-table wildcard handling will come immediately
        # afterward.
        # -----------------------------------------------------

        if len(tables) != 1:

            return RequiredUserInput(
                type=
                    "column_selection",

                reason=(
                    "The query contains SELECT *, but more than "
                    "one physical table is involved. The optimizer "
                    "needs explicit user intent before replacing "
                    "wildcard projections."
                ),

                columns=
                    [],
            )

        table = (
            tables[0]
        )

        available_columns = []

        for column in (
            table.get(
                "columns",
                [],
            )
        ):

            column_name = (
                column.get(
                    "name"
                )
            )

            if not column_name:

                continue

            available_columns.append(
                {
                    "name":
                        column_name,

                    "type":
                        column.get(
                            "type"
                        ),

                    "mode":
                        column.get(
                            "mode"
                        ),
                }
            )

        return RequiredUserInput(
            type=
                "column_selection",

            reason=(
                "SELECT * requires knowledge of which output "
                "columns are actually needed. The optimizer will "
                "not remove columns automatically because that "
                "could change application behavior."
            ),

            table=
                table.get(
                    "full_name"
                ),

            table_alias=
                table.get(
                    "alias"
                ),

            columns=
                available_columns,
        )