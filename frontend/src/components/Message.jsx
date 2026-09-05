import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ImageStrip from './ImageStrip.jsx'

export default function Message({ msg, onSpeak, speaking }) {
  const isUser = msg.role === 'user'

  if (isUser) {
    return (
      <div className="row row--user">
        <div className="bubble bubble--user">{msg.content}</div>
        <div className="avatar avatar--user">You</div>
      </div>
    )
  }

  return (
    <div className="row row--bot">
      <div className="avatar avatar--bot" aria-hidden>🎓</div>
      <div className="bubble bubble--bot">
        {msg.content ? (
          <Markdown remarkPlugins={[remarkGfm]}>{msg.content}</Markdown>
        ) : (
          <span className="typing"><i /><i /><i /></span>
        )}

        {msg.images?.length > 0 && <ImageStrip images={msg.images} />}

        {msg.sources?.length > 0 && (
          <details className="sources">
            <summary>Sources ({msg.sources.length})</summary>
            <ol>
              {msg.sources.map((s) => (
                <li key={s.n}>
                  {s.url ? (
                    <a href={s.url} target="_blank" rel="noreferrer">{s.label}</a>
                  ) : (
                    s.label
                  )}
                  <span className="score"> · {s.score}</span>
                </li>
              ))}
            </ol>
          </details>
        )}

        {msg.content && (
          <button
            className="speak-btn"
            onClick={() => onSpeak(msg.content)}
            title="Read this answer aloud"
          >
            {speaking ? '■ Stop' : '🔊 Listen'}
          </button>
        )}
      </div>
    </div>
  )
}
