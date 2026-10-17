import { useEffect, useRef, useState } from 'react';
import { recordingOutbox, type AudioChunk } from './recordingOutbox';

/** Shared durable recorder. Each stop/start produces a separately decodable file. */
export function useAnswerRecorder(answerId: string, sid: string, knownChunks: string[]) {
  const [recording, setRecording] = useState(false);
  const [pending, setPending] = useState(0);
  const [unsaved, setUnsaved] = useState(false);
  const [error, setError] = useState('');
  const [elapsed, setElapsed] = useState(0);
  const [uploading, setUploading] = useState(false);
  const media = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const running = useRef(false);
  const starting = useRef(false);
  const mounted = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const save = useRef<Promise<void>>(Promise.resolve());
  const stopped = useRef<Promise<void>>(Promise.resolve());
  const memory = useRef<AudioChunk[]>([]);
  const pumping = useRef<Promise<void> | null>(null);
  const sent = useRef(new Set(knownChunks));
  knownChunks.forEach(id => sent.current.add(id));

  async function persist() {
    const box = await recordingOutbox();
    for (const chunk of [...memory.current]) {
      await box.put(chunk);
      memory.current = memory.current.filter(c => c.id !== chunk.id);
    }
    if (mounted.current) setUnsaved(memory.current.length > 0);
  }
  async function pump(): Promise<void> {
    if (pumping.current) return pumping.current;
    pumping.current = (async () => {
      if (mounted.current) setUploading(true);
      const box = await recordingOutbox();
      await persist();
      let queue = await box.list(sid);
      if (mounted.current) setPending(queue.length);
      while (queue.length) {
        const chunk = queue[0];
        const form = new FormData();
        form.append('audio', chunk.blob, chunk.blob.type.includes('mp4') ? 'answer.mp4' : 'answer.webm');
        form.append('chunk_id', chunk.id);
        const response = await fetch(`/api/interview-prep/answers/${answerId}/audio`, { method: 'POST', body: form, signal: AbortSignal.timeout(195000) });
        if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(typeof body.detail === 'string' ? body.detail : '上传或转写未完成，请重试'); }
        sent.current.add(chunk.id);
        await box.remove(chunk.id);
        queue = await box.list(sid);
        if (mounted.current) setPending(queue.length);
      }
    })();
    try { await pumping.current; }
    finally { pumping.current = null; if (mounted.current) setUploading(false); }
  }
  async function stop() {
    running.current = false; clearTimeout(timer.current);
    const active = recorder.current;
    if (active && active.state !== 'inactive') active.stop();
    await stopped.current;
    await save.current.catch(() => undefined);
    media.current?.getTracks().forEach(t => t.stop()); media.current = null;
    if (mounted.current) setRecording(false);
    await persist();
  }
  async function start() {
    if (starting.current || running.current) return;
    starting.current = true;
    setError('');
    try {
      window.speechSynthesis?.cancel();
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('当前浏览器不支持录音，请使用 Edge 或 Chrome');
      await persist();
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      if (!mounted.current) { stream.getTracks().forEach(t => t.stop()); return; }
      media.current = stream;
      const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(m => MediaRecorder.isTypeSupported(m));
      const active = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recorder.current = active; running.current = true;
      let resolveStop: () => void = () => {};
      const segment = () => {
        stopped.current = new Promise<void>(resolve => { resolveStop = resolve; });
        active.start();
        timer.current = setTimeout(() => { if (running.current && active.state === 'recording') active.stop(); }, 10000);
      };
      active.ondataavailable = event => {
        if (!event.data.size) return;
        const chunk: AudioChunk = { id: crypto.randomUUID(), sid, blob: event.data, createdAt: Date.now(), order: Date.now() };
        memory.current.push(chunk);
        if (mounted.current) { setUnsaved(true); setPending(n => n + 1); }
        save.current = save.current.catch(() => undefined).then(persist);
        void save.current.then(pump).catch(cause => { if (mounted.current) setError(`录音已保留，请重试：${cause.message}`); });
      };
      active.onstop = () => {
        resolveStop();
        if (running.current && stream.getTracks().every(t => t.readyState === 'live')) segment();
        else if (mounted.current) setRecording(false);
      };
      active.onerror = () => { void stop().catch(() => undefined); setError('浏览器中断录音，请确认麦克风后继续'); };
      stream.getTracks().forEach(t => t.addEventListener('ended', () => { if (running.current) { void stop().catch(() => undefined); setError('麦克风被系统中断，已保存的片段仍可恢复'); } }));
      segment(); setRecording(true);
    } catch (cause) { media.current?.getTracks().forEach(t => t.stop()); setError((cause as Error).message); }
    finally { starting.current = false; }
  }
  async function flush() {
    setError(''); await stop(); await pump(); await pump();
    if ((await (await recordingOutbox()).list(sid)).length || memory.current.length) throw new Error('还有未上传的录音，请重试');
    return [...sent.current];
  }
  async function discard() {
    await stop();
    await pumping.current?.catch(() => undefined);
    const box = await recordingOutbox();
    for (const chunk of await box.list(sid)) await box.remove(chunk.id);
  }
  useEffect(() => {
    mounted.current = true;
    void pump().catch(cause => { if (mounted.current) setError(cause.message); });
    return () => {
      mounted.current = false; running.current = false; clearTimeout(timer.current);
      if (recorder.current?.state === 'recording') recorder.current.stop();
      media.current?.getTracks().forEach(t => t.stop());
    };
  }, [answerId]);
  useEffect(() => { if (!recording) return; const id = setInterval(() => setElapsed(n => n + 1), 1000); return () => clearInterval(id); }, [recording]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (recording || unsaved) event.preventDefault(); };
    addEventListener('beforeunload', warn); return () => removeEventListener('beforeunload', warn);
  }, [recording, unsaved]);
  return { recording, pending, unsaved, uploading, error, elapsed, start, stop, flush, discard, retry: pump };
}
