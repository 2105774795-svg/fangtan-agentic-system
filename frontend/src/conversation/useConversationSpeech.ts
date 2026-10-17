import { useEffect, useRef, useState } from 'react';

export function useConversationSpeech() {
  const [speaking, setSpeaking] = useState(false);
  const [notice, setNotice] = useState('');
  const finishRef = useRef<(() => void) | null>(null);
  const epoch = useRef(0);
  const voicesReady = useRef<Promise<void> | null>(null);

  function prime() {
    if (!('speechSynthesis' in window) || voicesReady.current) return;
    voicesReady.current = new Promise<void>(resolve => {
      if (speechSynthesis.getVoices().length) { resolve(); return; }
      const done = () => { clearTimeout(timer); speechSynthesis.removeEventListener('voiceschanged', done); resolve(); };
      const timer = window.setTimeout(done, 1200);
      speechSynthesis.addEventListener('voiceschanged', done);
    });
  }

  function cancel() {
    epoch.current++;
    finishRef.current?.(); finishRef.current = null;
    if ('speechSynthesis' in window) speechSynthesis.cancel();
    setSpeaking(false);
  }

  useEffect(() => { prime(); return () => { epoch.current++; finishRef.current?.(); if ('speechSynthesis' in window) speechSynthesis.cancel(); }; }, []);

  async function speak(text: string) {
    cancel(); const token = epoch.current;
    if (!('speechSynthesis' in window) || !window.SpeechSynthesisUtterance) { setNotice('当前设备不能朗读，已显示文字，自动对谈会继续听您说。'); return; }
    prime(); await voicesReady.current;
    if (epoch.current !== token) return;
    const voice = speechSynthesis.getVoices().find(v => /^zh/i.test(v.lang) && v.localService);
    if (!voice) { setNotice('当前设备没有本地中文声音，已显示文字，自动对谈会继续听您说。'); return; }
    setSpeaking(true); setNotice('');
    // 短句分段：单次播放故障不会把会话卡住 90 秒。
    const sentences = text.match(/[^。！？!?\n]{1,65}[。！？!?\n]?/g) || [text];
    for (const sentence of sentences) {
      if (epoch.current !== token) break;
      const ok = await new Promise<boolean>(resolve => {
        let settled = false, timer: number;
        const finish = (success: boolean) => {
          if (settled) return;
          settled = true; clearTimeout(timer); finishRef.current = null; resolve(success);
        };
        const utterance = new SpeechSynthesisUtterance(sentence);
        utterance.voice = voice; utterance.lang = voice.lang; utterance.rate = .88;
        finishRef.current = () => finish(false);
        utterance.onend = () => finish(true);
        utterance.onerror = () => finish(false);
        timer = window.setTimeout(() => { finish(false); speechSynthesis.cancel(); }, Math.min(45000, Math.max(8000, sentence.length * 700 + 4000)));
        try { speechSynthesis.speak(utterance); } catch { finish(false); }
      });
      if (!ok) {
        if (epoch.current === token) setNotice('朗读暂未完成，文字已保留，自动对谈会继续听您说。');
        break;
      }
    }
    if (epoch.current === token) setSpeaking(false);
  }
  return { speak, cancel, prime, speaking, notice };
}
