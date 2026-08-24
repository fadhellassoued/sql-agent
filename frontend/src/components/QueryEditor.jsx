import React from 'react';

import Editor from '@monaco-editor/react';


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
        ? 2
        : 3
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

  const number = (
    Number(cost)
  );

  if (
    number === 0
  ) {

    return '$0.000000';
  }

  if (
    number < 0.01
  ) {

    return `$${number.toFixed(
      6
    )}`;
  }

  return `$${number.toFixed(
    4
  )}`;
}


function QueryEditor({
  sql,
  setSql,
  onEstimateCost,
  onAnalyze,
  loading,
  estimatingCost,
  costEstimate,
  databaseType = 'bigquery',
}) {

  // ============================================================
  // DATABASE MODE
  // ============================================================

  const normalizedDatabaseType = (
    databaseType ||
    'bigquery'
  ).toLowerCase();

  const isBigQuery = (
    normalizedDatabaseType ===
    'bigquery'
  );

  // ============================================================
  // SQL CHANGE
  // ============================================================

  const handleSqlChange = (
    value
  ) => {

    setSql(
      value || ''
    );
  };

  // ============================================================
  // CAN OPTIMIZE
  // ============================================================
  //
  // BigQuery:
  //   Require the original dry-run estimate first.
  //
  // PostgreSQL:
  //   Do not require the BigQuery cost-estimate endpoint.
  // ============================================================

  const canOptimize = (
    Boolean(
      sql?.trim()
    )
    &&
    !loading
    &&
    !estimatingCost
    &&
    (
      !isBigQuery
      ||
      Boolean(
        costEstimate
      )
    )
  );

  // ============================================================
  // RENDER
  // ============================================================

  return (

    <div className="query-editor">

      {/* ====================================================== */}
      {/* HEADER */}
      {/* ====================================================== */}

      <div className="editor-header">

        <h3>
          📝 SQL Query
        </h3>

        <div className="editor-actions">

          {
            isBigQuery &&
            (

              <button
                type="button"

                onClick={() =>
                  onEstimateCost(
                    sql
                  )
                }

                disabled={
                  loading ||
                  estimatingCost ||
                  !sql?.trim()
                }

                className="btn-estimate"
              >

                {
                  estimatingCost
                    ? '⏳ Estimating...'
                    : '💰 Estimate Cost'
                }

              </button>
            )
          }

          <button
            type="button"

            onClick={() =>
              onAnalyze(
                sql
              )
            }

            disabled={
              !canOptimize
            }

            className="btn-analyze"
          >

            {
              loading
                ? '⏳ Optimizing...'
                : '⚡ Optimize Query'
            }

          </button>

        </div>

      </div>

      {/* ====================================================== */}
      {/* SQL EDITOR */}
      {/* ====================================================== */}

      <Editor
        height="200px"

        language="sql"

        value={
          sql
        }

        onChange={
          handleSqlChange
        }

        theme="vs-dark"

        options={{
          minimap: {
            enabled: false,
          },

          fontSize:
            14,

          lineNumbers:
            'on',

          automaticLayout:
            true,

          scrollBeyondLastLine:
            false,

          wordWrap:
            'on',
        }}
      />

      {/* ====================================================== */}
      {/* BIGQUERY COST ESTIMATE */}
      {/* ====================================================== */}

      {
        isBigQuery &&
        costEstimate &&
        (

          <div
            className={
              costEstimate
                .exceeds_threshold
                ? 'cost-estimate cost-warning'
                : 'cost-estimate cost-ok'
            }
          >

            <h4>
              💰 Original Query Estimate
            </h4>

            <div className="cost-estimate-grid">

              <div>

                <span>
                  Data processed
                </span>

                <strong>
                  {
                    formatBytes(
                      costEstimate
                        .bytes_processed
                    )
                  }
                </strong>

              </div>

              <div>

                <span>
                  Estimated cost
                </span>

                <strong>
                  {
                    formatCost(
                      costEstimate
                        .estimated_cost_usd
                    )
                  }
                </strong>

              </div>

              <div>

                <span>
                  Alert threshold
                </span>

                <strong>
                  {
                    formatCost(
                      costEstimate
                        .threshold_usd
                    )
                  }
                </strong>

              </div>

              <div>

                <span>
                  Dry-run latency
                </span>

                <strong>
                  {
                    Number(
                      costEstimate
                        .estimation_latency_ms
                    ).toFixed(
                      2
                    )
                  } ms
                </strong>

              </div>

            </div>

            {
              costEstimate
                .exceeds_threshold
                ? (

                  <div className="cost-alert">

                    ⚠️ This query exceeds
                    the configured cost threshold.

                  </div>

                )
                : (

                  <div className="cost-safe">

                    ✅ Estimated cost is
                    below the configured threshold.

                  </div>
                )
            }

            <small>

              BigQuery dry-run estimate.
              This is an estimated on-demand
              analysis cost, not a final
              Cloud Billing charge.

            </small>

          </div>
        )
      }

      {/* ====================================================== */}
      {/* HINT */}
      {/* ====================================================== */}

      <div className="editor-hint">

        {
          isBigQuery
            ? (
              <>
                💡 Estimate the original query first,
                then optimize it. If the query uses
                <strong> SELECT * </strong>
                and the required columns cannot be
                inferred safely, the optimizer will
                ask you to choose them.
              </>
            )
            : (
              <>
                💡 PostgreSQL optimization is analyzed
                separately using PostgreSQL-specific
                rules and execution-plan evidence.
              </>
            )
        }

      </div>

    </div>
  );
}


export default QueryEditor;