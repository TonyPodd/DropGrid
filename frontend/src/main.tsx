import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom';
import { get } from './api';
import './style.css';

type Row = { id: string; name: string; status?: string; publication_check_hours?: number };
type Health = { status: string; database: string };

function Dashboard() {
  const [health, setHealth] = useState<Health>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    get<Health>('/health', controller.signal).then(setHealth).catch((err: unknown) => {
      if (!controller.signal.aborted) setError(String(err));
    });
    return () => controller.abort();
  }, []);
  return <><h1>Dashboard</h1>{error ? <p role="alert">{error}</p> : health ?
    <p>Backend: {health.status} · Database: {health.database}</p> : <p>Loading…</p>}
    <p>Campaign management foundation. VK publishing is planned.</p></>;
}

function ListPage({ title, endpoint }: { title: string; endpoint: string }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    get<Row[]>(`/api/v1/${endpoint}?limit=100&offset=${offset}`, controller.signal)
      .then(setRows).catch((err: unknown) => {
        if (!controller.signal.aborted) setError(String(err));
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [endpoint, offset]);
  return <><h1>{title}</h1>{loading ? <p>Loading…</p> : error ? <p role="alert">{error}</p> :
    rows.length === 0 ? <p>No records yet.</p> : <table><thead><tr><th>Name</th><th>Status</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.id}><td>{row.name}</td><td>{row.status ?? '—'}</td></tr>)}</tbody></table>}
    <div><button disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous</button>
      <button disabled={rows.length < 100 || loading} onClick={() => setOffset(offset + 100)}>Next</button></div></>;
}

function App() {
  return <BrowserRouter><header><strong>DropGrid</strong><nav>
    <NavLink to="/">Dashboard</NavLink><NavLink to="/accounts">Accounts</NavLink>
    <NavLink to="/grids">Grids</NavLink><NavLink to="/campaigns">Campaigns</NavLink>
  </nav></header><main><Routes>
    <Route path="/" element={<Dashboard />} />
    <Route path="/accounts" element={<ListPage key="accounts" title="Accounts" endpoint="accounts" />} />
    <Route path="/grids" element={<ListPage key="grids" title="Grids" endpoint="grids" />} />
    <Route path="/campaigns" element={<ListPage key="campaigns" title="Campaigns" endpoint="campaigns" />} />
    <Route path="*" element={<h1>Page not found</h1>} />
  </Routes></main></BrowserRouter>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
