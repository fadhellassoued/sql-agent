from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AgentProposedChange(BaseModel):
    code: str
    title: str
    rationale: str
    candidate_kind: str | None = None
    requires_user_intent: bool = False
    requires_semantic_proof: bool = False


class AgentOptimizationPlan(BaseModel):
    """
    Strategy-only output from the LLM agent.

    This plan is NOT executable SQL and is NOT approval to run a query.
    Candidate construction and validation remain deterministic.
    """

    goal: str = Field(
        description="What the agent is trying to improve."
    )

    strategy: Literal[
        "NO_CHANGE",
        "COLUMN_PRUNING",
        "JOIN_REWRITE",
        "FILTER_REVIEW",
        "PARTITION_REVIEW",
        "PREAGGREGATION_REVIEW",
        "GENERAL_REVIEW",
    ]

    rationale: str

    tools_used: list[str] = Field(
        default_factory=list
    )

    evidence_refs: list[str] = Field(
        default_factory=list
    )

    proposed_changes: list[AgentProposedChange] = Field(
        default_factory=list
    )

    requires_user_intent: bool = False

    requested_user_input: str | None = None

    candidate_generation_requested: bool = False

    confidence: Literal[
        "low",
        "medium",
        "high",
    ] = "medium"

    limitations: list[str] = Field(
        default_factory=list
    )