from dataclasses import dataclass, field
from typing import Optional, List
from domain.execution_plan import ExecutionPlan 


@dataclass
class SQLQuery:
    text: str

    issues: Optional[List[str]] = None

    optimized_text: Optional[str] = None

    explanation: Optional[str] = None

    cost_estimate: Optional[float] = None

    recommendations: List[str] = field(
        default_factory=list
    )

    optimization_rules: List[str] = field(
        default_factory=list
    )

    execution_plan: Optional[ExecutionPlan] = None 