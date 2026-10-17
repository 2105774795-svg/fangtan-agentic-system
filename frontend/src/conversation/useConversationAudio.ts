import { useEffect, useRef, useState } from 'react';
import { recordingOutbox, type AudioChunk } from '../recordingOutbox';
import { requestId } from '../requestId';

type Draft = { id: string; next: number; sent: { id: string; index: number }[] };
type Options = {
  sessionId: string;
  silenceMs?: number;
  completedTurnIds: string[];
  upload: (chunk: AudioChunk, turnId: string) => Promise<{ text: string }>;
  submit: (body: { client_id: string; audio_ids: string[] }) => Promise<void>;
};

/** Transport-independent recorder. A 10s file is NOT a conversational turn.
 * Files survive failures in the original IndexedDB outbox; VAD or the explicit
 * "said enough" action submits one whole utterance in original part order.
 */
export function useConversationAudio(options: Options) {
  const optionsRef = useRef(options); optionsRef.current = options;
  const key = `conversation-pending-${options.sessionId}`;
  const [listening, setListening] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [unsaved, setUnsaved] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [transcript, setTranscript] = useState('');
  const draft = useRef<Draft | null>(null);
  const media = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const context = useRef<AudioContext | null>(null);
  const want = useRef(false);
  const segmentTimer = useRef<number | undefined>(undefined);
  const analyserTimer = useRef<number | undefined>(undefined);
  const stopWait = useRef<Promise<void>>(Promise.resolve());
  const writes = useRef<Promise<unknown>>(Promise.resolve());
  const memory = useRef<AudioChunk[]>([]);
  const uploading = useRef<Promise<void> | null>(null);
  const completing = useRef(false);
  const starting = useRef(false);
  const active = useRef(true);
  const queueKey = () => `conversation:${optionsRef.current.sessionId}:${draft.current?.id}`;
  const save = () => { if (draft.current) localStorage.setItem(key, JSON.stringify(draft.current)); else localStorage.removeItem(key); setPending(!!draft.current); };
  useEffect(() => {
    active.current = true;
    try { const stored = JSON.parse(localStorage.getItem(key) || 'null') as Draft | null; if (stored?.id && Array.isArray(stored.sent)) { if (optionsRef.current.completedTurnIds.includes(stored.id)) { localStorage.removeItem(key); draft.current = null; setPending(false); } else { draft.current = stored; setPending(true); } } } catch { setError('本机录音索引无法恢复，已保存的原音仍可在档案中查看'); }
    const prevent = (event: BeforeUnloadEvent) => { if (want.current || memory.current.length) { event.preventDefault(); event.returnValue = ''; } };
    addEventListener('beforeunload', prevent);
    return () => { active.current = false; want.current = false; clearTimeout(segmentTimer.current); clearInterval(analyserTimer.current); if (recorder.current?.state === 'recording') recorder.current.stop(); media.current?.getTracks().forEach(t => t.stop()); void context.current?.close(); removeEventListener('beforeunload', prevent); };
  }, [key]);

  async function pump(): Promise<void> {
    if (uploading.current) { await uploading.current; return pump(); }
    const current = draft.current;
    if (!current) return;
    uploading.current = (async () => {
      const box = await recordingOutbox();
      for (const chunk of [...memory.current]) { await box.put(chunk); memory.current = memory.current.filter(c => c.id !== chunk.id); }
      setUnsaved(memory.current.length > 0);
      let queue = await box.list(queueKey());
      while (queue.length) {
        const chunk = queue[0];
        const response = await optionsRef.current.upload(chunk, current.id);
        if (!current.sent.some(c => c.id === chunk.id)) current.sent.push({ id: chunk.id, index: chunk.order });
        save(); // Persist acknowledgement before removing the recoverable blob.
        await box.remove(chunk.id);
        if (response.text) setTranscript(value => value ? value + '\n' + response.text : response.text);
        queue = await box.list(queueKey());
      }
    })();
    try { await uploading.current; } finally { uploading.current = null; }
  }

  async function pause() {
    want.current = false; clearTimeout(segmentTimer.current); clearInterval(analyserTimer.current);
    const rec = recorder.current;
    if (rec?.state === 'recording') rec.stop();
    try { await stopWait.current; await writes.current; }
    finally {
      media.current?.getTracks().forEach(t => t.stop()); media.current = null;
      if (context.current?.state !== 'closed') await context.current?.close(); context.current = null;
      setListening(false);
    }
  }

  async function complete() {
    if (completing.current) return false;
    completing.current = true; setProcessing(true); setError('');
    try {
      await pause(); await pump();
      const current = draft.current;
      if (!current) return true;
      if (memory.current.length || (await (await recordingOutbox()).list(queueKey())).length) throw new Error('还有片段待上传，已保留供稍后重试');
      await optionsRef.current.submit({ client_id: current.id, audio_ids: [...current.sent].sort((a, b) => a.index - b.index).map(c => c.id) });
      draft.current = null; save(); setTranscript(''); return true;
    } catch (cause) { setError((cause as Error).message); return false; }
    finally { completing.current = false; setProcessing(false); }
  }

  async function start() {
    if (want.current || starting.current || completing.current) return;
    starting.current = true; setError('');
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('当前环境不能使用麦克风；请在本机或 HTTPS 中打开，也可以用文字聊天');
      const box = await recordingOutbox();
      const input = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      if (!active.current) { input.getTracks().forEach(t => t.stop()); return; }
      media.current = input;
      if (!draft.current) { draft.current = { id: requestId(), next: 0, sent: [] }; save(); setTranscript(''); }
      want.current = true;
      const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(value => MediaRecorder.isTypeSupported(value));
      const rec = new MediaRecorder(input, mime ? { mimeType: mime } : undefined); recorder.current = rec;
      let stopped: () => void = () => {};
      const begin = () => { stopWait.current = new Promise<void>(resolve => { stopped = resolve; }); rec.start(); segmentTimer.current = window.setTimeout(() => { if (want.current && rec.state === 'recording') rec.stop(); }, 10000); };
      rec.ondataavailable = event => {
        if (!event.data.size || !draft.current) return;
        const chunk = { id: requestId(), sid: queueKey(), order: draft.current.next++, blob: event.data, createdAt: Date.now() };
        save(); memory.current.push(chunk); setUnsaved(true);
        writes.current = writes.current.catch(() => undefined).then(async () => { await box.put(chunk); memory.current = memory.current.filter(c => c.id !== chunk.id); setUnsaved(memory.current.length > 0); });
        void writes.current.then(pump).catch(cause => setError(memory.current.length ? '录音尚未保存在本机，请保持页面打开并重试' : '原音已保留，转写暂未完成；可以暂停离开，稍后重试：' + cause.message));
      };
      rec.onstop = () => { stopped(); if (want.current && input.active) begin(); else setListening(false); };
      rec.onerror = () => { want.current = false; void pause().catch(() => {}); setError('麦克风被中断，已保存片段仍然保留，请重试'); };
      input.getTracks().forEach(t => t.addEventListener('ended', () => { if (want.current) { want.current = false; void pause().catch(() => {}); setError('麦克风已断开，可以重试或使用文字'); } }));
      begin(); setListening(true);
      // A generous pause ends a turn, only after sustained voice was detected.
      // No voice means no automatic question; silent audio cannot become a story.
      const audio = new AudioContext(); context.current = audio;
      await audio.resume();
      const analyser = audio.createAnalyser(); analyser.fftSize = 1024;
      audio.createMediaStreamSource(input).connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      let lastVoice = Date.now(), voiceMs = 0;
      analyserTimer.current = window.setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, v) => sum + v * v, 0) / samples.length);
        if (rms > .018) { lastVoice = Date.now(); voiceMs += 120; }
        if (want.current && voiceMs >= 360 && Date.now() - lastVoice >= (optionsRef.current.silenceMs || 5000)) { want.current = false; void complete(); }
      }, 120);
    } catch (cause) { want.current = false; media.current?.getTracks().forEach(t => t.stop()); setListening(false); setError((cause as Error).message); }
    finally { starting.current = false; }
  }
  return { listening, processing, unsaved, pending, error, transcript, start, pause, complete, setError };
}
