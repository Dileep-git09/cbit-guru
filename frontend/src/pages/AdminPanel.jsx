import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, auth } from '../lib/api.js'

const TABS = [
  { id: 'text', label: '📄 Text' },
  { id: 'file', label: '⬆ File' },
  { id: 'url', label: '🌐 URL' },
  { id: 'browse', label: '🗂 Browse Data' },
]

export default function AdminPanel() {
  const nav = useNavigate()
  const [tab, setTab] = useState('text')
  const [stats, setStats] = useState(null)
  const [toast, setToast] = useState(null)

  const [text, setText] = useState('')
  const [sourceName, setSourceName] = useState('manual-text')
  const [url, setUrl] = useState('')
  const [rows, setRows] = useState([])
  const [busy, setBusy] = useState(false)

  const refreshStats = useCallback(async () => {
    try {
      setStats(await api.stats())
    } catch (err) {
      // A 401 here means the JWT expired or was tampered with — bounce back
      // to login rather than showing a broken stats card.
      if (String(err.message).match(/token|401|expired/i)) {
        auth.clear()
        nav('/admin')
      }
    }
  }, [nav])

  useEffect(() => {
    if (!auth.get()) { nav('/admin'); return }
    refreshStats()
  }, [nav, refreshStats])

  function flash(message, kind = 'ok') {
    setToast({ message, kind })
    setTimeout(() => setToast(null), 4000)
  }

  async function guard(fn) {
    setBusy(true)
    try {
      const res = await fn()
      flash(`✓ ${res.message || 'Done'} — ${res.chunks ?? 0} chunks added`)
      await refreshStats()
    } catch (err) {
      flash(`✕ ${err.message}`, 'err')
    } finally {
      setBusy(false)
    }
  }

  const submitText = () => guard(() => api.ingestText(text, sourceName)).then(() => setText(''))
  const submitUrl = () => guard(() => api.ingestUrl(url)).then(() => setUrl(''))
  const submitFile = (e) => {
    const file = e.target.files?.[0]
    if (file) guard(() => api.ingestFile(file))
    e.target.value = ''
  }

  async function loadBrowse() {
    setBusy(true)
    try {
      setRows((await api.browse(250)).items)
    } catch (err) {
      flash(`✕ ${err.message}`, 'err')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => { if (tab === 'browse') loadBrowse() }, [tab]) // eslint-disable-line

  return (
    <div className="admin-bg admin-bg--panel">
      <div className="panel">
        <header className="panel__head">
          <div className="brand">
            <div className="brand__logo brand__logo--pink">🛡</div>
            <div>
              <div className="brand__name">CBIT Guru Admin</div>
              <div className="muted small">Data Ingestion Panel</div>
            </div>
          </div>
          <div className="topbar__actions">
            <span className="dot-online">● online</span>
            <Link className="chip" to="/">💬 Chat</Link>
            <button className="chip" onClick={() => { auth.clear(); nav('/admin') }}>
              ⇥ Logout
            </button>
          </div>
        </header>

        <div className="banner">
          📌 All data ingested here is <b>globally accessible</b> to all users asking questions.
        </div>

        <div className="statcard">
          <div>
            <div className="muted small">Total Rows in Qdrant</div>
            <div className="statcard__num">{stats ? stats.total_points.toLocaleString() : '…'}</div>
            {stats && (
              <div className="muted small">
                {stats.collection} · {stats.embedding_model} · {stats.llm_model}
              </div>
            )}
          </div>
          <button className="chip" onClick={refreshStats}>⟳</button>
        </div>

        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`tab ${tab === t.id ? 'tab--on' : ''}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>

        <section className="card">
          {tab === 'text' && (
            <>
              <h3>📄 Text Ingestion</h3>
              <p className="muted small">Paste or type text to add to the knowledge base</p>
              <input
                className="line" placeholder="Source label (e.g. Exam Notice Aug 2026)"
                value={sourceName} onChange={(e) => setSourceName(e.target.value)}
              />
              <textarea
                placeholder="Paste your text content here…"
                value={text} onChange={(e) => setText(e.target.value)} rows={10}
              />
              <button className="btn-primary" disabled={busy || text.trim().length < 20}
                      onClick={submitText}>
                Ingest Text
              </button>
            </>
          )}

          {tab === 'file' && (
            <>
              <h3>⬆ File Ingestion</h3>
              <p className="muted small">PDF, TXT, MD, HTML or CSV — max 25 MB</p>
              <label className="dropzone">
                <input type="file" accept=".pdf,.txt,.md,.html,.htm,.csv"
                       onChange={submitFile} disabled={busy} />
                <span>{busy ? 'Uploading…' : 'Click to choose a file'}</span>
              </label>
            </>
          )}

          {tab === 'url' && (
            <>
              <h3>🌐 URL Ingestion</h3>
              <p className="muted small">Fetch a page and add its text to the knowledge base</p>
              <input className="line" placeholder="https://www.cbit.ac.in/…"
                     value={url} onChange={(e) => setUrl(e.target.value)} />
              <button className="btn-primary" disabled={busy || !url.startsWith('http')}
                      onClick={submitUrl}>
                Fetch &amp; Ingest
              </button>
            </>
          )}

          {tab === 'browse' && (
            <>
              <div className="card__head">
                <div>
                  <h3>🗂 Browse Data</h3>
                  <p className="muted small">View ingested chunks (loaded {rows.length})</p>
                </div>
                <button className="chip" onClick={loadBrowse}>⟳ Refresh</button>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Source</th><th>Content Preview</th><th>ID</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={r.id}>
                        <td>
                          <span className={`pill pill--${r.source}`}>{r.source}</span>
                          <div className="muted tiny">{r.file_name || r.url}</div>
                        </td>
                        <td className="preview">{r.preview}…</td>
                        <td className="muted tiny">{r.id.slice(0, 8)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!rows.length && !busy && <p className="muted center">No data yet.</p>}
              </div>
            </>
          )}
        </section>
      </div>

      {toast && <div className={`toast toast--${toast.kind}`}>{toast.message}</div>}
    </div>
  )
}
