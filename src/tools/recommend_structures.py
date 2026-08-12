from domain.database_schema import DatabaseSchema

async def recommend_structures(sql: str, schema: DatabaseSchema) -> list:
    recommendations = []
    # Basic: recommend indexes on columns that appear in WHERE clauses (simplified)
    # In real implementation, parse SQL for column references
    # For demo, suggest indexing on any column ending with '_id' or 'date'
    for table in schema.tables:
        for col in table.columns:
            if col.name.endswith("_id") or "date" in col.name.lower():
                recommendations.append(
                    f"Consider creating an index on {table.name}({col.name}) if frequently filtered."
                )
    return recommendations[:3]  # Limit to 3