import React, { useState, useRef } from 'react';
import axios from 'axios';
import toast, { Toaster } from 'react-hot-toast';
import ConnectForm from './components/ConnectForm';
import QueryEditor from './components/QueryEditor';
import ResultsDisplay from './components/ResultsDisplay';
import './styles/App.css';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/ws/analyze';

function App() {
  const [sessionId, setSessionId] = useState(null);
  const [tables, setTables] = useState([]);
  const [isConnected, setIsConnected] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [sql, setSql] = useState('SELECT * FROM wdi_2016 LIMIT 10');

  // WebSocket streaming state
  const [streamingExplanation, setStreamingExplanation] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const wsRef = useRef(null);

  const handleConnect = async (credentials) => {
    setLoading(true);
    try {
      const response = await axios.post(`${API_URL}/connect`, credentials);
      setSessionId(response.data.session_id);
      setTables(response.data.tables);
      setIsConnected(true);
      toast.success(`Connected! Found ${response.data.tables.length} tables.`);
    } catch (error) {
      toast.error('Connection failed: ' + (error.response?.data?.detail || error.message));
    } finally {
      setLoading(false);
    }
  };

  const handleAnalyze = async (query) => {
    if (!sessionId) {
      toast.error('Please connect to a database first.');
      return;
    }

    // Reset states
    setResult(null);
    setStreamingExplanation('');
    setIsStreaming(true);

    // 1. Open WebSocket for streaming explanation + final result
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ session_id: sessionId, sql_query: query }));
    };

    ws.onmessage = async (event) => {
      const data = JSON.parse(event.data);
      console.log('[WebSocket] Message:', data);
      
      if (data.type === 'token') {
        // Streaming explanation token
        await new Promise(resolve => requestAnimationFrame(resolve));
        setStreamingExplanation((prev) => prev + data.content);
        
      } else if (data.type === 'result') {
        // ✅ Full result received from WebSocket
        setResult(data.data);
        setIsStreaming(false);
        ws.close();
        
      } else if (data.type === 'done') {
        setIsStreaming(false);
        ws.close();
        
      } else if (data.error) {
        toast.error('WebSocket error: ' + data.error);
        setIsStreaming(false);
        ws.close();
      }
    };

    ws.onerror = (error) => {
      toast.error('WebSocket connection error.');
      setIsStreaming(false);
    };

    ws.onclose = () => {
      setIsStreaming(false);
    };

    // 2. Call REST API as fallback (but WebSocket will usually win)
    setLoading(true);
    try {
      const response = await axios.post(`${API_URL}/analyze`, {
        session_id: sessionId,
        sql_query: query,
      });
      // Only set if WebSocket hasn't already set the result
      setResult(prev => prev || response.data);
      toast.success('Query analyzed successfully!');
    } catch (error) {
      // Only show error if WebSocket didn't handle it
      if (!result) {
        toast.error('Analysis failed: ' + (error.response?.data?.detail || error.message));
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-container">
      <Toaster position="top-right" />
      
      <header className="app-header">
        <h1>🧠 SQL Optimizer Agent</h1>
        <p>Analyze, optimize, and explain SQL queries</p>
      </header>

      <main className="app-main">
        {!isConnected ? (
          <ConnectForm onConnect={handleConnect} loading={loading} />
        ) : (
          <>
            <div className="connection-status">
              ✅ Connected to BigQuery • {tables.length} tables found
              <button 
                className="btn-disconnect"
                onClick={() => {
                  setIsConnected(false);
                  setSessionId(null);
                  setTables([]);
                  setResult(null);
                  setStreamingExplanation('');
                  if (wsRef.current) {
                    wsRef.current.close();
                  }
                }}
              >
                Disconnect
              </button>
            </div>
            <QueryEditor 
              sql={sql} 
              setSql={setSql} 
              onAnalyze={handleAnalyze} 
              loading={loading || isStreaming} 
            />
            <ResultsDisplay 
              result={result} 
              streamingExplanation={streamingExplanation}
              isStreaming={isStreaming}
            />
          </>
        )}
      </main>

      <footer className="app-footer">
        <p>Powered by Groq LLM • BigQuery • FastAPI</p>
      </footer>
    </div>
  );
}

export default App;