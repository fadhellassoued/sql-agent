import React from 'react';
import ReactMarkdown from 'react-markdown';

function ResultsDisplay({ result, streamingExplanation, isStreaming }) {
  return (
    <div className="results-display">
      <h3>📊 Optimization Results</h3>
      
      <div className="result-grid">
        {/* Issues */}
        <div className="result-card issues">
          <h4>❌ Issues Detected</h4>
          {result?.issues && result.issues.length > 0 ? (
            <ul>
              {result.issues.map((issue, i) => (
                <li key={i}>{issue}</li>
              ))}
            </ul>
          ) : (
            <p className="success">✅ No issues detected</p>
          )}
        </div>

        {/* Optimized Query */}
        <div className="result-card optimized">
          <h4>⚡ Optimized Query</h4>
          <pre className="sql-code">{result?.optimized_query || 'Waiting...'}</pre>
          {result?.optimized_query && (
            <button 
              className="btn-copy"
              onClick={() => navigator.clipboard.writeText(result.optimized_query)}
            >
              📋 Copy
            </button>
          )}
        </div>

        {/* Streaming Explanation */}
        <div className="result-card full-width explanation">
          <h4>💡 Explanation {isStreaming && <span className="streaming-indicator">● Live</span>}</h4>
          <div className="markdown-content">
            {streamingExplanation ? (
              <ReactMarkdown>{streamingExplanation}</ReactMarkdown>
            ) : result?.explanation && !isStreaming ? (
              <ReactMarkdown>{result.explanation}</ReactMarkdown>
            ) : (
              <p className="placeholder">⏳ Explanation will appear here...</p>
            )}
            {isStreaming && <span className="cursor-blink">▌</span>}
          </div>
        </div>

        {/* Metrics */}
        <div className="result-card metrics">
          <h4>📈 Metrics</h4>
          <div className="metric-item">
            <span className="metric-label">Estimated Cost</span>
            <span className="metric-value">${result?.estimated_cost_usd?.toFixed(6) || '—'}</span>
          </div>
          <div className="metric-item">
            <span className="metric-label">Execution Time</span>
            <span className="metric-value">{result?.execution_time_ms?.toFixed(2) || '—'} ms</span>
          </div>
        </div>

        {/* Recommendations */}
        {result?.recommendations && result.recommendations.length > 0 && (
          <div className="result-card full-width recommendations">
            <h4>💎 Recommendations</h4>
            <ul>
              {result.recommendations.map((rec, i) => (
                <li key={i}>{rec}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default ResultsDisplay;