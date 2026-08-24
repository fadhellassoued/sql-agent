# infrastructure/services/knowledge_catalog.py
from domain.column import Column
from domain.table import Table
from domain.database_schema import DatabaseSchema
from typing import List, Dict, Optional, Any
from src.config import settings
import re

class KnowledgeCatalogService:
    def __init__(self, project_id: str, location: str, lake: str, zone: str, dataset: str):
        self.project_id = project_id
        self.location = location
        self.lake = lake
        self.zone = zone
        self.dataset = dataset
        # We'll use BigQuery client for queries
        from google.cloud import bigquery
        self.bq_client = bigquery.Client(project=project_id)

    async def get_table_metadata(self, table_name: str) -> Dict[str, Any]:
        """Fetch metadata from INFORMATION_SCHEMA."""
        metadata = {
            "columns": {},
            "primary_keys": [],
            "foreign_keys": [],
            "descriptions": {}
        }
        # Get columns
        query = f"""
            SELECT column_name, data_type, is_nullable
            FROM `{self.project_id}.{self.dataset}.INFORMATION_SCHEMA.COLUMNS`
            WHERE table_name = '{table_name}'
        """
        try:
            result = self.bq_client.query(query).result()
            for row in result:
                col_name = row.column_name
                metadata["columns"][col_name] = {
                    "type": row.data_type,
                    "nullable": row.is_nullable == "YES",
                    "description": "",
                    "tags": []
                }
        except Exception:
            pass

        # Get primary keys (if available)
        try:
            pk_query = f"""
                SELECT column_name
                FROM `{self.project_id}.{self.dataset}.INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE`
                WHERE table_name = '{table_name}' AND constraint_name LIKE '%PRIMARY%'
            """
            pk_result = self.bq_client.query(pk_query).result()
            metadata["primary_keys"] = [row.column_name for row in pk_result]
        except Exception:
            pass

        # Get foreign keys
        try:
            fk_query = f"""
                SELECT column_name, referenced_table_name
                FROM `{self.project_id}.{self.dataset}.INFORMATION_SCHEMA.KEY_COLUMN_USAGE`
                WHERE table_name = '{table_name}' AND referenced_table_name IS NOT NULL
            """
            fk_result = self.bq_client.query(fk_query).result()
            metadata["foreign_keys"] = [{"column": row.column_name, "referenced_table": row.referenced_table_name} for row in fk_result]
        except Exception:
            pass

        return metadata

    async def get_column_usage(self, table_name: str, days_back: int = 30) -> Dict[str, int]:
        """Return empty dict – usage stats are not available in INFORMATION_SCHEMA."""
        return {}

    async def discover_schema(self) -> DatabaseSchema:
        """Discover schema via INFORMATION_SCHEMA."""
        tables = []
        relationships = {}

        # List all tables
        list_query = f"""
            SELECT table_name
            FROM `{self.project_id}.{self.dataset}.INFORMATION_SCHEMA.TABLES`
            WHERE table_type = 'BASE TABLE'
        """
        result = self.bq_client.query(list_query).result()
        for row in result:
            table_name = row.table_name
            # Get columns
            col_query = f"""
                SELECT column_name, data_type, is_nullable
                FROM `{self.project_id}.{self.dataset}.INFORMATION_SCHEMA.COLUMNS`
                WHERE table_name = '{table_name}'
            """
            col_result = self.bq_client.query(col_query).result()
            columns = []
            for col in col_result:
                columns.append(Column(
                    name=col.column_name,
                    data_type=col.data_type,
                    is_nullable=col.is_nullable == "YES",
                    description=""
                ))
            # Get row count and size from __TABLES__
            size_query = f"""
                SELECT row_count, size_bytes
                FROM `{self.project_id}.{self.dataset}.__TABLES__`
                WHERE table_id = '{table_name}'
            """
            size_result = self.bq_client.query(size_query).result()
            row_count = 0
            size_bytes = 0
            for size_row in size_result:
                row_count = size_row.row_count or 0
                size_bytes = size_row.size_bytes or 0

            tables.append(Table(
                name=table_name,
                columns=columns,
                row_count=row_count,
                size_bytes=size_bytes
            ))

            # Infer foreign key relationships from column names (simple heuristic)
            fks = [c.name for c in columns if c.name.endswith("_id") and c.name != "id"]
            if fks:
                relationships[table_name] = fks

        return DatabaseSchema(tables=tables, relationships=relationships)