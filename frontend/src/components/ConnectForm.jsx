import React, { useState } from 'react';

function ConnectForm({ onConnect, loading }) {
  const [credentials, setCredentials] = useState({
    db_type: 'bigquery',
    project_id: '',
    dataset: '',
  });

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!credentials.project_id || !credentials.dataset) {
      alert('Please fill in all fields.');
      return;
    }
    onConnect(credentials);
  };

  return (
    <div className="connect-form">
      <h2>🔗 Connect to Database</h2>
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label>Database Type</label>
          <select 
            value={credentials.db_type} 
            onChange={(e) => setCredentials({...credentials, db_type: e.target.value})}
          >
            <option value="bigquery">BigQuery</option>
            <option value="postgresql">PostgreSQL</option>
          </select>
        </div>

        <div className="form-group">
          <label>Project ID</label>
          <input
            type="text"
            placeholder="your-gcp-project-id"
            value={credentials.project_id}
            onChange={(e) => setCredentials({...credentials, project_id: e.target.value})}
            required
          />
        </div>

        <div className="form-group">
          <label>Dataset</label>
          <input
            type="text"
            placeholder="your_dataset_name"
            value={credentials.dataset}
            onChange={(e) => setCredentials({...credentials, dataset: e.target.value})}
            required
          />
        </div>

        <button type="submit" disabled={loading}>
          {loading ? '⏳ Connecting...' : '🚀 Connect'}
        </button>
      </form>
    </div>
  );
}

export default ConnectForm;