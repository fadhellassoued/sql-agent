from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutionPlan:
    total_bytes_processed: int
    scan_type: str
    cost: Optional[float] = None