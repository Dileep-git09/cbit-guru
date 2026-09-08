import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, auth } from '../lib/api.js'

export default function AdminLogin() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [online, setOnline] = useState(null)
  const nav = useNavigate()

  useEffect(() => {
    api.health().then((h) => setOnline(h.status === 'ok')).catch(() => setOnline(false))
    // Already holding a token from an earlier session? Skip straight past the
    // form — AdminPanel itself re-validates the token (via /admin/me) and
    // bounces back here if it's expired, so this can't strand anyone on a
    // broken "logged in" state.
    if (auth.get()) nav('/admin/panel')
  }, [nav])

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { access_token } = await api.login(email, password)
      auth.set(access_token)
      nav('/admin/panel')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="admin-bg">
      <form className="login-card" onSubmit={submit}>
        <div className="login-card__icon">🛡</div>
        <h2>Admin Login</h2>
        <p className="muted">Access the CBIT Guru admin panel</p>
        <div className={`login-card__status ${online ? 'is-online' : 'is-offline'}`}>
          ● Backend {online === null ? 'checking…' : online ? 'online' : 'offline'}
        </div>

        <input
          type="email" placeholder="Admin Email" value={email} required
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          type="password" placeholder="Password" value={password} required
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && <div className="error">{error}</div>}

        <button className="btn-primary" disabled={busy}>
          {busy ? 'Signing in…' : 'Login'}
        </button>
        <Link className="muted back" to="/">← Back to Chat</Link>
      </form>
    </div>
  )
}
