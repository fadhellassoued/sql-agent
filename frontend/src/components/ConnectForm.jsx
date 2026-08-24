import React, {
  useEffect,
  useState,
} from 'react';

import axios from 'axios';

import toast from 'react-hot-toast';


const API_URL =
  import.meta.env.VITE_API_URL ||
  'http://localhost:8000/api/v1';


function ConnectForm({
  onConnect,
  loading,
}) {

  // ============================================================
  // DATABASE TYPE
  // ============================================================

  const [
    dbType,
    setDbType,
  ] = useState(
    'bigquery'
  );

  // ============================================================
  // GOOGLE OAUTH
  // ============================================================

  const [
    gcpAuth,
    setGcpAuth,
  ] = useState({
    authenticated: false,
    user: null,
  });

  const [
    loadingAuth,
    setLoadingAuth,
  ] = useState(true);

  // ============================================================
  // BIGQUERY
  // ============================================================

  const [
    projectId,
    setProjectId,
  ] = useState('');

  const [
    datasets,
    setDatasets,
  ] = useState([]);

  const [
    selectedDataset,
    setSelectedDataset,
  ] = useState('');

  const [
    loadingDatasets,
    setLoadingDatasets,
  ] = useState(false);

  const [
    bigQueryStep,
    setBigQueryStep,
  ] = useState(1);

  // ============================================================
  // POSTGRESQL
  // ============================================================

  const [
    postgres,
    setPostgres,
  ] = useState({
    host: 'localhost',
    port: '5432',
    database: '',
    username: '',
    password: '',
  });

  // ============================================================
  // GOOGLE AUTH STATUS
  // ============================================================

  const loadGoogleAuthStatus =
    async () => {

      setLoadingAuth(
        true
      );

      try {

        const response =
          await axios.get(
            `${API_URL}/gcp/auth/status`,
            {
              withCredentials: true,
            }
          );

        setGcpAuth(
          response.data
        );

      } catch (error) {

        console.error(
          '[Google OAuth] Status failed:',
          error
        );

        setGcpAuth({
          authenticated: false,
          user: null,
        });

      } finally {

        setLoadingAuth(
          false
        );
      }
    };

  // ============================================================
  // ON LOAD
  // ============================================================

  useEffect(() => {

    loadGoogleAuthStatus();

  }, []);

  // ============================================================
  // DATABASE TYPE CHANGE
  // ============================================================

  const handleDatabaseTypeChange = (
    type
  ) => {

    setDbType(
      type
    );

    setDatasets([]);

    setSelectedDataset('');

    setBigQueryStep(1);
  };

  // ============================================================
  // GOOGLE SIGN IN
  // ============================================================

  const handleGoogleSignIn = () => {

    window.location.href =
      `${API_URL}/gcp/auth/login`;
  };

  // ============================================================
  // GOOGLE LOGOUT
  // ============================================================

  const handleGoogleLogout =
    async () => {

      try {

        await axios.post(
          `${API_URL}/gcp/auth/logout`,
          {},
          {
            withCredentials: true,
          }
        );

        setGcpAuth({
          authenticated: false,
          user: null,
        });

        setProjectId('');

        setDatasets([]);

        setSelectedDataset('');

        setBigQueryStep(1);

        toast.success(
          'Google Cloud account disconnected.'
        );

      } catch (error) {

        console.error(
          '[Google OAuth] Logout failed:',
          error
        );

        toast.error(
          'Unable to disconnect Google account.'
        );
      }
    };

  // ============================================================
  // LOAD BIGQUERY DATASETS
  // ============================================================

  const handleLoadDatasets =
    async () => {

      if (
        !gcpAuth.authenticated
      ) {

        toast.error(
          'Sign in with Google first.'
        );

        return;
      }

      const project = (
        projectId.trim()
      );

      if (!project) {

        toast.error(
          'Enter a GCP Project ID.'
        );

        return;
      }

      setLoadingDatasets(
        true
      );

      try {

        const response =
          await axios.post(
            `${API_URL}/gcp/datasets`,
            {
              project_id:
                project,
            },
            {
              withCredentials:
                true,
            }
          );

        const datasetList = (
          response.data.datasets ||
          []
        );

        setDatasets(
          datasetList
        );

        setSelectedDataset(
          ''
        );

        if (
          datasetList.length === 0
        ) {

          toast.error(
            'No accessible BigQuery datasets found for this Google account.'
          );

          return;
        }

        setBigQueryStep(
          2
        );

        toast.success(
          `Found ${
            datasetList.length
          } accessible dataset(s).`
        );

      } catch (error) {

        console.error(
          '[BigQuery] Dataset discovery failed:',
          error
        );

        toast.error(
          error.response?.data?.detail ||
          'Unable to load BigQuery datasets.'
        );

      } finally {

        setLoadingDatasets(
          false
        );
      }
    };

  // ============================================================
  // CONNECT BIGQUERY
  // ============================================================

  const handleBigQueryConnect =
    async () => {

      if (
        !gcpAuth.authenticated
      ) {

        toast.error(
          'Google Cloud authentication is required.'
        );

        return;
      }

      if (
        !selectedDataset
      ) {

        toast.error(
          'Select a BigQuery dataset.'
        );

        return;
      }

      await onConnect({
        db_type:
          'bigquery',

        project_id:
          projectId.trim(),

        dataset:
          selectedDataset,
      });
    };

  // ============================================================
  // POSTGRES FIELD CHANGE
  // ============================================================

  const handlePostgresChange = (
    event
  ) => {

    const {
      name,
      value,
    } = event.target;

    setPostgres(
      (
        previous
      ) => ({
        ...previous,

        [name]:
          value,
      })
    );
  };

  // ============================================================
  // CONNECT POSTGRES
  // ============================================================

  const handlePostgresConnect =
    async () => {

      if (
        !postgres.host ||
        !postgres.port ||
        !postgres.database ||
        !postgres.username ||
        !postgres.password
      ) {

        toast.error(
          'Please complete all PostgreSQL fields.'
        );

        return;
      }

      await onConnect({
        db_type:
          'postgresql',

        host:
          postgres.host.trim(),

        port:
          Number(
            postgres.port
          ),

        database:
          postgres.database.trim(),

        username:
          postgres.username,

        password:
          postgres.password,
      });
    };

  // ============================================================
  // RENDER
  // ============================================================

  return (

    <div className="connect-form">

      <h2>
        🔗 Connect to Database
      </h2>

      <p className="connection-subtitle">
        Choose your database platform and establish
        a secure connection.
      </p>

      {/* ====================================================== */}
      {/* DATABASE TYPE */}
      {/* ====================================================== */}

      <div className="form-group">

        <label>
          Database Type
        </label>

        <div className="database-type-selector">

          <button
            type="button"
            className={
              dbType === 'bigquery'
                ? 'active'
                : ''
            }
            onClick={() =>
              handleDatabaseTypeChange(
                'bigquery'
              )
            }
          >
            ☁️ Google BigQuery
          </button>

          <button
            type="button"
            className={
              dbType === 'postgresql'
                ? 'active'
                : ''
            }
            onClick={() =>
              handleDatabaseTypeChange(
                'postgresql'
              )
            }
          >
            🐘 PostgreSQL
          </button>

        </div>

      </div>

      {/* ====================================================== */}
      {/* BIGQUERY */}
      {/* ====================================================== */}

      {dbType === 'bigquery' && (

        <div>

          {/* ================================================== */}
          {/* GOOGLE ACCOUNT */}
          {/* ================================================== */}

          <div className="security-notice">

            <strong>
              🔐 Google Cloud Account
            </strong>

            {loadingAuth ? (

              <p>
                Checking Google authentication...
              </p>

            ) : gcpAuth.authenticated ? (

              <>

                <p>
                  ✅ Connected as
                </p>

                <strong>
                  {
                    gcpAuth.user?.email
                  }
                </strong>

                <p>
                  BigQuery requests will use this
                  Google account and its IAM permissions.
                </p>

                <button
                  type="button"
                  className="btn-secondary"
                  onClick={
                    handleGoogleLogout
                  }
                >
                  Disconnect Google Account
                </button>

              </>

            ) : (

              <>

                <p>
                  Sign in securely with Google to
                  access BigQuery projects and datasets
                  available to your account.
                </p>

                <button
                  type="button"
                  onClick={
                    handleGoogleSignIn
                  }
                >
                  🔐 Sign in with Google
                </button>

                <small>
                  Your Google password is never sent
                  to SQL Optimizer.
                </small>

              </>
            )}

          </div>

          {/* ================================================== */}
          {/* PROJECT SELECTION */}
          {/* ================================================== */}

          {
            gcpAuth.authenticated &&
            bigQueryStep === 1 &&
            (

              <>

                <div className="form-group">

                  <label>
                    GCP Project ID
                  </label>

                  <input
                    type="text"
                    value={
                      projectId
                    }
                    onChange={
                      (
                        event
                      ) =>
                        setProjectId(
                          event.target.value
                        )
                    }
                    placeholder="your-gcp-project-id"
                    autoComplete="off"
                  />

                </div>

                <button
                  type="button"
                  onClick={
                    handleLoadDatasets
                  }
                  disabled={
                    loadingDatasets ||
                    loading
                  }
                >
                  {
                    loadingDatasets
                      ? '⏳ Loading datasets...'
                      : '🔎 Load Datasets'
                  }
                </button>

              </>
            )
          }

          {/* ================================================== */}
          {/* DATASET SELECTION */}
          {/* ================================================== */}

          {
            gcpAuth.authenticated &&
            bigQueryStep === 2 &&
            (

              <>

                <div className="form-group">

                  <label>
                    Google Account
                  </label>

                  <input
                    type="text"
                    value={
                      gcpAuth.user?.email ||
                      ''
                    }
                    readOnly
                  />

                </div>

                <div className="form-group">

                  <label>
                    GCP Project
                  </label>

                  <input
                    type="text"
                    value={
                      projectId
                    }
                    readOnly
                  />

                </div>

                <div className="form-group">

                  <label>
                    BigQuery Dataset
                  </label>

                  <select
                    value={
                      selectedDataset
                    }
                    onChange={
                      (
                        event
                      ) =>
                        setSelectedDataset(
                          event.target.value
                        )
                    }
                  >

                    <option value="">
                      -- Select a dataset --
                    </option>

                    {
                      datasets.map(
                        (
                          dataset
                        ) => (

                          <option
                            key={
                              dataset.dataset_id
                            }
                            value={
                              dataset.dataset_id
                            }
                          >
                            {
                              dataset.dataset_id
                            }
                          </option>

                        )
                      )
                    }

                  </select>

                </div>

                <p>
                  📦 {datasets.length}{' '}
                  accessible dataset
                  {
                    datasets.length !== 1
                      ? 's'
                      : ''
                  }
                </p>

                <div className="connection-actions">

                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => {

                      setBigQueryStep(
                        1
                      );

                      setSelectedDataset(
                        ''
                      );
                    }}
                  >
                    ← Back
                  </button>

                  <button
                    type="button"
                    onClick={
                      handleBigQueryConnect
                    }
                    disabled={
                      loading ||
                      !selectedDataset
                    }
                  >
                    {
                      loading
                        ? '⏳ Connecting...'
                        : '🚀 Connect'
                    }
                  </button>

                </div>

              </>
            )
          }

        </div>
      )}

      {/* ====================================================== */}
      {/* POSTGRESQL */}
      {/* ====================================================== */}

      {dbType === 'postgresql' && (

        <div>

          <div className="form-group">

            <label>
              Host
            </label>

            <input
              type="text"
              name="host"
              value={
                postgres.host
              }
              onChange={
                handlePostgresChange
              }
              placeholder="localhost"
            />

          </div>

          <div className="form-group">

            <label>
              Port
            </label>

            <input
              type="number"
              name="port"
              value={
                postgres.port
              }
              onChange={
                handlePostgresChange
              }
              placeholder="5432"
            />

          </div>

          <div className="form-group">

            <label>
              Database
            </label>

            <input
              type="text"
              name="database"
              value={
                postgres.database
              }
              onChange={
                handlePostgresChange
              }
              placeholder="my_database"
            />

          </div>

          <div className="form-group">

            <label>
              Username
            </label>

            <input
              type="text"
              name="username"
              value={
                postgres.username
              }
              onChange={
                handlePostgresChange
              }
              autoComplete="username"
            />

          </div>

          <div className="form-group">

            <label>
              Password
            </label>

            <input
              type="password"
              name="password"
              value={
                postgres.password
              }
              onChange={
                handlePostgresChange
              }
              autoComplete="current-password"
            />

          </div>

          <button
            type="button"
            onClick={
              handlePostgresConnect
            }
            disabled={
              loading
            }
          >
            {
              loading
                ? '⏳ Connecting...'
                : '🚀 Connect to PostgreSQL'
            }
          </button>

        </div>
      )}

    </div>
  );
}


export default ConnectForm;