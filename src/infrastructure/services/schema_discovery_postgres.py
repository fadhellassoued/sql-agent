from sqlalchemy import text
from domain.column import Column
from domain.table import Table
from domain.database_schema import DatabaseSchema
from application.interfaces.schema_discovery_service import SchemaDiscoveryService

class PostgresSchemaDiscovery(SchemaDiscoveryService):
    def __init__(self, engine):
        self.engine = engine

    async def discover_schema(self, connection=None):
        with self.engine.connect() as conn:
            # Get tables
            tables_res = conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """))
            tables = []
            relationships = {}
            for (table_name,) in tables_res:
                # Get columns
                cols_res = conn.execute(text("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = :table_name
                """), {"table_name": table_name})
                columns = []
                fks = []
                for col_name, data_type, is_nullable in cols_res:
                    columns.append(Column(
                        name=col_name,
                        data_type=data_type,
                        is_nullable=is_nullable == "YES"
                    ))
                    if col_name.endswith("_id"):
                        fks.append(col_name)
                # Get row count and size
                count_res = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                row_count = count_res.scalar()
                size_res = conn.execute(text(f"SELECT pg_total_relation_size('{table_name}')"))
                size_bytes = size_res.scalar()
                tables.append(Table(
                    name=table_name,
                    columns=columns,
                    row_count=row_count,
                    size_bytes=size_bytes
                ))
                if fks:
                    relationships[table_name] = fks
            return DatabaseSchema(tables=tables, relationships=relationships)