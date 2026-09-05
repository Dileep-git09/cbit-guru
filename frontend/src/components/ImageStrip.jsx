import { useState } from 'react'

/** "Related Images" row under an answer — report Figures 3.6 / 3.7. */
export default function ImageStrip({ images }) {
  const [lightbox, setLightbox] = useState(null)
  const visible = images.filter((i) => i.url)
  if (!visible.length) return null

  return (
    <>
      <div className="imgstrip">
        <div className="imgstrip__label">🖼 Related Images ({visible.length})</div>
        <div className="imgstrip__row">
          {visible.map((img) => (
            <button
              key={img.url}
              className="imgcard"
              onClick={() => setLightbox(img)}
              title={img.caption}
            >
              <img src={img.url} alt={img.caption} loading="lazy"
                   onError={(e) => { e.currentTarget.parentElement.style.display = 'none' }} />
              <span className="imgcard__cap">{img.caption}</span>
              {img.tags?.length > 0 && (
                <span className="imgcard__tag">{img.tags[0]}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {lightbox && (
        <div className="lightbox" onClick={() => setLightbox(null)} role="dialog">
          <figure onClick={(e) => e.stopPropagation()}>
            <img src={lightbox.url} alt={lightbox.caption} />
            <figcaption>{lightbox.caption}</figcaption>
          </figure>
          <button className="lightbox__close" onClick={() => setLightbox(null)}>✕</button>
        </div>
      )}
    </>
  )
}
