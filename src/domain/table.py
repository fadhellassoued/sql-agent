from dataclasses import dataclass
from typing import List
from .column import Column

@dataclass
class Table:
    name: str
    columns: List[Column]
    row_count: int = 0
    size_bytes: int = 0