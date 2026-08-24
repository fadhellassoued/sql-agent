import React, {
  useState,
  useRef,
} from 'react';

import axios from 'axios';

import toast, {
  Toaster,
} from 'react-hot-toast';

import ConnectForm from './components/ConnectForm';

import MetadataExplorer from './components/MetadataExplorer';

import QueryEditor from './components/QueryEditor';

import ResultsDisplay from './components/ResultsDisplay';

import './styles/App.css';


const API_URL =
  import.meta.env.VITE_API_URL ||
  'http://localhost:8000/api/v1';

const WS_URL =
  import.meta.env.VITE_WS_URL ||
  'ws://localhost:8000/ws/analyze';


function App() {

  // ============================================================
  // CONNECTION
  // ============================================================

  const [
    sessionId,
    setSessionId,
  ] = useState(null);

  const [
    tables,
    setTables,
  ] = useState([]);

  const [
    isConnected,
    setIsConnected,
  ] = useState(false);

  const [
    connectionInfo,
    setConnectionInfo,
  ] = useState(null);

  // ============================================================
  // METADATA
  // ============================================================

  const [
    metadata,
    setMetadata,
  ] = useState(null);

  const [
    metadataLoading,
    setMetadataLoading,
  ] = useState(false);

  // ============================================================
  // COST ESTIMATION
  // ============================================================

  const [
    costEstimate,
    setCostEstimate,
  ] = useState(null);

  const [
    estimatingCost,
    setEstimatingCost,
  ] = useState(false);

  // ============================================================
  // APP
  // ============================================================

  const [
    loading,
    setLoading,
  ] = useState(false);

  const [
    result,
    setResult,
  ] = useState(null);

  const [
    sql,
    setSql,
  ] = useState(
    'SELECT * FROM stackoverflow.users LIMIT 10'
  );

  // ============================================================
  // STREAM
  // ============================================================

  const [
    streamingExplanation,
    setStreamingExplanation,
  ] = useState('');

  const [
    isStreaming,
    setIsStreaming,
  ] = useState(false);

  const wsRef = useRef(
    null
  );

  // ============================================================
  // LOAD METADATA
  // ============================================================

  const loadMetadata = async (
    newSessionId
  ) => {

    setMetadataLoading(
      true
    );

    try {

      const response =
        await axios.get(
          `${API_URL}/sessions/${newSessionId}/metadata`,
          {
            withCredentials:
              true,
          }
        );

      setMetadata(
        response.data
      );

    } catch (error) {

      console.error(
        '[Metadata] Failed:',
        error
      );

      toast.error(
        'Connected, but metadata could not be loaded: ' +
        (
          error.response?.data?.detail ||
          error.message
        )
      );

    } finally {

      setMetadataLoading(
        false
      );
    }
  };

  // ============================================================
  // CONNECT
  // ============================================================

  const handleConnect = async (
    credentials
  ) => {

    setLoading(
      true
    );

    setCostEstimate(
      null
    );

    setResult(
      null
    );

    try {

      const response =
        await axios.post(
          `${API_URL}/connect`,
          credentials,
          {
            withCredentials:
              true,
          }
        );

      const newSessionId = (
        response.data.session_id
      );

      setSessionId(
        newSessionId
      );

      setTables(
        response.data.tables ||
        []
      );

      setConnectionInfo({
        db_type:
          response.data.db_type ||
          credentials.db_type,

        project_id:
          response.data.project_id ||
          credentials.project_id,

        dataset:
          response.data.dataset ||
          credentials.dataset,

        host:
          credentials.host,

        port:
          credentials.port,

        database:
          response.data.database ||
          credentials.database,
      });

      setIsConnected(
        true
      );

      toast.success(
        `Connected! Found ${
          response.data.tables
            ?.length || 0
        } tables.`
      );

      await loadMetadata(
        newSessionId
      );

    } catch (error) {

      console.error(
        '[Connection] Failed:',
        error
      );

      toast.error(
        'Connection failed: ' +
        (
          error.response?.data?.detail ||
          error.message
        )
      );

    } finally {

      setLoading(
        false
      );
    }
  };

  // ============================================================
  // ESTIMATE ORIGINAL QUERY COST
  // ============================================================

  const handleEstimateCost = async (
    query
  ) => {

    if (!sessionId) {

      toast.error(
        'Please connect to BigQuery first.'
      );

      return;
    }

    if (
      !query ||
      !query.trim()
    ) {

      toast.error(
        'Enter a SQL query first.'
      );

      return;
    }

    if (
      connectionInfo
        ?.db_type !==
      'bigquery'
    ) {

      toast.error(
        'Pre-optimization cost estimation is currently available for BigQuery only.'
      );

      return;
    }

    setEstimatingCost(
      true
    );

    setCostEstimate(
      null
    );

    // --------------------------------------------------------
    // Previous optimization becomes stale for a newly
    // estimated SQL query.
    // --------------------------------------------------------

    setResult(
      null
    );

    setStreamingExplanation(
      ''
    );

    setIsStreaming(
      false
    );

    try {

      const response =
        await axios.post(
          `${API_URL}/cost/estimate`,
          {
            session_id:
              sessionId,

            sql_query:
              query,
          },
          {
            withCredentials:
              true,
          }
        );

      setCostEstimate(
        response.data
      );

      if (
        response.data
          .exceeds_threshold
      ) {

        toast.error(
          `Estimated cost exceeds the configured threshold of $${Number(
            response.data
              .threshold_usd
          ).toFixed(2)}`
        );

      } else {

        toast.success(
          'Original query cost estimated.'
        );
      }

    } catch (error) {

      console.error(
        '[Cost Estimate] Failed:',
        error
      );

      toast.error(
        error.response
          ?.data
          ?.detail ||
        'Unable to estimate query cost.'
      );

    } finally {

      setEstimatingCost(
        false
      );
    }
  };

  // ============================================================
  // SQL CHANGE
  // ============================================================

  const handleSqlChange = (
    value
  ) => {

    setSql(
      value
    );

    // --------------------------------------------------------
    // If SQL changes, the old estimate is no longer valid.
    // --------------------------------------------------------

    setCostEstimate(
      null
    );

    setResult(
      null
    );

    setStreamingExplanation(
      ''
    );
  };

  // ============================================================
  // ANALYZE / OPTIMIZE
  // ============================================================

  const handleAnalyze = async (
    query
  ) => {

    if (!sessionId) {

      toast.error(
        'Please connect to a database first.'
      );

      return;
    }

    if (
      connectionInfo
        ?.db_type ===
      'bigquery'
      &&
      !costEstimate
    ) {

      toast.error(
        'Estimate the original query cost before optimization.'
      );

      return;
    }

    if (
      wsRef.current &&
      (
        wsRef.current.readyState ===
        WebSocket.OPEN
        ||
        wsRef.current.readyState ===
        WebSocket.CONNECTING
      )
    ) {

      wsRef.current.close();
    }

    setResult(
      null
    );

    setStreamingExplanation(
      ''
    );

    setIsStreaming(
      false
    );

    setLoading(
      true
    );

    const ws = new WebSocket(
      WS_URL
    );

    wsRef.current = (
      ws
    );

    // ==========================================================
    // OPEN
    // ==========================================================

    ws.onopen = () => {

      console.log(
        '[WebSocket] Connection opened'
      );

      ws.send(
        JSON.stringify({
          session_id:
            sessionId,

          sql_query:
            query,
        })
      );
    };

    // ==========================================================
    // MESSAGE
    // ==========================================================

    ws.onmessage = (
      event
    ) => {

      try {

        const data = (
          JSON.parse(
            event.data
          )
        );

        console.log(
          '[WebSocket] Message:',
          data
        );

        // ------------------------------------------------------
        // DETERMINISTIC RESULT
        // ------------------------------------------------------

        if (
          data.type ===
          'result'
        ) {

          setResult(
            data.data
          );

          setLoading(
            false
          );

          setIsStreaming(
            true
          );

          toast.success(
            'Query analysis completed!'
          );

          return;
        }

        // ------------------------------------------------------
        // GEMINI TOKEN
        // ------------------------------------------------------

        if (
          data.type ===
          'token'
        ) {

          setIsStreaming(
            true
          );

          setStreamingExplanation(
            (
              previous
            ) =>
              previous +
              data.content
          );

          return;
        }

        // ------------------------------------------------------
        // EXPLANATION COMPLETE
        // ------------------------------------------------------

        if (
          data.type ===
          'explanation_complete'
        ) {

          setStreamingExplanation(
            data.content ||
            ''
          );

          setIsStreaming(
            false
          );

          return;
        }

        // ------------------------------------------------------
        // DONE
        // ------------------------------------------------------

        if (
          data.type ===
          'done'
        ) {

          setLoading(
            false
          );

          setIsStreaming(
            false
          );

          if (
            ws.readyState ===
            WebSocket.OPEN
          ) {

            ws.close();
          }

          return;
        }

        // ------------------------------------------------------
        // ERROR
        // ------------------------------------------------------

        if (
          data.error
        ) {

          toast.error(
            'Analysis error: ' +
            data.error
          );

          setLoading(
            false
          );

          setIsStreaming(
            false
          );

          if (
            ws.readyState ===
            WebSocket.OPEN
          ) {

            ws.close();
          }
        }

      } catch (error) {

        console.error(
          '[WebSocket] Invalid message:',
          error
        );
      }
    };

    // ==========================================================
    // ERROR
    // ==========================================================

    ws.onerror = (
      error
    ) => {

      console.error(
        '[WebSocket] Error:',
        error
      );

      toast.error(
        'WebSocket connection error.'
      );

      setLoading(
        false
      );

      setIsStreaming(
        false
      );
    };

    // ==========================================================
    // CLOSE
    // ==========================================================

    ws.onclose = () => {

      console.log(
        '[WebSocket] Connection closed'
      );

      setLoading(
        false
      );

      setIsStreaming(
        false
      );

      wsRef.current = (
        null
      );
    };
  };

  // ============================================================
  // DISCONNECT DATABASE SESSION
  // ============================================================

  const handleDisconnect = () => {

    if (
      wsRef.current
    ) {

      wsRef.current.close();

      wsRef.current = null;
    }

    setIsConnected(
      false
    );

    setSessionId(
      null
    );

    setTables([]);

    setMetadata(
      null
    );

    setConnectionInfo(
      null
    );

    setCostEstimate(
      null
    );

    setEstimatingCost(
      false
    );

    setResult(
      null
    );

    setStreamingExplanation(
      ''
    );

    setIsStreaming(
      false
    );

    setLoading(
      false
    );
  };

  // ============================================================
  // RENDER
  // ============================================================

  return (

    <div className="app-container">

      <Toaster
        position="top-right"
      />

      <header className="app-header">

        <h1>
          🧠 SQL Optimizer Agent
        </h1>

        <p>
          Analyze, optimize, and explain SQL queries
        </p>

      </header>

      <main className="app-main">

        {!isConnected ? (

          <ConnectForm
            onConnect={
              handleConnect
            }
            loading={
              loading
            }
          />

        ) : (

          <>

            {/* ================================================== */}
            {/* CONNECTION STATUS */}
            {/* ================================================== */}

            <div className="connection-status">

              ✅ Connected to{' '}

              <strong>
                {
                  connectionInfo
                    ?.db_type ===
                  'postgresql'
                    ? 'PostgreSQL'
                    : 'BigQuery'
                }
              </strong>

              {
                connectionInfo
                  ?.db_type ===
                'bigquery' &&
                (

                  <>
                    {' • '}

                    {
                      connectionInfo
                        ?.project_id
                    }

                    {' / '}

                    {
                      connectionInfo
                        ?.dataset
                    }
                  </>
                )
              }

              {
                connectionInfo
                  ?.db_type ===
                'postgresql' &&
                (

                  <>
                    {' • '}

                    {
                      connectionInfo
                        ?.database
                    }

                    {' @ '}

                    {
                      connectionInfo
                        ?.host
                    }
                  </>
                )
              }

              {' • '}

              {tables.length}
              {' '}
              tables found

              <button
                className="btn-disconnect"
                onClick={
                  handleDisconnect
                }
              >
                Disconnect
              </button>

            </div>

            {/* ================================================== */}
            {/* METADATA */}
            {/* ================================================== */}

            <MetadataExplorer
              metadata={
                metadata
              }
              loading={
                metadataLoading
              }
            />

            {/* ================================================== */}
            {/* QUERY EDITOR */}
            {/* ================================================== */}

            <QueryEditor
              sql={
                sql
              }

              setSql={
                handleSqlChange
              }

              onEstimateCost={
                handleEstimateCost
              }

              onAnalyze={
                handleAnalyze
              }

              loading={
                loading ||
                isStreaming
              }

              estimatingCost={
                estimatingCost
              }

              costEstimate={
                costEstimate
              }
            />

            {/* ================================================== */}
            {/* RESULTS */}
            {/* ================================================== */}

            <ResultsDisplay
              result={
                result
              }

              costEstimate={
                costEstimate
              }

              streamingExplanation={
                streamingExplanation
              }

              isStreaming={
                isStreaming
              }
            />

          </>
        )}

      </main>

      <footer className="app-footer">

        <p>
          Powered by Gemini • Vertex AI • BigQuery • PostgreSQL • FastAPI
        </p>

      </footer>

    </div>
  );
}


export default App;