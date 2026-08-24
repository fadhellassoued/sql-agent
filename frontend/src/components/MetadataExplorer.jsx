import React, {
  useState,
} from 'react';


function formatBytes(bytes) {

  if (
    bytes === null ||
    bytes === undefined
  ) {
    return '—';
  }

  if (bytes === 0) {
    return '0 B';
  }

  const units = [
    'B',
    'KB',
    'MB',
    'GB',
    'TB',
    'PB',
  ];

  const index =
    Math.floor(
      Math.log(bytes) /
      Math.log(1024)
    );

  const value =
    bytes /
    Math.pow(
      1024,
      index
    );

  return (
    `${value.toFixed(2)} ` +
    `${units[index]}`
  );
}


function formatNumber(value) {

  if (
    value === null ||
    value === undefined
  ) {
    return '—';
  }

  return new Intl.NumberFormat(
    'en-US'
  ).format(value);
}


function MetadataExplorer({
  metadata,
  loading,
}) {

  const [
    expandedTable,
    setExpandedTable,
  ] = useState(null);

  if (loading) {

    return (
      <div className="metadata-explorer">

        <h2>
          🗂️ Database Metadata
        </h2>

        <p>
          ⏳ Loading database metadata...
        </p>

      </div>
    );
  }

  if (!metadata) {
    return null;
  }

  const isBigQuery =
    metadata.database_type ===
    'bigquery';

  return (

    <div className="metadata-explorer">

      <h2>
        🗂️ Database Metadata
      </h2>

      {/* ====================================================== */}
      {/* DATABASE IDENTITY */}
      {/* ====================================================== */}

      <div className="metadata-header">

        {isBigQuery ? (

          <>
            <strong>
              ☁️ BigQuery
            </strong>

            <p>
              {metadata.project_id}
              {' / '}
              {metadata.dataset}
            </p>
          </>

        ) : (

          <>
            <strong>
              🐘 PostgreSQL
            </strong>

            <p>
              {metadata.database}
              {' @ '}
              {metadata.host}
              :
              {metadata.port}
            </p>
          </>
        )}

      </div>

      {/* ====================================================== */}
      {/* OVERVIEW */}
      {/* ====================================================== */}

      <h3>
        📊 Overview
      </h3>

      <div className="metadata-overview">

        <div>
          <span>
            Tables
          </span>

          <strong>
            {
              metadata.overview
                ?.table_count ??
              metadata.tables
                ?.length ??
              0
            }
          </strong>
        </div>

        {isBigQuery && (

          <>
            <div>
              <span>
                Total Rows
              </span>

              <strong>
                {formatNumber(
                  metadata.overview
                    ?.total_rows
                )}
              </strong>
            </div>

            <div>
              <span>
                Logical Size
              </span>

              <strong>
                {formatBytes(
                  metadata.overview
                    ?.total_bytes
                )}
              </strong>
            </div>

            <div>
              <span>
                Location
              </span>

              <strong>
                {
                  metadata.overview
                    ?.location ||
                  '—'
                }
              </strong>
            </div>
          </>
        )}

        {!isBigQuery && (

          <>
            <div>
              <span>
                Schemas
              </span>

              <strong>
                {
                  metadata.overview
                    ?.schema_count ??
                  '—'
                }
              </strong>
            </div>

            <div>
              <span>
                Database Size
              </span>

              <strong>
                {formatBytes(
                  metadata.overview
                    ?.database_size_bytes
                )}
              </strong>
            </div>
          </>
        )}

      </div>

      {/* ====================================================== */}
      {/* TABLES */}
      {/* ====================================================== */}

      <h3>
        📦 Tables
      </h3>

      <div className="metadata-table-list">

        {metadata.tables?.map(
          (table) => {

            const key =
              table.full_name ||
              table.name;

            const expanded =
              expandedTable === key;

            return (

              <div
                key={key}
                className="metadata-table-card"
              >

                <button
                  type="button"
                  className="metadata-table-title"
                  onClick={() =>
                    setExpandedTable(
                      expanded
                        ? null
                        : key
                    )
                  }
                >

                  <span>
                    📄 {
                      table.full_name ||
                      table.name
                    }
                  </span>

                  <span>
                    {expanded
                      ? '▲'
                      : '▼'}
                  </span>

                </button>

                <div className="metadata-table-summary">

                  <span>
                    Rows:{' '}
                    <strong>
                      {formatNumber(
                        table.row_count
                      )}

                      {
                        table
                          .row_count_is_estimate
                          ? ' ≈'
                          : ''
                      }
                    </strong>
                  </span>

                  <span>
                    Size:{' '}
                    <strong>
                      {formatBytes(
                        table.size_bytes
                      )}
                    </strong>
                  </span>

                  {isBigQuery && (

                    <span>
                      Partitioned:{' '}
                      <strong>
                        {table.partitioned
                          ? 'Yes'
                          : 'No'}
                      </strong>
                    </span>
                  )}

                </div>

                {/* ============================================ */}
                {/* EXPANDED */}
                {/* ============================================ */}

                {expanded && (

                  <div className="metadata-table-details">

                    {isBigQuery && (

                      <>

                        <p>
                          <strong>
                            Partition field:
                          </strong>{' '}
                          {
                            table.partition_field ||
                            'None'
                          }
                        </p>

                        <p>
                          <strong>
                            Clustering:
                          </strong>{' '}
                          {
                            table
                              .clustering_fields
                              ?.length
                              ? table
                                  .clustering_fields
                                  .join(', ')
                              : 'None'
                          }
                        </p>

                      </>
                    )}

                    {!isBigQuery && (

                      <>

                        <p>
                          <strong>
                            Primary key:
                          </strong>{' '}
                          {
                            table.primary_key
                              ?.length
                              ? table.primary_key
                                  .join(', ')
                              : 'None'
                          }
                        </p>

                        <p>
                          <strong>
                            Indexes:
                          </strong>{' '}
                          {
                            table.indexes
                              ?.length ||
                            0
                          }
                        </p>

                        <p>
                          <strong>
                            Foreign keys:
                          </strong>{' '}
                          {
                            table.foreign_keys
                              ?.length ||
                            0
                          }
                        </p>

                      </>
                    )}

                    <h4>
                      Columns
                    </h4>

                    <div className="metadata-columns">

                      <table>

                        <thead>
                          <tr>
                            <th>
                              Column
                            </th>

                            <th>
                              Type
                            </th>

                            <th>
                              Mode /
                              Nullable
                            </th>
                          </tr>
                        </thead>

                        <tbody>

                          {table.columns
                            ?.map(
                              (
                                column
                              ) => (

                              <tr
                                key={
                                  column.name
                                }
                              >

                                <td>
                                  {
                                    column.name
                                  }
                                </td>

                                <td>
                                  {
                                    column.type
                                  }
                                </td>

                                <td>
                                  {
                                    isBigQuery
                                      ? column.mode
                                      : (
                                          column.nullable
                                            ? 'Nullable'
                                            : 'Required'
                                        )
                                  }
                                </td>

                              </tr>

                            ))}

                        </tbody>

                      </table>

                    </div>

                  </div>
                )}

              </div>
            );
          }
        )}

      </div>

    </div>
  );
}


export default MetadataExplorer;