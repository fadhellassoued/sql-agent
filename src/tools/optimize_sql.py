import sqlglot
from sqlglot import parse_one
from domain.database_schema import DatabaseSchema
from infrastructure.services.knowledge_catalog import KnowledgeCatalogService
from infrastructure.repositories.bigquery_repository import BigQueryRepository
import re
import asyncio
from datetime import datetime, timedelta

class OptimizationContext:
    def __init__(self, sql: str, schema: DatabaseSchema, dataset: str,
                 db_repo: BigQueryRepository, knowledge_catalog: KnowledgeCatalogService = None):
        self.sql = sql
        self.schema = schema
        self.dataset = dataset
        self.db_repo = db_repo
        self.knowledge_catalog = knowledge_catalog
        self.parsed = parse_one(sql, dialect="bigquery")
        self.table_names = self._extract_tables()
        self.table_stats = {}
        self.date_ranges = {}
        self.column_usage = {}
        self.column_metadata = {}

    def _extract_tables(self):
        tables = []
        for table in self.parsed.find_all(sqlglot.expressions.Table):
            name = table.name.split('.')[-1] if '.' in table.name else table.name
            tables.append(name)
        return list(set(tables))

    async def load_context(self):
        for table_name in self.table_names:
            try:
                row_count = await self.db_repo.get_table_row_count(table_name)
                size_bytes = await self.db_repo.get_table_size_bytes(table_name)
                self.table_stats[table_name] = {"row_count": row_count, "size_bytes": size_bytes}
            except Exception as e:
                print(f"[OPTIMIZE] Error getting stats for {table_name}: {e}")
                self.table_stats[table_name] = {"row_count": 0, "size_bytes": 0}

            table_obj = next((t for t in self.schema.tables if t.name == table_name), None)
            if not table_obj:
                continue

            self.column_metadata[table_name] = {}
            if self.knowledge_catalog:
                try:
                    metadata = await self.knowledge_catalog.get_table_metadata(table_name)
                    for col in table_obj.columns:
                        col_name = col.name
                        meta = metadata["columns"].get(col_name, {})
                        self.column_metadata[table_name][col_name] = {
                            "description": meta.get("description", ""),
                            "is_primary": col_name in metadata["primary_keys"],
                            "is_foreign": any(fk["column"] == col_name for fk in metadata["foreign_keys"]),
                            "tags": meta.get("tags", [])
                        }
                    usage = await self.knowledge_catalog.get_column_usage(table_name)
                    self.column_usage[table_name] = usage
                except Exception as e:
                    print(f"[OPTIMIZE] Knowledge Catalog error for {table_name}: {e}. Using basic metadata.")
                    for col in table_obj.columns:
                        self.column_metadata[table_name][col.name] = {
                            "description": getattr(col, "description", ""),
                            "is_primary": getattr(col, "is_primary_key", False),
                            "is_foreign": getattr(col, "is_foreign_key", False),
                            "tags": []
                        }
                    self.column_usage[table_name] = {}
            else:
                for col in table_obj.columns:
                    self.column_metadata[table_name][col.name] = {
                        "description": getattr(col, "description", ""),
                        "is_primary": getattr(col, "is_primary_key", False),
                        "is_foreign": getattr(col, "is_foreign_key", False),
                        "tags": []
                    }
                self.column_usage[table_name] = {}

            for col in table_obj.columns:
                if any(k in col.name.lower() for k in ['date', 'timestamp', 'created', 'updated']):
                    try:
                        min_val, max_val = await self.db_repo.get_date_range(table_name, col.name)
                        if min_val and max_val:
                            self.date_ranges[(table_name, col.name)] = (min_val, max_val)
                    except Exception as e:
                        print(f"[OPTIMIZE] Error getting date range for {table_name}.{col.name}: {e}")

async def optimize_sql(sql: str, schema: DatabaseSchema, dataset: str = None,
                       db_repo: BigQueryRepository = None,
                       knowledge_catalog: KnowledgeCatalogService = None) -> dict:
    ctx = OptimizationContext(sql, schema, dataset, db_repo, knowledge_catalog)
    await ctx.load_context()

    parsed = ctx.parsed
    modifications = []
    selected_columns = []  # Will hold the columns to replace SELECT *

    # 1. SELECT * replacement using AST + fallback
    for select in parsed.find_all(sqlglot.expressions.Select):
        has_star = any(isinstance(expr, sqlglot.expressions.Star) for expr in select.args.get('expressions', []))
        if has_star:
            from_clause = select.args.get('from')
            if from_clause and from_clause.args.get('expressions'):
                table_expr = from_clause.args['expressions'][0]
                table_name = table_expr.args.get('this', table_expr).name.split('.')[-1]
                row_count = ctx.table_stats.get(table_name, {}).get('row_count', 0)
                table_obj = next((t for t in schema.tables if t.name == table_name), None)
                if table_obj:
                    priority_cols = []
                    other_cols = []
                    usage = ctx.column_usage.get(table_name, {})
                    for col in table_obj.columns:
                        meta = ctx.column_metadata.get(table_name, {}).get(col.name, {})
                        if meta.get('is_primary') or meta.get('is_foreign'):
                            priority_cols.append(col.name)
                        elif 'date' in col.name.lower() or 'id' in col.name.lower():
                            priority_cols.append(col.name)
                        else:
                            other_cols.append((col.name, usage.get(col.name, 0)))
                    other_cols.sort(key=lambda x: x[1], reverse=True)
                    final_cols = priority_cols[:3]
                    remaining = 5 - len(final_cols)
                    if remaining > 0:
                        for col_name, _ in other_cols[:remaining]:
                            final_cols.append(col_name)
                    if final_cols:
                        selected_columns = final_cols
                    else:
                        # Final fallback: take the first 5 columns
                        selected_columns = [c.name for c in table_obj.columns[:5]]

                    if selected_columns:
                        select.args['expressions'] = [
                            sqlglot.expressions.Column(this=col) for col in selected_columns
                        ]
                        modifications.append(f"SELECT * replaced with {len(selected_columns)} columns (table has {row_count} rows)")

    # 2. Add WHERE (if missing)
    if not parsed.find(sqlglot.expressions.Where):
        best_col = None
        best_table = None
        best_days = None
        for table_name in ctx.table_names:
            table_obj = next((t for t in schema.tables if t.name == table_name), None)
            if not table_obj:
                continue
            for col in table_obj.columns:
                if (table_name, col.name) in ctx.date_ranges:
                    min_val, max_val = ctx.date_ranges[(table_name, col.name)]
                    if min_val and max_val:
                        range_days = (max_val - min_val).days
                        if range_days > 365:
                            days = 30
                        elif range_days > 90:
                            days = 14
                        else:
                            days = 7
                        usage = ctx.column_usage.get(table_name, {}).get(col.name, 0)
                        is_primary = ctx.column_metadata.get(table_name, {}).get(col.name, {}).get('is_primary', False)
                        if is_primary or usage > 0:
                            best_col = col.name
                            best_table = table_name
                            best_days = days
                            break
            if best_col:
                break

        if best_col:
            condition = sqlglot.parse_one(
                f"{best_col} > CURRENT_TIMESTAMP() - INTERVAL {best_days} DAY",
                dialect="bigquery"
            )
            where_node = sqlglot.expressions.Where(this=condition)
            parsed.set('where', where_node)
            modifications.append(f"Added WHERE on {best_col} (filtering last {best_days} days)")
        else:
            # Fallback: add WHERE on first date column with default 30 days
            for table_name in ctx.table_names:
                table_obj = next((t for t in schema.tables if t.name == table_name), None)
                if table_obj:
                    for col in table_obj.columns:
                        if any(k in col.name.lower() for k in ['date', 'timestamp', 'created', 'updated']):
                            condition = sqlglot.parse_one(
                                f"{col.name} > CURRENT_TIMESTAMP() - INTERVAL 30 DAY",
                                dialect="bigquery"
                            )
                            where_node = sqlglot.expressions.Where(this=condition)
                            parsed.set('where', where_node)
                            modifications.append(f"Added WHERE on {col.name} (fallback, 30 days)")
                            break
                if modifications:
                    break

    # 3. Add LIMIT if missing
    if not parsed.find(sqlglot.expressions.Limit):
        max_rows = max([ctx.table_stats.get(t, {}).get('row_count', 0) for t in ctx.table_names], default=0)
        if max_rows > 10_000_000:
            limit = 1000
        elif max_rows > 1_000_000:
            limit = 500
        else:
            limit = 100
        parsed = parsed.limit(limit)
        modifications.append(f"Added LIMIT {limit} (table has {max_rows} rows)")

    # Generate the optimized SQL from the AST
    optimized = parsed.sql(dialect="bigquery")

    # 4. String fallback: if SELECT * is still present, replace it (case-insensitive)
    # This handles cases where the AST mutation didn't stick.
    if "SELECT *" in optimized.upper() and selected_columns:
        # Replace the first occurrence only to avoid messing up subqueries
        optimized = optimized.replace("SELECT *", f"SELECT {', '.join(selected_columns)}", 1)
        modifications.append(f"SELECT * replaced via string fallback with: {', '.join(selected_columns)}")

    # Additional safety: if SELECT * still remains (e.g., if selected_columns was empty), replace with first 5 columns of the first table
    if "SELECT *" in optimized.upper():
        # Extract the first table name from the query
        match = re.search(r"FROM\s+([\w\.]+)", optimized, re.IGNORECASE)
        if match:
            table_full = match.group(1)
            table_name = table_full.split('.')[-1]
            for table in schema.tables:
                if table.name == table_name:
                    fallback_cols = [c.name for c in table.columns[:5]]
                    optimized = optimized.replace("SELECT *", f"SELECT {', '.join(fallback_cols)}", 1)
                    modifications.append(f"SELECT * replaced with fallback columns: {', '.join(fallback_cols)}")
                    break

    explanation = ". ".join(modifications) if modifications else "No optimization needed"

    return {
        "optimized_sql": optimized,
        "rule_applied": explanation
    }