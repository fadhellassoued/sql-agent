from __future__ import annotations

import os
import time
from statistics import median
from typing import Any

import sqlglot
from sqlglot import exp
from google.cloud import bigquery


class BigQueryCandidateValidator:
    """
    Multi-dimensional BigQuery candidate quality gate.

    Dry-run evidence answers:
      - Is the candidate valid BigQuery SQL?
      - How many bytes does BigQuery estimate it will process?
      - What is the estimated on-demand scan cost?

    Dry-run evidence does NOT prove runtime improvement for rewrites
    whose scanned columns are unchanged (for example a join-key CAST
    rewrite). Equal dry-run bytes therefore no longer mean automatic
    rejection.

    Optional runtime validation is intentionally controlled and is
    disabled by default. It is only attempted when:
      - caller explicitly marks the candidate runtime-eligible;
      - BIGQUERY_RUNTIME_VALIDATION_ENABLED=true;
      - both queries are read-only SELECT-like statements;
      - dry-run bytes are under the configured safety ceiling.

    Runtime execution can incur real BigQuery cost. Keep it disabled
    unless you explicitly want controlled benchmarking.
    """

    APPROVED_DECISIONS = {
        "APPROVE_SCAN_COST",
        "APPROVE_RUNTIME_IMPROVEMENT",
    }

    def __init__(self) -> None:
        self.runtime_enabled = (
            os.getenv(
                "BIGQUERY_RUNTIME_VALIDATION_ENABLED",
                "false",
            ).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        self.runtime_max_bytes = int(
            os.getenv(
                "BIGQUERY_RUNTIME_VALIDATION_MAX_BYTES",
                str(1024 ** 3),  # 1 GiB default safety ceiling
            )
        )

        self.runtime_timeout_seconds = float(
            os.getenv(
                "BIGQUERY_RUNTIME_VALIDATION_TIMEOUT_SECONDS",
                "30",
            )
        )

        self.runtime_min_improvement_percent = float(
            os.getenv(
                "BIGQUERY_RUNTIME_MIN_IMPROVEMENT_PERCENT",
                "5.0",
            )
        )

        self.runtime_benchmark_pairs = max(
            1,
            int(
                os.getenv(
                    "BIGQUERY_RUNTIME_BENCHMARK_PAIRS",
                    "3",
                )
            ),
        )

        self.runtime_min_win_count = max(
            1,
            int(
                os.getenv(
                    "BIGQUERY_RUNTIME_MIN_WIN_COUNT",
                    "2",
                )
            ),
        )

        # A minimum win count larger than the number of pairs would make
        # approval impossible, so clamp it defensively.
        self.runtime_min_win_count = min(
            self.runtime_min_win_count,
            self.runtime_benchmark_pairs,
        )

    async def compare(
        self,
        *,
        repository,
        original_sql: str,
        candidate_sql: str,
        original_estimate: dict | None = None,
        allow_runtime_validation: bool = False,
        execute_runtime_validation: bool = False,
    ) -> dict:
        """
        Compare original vs candidate.

        original_estimate lets callers reuse an already-computed
        original dry run, avoiding a duplicate BigQuery API call.
        """

        original = (
            self._normalize_estimate(
                original_estimate
            )
            if original_estimate
            else await self._dry_run(
                repository=repository,
                sql=original_sql,
            )
        )

        try:
            candidate = await self._dry_run(
                repository=repository,
                sql=candidate_sql,
            )
        except Exception as exc:
            return {
                "decision": "REJECT_INVALID_CANDIDATE",
                "approved": False,
                "reason": (
                    "BigQuery could not validate the candidate SQL: "
                    f"{exc}"
                ),
                "scan_cost_status": "INVALID",
                "runtime_status": "NOT_RUN",
                "original": original,
                "candidate": {},
                "savings": {},
                "runtime_evidence": self._runtime_not_run(
                    "Candidate dry-run validation failed."
                ),
            }

        original_bytes = int(
            original.get(
                "bytes_processed",
                0,
            )
            or 0
        )

        candidate_bytes = int(
            candidate.get(
                "bytes_processed",
                0,
            )
            or 0
        )

        original_cost = original.get(
            "estimated_cost_usd"
        )

        candidate_cost = candidate.get(
            "estimated_cost_usd"
        )

        bytes_saved = (
            original_bytes
            - candidate_bytes
        )

        reduction_percent = (
            (
                bytes_saved
                / original_bytes
            )
            * 100.0
            if original_bytes > 0
            else 0.0
        )

        estimated_cost_saved_usd = None

        if (
            original_cost is not None
            and candidate_cost is not None
        ):
            estimated_cost_saved_usd = (
                float(original_cost)
                - float(candidate_cost)
            )

        savings = {
            "bytes_saved": bytes_saved,
            "reduction_percent": reduction_percent,
            "estimated_cost_saved_usd": estimated_cost_saved_usd,
        }

        # =====================================================
        # SCAN-COST GATE
        # =====================================================

        if candidate_bytes < original_bytes:
            return {
                "decision": "APPROVE_SCAN_COST",
                "approved": True,
                "reason": (
                    "Candidate query reduces BigQuery dry-run bytes "
                    "processed."
                ),
                "scan_cost_status": "IMPROVED",
                "runtime_status": "NOT_REQUIRED",
                "original": original,
                "candidate": candidate,
                "savings": savings,
                "runtime_evidence": self._runtime_not_run(
                    "Runtime validation is not required for scan-cost approval."
                ),
            }

        if candidate_bytes > original_bytes:
            return {
                "decision": "REJECT_HIGHER_SCAN_COST",
                "approved": False,
                "reason": (
                    "Candidate query increases BigQuery dry-run bytes "
                    "processed."
                ),
                "scan_cost_status": "REGRESSED",
                "runtime_status": "NOT_RUN",
                "original": original,
                "candidate": candidate,
                "savings": savings,
                "runtime_evidence": self._runtime_not_run(
                    "Runtime validation is skipped because scan cost regressed."
                ),
            }

        # =====================================================
        # EQUAL DRY-RUN BYTES
        # =====================================================

        runtime_evidence = self._runtime_not_run(
            "Dry-run scan cost is unchanged."
        )

        if not allow_runtime_validation:
            return {
                "decision": "SCAN_COST_UNCHANGED",
                "approved": False,
                "reason": (
                    "Candidate query has the same BigQuery dry-run bytes "
                    "as the original. No scan-cost improvement was measured."
                ),
                "scan_cost_status": "UNCHANGED",
                "runtime_status": "NOT_REQUESTED",
                "original": original,
                "candidate": candidate,
                "savings": savings,
                "runtime_evidence": runtime_evidence,
            }

        if not allow_runtime_validation:
            return {
                "decision": "SCAN_COST_UNCHANGED",
                "approved": False,
                "reason": (
                    "Candidate query has the same BigQuery dry-run bytes "
                    "as the original. No scan-cost improvement was measured."
                ),
                "scan_cost_status": "UNCHANGED",
                "runtime_status": "NOT_ELIGIBLE",
                "original": original,
                "candidate": candidate,
                "savings": savings,
                "runtime_evidence": self._runtime_not_run(
                    "This candidate is not eligible for controlled runtime validation."
                ),
            }

        if not self.runtime_enabled:
            return {
                "decision": "NEEDS_RUNTIME_VALIDATION",
                "approved": False,
                "reason": (
                    "Dry-run scan bytes are unchanged. The candidate passed "
                    "the supported semantic proof and is eligible for a "
                    "user-triggered runtime benchmark, but runtime validation "
                    "is disabled by configuration."
                ),
                "scan_cost_status": "UNCHANGED",
                "runtime_status": "DISABLED",
                "original": original,
                "candidate": candidate,
                "savings": savings,
                "runtime_evidence": self._runtime_not_run(
                    "Set BIGQUERY_RUNTIME_VALIDATION_ENABLED=true to allow "
                    "the user-triggered runtime benchmark."
                ),
            }

        runtime_guard = self._runtime_guard(
            original_sql=original_sql,
            candidate_sql=candidate_sql,
            original_bytes=original_bytes,
            candidate_bytes=candidate_bytes,
        )

        if not runtime_guard["allowed"]:
            return {
                "decision": "RUNTIME_VALIDATION_BLOCKED",
                "approved": False,
                "reason": runtime_guard["reason"],
                "scan_cost_status": "UNCHANGED",
                "runtime_status": "BLOCKED",
                "original": original,
                "candidate": candidate,
                "savings": savings,
                "runtime_evidence": self._runtime_not_run(
                    runtime_guard["reason"]
                ),
            }

        # Runtime execution is deliberately user-triggered. Merely enabling
        # the feature in .env makes the benchmark available; it does not
        # execute real BigQuery jobs during normal Optimize Query analysis.
        if not execute_runtime_validation:
            return {
                "decision": "NEEDS_RUNTIME_VALIDATION",
                "approved": False,
                "reason": (
                    "Dry-run scan bytes are unchanged. Semantic proof and "
                    "BigQuery dry-run validation passed. Controlled runtime "
                    "benchmarking is available and must be started explicitly "
                    "by the user."
                ),
                "scan_cost_status": "UNCHANGED",
                "runtime_status": "READY",
                "original": original,
                "candidate": candidate,
                "savings": savings,
                "runtime_evidence": self._runtime_not_run(
                    "Runtime benchmark is ready but has not been started."
                ),
            }

        runtime_evidence = await self._compare_runtime(
            repository=repository,
            original_sql=original_sql,
            candidate_sql=candidate_sql,
        )

        runtime_status = runtime_evidence.get(
            "status",
            "INCONCLUSIVE",
        )

        if runtime_status == "IMPROVED":
            return {
                "decision": "APPROVE_RUNTIME_IMPROVEMENT",
                "approved": True,
                "reason": (
                    "Dry-run scan bytes are unchanged, but controlled "
                    "runtime evidence measured a meaningful reduction in "
                    "BigQuery slot milliseconds."
                ),
                "scan_cost_status": "UNCHANGED",
                "runtime_status": "IMPROVED",
                "original": original,
                "candidate": candidate,
                "savings": savings,
                "runtime_evidence": runtime_evidence,
            }

        if runtime_status == "NO_MEASURED_IMPROVEMENT":
            return {
                "decision": "RUNTIME_NO_MEASURED_IMPROVEMENT",
                "approved": False,
                "reason": (
                    "Dry-run scan bytes are unchanged and controlled runtime "
                    "evidence did not measure a meaningful slot-time improvement."
                ),
                "scan_cost_status": "UNCHANGED",
                "runtime_status": "NO_MEASURED_IMPROVEMENT",
                "original": original,
                "candidate": candidate,
                "savings": savings,
                "runtime_evidence": runtime_evidence,
            }

        return {
            "decision": "RUNTIME_INCONCLUSIVE",
            "approved": False,
            "reason": (
                "Dry-run scan bytes are unchanged and runtime evidence was "
                "not sufficient for approval."
            ),
            "scan_cost_status": "UNCHANGED",
            "runtime_status": runtime_status,
            "original": original,
            "candidate": candidate,
            "savings": savings,
            "runtime_evidence": runtime_evidence,
        }

    # =========================================================
    # DRY RUN
    # =========================================================

    async def _dry_run(
        self,
        *,
        repository,
        sql: str,
    ) -> dict:
        plan = await repository.get_execution_plan(
            sql
        )

        return {
            "bytes_processed": (
                plan.total_bytes_processed
                if plan.total_bytes_processed is not None
                else 0
            ),
            "estimated_cost_usd": plan.cost,
            "scan_type": plan.scan_type,
            "dry_run": True,
        }

    def _normalize_estimate(
        self,
        estimate: dict,
    ) -> dict:
        """
        Accept the WebSocket's already-computed original dry-run data.
        """
        return {
            "bytes_processed": int(
                estimate.get(
                    "bytes_processed",
                    estimate.get(
                        "total_bytes_processed",
                        0,
                    ),
                )
                or 0
            ),
            "estimated_cost_usd": estimate.get(
                "estimated_cost_usd",
                estimate.get(
                    "cost"
                ),
            ),
            "scan_type": estimate.get(
                "scan_type",
                "DryRun",
            ),
            "dry_run": True,
            "reused_existing_estimate": True,
        }

    # =========================================================
    # CONTROLLED RUNTIME VALIDATION
    # =========================================================

    def _runtime_guard(
        self,
        *,
        original_sql: str,
        candidate_sql: str,
        original_bytes: int,
        candidate_bytes: int,
    ) -> dict:
        if not self._is_read_only_query(
            original_sql
        ):
            return {
                "allowed": False,
                "reason": (
                    "Runtime validation is blocked because the original SQL "
                    "is not a read-only SELECT-like query."
                ),
            }

        if not self._is_read_only_query(
            candidate_sql
        ):
            return {
                "allowed": False,
                "reason": (
                    "Runtime validation is blocked because the candidate SQL "
                    "is not a read-only SELECT-like query."
                ),
            }

        largest_dry_run = max(
            original_bytes,
            candidate_bytes,
        )

        if largest_dry_run > self.runtime_max_bytes:
            return {
                "allowed": False,
                "reason": (
                    "Runtime validation is blocked by the configured dry-run "
                    "byte safety ceiling."
                ),
            }

        return {
            "allowed": True,
            "reason": "Runtime safety checks passed.",
        }

    def _is_read_only_query(
        self,
        sql: str,
    ) -> bool:
        try:
            tree = sqlglot.parse_one(
                sql,
                read="bigquery",
            )
        except Exception:
            return False

        forbidden = (
            exp.Insert,
            exp.Update,
            exp.Delete,
            exp.Merge,
            exp.Create,
            exp.Drop,
            exp.Alter,
        )

        for node_type in forbidden:
            if tree.find(
                node_type
            ) is not None:
                return False

        return (
            tree.find(
                exp.Select
            )
            is not None
        )

    async def _compare_runtime(
        self,
        *,
        repository,
        original_sql: str,
        candidate_sql: str,
    ) -> dict:
        """
        Controlled paired benchmark.

        Default:
          Pair 1: original -> candidate
          Pair 2: candidate -> original
          Pair 3: original -> candidate

        Approval evidence is based on median slot_millis across the paired
        runs plus a minimum candidate-win count.
        """

        pairs: list[dict] = []
        original_runs: list[dict] = []
        candidate_runs: list[dict] = []

        try:
            for pair_index in range(
                self.runtime_benchmark_pairs
            ):
                original_first = (
                    pair_index % 2 == 0
                )

                if original_first:
                    execution_order = [
                        ("original", original_sql),
                        ("candidate", candidate_sql),
                    ]
                else:
                    execution_order = [
                        ("candidate", candidate_sql),
                        ("original", original_sql),
                    ]

                pair_results: dict[str, dict] = {}

                for label, sql in execution_order:
                    result = self._execute_runtime_once(
                        repository=repository,
                        sql=sql,
                    )

                    pair_results[label] = result

                    if label == "original":
                        original_runs.append(
                            result
                        )
                    else:
                        candidate_runs.append(
                            result
                        )

                original_slot_ms = (
                    pair_results[
                        "original"
                    ].get(
                        "slot_millis"
                    )
                )

                candidate_slot_ms = (
                    pair_results[
                        "candidate"
                    ].get(
                        "slot_millis"
                    )
                )

                winner = "INCONCLUSIVE"

                if (
                    original_slot_ms is not None
                    and
                    candidate_slot_ms is not None
                ):
                    if (
                        candidate_slot_ms
                        <
                        original_slot_ms
                    ):
                        winner = "CANDIDATE"
                    elif (
                        candidate_slot_ms
                        >
                        original_slot_ms
                    ):
                        winner = "ORIGINAL"
                    else:
                        winner = "TIE"

                pairs.append(
                    {
                        "pair_number":
                            pair_index + 1,

                        "execution_order": [
                            label
                            for label, _
                            in execution_order
                        ],

                        "winner":
                            winner,

                        "original":
                            pair_results[
                                "original"
                            ],

                        "candidate":
                            pair_results[
                                "candidate"
                            ],
                    }
                )

        except Exception as exc:
            return {
                "status":
                    "INCONCLUSIVE",

                "reason": (
                    "Controlled runtime validation failed: "
                    f"{exc}"
                ),

                "benchmark_pairs":
                    self.runtime_benchmark_pairs,

                "completed_pairs":
                    len(
                        pairs
                    ),

                "required_candidate_wins":
                    self.runtime_min_win_count,

                "pairs":
                    pairs,

                "original_runs":
                    original_runs,

                "candidate_runs":
                    candidate_runs,

                "original":
                    {},

                "candidate":
                    {},
            }

        original_slot_values = [
            int(
                run[
                    "slot_millis"
                ]
            )
            for run in original_runs
            if run.get(
                "slot_millis"
            ) is not None
        ]

        candidate_slot_values = [
            int(
                run[
                    "slot_millis"
                ]
            )
            for run in candidate_runs
            if run.get(
                "slot_millis"
            ) is not None
        ]

        original_wall_values = [
            float(
                run[
                    "wall_time_ms"
                ]
            )
            for run in original_runs
            if run.get(
                "wall_time_ms"
            ) is not None
        ]

        candidate_wall_values = [
            float(
                run[
                    "wall_time_ms"
                ]
            )
            for run in candidate_runs
            if run.get(
                "wall_time_ms"
            ) is not None
        ]

        candidate_win_count = sum(
            1
            for pair in pairs
            if pair.get(
                "winner"
            )
            ==
            "CANDIDATE"
        )

        original_win_count = sum(
            1
            for pair in pairs
            if pair.get(
                "winner"
            )
            ==
            "ORIGINAL"
        )

        tie_count = sum(
            1
            for pair in pairs
            if pair.get(
                "winner"
            )
            ==
            "TIE"
        )

        if (
            len(
                original_slot_values
            )
            !=
            self.runtime_benchmark_pairs
            or
            len(
                candidate_slot_values
            )
            !=
            self.runtime_benchmark_pairs
        ):
            return {
                "status":
                    "INCONCLUSIVE",

                "reason": (
                    "BigQuery did not expose comparable slot-millisecond "
                    "values for every paired benchmark run."
                ),

                "benchmark_pairs":
                    self.runtime_benchmark_pairs,

                "completed_pairs":
                    len(
                        pairs
                    ),

                "candidate_win_count":
                    candidate_win_count,

                "required_candidate_wins":
                    self.runtime_min_win_count,

                "pairs":
                    pairs,

                "original_runs":
                    original_runs,

                "candidate_runs":
                    candidate_runs,

                "original":
                    {},

                "candidate":
                    {},
            }

        original_median_slot_ms = float(
            median(
                original_slot_values
            )
        )

        candidate_median_slot_ms = float(
            median(
                candidate_slot_values
            )
        )

        original_median_wall_ms = (
            float(
                median(
                    original_wall_values
                )
            )
            if original_wall_values
            else None
        )

        candidate_median_wall_ms = (
            float(
                median(
                    candidate_wall_values
                )
            )
            if candidate_wall_values
            else None
        )

        improvement_percent = (
            (
                original_median_slot_ms
                -
                candidate_median_slot_ms
            )
            /
            original_median_slot_ms
        ) * 100.0 if (
            original_median_slot_ms
            >
            0
        ) else None

        passes_improvement_threshold = (
            improvement_percent is not None
            and
            improvement_percent
            >=
            self.runtime_min_improvement_percent
        )

        passes_win_count = (
            candidate_win_count
            >=
            self.runtime_min_win_count
        )

        if (
            passes_improvement_threshold
            and
            passes_win_count
        ):
            status = (
                "IMPROVED"
            )

            reason = (
                "Candidate passed the paired runtime benchmark: median "
                "slot milliseconds improved by at least the configured "
                "threshold and the candidate won the required number "
                "of paired runs."
            )

        else:
            status = (
                "NO_MEASURED_IMPROVEMENT"
            )

            reason = (
                "Candidate did not satisfy both runtime approval "
                "requirements: median slot-time improvement threshold "
                "and minimum paired-run win count."
            )

        return {
            "status":
                status,

            "reason":
                reason,

            "benchmark_pairs":
                self.runtime_benchmark_pairs,

            "completed_pairs":
                len(
                    pairs
                ),

            "minimum_improvement_percent":
                self.runtime_min_improvement_percent,

            "required_candidate_wins":
                self.runtime_min_win_count,

            "candidate_win_count":
                candidate_win_count,

            "original_win_count":
                original_win_count,

            "tie_count":
                tie_count,

            "slot_millis_improvement_percent":
                improvement_percent,

            # Keep aggregate original/candidate keys for the existing UI.
            "original": {
                "slot_millis":
                    original_median_slot_ms,

                "wall_time_ms":
                    original_median_wall_ms,

                "aggregation":
                    "MEDIAN",

                "run_count":
                    len(
                        original_runs
                    ),
            },

            "candidate": {
                "slot_millis":
                    candidate_median_slot_ms,

                "wall_time_ms":
                    candidate_median_wall_ms,

                "aggregation":
                    "MEDIAN",

                "run_count":
                    len(
                        candidate_runs
                    ),
            },

            "original_runs":
                original_runs,

            "candidate_runs":
                candidate_runs,

            "pairs":
                pairs,

            "warning": (
                "Runtime evidence is based on paired benchmark observations "
                "with alternating execution order. It is still not a guarantee "
                "of future BigQuery performance."
            ),
        }

    def _execute_runtime_once(
        self,
        *,
        repository,
        sql: str,
    ) -> dict:
        client = repository.client

        job_config = bigquery.QueryJobConfig(
            use_query_cache=False,
            maximum_bytes_billed=(
                self.runtime_max_bytes
            ),
        )

        started = time.perf_counter()

        query_job = client.query(
            sql,
            job_config=job_config,
        )

        try:
            # result() waits for query completion. We intentionally do not
            # iterate through every returned row.
            query_job.result(
                timeout=(
                    self.runtime_timeout_seconds
                )
            )
        except Exception:
            try:
                query_job.cancel()
            except Exception:
                pass
            raise

        wall_time_ms = (
            time.perf_counter()
            - started
        ) * 1000.0

        return {
            "wall_time_ms": wall_time_ms,
            "slot_millis": getattr(
                query_job,
                "slot_millis",
                None,
            ),
            "total_bytes_processed": getattr(
                query_job,
                "total_bytes_processed",
                None,
            ),
            "total_bytes_billed": getattr(
                query_job,
                "total_bytes_billed",
                None,
            ),
            "cache_hit": getattr(
                query_job,
                "cache_hit",
                None,
            ),
        }

    def _runtime_not_run(
        self,
        reason: str,
    ) -> dict:
        return {
            "status": "NOT_RUN",
            "reason": reason,
            "original": {},
            "candidate": {},
        }