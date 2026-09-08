import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, auth } from '../lib/api.js'

const BASE_TABS = [
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
  const [me, setMe] = useState(null)   // {id, email, role} — decides whether the Admins tab shows at all

  const [text, setText] = useState('')
  const [sourceName, setSourceName] = useState('manual-text')
  const [url, setUrl] = useState('')
  const [rows, setRows] = useState([])
  const [busy, setBusy] = useState(false)

  const [admins, setAdmins] = useState([])
  const [showAddAdmin, setShowAddAdmin] = useState(false)
  const [newEmail, setNewEmail] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newRole, setNewRole] = useState('admin')
  const [curPass, setCurPass] = useState('')
  const [nextPass, setNextPass] = useState('')

  const ROLE_LABEL = { superadmin: '👑 Super Admin', admin: '🛡 Admin Member' }

  const TABS = me?.role === 'superadmin' ? [...BASE_TABS, { id: 'admins', label: '👑 Admins' }] : BASE_TABS

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
    api.me().then(setMe).catch(() => {})
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

  async function loadAdmins() {
    setBusy(true)
    try {
      setAdmins(await api.listAdmins())
    } catch (err) {
      flash(`✕ ${err.message}`, 'err')
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => { if (tab === 'admins') loadAdmins() }, [tab]) // eslint-disable-line

  async function submitCreateAdmin() {
    setBusy(true)
    try {
      await api.createAdmin(newEmail, newPassword, newRole)
      flash(`✓ Created ${newEmail}`)
      setNewEmail(''); setNewPassword(''); setNewRole('admin'); setShowAddAdmin(false)
      await loadAdmins()
    } catch (err) {
      flash(`✕ ${err.message}`, 'err')
    } finally {
      setBusy(false)
    }
  }

  async function resetSomeonesPassword(userId, email) {
    const pw = window.prompt(`New password for ${email} (min 8 chars):`)
    if (!pw) return
    try {
      await api.resetAdminPassword(userId, pw)
      flash(`✓ Password reset for ${email}`)
    } catch (err) {
      flash(`✕ ${err.message}`, 'err')
    }
  }

  async function removeAdmin(userId, email) {
    if (!window.confirm(`Remove admin account ${email}? This can't be undone.`)) return
    try {
      await api.deleteAdmin(userId)
      flash(`✓ Removed ${email}`)
      await loadAdmins()
    } catch (err) {
      flash(`✕ ${err.message}`, 'err')
    }
  }

  async function submitChangeOwnPassword() {
    setBusy(true)
    try {
      await api.changeOwnPassword(curPass, nextPass)
      flash('✓ Password updated')
      setCurPass(''); setNextPass('')
    } catch (err) {
      flash(`✕ ${err.message}`, 'err')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="admin-bg admin-bg--panel">
      <div className="panel">
        <header className="panel__head">
          <div className="brand">
            <div className="brand__logo brand__logo--pink">🛡</div>
            <div>
              <div className="brand__name">
                CBIT Guru Admin
                {me && (
                  <span className={`pill pill--${me.role}`} style={{ marginLeft: 8 }}>
                    {ROLE_LABEL[me.role] || me.role}
                  </span>
                )}
              </div>
              <div className="muted small">{me ? me.email : 'Data Ingestion Panel'}</div>
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

        {me && (
          <details className="card">
            <summary style={{ cursor: 'pointer' }}>🔑 Change my password</summary>
            <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <input
                className="line" type="password" placeholder="Current password"
                value={curPass} onChange={(e) => setCurPass(e.target.value)}
              />
              <input
                className="line" type="password" placeholder="New password (min 8 chars)"
                value={nextPass} onChange={(e) => setNextPass(e.target.value)}
              />
              <button
                className="btn-primary" disabled={busy || !curPass || nextPass.length < 8}
                onClick={submitChangeOwnPassword}
              >
                Update Password
              </button>
            </div>
          </details>
        )}

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

          {tab === 'admins' && (
            <>
              <div className="card__head">
                <div>
                  <h3>👑 Admin Accounts</h3>
                  <p className="muted small">Super-admin only — create, reset, or remove admin logins</p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="chip" onClick={loadAdmins}>⟳ Refresh</button>
                  <button
                    className={`chip ${showAddAdmin ? 'chip--on' : ''}`}
                    onClick={() => setShowAddAdmin((v) => !v)}
                  >
                    + Add Admin
                  </button>
                </div>
              </div>

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Email</th><th>Role</th><th>Created</th><th></th></tr>
                  </thead>
                  <tbody>
                    {admins.map((u) => (
                      <tr key={u.id}>
                        <td>{u.email}</td>
                        <td><span className={`pill pill--${u.role}`}>{u.role}</span></td>
                        <td className="muted tiny">{new Date(u.created_at).toLocaleDateString()}</td>
                        <td style={{ display: 'flex', gap: 6 }}>
                          <button className="chip" onClick={() => resetSomeonesPassword(u.id, u.email)}>
                            Reset password
                          </button>
                          <button className="chip" onClick={() => removeAdmin(u.id, u.email)}>
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {showAddAdmin && (
                <div style={{ marginTop: 18, display: 'flex', flexDirection: 'column', gap: 10 }}>
                  <h3 style={{ margin: 0 }}>➕ New Admin Account</h3>
                  <input
                    className="line" placeholder="new.admin@cbit.ac.in"
                    value={newEmail} onChange={(e) => setNewEmail(e.target.value)}
                  />
                  <input
                    className="line" type="password" placeholder="Temporary password (min 8 chars)"
                    value={newPassword} onChange={(e) => setNewPassword(e.target.value)}
                  />
                  <select className="line" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
                    <option value="admin">admin</option>
                    <option value="superadmin">superadmin</option>
                  </select>
                  <button
                    className="btn-primary"
                    disabled={busy || !newEmail || newPassword.length < 8}
                    onClick={submitCreateAdmin}
                  >
                    Create Admin
                  </button>
                </div>
              )}
            </>
          )}
        </section>
      </div>

      {toast && <div className={`toast toast--${toast.kind}`}>{toast.message}</div>}
    </div>
  )
}
