import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Speech-to-text via the Web Speech API (Chrome/Edge) and text-to-speech via
 * SpeechSynthesis. Report §3.4.2 "Frontend Layer" / §3.5.5 "Voice-Based
 * Interaction": spoken query -> text -> RAG -> spoken answer.
 */
export function useSpeechRecognition({ lang = 'en-IN', onResult } = {}) {
  const [listening, setListening] = useState(false)
  const [supported, setSupported] = useState(false)
  const recRef = useRef(null)

  useEffect(() => {
    // Firefox has no SpeechRecognition implementation at all — `supported`
    // stays false there and the mic button simply doesn't render.
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) return
    setSupported(true)

    const rec = new SR()
    rec.lang = lang
    rec.interimResults = true   // fire onresult continuously while speaking, not just at the end
    rec.continuous = false
    rec.maxAlternatives = 1

    rec.onresult = (e) => {
      let finalText = ''
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript
        if (e.results[i].isFinal) finalText += t
        else interim += t
      }
      onResult?.(finalText || interim, Boolean(finalText))
    }
    rec.onerror = () => setListening(false)
    rec.onend = () => setListening(false)

    recRef.current = rec
    return () => { try { rec.abort() } catch { /* already stopped */ } }
  }, [lang, onResult])

  const start = useCallback(() => {
    if (!recRef.current || listening) return
    try {
      recRef.current.start()
      setListening(true)
    } catch { /* start() throws if already running */ }
  }, [listening])

  const stop = useCallback(() => {
    try { recRef.current?.stop() } catch { /* noop */ }
    setListening(false)
  }, [])

  return { listening, supported, start, stop, toggle: () => (listening ? stop() : start()) }
}

/** Strip markdown so the speech synthesiser doesn't read out asterisks. */
function plain(text) {
  return text
    .replace(/```[\s\S]*?```/g, '')
    .replace(/[*_#>`[\]()]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

/** Guess a BCP-47 voice tag from the script the model replied in.
 *
 * This is the concrete mechanism behind "how does voice handle Telugu?":
 * rather than tracking what language the user asked in, we just look at
 * which Unicode block the model's *reply* landed in — the LLM already
 * decided the reply language via llm.py's system prompt, so the frontend
 * only has to read that decision off the text.
 */
function detectLang(text) {
  if (/[ఀ-౿]/.test(text)) return 'te-IN' // Telugu Unicode block
  if (/[ऀ-ॿ]/.test(text)) return 'hi-IN' // Devanagari Unicode block
  return 'en-IN'
}

export function useSpeechSynthesis() {
  const [speaking, setSpeaking] = useState(false)
  const supported = typeof window !== 'undefined' && 'speechSynthesis' in window

  const speak = useCallback((text) => {
    if (!supported || !text) return
    window.speechSynthesis.cancel()   // stop any in-flight utterance before starting a new one

    const utter = new SpeechSynthesisUtterance(plain(text).slice(0, 1200))
    utter.lang = detectLang(text)
    utter.rate = 1.0
    utter.pitch = 1.0

    const match = window.speechSynthesis
      .getVoices()
      .find((v) => v.lang === utter.lang)
    if (match) utter.voice = match

    utter.onend = () => setSpeaking(false)
    utter.onerror = () => setSpeaking(false)
    setSpeaking(true)
    window.speechSynthesis.speak(utter)
  }, [supported])

  const cancel = useCallback(() => {
    if (!supported) return
    window.speechSynthesis.cancel()
    setSpeaking(false)
  }, [supported])

  return { speak, cancel, speaking, supported }
}
