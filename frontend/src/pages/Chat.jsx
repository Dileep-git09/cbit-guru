import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import Message from '../components/Message.jsx'
import { api } from '../lib/api.js'
import { useSpeechRecognition, useSpeechSynthesis } from '../lib/useVoice.js'

const SUGGESTIONS = [
  { icon: '🎯', text: 'Tell me about admission requirements' },
  { icon: '📊', text: 'What are the placement statistics?' },
  { icon: '🏛', text: 'Describe the campus facilities' },
  { icon: '🎪', text: 'What events are happening?' },
]

export default function Chat() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [online, setOnline] = useState(null)
  const [autoSpeak, setAutoSpeak] = useState(false)

  const scrollRef = useRef(null)
  const tts = useSpeechSynthesis()

  const handleVoiceResult = useCallback((text, isFinal) => {
    setInput(text)
    // Small delay after a final transcript lets React commit the input
    // update before send() reads it, and gives a beat for the user to see
    // what was transcribed before it's sent.
    if (isFinal && text.trim()) setTimeout(() => send(text), 150)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const stt = useSpeechRecognition({ lang: 'en-IN', onResult: handleVoiceResult })

  useEffect(() => {
    api.health().then((h) => setOnline(h.status === 'ok')).catch(() => setOnline(false))
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  async function send(rawText) {
    const text = (rawText ?? input).trim()
    if (!text || busy) return

    setInput('')
    setBusy(true)

    const history = messages.map((m) => ({ role: m.role, content: m.content }))
    const userMsg = { role: 'user', content: text }
    // Push an empty assistant message immediately so the typing-dots
    // indicator shows right away, then patch it in place as tokens arrive.
    const botMsg = { role: 'assistant', content: '', sources: [], images: [] }
    setMessages((m) => [...m, userMsg, botMsg])

    let accumulated = ''
    const patchBot = (patch) =>
      setMessages((m) => {
        const next = [...m]
        next[next.length - 1] = { ...next[next.length - 1], ...patch }
        return next
      })

    try {
      await api.chatStream(text, history, {
        onMeta: ({ sources, images }) => patchBot({ sources, images }),
        onToken: (t) => {
          accumulated += t
          patchBot({ content: accumulated })
        },
        onError: (err) => patchBot({ content: `⚠️ ${err.message}` }),
      })
      if (autoSpeak && accumulated) tts.speak(accumulated)
    } catch (err) {
      patchBot({ content: `⚠️ ${err.message}` })
    } finally {
      setBusy(false)
    }
  }

  const onSpeak = (text) => (tts.speaking ? tts.cancel() : tts.speak(text))
  const empty = messages.length === 0

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand__logo">🎓</div>
          <div>
            <div className="brand__name">CBIT Guru</div>
            <div className={`brand__status ${online ? 'is-online' : 'is-offline'}`}>
              ● {online === null ? 'connecting…' : online ? 'Online' : 'Backend offline'}
            </div>
          </div>
        </div>

        <div className="topbar__actions">
          <button
            className={`chip ${autoSpeak ? 'chip--on' : ''}`}
            onClick={() => setAutoSpeak((v) => !v)}
            title="Automatically read answers aloud"
          >
            🔊 Auto-speak
          </button>
          <button className="chip" onClick={() => { setMessages([]); tts.cancel() }}>
            ↻ Clear
          </button>
          <Link className="chip" to="/admin">⚙ Admin</Link>
        </div>
      </header>

      <main className="scroll" ref={scrollRef}>
        {empty ? (
          <section className="hero">
            <div className="hero__logo">🎓</div>
            <h1>Hello, I am CBIT Guru</h1>
            <p>Ask me about Placements, Academics, Campus Life, or anything related to CBIT!</p>
            <div className="hero__grid">
              {SUGGESTIONS.map((s) => (
                <button key={s.text} className="suggest" onClick={() => send(s.text)}>
                  <span className="suggest__icon">{s.icon}</span>
                  {s.text}
                </button>
              ))}
            </div>
          </section>
        ) : (
          <div className="thread">
            {messages.map((m, i) => (
              <Message
                key={i}
                msg={m}
                onSpeak={onSpeak}
                speaking={tts.speaking && i === messages.length - 1}
              />
            ))}
          </div>
        )}
      </main>

      <footer className="composer">
        {stt.supported && (
          <button
            className={`mic ${stt.listening ? 'mic--live' : ''}`}
            onClick={stt.toggle}
            title={stt.listening ? 'Stop listening' : 'Ask by voice'}
          >
            🎙
          </button>
        )}
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && send()}
          placeholder={stt.listening ? 'Listening…' : 'Ask about CBIT…'}
          disabled={busy}
        />
        <button className="send" onClick={() => send()} disabled={busy || !input.trim()}>
          ➤
        </button>
      </footer>
    </div>
  )
}
