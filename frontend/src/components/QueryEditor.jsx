import React from 'react';
import Editor from '@monaco-editor/react';

function QueryEditor({ sql, setSql, onAnalyze, loading }) {
  return (
    <div className="query-editor">
      <div className="editor-header">
        <h3>📝 SQL Query</h3>
        <button 
          onClick={() => onAnalyze(sql)} 
          disabled={loading}
          className="btn-analyze"
        >
          {loading ? '⏳ Analyzing...' : '🔍 Analyze'}
        </button>
      </div>
      <Editor
        height="200px"
        language="sql"
        value={sql}
        onChange={(value) => setSql(value)}
        theme="vs-dark"
        options={{
          minimap: { enabled: false },
          fontSize: 14,
          lineNumbers: 'on',
          automaticLayout: true,
        }}
      />
      <div className="editor-hint">
        💡 Tip: Try `SELECT * FROM wdi_2016` to see the agent in action
      </div>
    </div>
  );
}

export default QueryEditor;