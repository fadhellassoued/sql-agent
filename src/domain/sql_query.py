from dataclasses import dataclass
from typing import Optional, List

@dataclass
class SQLQuery:
    text: str
    issues: Optional[List[str]] = None
    optimized_text: Optional[str] = None
    explanation: Optional[str] = None
    cost_estimate: Optional[float] = None