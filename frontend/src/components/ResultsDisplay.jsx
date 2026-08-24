import React, {
  useEffect,
  useMemo,
  useState,
} from 'react';

import axios from 'axios';

import ReactMarkdown from 'react-markdown';


const API_URL = (
  import.meta.env.VITE_API_URL ||
  'http://localhost:8000/api/v1'
);


function formatBytes(
  bytes
) {

  if (
    bytes === null ||
    bytes === undefined
  ) {

    return '—';
  }

  if (
    Number(bytes) === 0
  ) {

    return '0 B';
  }

  const units = [
    'B',
    'KiB',
    'MiB',
    'GiB',
    'TiB',
    'PiB',
  ];

  let value =
    Number(bytes);

  let unitIndex = 0;

  while (
    value >= 1024 &&
    unitIndex <
      units.length - 1
  ) {

    value /= 1024;

    unitIndex += 1;
  }

  return `${
    value.toFixed(
      value >= 10
        ? 1
        : 2
    )
  } ${units[unitIndex]}`;
}


function formatCost(
  cost
) {

  if (
    cost === null ||
    cost === undefined
  ) {

    return '—';
  }

  const value = (
    Number(cost)
  );

  if (
    value < 0.01
  ) {

    return `$${value.toFixed(
      6
    )}`;
  }

  return `$${value.toFixed(
    4
  )}`;
}


function formatLatency(
  ms
) {

  if (
    ms === null ||
    ms === undefined
  ) {

    return '—';
  }

  return `${
    Number(ms).toFixed(
      2
    )
  } ms`;
}


function ResultsDisplay({
  result,
  costEstimate,
  streamingExplanation,
  isStreaming,
  onUseCandidate,
}) {

  const metrics = (
    result?.metrics ||
    {}
  );

  const optimizationChanged = (
    result
      ?.optimization_changed
    ??
    false
  );

  const optimizationStatus = (
    result
      ?.optimization_status
    ||
    (
      optimizationChanged
        ? 'CANDIDATE_REWRITE'
        : 'NO_SAFE_REWRITE'
    )
  );

  const optimizationRules = (
    result
      ?.optimization_rules
    ||
    []
  );

  const requiredUserInput = (
    result
      ?.required_user_input
    ||
    null
  );

  // ============================================================
  // CROSS JOIN USER-INTENT UX
  // ============================================================
  // CROSS JOIN intent currently comes from deterministic join
  // optimization evidence (BQ_OPT_CROSS_JOIN_REVIEW) rather than
  // the SELECT-* required_user_input payload. Detect the validated
  // backend finding here so NEEDS_USER_INTENT can render a specific
  // and actionable message without inventing a join predicate.

  const databaseFindings = (
    result
      ?.database_analysis
      ?.findings
    ||
    []
  );

  const joinOpportunities = (
    result
      ?.bigquery_join_optimization
      ?.opportunities
    ||
    []
  );

  const hasCrossJoinIntentRequirement = (
    databaseFindings.some(
      (finding) =>
        finding?.code === 'BQ_CROSS_JOIN'
    )
    ||
    joinOpportunities.some(
      (opportunity) =>
        opportunity?.code === 'BQ_OPT_CROSS_JOIN_REVIEW'
        &&
        opportunity?.rewrite_status === 'REQUIRES_USER_INTENT'
    )
  );

  // ============================================================
  // INFORMATIONAL DATABASE FINDINGS
  // ============================================================

  const informationFindings = (
    Array.isArray(
      result?.information
    )
      ? result.information
      : (
          result
            ?.database_analysis
            ?.informational_findings
          ||
          []
        )
          .map(
            (finding) =>
              finding?.message
          )
          .filter(
            Boolean
          )
  );


  // ============================================================
  // COLUMN-SELECTION STATE
  // ============================================================

  const availableColumns = (
    requiredUserInput
      ?.columns
    ||
    []
  );

  const [selectedColumns, setSelectedColumns] = (
    useState(
      []
    )
  );

  const [generatingCandidate, setGeneratingCandidate] = (
    useState(
      false
    )
  );

  const [candidateResult, setCandidateResult] = (
    useState(
      null
    )
  );

  const [candidateError, setCandidateError] = (
    useState(
      ''
    )
  );


  const [runningRuntimeBenchmark, setRunningRuntimeBenchmark] = (
    useState(
      false
    )
  );

  const [runtimeBenchmarkError, setRuntimeBenchmarkError] = (
    useState(
      ''
    )
  );

  const automaticJoinCandidate = (
    result
      ?.bigquery_join_candidate
    ||
    null
  );

  // Reset selection whenever the optimization result changes.
  useEffect(
    () => {

      setSelectedColumns(
        []
      );

      setCandidateResult(
        (
          automaticJoinCandidate
          &&
          automaticJoinCandidate.generated
            ? {
                ...automaticJoinCandidate,
                original_query:
                  result?.original_query,
              }
            : null
        )
      );

      setCandidateError(
        ''
      );

      setRuntimeBenchmarkError(
        ''
      );

    },
    [
      result
        ?.original_query,
      optimizationStatus,
      automaticJoinCandidate,
    ]
  );

  // ============================================================
  // COLUMN HELPERS
  // ============================================================

  const allColumnNames = (
    useMemo(
      () =>
        availableColumns
          .map(
            (column) =>
              column?.name
          )
          .filter(
            Boolean
          ),
      [
        availableColumns,
      ]
    )
  );

  const allSelected = (
    allColumnNames.length > 0
    &&
    selectedColumns.length ===
      allColumnNames.length
  );

  const toggleColumn = (
    columnName
  ) => {

    setSelectedColumns(
      (current) => {

        if (
          current.includes(
            columnName
          )
        ) {

          return current.filter(
            (name) =>
              name !== columnName
          );
        }

        return [
          ...current,
          columnName,
        ];
      }
    );

    setCandidateResult(
      null
    );

    setCandidateError(
      ''
    );
  };


  const toggleAllColumns = () => {

    if (allSelected) {

      setSelectedColumns(
        []
      );

    } else {

      setSelectedColumns(
        allColumnNames
      );
    }

    setCandidateResult(
      null
    );

    setCandidateError(
      ''
    );
  };


  // ============================================================
  // GENERATE + VALIDATE CANDIDATE
  // ============================================================

  const handleGenerateCandidate = async () => {

    if (
      !result?.session_id
    ) {

      setCandidateError(
        'Session ID is missing from the optimization result.'
      );

      return;
    }

    if (
      !result?.original_query
    ) {

      setCandidateError(
        'Original SQL query is missing.'
      );

      return;
    }

    if (
      selectedColumns.length === 0
    ) {

      setCandidateError(
        'Choose at least one column first.'
      );

      return;
    }

    setGeneratingCandidate(
      true
    );

    setCandidateError(
      ''
    );

    setCandidateResult(
      null
    );

    try {

      const response = (
        await axios.post(
          `${API_URL}/optimization/bigquery/columns`,
          {
            session_id:
              result.session_id,

            sql_query:
              result.original_query,

            selected_columns:
              selectedColumns,
          },
          {
            withCredentials:
              true,
          }
        )
      );

      setCandidateResult(
        response.data
      );

    } catch (error) {

      const message = (
        error
          ?.response
          ?.data
          ?.detail
        ||
        error
          ?.message
        ||
        'Unable to generate candidate query.'
      );

      setCandidateError(
        message
      );

    } finally {

      setGeneratingCandidate(
        false
      );
    }
  };


  // ============================================================
  // USER-TRIGGERED RUNTIME BENCHMARK
  // ============================================================

  const handleRuntimeBenchmark = async () => {

    if (
      !result?.session_id
      ||
      !result?.original_query
    ) {
      setRuntimeBenchmarkError(
        'Session or original SQL is missing.'
      );
      return;
    }

    setRunningRuntimeBenchmark(
      true
    );

    setRuntimeBenchmarkError(
      ''
    );

    try {

      const response = (
        await axios.post(
          `${API_URL}/optimization/bigquery/runtime`,
          {
            session_id:
              result.session_id,

            sql_query:
              result.original_query,
          },
          {
            withCredentials:
              true,
          }
        )
      );

      setCandidateResult(
        response.data
      );

    } catch (error) {

      setRuntimeBenchmarkError(
        error
          ?.response
          ?.data
          ?.detail
        ||
        error
          ?.message
        ||
        'Runtime benchmark could not be completed.'
      );

    } finally {

      setRunningRuntimeBenchmark(
        false
      );
    }
  };


  // Only use a candidate if it belongs to the SQL result that is
  // currently on screen. React effects run after render, so without
  // this guard a candidate from the previous query can briefly leak
  // into the next query's status/UI.
  const currentCandidateResult = (
    candidateResult
    &&
    candidateResult?.original_query ===
      result?.original_query
      ? candidateResult
      : null
  );

  // ============================================================
  // CANDIDATE QUALITY GATE
  // ============================================================

  const qualityGate = (
    currentCandidateResult
      ?.quality_gate
    ||
    null
  );

  const candidateDecision = (
    qualityGate
      ?.decision
    ||
    null
  );

  const candidateApproved = (
    [
      'APPROVE_SCAN_COST',
      'APPROVE_RUNTIME_IMPROVEMENT',
      // Backward compatibility with older validator responses.
      'APPROVE',
    ].includes(
      candidateDecision
    )
  );

  const semanticProof = (
    currentCandidateResult
      ?.semantic_proof
    ||
    {}
  );

  const runtimeEvidence = (
    qualityGate
      ?.runtime_evidence
    ||
    {}
  );

  const originalRuntime = (
    runtimeEvidence
      ?.original
    ||
    {}
  );

  const candidateRuntime = (
    runtimeEvidence
      ?.candidate
    ||
    {}
  );


  const runtimePairCount = (
    runtimeEvidence
      ?.completed_pairs
    ??
    runtimeEvidence
      ?.benchmark_pairs
    ??
    null
  );

  const runtimeCandidateWins = (
    runtimeEvidence
      ?.candidate_win_count
    ??
    null
  );

  const runtimeRequiredWins = (
    runtimeEvidence
      ?.required_candidate_wins
    ??
    null
  );


  const runtimeBenchmarkDurationMs = (
    currentCandidateResult
      ?.runtime_benchmark_duration_ms
    ??
    null
  );

  const effectiveOptimizationStatus = (
    currentCandidateResult
      ?.optimization_status
    ||
    optimizationStatus
  );

  const effectiveOptimizationRules = (
    currentCandidateResult
      ?.optimization_rules
    ||
    optimizationRules
  );

  const candidateExplanation = (
    currentCandidateResult
      ?.candidate_explanation
    ||
    null
  );

  const originalComparison = (
    qualityGate
      ?.original
    ||
    {}
  );

  const candidateComparison = (
    qualityGate
      ?.candidate
    ||
    {}
  );

  const savings = (
    qualityGate
      ?.savings
    ||
    {}
  );

  // ============================================================
  // OPTIMIZATION STATUS MESSAGE
  // ============================================================

  const renderOptimizationStatus = () => {

    if (
      effectiveOptimizationStatus ===
      'NEEDS_USER_INTENT'
    ) {

      if (
        hasCrossJoinIntentRequirement
      ) {

        return (
          <p className="warning">
            ⚠️ CROSS JOIN requires your intent. Confirm that the
            Cartesian product is intentional, or provide the correct
            join predicate in the SQL editor. The optimizer will not
            invent a join condition.
          </p>
        );
      }

      return (

        <p className="warning">

          ⚠️ The optimizer needs your input
          before it can safely generate a
          performance candidate.

        </p>
      );
    }

    if (
      effectiveOptimizationStatus ===
      'CANDIDATE_REWRITE'
    ) {

      return (

        <p className="success">

          ✅ A performance-oriented deterministic
          candidate rewrite was generated.

        </p>
      );
    }

    if (
      effectiveOptimizationStatus ===
      'NEEDS_RUNTIME_VALIDATION'
    ) {

      return (

        <p className="info">

          🧪 A semantically proven candidate was generated.
          BigQuery scan bytes are unchanged, so runtime
          validation is the next evidence gate.

        </p>
      );
    }

    if (
      effectiveOptimizationStatus ===
      'SCAN_COST_UNCHANGED'
    ) {

      return (

        <p className="info">

          ℹ️ A candidate was generated and evaluated,
          but it has not been approved as an applied rewrite.

        </p>
      );
    }

    if (
      effectiveOptimizationStatus ===
      'RUNTIME_VALIDATION_BLOCKED'
    ) {

      return (
        <p className="warning">
          🛡️ A semantically valid candidate was generated, but runtime
          validation was not permitted by the configured safety policy.
          The candidate was not rejected for semantic correctness.
        </p>
      );
    }

    if (
      [
        'REJECT_HIGHER_SCAN_COST',
        'REJECT_INVALID_CANDIDATE',
        'REJECT_VALIDATION_ERROR',
        'RUNTIME_NO_MEASURED_IMPROVEMENT',
        'RUNTIME_INCONCLUSIVE',
      ].includes(
        effectiveOptimizationStatus
      )
    ) {

      return (

        <p className="warning">

          ⚠️ A candidate was tested but failed one of the
          quality-gate requirements.

        </p>
      );
    }

    if (
      effectiveOptimizationStatus ===
      'APPROVE_SCAN_COST'
    ) {

      return (
        <p className="success">
          ✅ Candidate approved by the BigQuery scan-cost quality gate.
        </p>
      );
    }

    if (
      effectiveOptimizationStatus ===
      'APPROVE_RUNTIME_IMPROVEMENT'
    ) {

      return (
        <p className="success">
          ✅ Candidate approved by controlled runtime evidence.
        </p>
      );
    }

    if (
      effectiveOptimizationStatus ===
      'RECOMMENDATIONS_ONLY'
    ) {

      return (
        <p className="info">
          ℹ️ Review opportunities were found, but no validated executable
          rewrite is currently approved.
        </p>
      );
    }

    if (
      effectiveOptimizationStatus ===
      'NO_OPTIMIZATION_NEEDED'
    ) {

      return (
        <p className="success">
          ✅ No supported optimization opportunity requiring action was found.
        </p>
      );
    }

    return (
      <p className="info">
        ℹ️ Optimization analysis completed with status {
          effectiveOptimizationStatus
        }.
      </p>
    );
  };


  // ============================================================
  // RENDER
  // ============================================================

  return (

    <div className="results-display">

      <h3>
        📊 Optimization Results
      </h3>

      <div className="result-grid">

        {/* ===================================================== */}
        {/* ORIGINAL QUERY COST */}
        {/* ===================================================== */}

        {
          costEstimate &&
          (

            <div className="result-card full-width original-cost">

              <h4>
                💰 Original Query — Pre-Optimization Estimate
              </h4>

              <div className="metrics-comparison-grid">

                <div className="metric-item">

                  <span className="metric-label">
                    Data Processed
                  </span>

                  <span className="metric-value">
                    {
                      formatBytes(
                        costEstimate
                          .bytes_processed
                      )
                    }
                  </span>

                </div>

                <div className="metric-item">

                  <span className="metric-label">
                    Estimated On-Demand Cost
                  </span>

                  <span className="metric-value">
                    {
                      formatCost(
                        costEstimate
                          .estimated_cost_usd
                      )
                    }
                  </span>

                </div>

                <div className="metric-item">

                  <span className="metric-label">
                    Threshold
                  </span>

                  <span className="metric-value">
                    {
                      formatCost(
                        costEstimate
                          .threshold_usd
                      )
                    }
                  </span>

                </div>

                <div className="metric-item">

                  <span className="metric-label">
                    Dry Run
                  </span>

                  <span className="metric-value">
                    ✅ Yes
                  </span>

                </div>

              </div>

              {
                costEstimate
                  .exceeds_threshold
                  ? (

                    <p className="warning">

                      ⚠️ Original query exceeds
                      the configured maximum cost
                      threshold.

                    </p>

                  )
                  : (

                    <p className="success">

                      ✅ Original query is below
                      the configured maximum cost
                      threshold.

                    </p>
                  )
              }

              <small>

                Estimated using BigQuery dry-run
                bytes. For projects using capacity
                / Editions pricing, this dollar
                value is not the actual per-query
                billing amount.

              </small>

            </div>
          )
        }

        {/* ===================================================== */}
        {/* ISSUES */}
        {/* ===================================================== */}

        <div className="result-card issues">

          <h4>
            ❌ Issues Detected
          </h4>

          {
            result
              ?.issues
            &&
            result.issues.length > 0
              ? (

                <ul>

                  {
                    result.issues.map(
                      (
                        issue,
                        i
                      ) => (

                        <li key={i}>
                          {issue}
                        </li>

                      )
                    )
                  }

                </ul>

              )
              : (

                <p className="success">
                  ✅ No issues detected
                </p>
              )
          }

        </div>

        {/* ===================================================== */}
        {/* INFORMATIONAL ANALYSIS FINDINGS */}
        {/* ===================================================== */}

        {
          informationFindings.length > 0
          &&
          (

            <div className="result-card analysis-findings">

              <h4>
                🔎 Analysis Findings
              </h4>

              <ul>

                {
                  informationFindings.map(
                    (
                      finding,
                      i
                    ) => (

                      <li key={`info-${i}`}>
                        ℹ️ {finding}
                      </li>

                    )
                  )
                }

              </ul>

            </div>
          )
        }

        {/* ===================================================== */}
        {/* SQL / STATUS */}
        {/* ===================================================== */}

        <div className="result-card optimized">

          <h4>

            {
              currentCandidateResult
                ? '🧾 Original / Current Query'
                : (
                    effectiveOptimizationStatus ===
                    'NEEDS_USER_INTENT'
                      ? '🧠 Current Query'
                      : '⚡ Normalized / Current Query'
                  )
            }

          </h4>

          <pre className="sql-code">
            {
              currentCandidateResult
                ? (
                    result
                      ?.original_query
                    ||
                    result
                      ?.optimized_query
                  )
                : (
                    result
                      ?.optimized_query
                  )
              ||
              'Waiting...'
            }
          </pre>

          {
            (
              currentCandidateResult
                ? result?.original_query
                : result?.optimized_query
            )
            &&
            (

              <button
                className="btn-copy"

                onClick={() =>
                  navigator
                    .clipboard
                    .writeText(
                      currentCandidateResult
                        ? result.original_query
                        : result.optimized_query
                    )
                }
              >
                📋 Copy
              </button>
            )
          }

          {
            result &&
            (

              <div className="optimization-status">

                {
                  renderOptimizationStatus()
                }

              </div>
            )
          }

        </div>

        {/* ===================================================== */}
        {/* USER INPUT: CROSS JOIN INTENT */}
        {/* ===================================================== */}

        {
          effectiveOptimizationStatus ===
            'NEEDS_USER_INTENT'
          &&
          hasCrossJoinIntentRequirement
          &&
          (
            <div className="result-card full-width user-intent">
              <h4>
                🔀 Confirm CROSS JOIN Intent
              </h4>

              <p>
                This query contains a CROSS JOIN, so BigQuery will form
                a Cartesian combination of the joined inputs. The optimizer
                cannot safely infer a replacement join condition.
              </p>

              <p>
                <strong>Is this Cartesian product intentional?</strong>
              </p>

              <ul>
                <li>
                  <strong>Yes:</strong> keep the CROSS JOIN as written. No
                  automatic rewrite is required.
                </li>
                <li>
                  <strong>No:</strong> replace CROSS JOIN with the intended
                  JOIN and ON predicate in the SQL editor, then run Optimize
                  Query again.
                </li>
              </ul>

              <small>
                Safety rule: the optimizer never invents a business join
                predicate because doing so could change query semantics.
              </small>
            </div>
          )
        }

        {/* ===================================================== */}
        {/* USER INPUT: COLUMN SELECTION */}
        {/* ===================================================== */}

        {
          effectiveOptimizationStatus ===
            'NEEDS_USER_INTENT'
          &&
          requiredUserInput
          &&
          requiredUserInput.type ===
            'column_selection'
          &&
          (

            <div className="result-card full-width user-intent">

              <h4>
                🧩 Choose the Columns You Actually Need
              </h4>

              <p>

                {
                  requiredUserInput.reason
                }

              </p>

              {
                requiredUserInput.table &&
                (

                  <p>
                    <strong>Table:</strong>{' '}
                    {requiredUserInput.table}
                  </p>
                )
              }

              {
                availableColumns.length > 0
                  ? (

                    <>

                      <div className="column-selection-toolbar">

                        <label>

                          <input
                            type="checkbox"

                            checked={
                              allSelected
                            }

                            onChange={
                              toggleAllColumns
                            }
                          />

                          {' '}
                          Select all

                        </label>

                        <span>
                          {
                            selectedColumns.length
                          } / {
                            allColumnNames.length
                          } selected
                        </span>

                      </div>

                      <div className="column-selection-grid">

                        {
                          availableColumns.map(
                            (
                              column
                            ) => (

                              <label
                                key={
                                  column.name
                                }
                                className="column-option"
                              >

                                <input
                                  type="checkbox"

                                  checked={
                                    selectedColumns.includes(
                                      column.name
                                    )
                                  }

                                  onChange={() =>
                                    toggleColumn(
                                      column.name
                                    )
                                  }
                                />

                                <span>

                                  <strong>
                                    {column.name}
                                  </strong>

                                  {
                                    column.type &&
                                    (
                                      <>
                                        {' '}
                                        <small>
                                          ({column.type})
                                        </small>
                                      </>
                                    )
                                  }

                                </span>

                              </label>
                            )
                          )
                        }

                      </div>

                      <div className="candidate-actions">

                        <button
                          type="button"

                          className="btn-analyze"

                          disabled={
                            generatingCandidate
                            ||
                            selectedColumns.length === 0
                          }

                          onClick={
                            handleGenerateCandidate
                          }
                        >

                          {
                            generatingCandidate
                              ? '⏳ Validating Candidate...'
                              : '⚡ Generate & Validate Candidate'
                          }

                        </button>

                      </div>

                    </>

                  )
                  : (

                    <p className="warning">

                      Column metadata was not available.
                      The optimizer cannot safely create
                      a column-pruned candidate.

                    </p>
                  )
              }

              {
                candidateError &&
                (

                  <p className="warning">
                    ⚠️ {candidateError}
                  </p>
                )
              }

            </div>
          )
        }

        {/* ===================================================== */}
        {/* CANDIDATE COMPARISON / QUALITY GATE */}
        {/* ===================================================== */}

        {
          currentCandidateResult &&
          (

            <div className="result-card full-width candidate-comparison">

              <h4>
                🧪 Original vs Candidate — BigQuery Quality Gate
              </h4>

              <div className="metrics-comparison-grid">

                <div className="metric-item">

                  <span className="metric-label">
                    Original Data
                  </span>

                  <span className="metric-value">
                    {
                      formatBytes(
                        originalComparison
                          .bytes_processed
                      )
                    }
                  </span>

                </div>

                <div className="metric-item">

                  <span className="metric-label">
                    Candidate Data
                  </span>

                  <span className="metric-value">
                    {
                      formatBytes(
                        candidateComparison
                          .bytes_processed
                      )
                    }
                  </span>

                </div>

                <div className="metric-item">

                  <span className="metric-label">
                    Original Cost
                  </span>

                  <span className="metric-value">
                    {
                      formatCost(
                        originalComparison
                          .estimated_cost_usd
                      )
                    }
                  </span>

                </div>

                <div className="metric-item">

                  <span className="metric-label">
                    Candidate Cost
                  </span>

                  <span className="metric-value">
                    {
                      formatCost(
                        candidateComparison
                          .estimated_cost_usd
                      )
                    }
                  </span>

                </div>

                <div className="metric-item">

                  <span className="metric-label">
                    Data Reduction
                  </span>

                  <span className="metric-value">

                    {
                      savings
                        .reduction_percent
                      !== undefined
                      &&
                      savings
                        .reduction_percent
                      !== null

                        ? `${
                            Number(
                              savings
                                .reduction_percent
                            ).toFixed(
                              2
                            )
                          }%`

                        : '—'
                    }

                  </span>

                </div>

                <div className="metric-item">

                  <span className="metric-label">
                    Estimated Cost Saved
                  </span>

                  <span className="metric-value">

                    {
                      formatCost(
                        savings
                          .estimated_cost_saved_usd
                      )
                    }

                  </span>

                </div>

              </div>

              <div className="quality-gate-status">

                {
                  candidateApproved
                    ? (

                      <p className="success">

                        ✅ {candidateDecision} — {
                          qualityGate.reason
                        }

                      </p>

                    )
                    : candidateDecision ===
                        'NEEDS_RUNTIME_VALIDATION'
                      ? (

                        <p className="info">

                          🧪 NEEDS_RUNTIME_VALIDATION — {
                            qualityGate.reason
                          }

                        </p>

                      )
                      : candidateDecision ===
                          'SCAN_COST_UNCHANGED'
                        ? (

                          <p className="info">

                            ℹ️ SCAN_COST_UNCHANGED — {
                              qualityGate.reason
                            }

                          </p>

                        )
                        : (

                          <p className="warning">

                            ⚠️ {
                              candidateDecision
                              ||
                              'QUALITY_GATE_NOT_APPROVED'
                            } — {
                              qualityGate
                                ?.reason
                            }

                          </p>
                        )
                }

              </div>

              {
                candidateDecision ===
                  'NEEDS_RUNTIME_VALIDATION'
                &&
                qualityGate
                  ?.runtime_status ===
                  'READY'
                &&
                (

                  <div className="candidate-actions">

                    <button
                      type="button"

                      className="btn-primary"

                      disabled={
                        runningRuntimeBenchmark
                      }

                      onClick={
                        handleRuntimeBenchmark
                      }
                    >
                      {
                        runningRuntimeBenchmark
                          ? '⏱️ Running 3-Pair Benchmark...'
                          : '⏱️ Run Runtime Benchmark'
                      }
                    </button>

                    <small>
                      Executes real BigQuery jobs. The backend re-establishes
                      semantic proof and enforces the configured byte ceiling
                      before benchmarking.
                    </small>

                  </div>
                )
              }

              {
                runtimeBenchmarkError
                &&
                (
                  <p className="warning">
                    ⚠️ {runtimeBenchmarkError}
                  </p>
                )
              }

              <div className="metrics-comparison-grid">

                <div className="metric-item">
                  <span className="metric-label">
                    {
                      semanticProof?.status ===
                        'USER_INTENT_CONFIRMED'
                        ? 'Safety Basis'
                        : 'Semantic Proof'
                    }
                  </span>
                  <span className="metric-value">
                    {
                      semanticProof
                        ?.status
                      ||
                      '—'
                    }
                  </span>
                </div>

                <div className="metric-item">
                  <span className="metric-label">
                    Scan-Cost Status
                  </span>
                  <span className="metric-value">
                    {
                      qualityGate
                        ?.scan_cost_status
                      ||
                      '—'
                    }
                  </span>
                </div>

                <div className="metric-item">
                  <span className="metric-label">
                    Runtime Status
                  </span>
                  <span className="metric-value">
                    {
                      qualityGate
                        ?.runtime_status
                      ||
                      runtimeEvidence
                        ?.status
                      ||
                      'NOT_RUN'
                    }
                  </span>
                </div>

                <div className="metric-item">
                  <span className="metric-label">
                    Slot-Time Improvement
                  </span>
                  <span className="metric-value">
                    {
                      runtimeEvidence
                        ?.slot_millis_improvement_percent
                      !== undefined
                      &&
                      runtimeEvidence
                        ?.slot_millis_improvement_percent
                      !== null
                        ? `${Number(
                            runtimeEvidence
                              .slot_millis_improvement_percent
                          ).toFixed(2)}%`
                        : '—'
                    }
                  </span>
                </div>

              </div>

              {
                runtimeEvidence
                  ?.status
                &&
                runtimeEvidence.status !==
                  'NOT_RUN'
                &&
                (

                  <div className="runtime-evidence">

                    <h5>
                      ⏱️ Controlled Runtime Evidence
                    </h5>

                    <div className="metrics-comparison-grid">

                      <div className="metric-item">
                        <span className="metric-label">
                          Original Median Slot ms
                        </span>
                        <span className="metric-value">
                          {
                            originalRuntime
                              ?.slot_millis
                            ??
                            '—'
                          }
                        </span>
                      </div>

                      <div className="metric-item">
                        <span className="metric-label">
                          Candidate Median Slot ms
                        </span>
                        <span className="metric-value">
                          {
                            candidateRuntime
                              ?.slot_millis
                            ??
                            '—'
                          }
                        </span>
                      </div>

                      <div className="metric-item">
                        <span className="metric-label">
                          Original Median Wall Time
                        </span>
                        <span className="metric-value">
                          {
                            originalRuntime
                              ?.wall_time_ms
                            !== undefined
                              ? formatLatency(
                                  originalRuntime
                                    .wall_time_ms
                                )
                              : '—'
                          }
                        </span>
                      </div>

                      <div className="metric-item">
                        <span className="metric-label">
                          Candidate Median Wall Time
                        </span>
                        <span className="metric-value">
                          {
                            candidateRuntime
                              ?.wall_time_ms
                            !== undefined
                              ? formatLatency(
                                  candidateRuntime
                                    .wall_time_ms
                                )
                              : '—'
                          }
                        </span>
                      </div>

                    </div>

                    {
                      runtimePairCount !== null
                      &&
                      (
                        <p className="info">
                          Paired benchmark: {runtimePairCount} completed pair(s)
                          {' • '}
                          Candidate wins: {
                            runtimeCandidateWins ?? '—'
                          } / {
                            runtimeRequiredWins ?? '—'
                          } required
                          {' • '}
                          Execution order alternates between original-first
                          and candidate-first.
                        </p>
                      )
                    }

                    {
                      runtimeBenchmarkDurationMs !== null
                      &&
                      (
                        <p className="info">
                          Runtime Benchmark Duration: {
                            formatLatency(
                              runtimeBenchmarkDurationMs
                            )
                          }
                        </p>
                      )
                    }

                    {
                      runtimeEvidence
                        ?.warning
                      &&
                      (
                        <small>
                          {runtimeEvidence.warning}
                        </small>
                      )
                    }

                  </div>
                )
              }

              {
                runtimeEvidence
                  ?.status ===
                'NOT_RUN'
                &&
                (
                  <p className="info">
                    ⏱️ Runtime evidence not collected — {
                      runtimeEvidence.reason
                    }
                  </p>
                )
              }

              <h5>
                Candidate SQL
              </h5>

              <pre className="sql-code">

                {
                  currentCandidateResult
                    .candidate_query
                }

              </pre>

              <div className="candidate-actions">

                <button
                  type="button"

                  className="btn-copy"

                  onClick={() =>
                    navigator
                      .clipboard
                      .writeText(
                        currentCandidateResult
                          .candidate_query
                      )
                  }
                >
                  📋 Copy Candidate
                </button>

                {
                  candidateApproved &&
                  onUseCandidate &&
                  (

                    <button
                      type="button"

                      className="btn-analyze"

                      onClick={() =>
                        onUseCandidate(
                          currentCandidateResult
                            .candidate_query
                        )
                      }
                    >
                      ✅ Use Candidate in Editor
                    </button>
                  )
                }

              </div>

              <small>

                Candidate validity and scan cost are checked with
                BigQuery dry-run evidence. Equal scan bytes are not an
                automatic rejection: supported candidates may proceed to
                controlled runtime validation when explicitly enabled.

              </small>

            </div>
          )
        }

        {/* ===================================================== */}
        {/* OPTIMIZATIONS APPLIED */}
        {/* ===================================================== */}

        {
          result &&
          (

            <div className="result-card full-width optimization-rules">

              <h4>
                🛠️ Optimizations Applied
              </h4>

              {
                effectiveOptimizationRules.length > 0
                  ? (

                    <ul>

                      {
                        effectiveOptimizationRules.map(
                          (
                            rule,
                            i
                          ) => (

                            <li key={i}>
                              {rule}
                            </li>

                          )
                        )
                      }

                    </ul>

                  )
                  : (

                    <p className="placeholder">

                      {
                        currentCandidateResult
                          ? 'Candidate evaluated, but no rewrite was approved by the quality gate.'
                          : 'No validated performance rewrite was approved automatically.'
                      }

                    </p>
                  )
              }

            </div>
          )
        }

        {/* ===================================================== */}
        {/* EXPLANATION */}
        {/* ===================================================== */}

        <div className="result-card full-width explanation">

          <h4>

            💡 Explanation{' '}

            {
              isStreaming &&
              (

                <span className="streaming-indicator">
                  ● Live
                </span>
              )
            }

          </h4>

          <div className="markdown-content">

            {
              candidateExplanation
                ? (

                  <ReactMarkdown>
                    {candidateExplanation}
                  </ReactMarkdown>

                )
                : streamingExplanation
                  ? (

                    <ReactMarkdown>
                      {
                        streamingExplanation
                      }
                    </ReactMarkdown>

                  )
                  :
                result
                  ?.explanation
                  &&
                !isStreaming
                  ? (

                    <ReactMarkdown>
                      {
                        result.explanation
                      }
                    </ReactMarkdown>

                  )
                  : (

                  <p className="placeholder">

                    ⏳ Explanation will
                    appear here...

                  </p>
                )
            }

            {
              isStreaming &&
              (

                <span className="cursor-blink">
                  ▌
                </span>
              )
            }

          </div>

        </div>

        {/* ===================================================== */}
        {/* OPTIMIZATION METRICS */}
        {/* ===================================================== */}

        <div className="result-card metrics">

          <h4>
            📈 Optimization Metrics
          </h4>

          <div className="metric-item">

            <span className="metric-label">
              Data Processed
            </span>

            <span className="metric-value">

              {
                metrics
                  .bytes_processed
                  !== undefined
                &&
                metrics
                  .bytes_processed
                  !== null

                  ? (
                    metrics
                      .bytes_processed_formatted
                    ||
                    formatBytes(
                      metrics
                        .bytes_processed
                    )
                  )

                  : '—'
              }

            </span>

          </div>

          <div className="metric-item">

            <span className="metric-label">
              Estimated Cost
            </span>

            <span className="metric-value">

              {
                formatCost(
                  metrics
                    .estimated_cost_usd
                )
              }

            </span>

          </div>

          <div className="metric-item">

            <span className="metric-label">
              Analysis Latency
            </span>

            <span className="metric-value">

              {
                formatLatency(
                  metrics
                    .analysis_latency_ms
                )
              }

            </span>

          </div>

        </div>

        {/* ===================================================== */}
        {/* RECOMMENDATIONS */}
        {/* ===================================================== */}

        {
          result
            ?.recommendations
          &&
          result.recommendations.length > 0
          &&
          (

            <div className="result-card full-width recommendations">

              <h4>
                💎 Recommendations
              </h4>

              <ul>

                {
                  result.recommendations.map(
                    (
                      recommendation,
                      i
                    ) => (

                      <li key={i}>
                        {recommendation}
                      </li>

                    )
                  )
                }

              </ul>

            </div>
          )
        }

      </div>

    </div>
  );
}


export default ResultsDisplay;