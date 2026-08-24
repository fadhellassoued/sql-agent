from dataclasses import dataclass, field
from typing import List


@dataclass
class OptimizationResult:
    """
    Result produced by the deterministic SQL optimization engine.

    The optimizer is intentionally independent from the LLM.
    The LLM may explain this result, but must not replace or modify
    the optimized SQL returned here.
    """

    original_sql: str
    optimized_sql: str
    rules_applied: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.original_sql.strip() != self.optimized_sql.strip()