from dataclasses import dataclass
from typing import List, Dict
from .table import Table

@dataclass
class DatabaseSchema:
    tables: List[Table]
    relationships: Dict[str, List[str]]  # table_name -> list of foreign key column names