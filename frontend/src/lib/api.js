// Every backend call funnels through this one module — components never call
// fetch() directly, so the API surface has exactly one place to change if a
// route or auth scheme changes.
const BASE = import.meta.env.VITE_API_BASE || '/api'

const TOKEN_KEY = 'cbit_guru_admin_token'

export const auth = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

/** Pulls a human-readable message out of a failed response.
 *
 * Most of our own errors send {detail: "a plain string"} — but a
 * FastAPI/Pydantic input-validation failure (bad field, blank message,
 * message over 4000 chars, etc.) sends {detail: [{msg, loc, ...}, ...]}
 * instead. Without unpacking that, an Error built straight from the array
 * stringifies to "[object Object]" wherever it's displayed.
 */
async function errorDetail(res) {
  let detail = `${res.status} ${res.statusText}`.trim()
  try {
    const body = await res.json()
    if (Array.isArray(body.detail)) {
      detail = body.detail.map((e) => e.msg || JSON.stringify(e)).join('; ')
    } else if (body.detail) {
      detail = body.detail
    }
  } catch { /* non-JSON error body */ }
  return detail
}

async function handle(res) {
  if (!res.ok) throw new Error(await errorDetail(res))
  return res.json()
}

function adminHeaders(extra = {}) {
  return { Authorization: `Bearer ${auth.get()}`, ...extra }
}

export const api = {
  health: () => fetch(`${BASE}/health`).then(handle),

  chat: (message, history = []) =>
    fetch(`${BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, history }),
    }).then(handle),

  /**
   * Streaming chat over SSE.
   * onMeta({sources, images, grounded}) fires once, then onToken(text) repeatedly.
   * Parses the raw SSE wire format manually (event: / data: lines separated
   * by blank lines) since the browser's built-in EventSource API only
   * supports GET requests, and this needs to POST a JSON body.
   */
  chatStream: async (message, history, { onMeta, onToken, onDone, onError }) => {
    const res = await fetch(`${BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, history }),
    })
    if (!res.ok) {
      onError?.(new Error(await errorDetail(res)))
      return
    }
    if (!res.body) {
      onError?.(new Error('Stream failed: empty response body'))
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE frames are separated by a blank line; the last split() piece may
      // be a partial frame still arriving, so it's kept in `buffer` for the
      // next chunk instead of being parsed prematurely.
      const frames = buffer.split('\n\n')
      buffer = frames.pop() || ''

      for (const frame of frames) {
        const evLine = frame.split('\n').find((l) => l.startsWith('event: '))
        const dataLine = frame.split('\n').find((l) => l.startsWith('data: '))
        if (!evLine || !dataLine) continue
        const event = evLine.slice(7).trim()
        let payload = {}
        try { payload = JSON.parse(dataLine.slice(6)) } catch { continue }

        if (event === 'meta') onMeta?.(payload)
        else if (event === 'token') onToken?.(payload.t)
        else if (event === 'error') onError?.(new Error(payload.detail))
        else if (event === 'done') onDone?.()
      }
    }
    onDone?.()
  },

  login: (email, password) =>
    fetch(`${BASE}/admin/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    }).then(handle),

  stats: () => fetch(`${BASE}/admin/stats`, { headers: adminHeaders() }).then(handle),

  me: () => fetch(`${BASE}/admin/me`, { headers: adminHeaders() }).then(handle),

  listAdmins: () => fetch(`${BASE}/admin/users`, { headers: adminHeaders() }).then(handle),

  createAdmin: (email, password, role) =>
    fetch(`${BASE}/admin/users`, {
      method: 'POST',
      headers: adminHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ email, password, role }),
    }).then(handle),

  deleteAdmin: (userId) =>
    fetch(`${BASE}/admin/users/${userId}`, {
      method: 'DELETE',
      headers: adminHeaders(),
    }).then(handle),

  changeOwnPassword: (current_password, new_password) =>
    fetch(`${BASE}/admin/me/password`, {
      method: 'PATCH',
      headers: adminHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ current_password, new_password }),
    }).then(handle),

  resetAdminPassword: (userId, new_password) =>
    fetch(`${BASE}/admin/users/${userId}/password`, {
      method: 'PATCH',
      headers: adminHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ new_password }),
    }).then(handle),

  browse: (limit = 250) =>
    fetch(`${BASE}/admin/browse?limit=${limit}`, { headers: adminHeaders() }).then(handle),

  deleteDoc: (docId) =>
    fetch(`${BASE}/admin/doc/${docId}`, {
      method: 'DELETE',
      headers: adminHeaders(),
    }).then(handle),

  ingestText: (text, source_name) =>
    fetch(`${BASE}/admin/ingest/text`, {
      method: 'POST',
      headers: adminHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ text, source_name }),
    }).then(handle),

  ingestUrl: (url) =>
    fetch(`${BASE}/admin/ingest/url`, {
      method: 'POST',
      headers: adminHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ url }),
    }).then(handle),

  ingestFile: (file) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`${BASE}/admin/ingest/file`, {
      method: 'POST',
      headers: adminHeaders(),   // no Content-Type here — the browser sets the multipart boundary itself
      body: fd,
    }).then(handle)
  },
}
