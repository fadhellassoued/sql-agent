from google.cloud import bigquery
from domain.column import Column
from domain.table import Table
from domain.database_schema import DatabaseSchema
from application.interfaces.schema_discovery_service import SchemaDiscoveryService

class BigQuerySchemaDiscovery(SchemaDiscoveryService):
    def __init__(self, client: bigquery.Client, dataset: str):
        self.client = client
        self.dataset = dataset

    async def discover_schema(self, connection=None):
        tables = self.client.list_tables(self.dataset)
        result = []
        relationships = {}
        for table in tables:
            table_ref = self.client.dataset(self.dataset).table(table.table_id)
            table_obj = self.client.get_table(table_ref)
            columns = []
            for schema_field in table_obj.schema:
                columns.append(Column(
                    name=schema_field.name,
                    data_type=schema_field.field_type,
                    is_nullable=schema_field.is_nullable
                ))
            result.append(Table(
                name=table.table_id,
                columns=columns,
                row_count=table_obj.num_rows,
                size_bytes=table_obj.num_bytes
            ))
            # Detect foreign keys (simplified: by naming convention)
            fks = [c.name for c in columns if c.name.endswith("_id")]
            if fks:
                relationships[table.table_id] = fks
        return DatabaseSchema(tables=result, relationships=relationships)