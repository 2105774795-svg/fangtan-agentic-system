import { useEffect, useRef, useState } from 'react';
import { recordingOutbox, type AudioChunk } from '../recordingOutbox';
import { requestId } from '../requestId';
import { createVoiceDetector, type VoiceDetector } from './voiceDetector';
import { retryConversation } from './retry';

type Draft = { id: string; next: number; sent: { id: string; index: number }[] };
type Options = {
  sessionId: string;
  silenceMs?: number;
  completedTurnIds: string[];
  upload: (chunk: AudioChunk, turnId: string) => Promise<{ text: string }>;
  submit: (body: { client_id: string; audio_ids: string[] }) => Promise<void>;
};

/** 设备属于整场会话；10 秒存储分片与对话轮次相互独立。 */
export function useConversationAudio(options: Options) {
  const optionsRef = useRef(options); optionsRef.current = options;
  const key = `conversation-pending-${options.sessionId}`;
  const [listening, setListening] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [unsaved, setUnsaved] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [transcript, setTranscript] = useState('');
  const [countdown, setCountdown] = useState<number | null>(null);
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [retryAttempt, setRetryAttempt] = useState(0);
  const draft = useRef<Draft | null>(null);
  const media = useRef<MediaStream | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const context = useRef<AudioContext | null>(null);
  const detector = useRef<VoiceDetector | null>(null);
  const source = useRef<MediaStreamAudioSourceNode | null>(null);
  const probability = useRef({ value: 0, at: 0 });
  const hadSpeech = useRef(false);
  const want = useRef(false);
  const epoch = useRef(0);
  const segmentTimer = useRef<number | undefined>(undefined);
  const analyserTimer = useRef<number | undefined>(undefined);
  const stopWait = useRef<Promise<void>>(Promise.resolve());
  const writes = useRef<Promise<unknown>>(Promise.resolve());
  const memory = useRef<AudioChunk[]>([]);
  const uploading = useRef<Promise<void> | null>(null);
  const completing = useRef<Promise<boolean> | null>(null);
  const starting = useRef<Promise<boolean> | null>(null);
  const preparing = useRef<Promise<boolean> | null>(null);
  const active = useRef(true);
  const queueKey = (current: Draft) => `conversation:${optionsRef.current.sessionId}:${current.id}`;
  const save = () => { if (draft.current) localStorage.setItem(key, JSON.stringify(draft.current)); else localStorage.removeItem(key); if (active.current) setPending(!!draft.current); };
  const ensureDraft = () => {
    if (!draft.current) { draft.current = { id: requestId(), next: 0, sent: [] }; save(); setTranscript(''); }
    return draft.current;
  };

  useEffect(() => {
    active.current = true;
    try {
      const stored = JSON.parse(localStorage.getItem(key) || 'null') as Draft | null;
      draft.current = null; setPending(false);
      if (stored?.id && Array.isArray(stored.sent)) {
        if (optionsRef.current.completedTurnIds.includes(stored.id)) localStorage.removeItem(key);
        else { draft.current = stored; setPending(true); }
      }
    } catch { setError('本机录音索引无法恢复，已保存的原音仍可在档案中查看'); }
    const prevent = (event: BeforeUnloadEvent) => { if (want.current || memory.current.length) { event.preventDefault(); event.returnValue = ''; } };
    addEventListener('beforeunload', prevent);
    return () => {
      active.current = false; epoch.current++; want.current = false;
      clearTimeout(segmentTimer.current); clearInterval(analyserTimer.current);
      if (recorder.current?.state === 'recording') recorder.current.stop();
      void release(); removeEventListener('beforeunload', prevent);
    };
  }, [key]);

  async function release() {
    const oldDetector = detector.current; detector.current = null;
    const oldContext = context.current; context.current = null;
    source.current?.disconnect(); source.current = null;
    media.current?.getTracks().forEach(t => t.stop()); media.current = null;
    try { await oldDetector?.destroy(); } catch { /* 设备已断开也必须释放上下文。 */ }
    if (oldContext && oldContext.state !== 'closed') {
      try { await oldContext.close(); } catch { /* 轨道已释放，不让设备关闭错误阻塞暂停。 */ }
    }
  }

  /** 首次用户点击时初始化设备与音频上下文，后续换轮复用。 */
  function prepare(): Promise<boolean> {
    const token = epoch.current;
    if (preparing.current) return preparing.current.then(() => {
      if (!active.current || epoch.current !== token) return false;
      return prepare();
    });
    const task = (async () => {
      try {
        if (media.current?.active && context.current?.state !== 'closed') {
          await context.current?.resume(); return true;
        }
        if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) throw new Error('当前环境不能使用麦克风；请在本机或 HTTPS 中打开，也可以用文字聊天');
        const audio = new AudioContext(); context.current = audio;
        await audio.resume();
        const input = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
        if (!active.current || epoch.current !== token) { input.getTracks().forEach(t => t.stop()); return false; }
        media.current = input;
        input.getTracks().forEach(t => t.addEventListener('ended', () => {
          if (media.current !== input) return;
          void pause().catch(cause => setError(cause.message)); setError('麦克风已断开，请重新连接后继续自动对谈，或改用文字');
        }));
        try {
          let expired = false, timer: number | undefined;
          const initialization = createVoiceDetector(audio, input, value => { probability.current = { value, at: Date.now() }; }).then(value => {
            if (expired) { void value.destroy().catch(() => {}); throw new Error('人声检测初始化超时'); }
            return value;
          });
          let value: VoiceDetector;
          try {
            value = await Promise.race([initialization, new Promise<never>((_, reject) => {
              timer = window.setTimeout(() => { expired = true; reject(new Error('人声检测初始化超时')); }, 12000);
            })]);
          } finally { clearTimeout(timer); }
          if (!active.current || epoch.current !== token) { await value.destroy(); return false; }
          detector.current = value; setNotice('');
        } catch { if (active.current && epoch.current === token) setNotice('人声检测暂不可用，已切换到音量检测；建议在安静环境下讲述。'); }
        return active.current && epoch.current === token;
      } catch (cause) {
        if (active.current && epoch.current === token) setError((cause as Error).message);
        await release(); return false;
      }
    })();
    preparing.current = task;
    void task.finally(() => { if (preparing.current === task) preparing.current = null; });
    return task;
  }

  async function pump(): Promise<void> {
    if (uploading.current) { await uploading.current; return pump(); }
    const current = draft.current;
    if (!current) return;
    const sid = queueKey(current);
    uploading.current = (async () => {
      const box = await recordingOutbox();
      for (const chunk of [...memory.current]) { await box.put(chunk); memory.current = memory.current.filter(c => c.id !== chunk.id); }
      setUnsaved(memory.current.length > 0);
      let queue = await box.list(sid);
      while (queue.length) {
        const chunk = queue[0];
        const response = await retryConversation(() => optionsRef.current.upload(chunk, current.id), setRetryAttempt, () => !active.current);
        setRetryAttempt(0);
        if (!current.sent.some(c => c.id === chunk.id)) current.sent.push({ id: chunk.id, index: chunk.order });
        save();
        await box.remove(chunk.id);
        if (response.text) setTranscript(value => value ? value + '\n' + response.text : response.text);
        queue = await box.list(sid);
      }
    })();
    try { await uploading.current; } finally { uploading.current = null; setRetryAttempt(0); }
  }

  async function stopCapture() {
    want.current = false; clearTimeout(segmentTimer.current); clearInterval(analyserTimer.current);
    source.current?.disconnect(); source.current = null;
    setCountdown(null); setUserSpeaking(false); setListening(false);
    const rec = recorder.current;
    if (rec?.state === 'recording') rec.stop();
    await stopWait.current;
    await writes.current;
    if (memory.current.length) throw new Error('还有录音尚未保存到本机，请留在这里重试');
    await detector.current?.pause();
  }

  async function pause() {
    epoch.current++; want.current = false;
    try {
      await stopCapture();
    } finally { await release(); }
  }

  function complete(): Promise<boolean> {
    if (completing.current) return completing.current;
    setProcessing(true); setError('');
    const task = (async () => {
      try {
        await stopCapture(); await pump();
        const current = draft.current;
        if (!current) return true;
        if (memory.current.length || (await (await recordingOutbox()).list(queueKey(current))).length) throw new Error('还有片段待上传，已保留供稍后重试');
        if (current.sent.length) await retryConversation(() => optionsRef.current.submit({ client_id: current.id, audio_ids: [...current.sent].sort((a, b) => a.index - b.index).map(c => c.id) }), setRetryAttempt, () => !active.current);
        draft.current = null; save(); setTranscript(''); hadSpeech.current = false; return true;
      } catch (cause) { setError((cause as Error).message); return false; }
      finally { completing.current = null; setProcessing(false); setRetryAttempt(0); }
    })();
    completing.current = task;
    return task;
  }

  /** 忙时等待完成；暂停会使所有等待中的收音请求失效。 */
  function start(): Promise<boolean> {
    if (want.current) return Promise.resolve(true);
    const token = epoch.current;
    if (starting.current) return starting.current.then(() => {
      if (!active.current || epoch.current !== token) return false;
      return start();
    });
    const valid = () => active.current && epoch.current === token;
    const task = (async () => {
      try {
        if (completing.current && !await completing.current) return false;
        if (!valid()) return false;
        setError('');
        if (!await prepare() || !valid()) return false;
        const box = await recordingOutbox();
        if (!valid()) return false;
        const input = media.current!;
        const audio = context.current!;
        probability.current = { value: 0, at: 0 };
        await detector.current?.start();
        if (!valid()) return false;
        want.current = true; hadSpeech.current = !!draft.current;
        const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(value => MediaRecorder.isTypeSupported(value));
        const rec = new MediaRecorder(input, mime ? { mimeType: mime } : undefined); recorder.current = rec;
        let preRoll: Blob | null = null;
        let stopped: () => void = () => {};
        const begin = () => {
          stopWait.current = new Promise<void>(resolve => { stopped = resolve; });
          rec.start(); segmentTimer.current = window.setTimeout(() => { if (want.current && rec.state === 'recording') rec.stop(); }, hadSpeech.current ? 10000 : 1000);
        };
        const store = (blob: Blob) => {
          const current = ensureDraft();
          const chunk = { id: requestId(), sid: queueKey(current), order: current.next++, blob, createdAt: Date.now() };
          save(); memory.current.push(chunk); setUnsaved(true);
          writes.current = writes.current.catch(() => undefined).then(async () => { await box.put(chunk); memory.current = memory.current.filter(c => c.id !== chunk.id); setUnsaved(memory.current.length > 0); });
          void writes.current.then(pump).catch(cause => setError(memory.current.length ? '录音尚未保存在本机，请保持页面打开并重试' : '原音已保留，转写暂未完成：' + cause.message));
        };
        rec.ondataavailable = event => {
          if (!event.data.size) return;
          // 仅在内存保留最近一秒前导音；纯静音不上传，发言跨分片边界也不丢开头。
          if (!hadSpeech.current) { preRoll = event.data; return; }
          if (preRoll) { store(preRoll); preRoll = null; }
          store(event.data);
        };
        rec.onstop = () => { stopped(); if (want.current && input.active && valid()) begin(); };
        rec.onerror = () => { void pause().catch(cause => setError(cause.message)); setError('麦克风被中断，已保存片段仍然保留，请重试'); };
        begin(); setListening(true); setCountdown(null);
        const analyser = audio.createAnalyser(); analyser.fftSize = 1024;
        source.current = audio.createMediaStreamSource(input); source.current.connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        let lastVoice = Date.now(), consecutiveMs = 0, floor = .003, positive = false;
        const silenceMs = optionsRef.current.silenceMs || 5000;
        analyserTimer.current = window.setInterval(() => {
          if (!want.current) return;
          const now = Date.now();
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(samples.reduce((sum, v) => sum + v * v, 0) / samples.length);
          const modelReady = detector.current && now - probability.current.at < 2000;
          if (modelReady) positive = probability.current.value >= (positive ? .35 : .6);
          else {
            const threshold = Math.max(.012, Math.min(.055, floor * 3));
            positive = rms > (positive ? threshold * .75 : threshold);
            if (!positive) floor = floor * .98 + rms * .02;
          }
          if (positive) {
            lastVoice = now; consecutiveMs += 100; setCountdown(null);
            if (consecutiveMs >= 300) { hadSpeech.current = true; ensureDraft(); setUserSpeaking(true); }
          } else {
            consecutiveMs = 0; setUserSpeaking(false);
            if (hadSpeech.current) {
              const remaining = silenceMs - (now - lastVoice);
              setCountdown(Math.max(0, Math.ceil(remaining / 1000)));
              // 短确认窗保留尾字；计时内重新发言仍属于当前轮。
              if (remaining <= -300) void complete();
            }
          }
        }, 100);
        return true;
      } catch (cause) {
        if (valid()) {
          setError((cause as Error).message);
          try { await stopCapture(); } catch (saveError) { setError((saveError as Error).message); }
          finally { await release(); }
        }
        return false;
      }
    })();
    starting.current = task;
    void task.finally(() => { if (starting.current === task) starting.current = null; });
    return task;
  }
  return { listening, processing, unsaved, pending, error, notice, transcript, countdown, userSpeaking, retryAttempt, prepare, start, pause, complete, setError };
}
