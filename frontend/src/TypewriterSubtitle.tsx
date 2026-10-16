import { useEffect, useState } from 'react';

// Timing follows MiMoCodeWeb's useHeroSubtitleTyping; adapted for accessibility
// and a reserved text box so the composer never moves while the line is typed.
// Reference: https://github.com/kklt-ai/MiMoCodeWeb/blob/main/docs/subtitle-typewriter-effect.md
export function TypewriterSubtitle({ text }: { text: string }) {
  const chars = Array.from(text);
  const [count, setCount] = useState(0);
  const [animate, setAnimate] = useState(true);
  useEffect(() => {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    const mobile = matchMedia('(max-width: 700px)');
    let timers: number[] = [];
    const finish = () => { timers.forEach(clearTimeout); setAnimate(false); setCount(Array.from(text).length); };
    const enabled = !reduced.matches && !mobile.matches;
    setAnimate(enabled);
    setCount(enabled ? 0 : Array.from(text).length);
    if (enabled) timers = Array.from(text).map((_, i) => window.setTimeout(() => setCount(i + 1), 350 + i * 55));
    // Environment changes finish the line instead of replaying it during resize.
    reduced.addEventListener('change', finish);
    mobile.addEventListener('change', finish);
    return () => { timers.forEach(clearTimeout); reduced.removeEventListener('change', finish); mobile.removeEventListener('change', finish); };
  }, [text]);
  const done = !animate || count >= chars.length;
  return <h1 className={`hero-subtitle ${done ? 'is-done' : 'is-typing'}`} aria-label={text}>
    <span className="typewriter-measure" aria-hidden="true">{text}</span>
    <span className="typewriter-line" aria-hidden="true"><span className="typewriter-text">{chars.slice(0, count).join('')}</span><span className="typewriter-caret"/></span>
  </h1>;
}
