from __future__ import annotations

import re
from typing import Any


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_PROJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class BigQueryPreaggregationCandidateGenerator:
    """Controlled builder for a semantically proven pre-aggregation rewrite."""

    REQUIRED_PROOF_STATUS = "PROVEN_FOR_SUPPORTED_PREAGGREGATION_PATTERN"

    def generate(self, *, semantic_proof: dict | None) -> dict[str, Any]:
        proof = semantic_proof or {}
        if proof.get("status") != self.REQUIRED_PROOF_STATUS:
            return {
                "generated": False,
                "candidate_sql": None,
                "rewrite_type": None,
                "rule_codes": [],
                "semantic_proof": proof,
                "requires_quality_gate": False,
                "reason": "Deterministic pre-aggregation semantic proof did not succeed.",
            }

        candidate_table = (proof.get("candidate_table") or "").strip()
        output_columns = proof.get("output_columns", []) or []
        if not candidate_table or not output_columns:
            return {
                "generated": False,
                "candidate_sql": None,
                "rewrite_type": None,
                "rule_codes": [],
                "semantic_proof": proof,
                "requires_quality_gate": False,
                "reason": "Semantic proof is missing candidate mapping data.",
            }

        parts = candidate_table.split(".")
        if (
            len(parts) != 3
            or not _SAFE_PROJECT.fullmatch(parts[0])
            or not _SAFE_IDENTIFIER.fullmatch(parts[1])
            or not _SAFE_IDENTIFIER.fullmatch(parts[2])
        ):
            return {
                "generated": False,
                "candidate_sql": None,
                "rewrite_type": None,
                "rule_codes": [],
                "semantic_proof": proof,
                "requires_quality_gate": False,
                "reason": "Candidate table identifier failed validation.",
            }

        if any(not _SAFE_IDENTIFIER.fullmatch(str(column)) for column in output_columns):
            return {
                "generated": False,
                "candidate_sql": None,
                "rewrite_type": None,
                "rule_codes": [],
                "semantic_proof": proof,
                "requires_quality_gate": False,
                "reason": "Candidate output identifier failed validation.",
            }

        sql = (
            "SELECT "
            + ", ".join(f"`{column}`" for column in output_columns)
            + f" FROM `{candidate_table}`"
        )

        order_by = proof.get("order_by", []) or []
        if order_by:
            order_parts = []
            for item in order_by:
                column = str(item.get("column") or "")
                if column not in output_columns or not _SAFE_IDENTIFIER.fullmatch(column):
                    return {
                        "generated": False,
                        "candidate_sql": None,
                        "rewrite_type": None,
                        "rule_codes": [],
                        "semantic_proof": proof,
                        "requires_quality_gate": False,
                        "reason": "ORDER BY mapping failed deterministic validation.",
                    }
                direction = " DESC" if item.get("descending", False) else ""
                order_parts.append(f"`{column}`{direction}")
            sql += " ORDER BY " + ", ".join(order_parts)

        return {
            "generated": True,
            "candidate_sql": sql,
            "rewrite_type": "PREAGGREGATED_TABLE_SUBSTITUTION",
            "rule_codes": ["BQ_PREAGGREGATION_SUBSTITUTION"],
            "semantic_proof": proof,
            "requires_quality_gate": True,
            "reason": "Controlled candidate generated after deterministic semantic proof.",
        }