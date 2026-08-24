from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TableReference:
    """
    A physical table referenced by the SQL query.
    """

    name: str

    database: Optional[str] = None

    schema: Optional[str] = None

    alias: Optional[str] = None

    full_name: Optional[str] = None


@dataclass
class ColumnReference:
    """
    A column referenced somewhere in the query.
    """

    name: str

    table: Optional[str] = None


@dataclass
class JoinInfo:
    """
    Structural information about a JOIN.
    """

    join_type: str

    right_table: Optional[str]

    right_alias: Optional[str]

    condition: Optional[str]

    columns: list[str] = field(
        default_factory=list
    )


@dataclass
class CTEInfo:
    """
    Common Table Expression information.
    """

    name: str

    sql: str

    referenced_tables: list[str] = field(
        default_factory=list
    )


@dataclass
class QueryStructure:
    """
    Database-independent structural representation
    of an SQL query.

    IMPORTANT:
    This object contains SQL structure only.

    It does NOT contain:
        BigQuery cost logic
        BigQuery partition logic
        PostgreSQL index advice
        PostgreSQL execution-plan logic

    Database-specific optimizers consume this structure.
    """

    original_sql: str

    tables: list[TableReference] = field(
        default_factory=list
    )

    columns: list[ColumnReference] = field(
        default_factory=list
    )

    joins: list[JoinInfo] = field(
        default_factory=list
    )

    ctes: list[CTEInfo] = field(
        default_factory=list
    )

    where_predicates: list[str] = field(
        default_factory=list
    )

    having_predicates: list[str] = field(
        default_factory=list
    )

    group_by: list[str] = field(
        default_factory=list
    )

    order_by: list[str] = field(
        default_factory=list
    )

    aggregations: list[str] = field(
        default_factory=list
    )

    subqueries: list[str] = field(
        default_factory=list
    )

    select_expressions: list[str] = field(
        default_factory=list
    )

    has_select_star: bool = False

    has_distinct: bool = False

    has_limit: bool = False

    limit_value: Optional[int] = None

    join_count: int = 0

    cte_count: int = 0

    subquery_count: int = 0