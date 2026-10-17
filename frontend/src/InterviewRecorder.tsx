import { useEffect, useRef, useState } from 'react';
import { Check, CircleNotch, FileArrowUp, Microphone, Pause, Play, Plus, WarningCircle } from '@phosphor-icons/react';
import { legacyApi, type Artifact, type Project } from './types';
import { recordingOutbox, type AudioChunk } from './recordingOutbox';
import { requestId } from './requestId';
import { recordingUnavailableReason, recordingFailureMessage } from './recordingSupport';

type Suggestion = { id: number; kind: string; status: string; payload: string };
function suggestionData(item: Suggestion) { try { return JSON.parse(item.payload) as { scripts?: { text: string; why?: string }[]; consent?: { text: string }[]; question?: string }; } catch { return {}; } }
type Session = { id: string; subject: string; status: string; has_audio?: boolean; recorded_ms?: number; recording_state?: string; outline: string[]; report_status?: string; utterances?: { id: number; text: string }[]; suggestions?: Suggestion[]; coverage?: { dims: { item: string; manual_asked: boolean; status_label: string }[] } };
type ImportJob = { job_id: string; status: string; session_id?: string; stage?: string; progress?: number; error?: string };
type Props = { project: Project; outlineArtifact?: Artifact; busy: boolean; command: (body: Record<string, unknown>) => Promise<Project | undefined>; onBusy: (busy: boolean) => void; onPaste: () => void; onOpen: (id: string) => void; onReturn: () => void };
const request = <T,>(url: string, body: unknown) => legacyApi<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

export function InterviewRecorder({ project, outlineArtifact, busy, command, onBusy, onPaste, onOpen, onReturn }: Props) {
  const questions = project.outline_questions || [];
  const [phase, setPhase] = useState<'outline' | 'recording' | 'import'>(project.active_session_id ? 'recording' : 'outline');
  const [selected, setSelected] = useState<string[]>(questions);
  const [sid, setSid] = useState(project.active_session_id || '');
  const [session, setSession] = useState<Session | null>(null);
  const [interviewTitle, setInterviewTitle] = useState('');
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [recording, setRecording] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [consent, setConsent] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [pending, setPending] = useState(0);
  const [unsavedAudio, setUnsavedAudio] = useState(false);
  const mounted = useRef(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('准备好后开始现场采集');
  const [importId, setImportId] = useState(() => localStorage.getItem(`research-import-${project.id}`) || '');
  const [importJob, setImportJob] = useState<ImportJob | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const segmentTimer = useRef<number | undefined>(undefined);
  const wantsRecording = useRef(false);
  const persistWait = useRef<Promise<unknown>>(Promise.resolve());
  const stopWait = useRef<Promise<void>>(Promise.resolve());
  const memoryChunks = useRef<AudioChunk[]>([]);
  const pumping = useRef<Promise<void> | null>(null);
  const activeSid = useRef(sid); activeSid.current = sid;
  const isRecording = useRef(recording); isRecording.current = recording;
  const input = useRef<HTMLInputElement>(null);
  const creation = useRef(requestId());
  const currentProject = useRef(project);
  currentProject.current = project;
  const syncingReport = useRef(false);
  const commandRef = useRef(command); commandRef.current = command;

  useEffect(() => { setSelected(project.outline_questions || []); }, [outlineArtifact?.id, outlineArtifact?.version]);
  useEffect(() => { onBusy(recording || unsavedAudio); return () => onBusy(false); }, [recording, unsavedAudio, onBusy]);
  useEffect(() => { if (!recording) return; const timer = window.setInterval(() => setElapsed(value => value + 1), 1000); return () => clearInterval(timer); }, [recording]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; wantsRecording.current = false; clearTimeout(segmentTimer.current); if (recorder.current?.state === 'recording') recorder.current.stop(); stream.current?.getTracks().forEach(track => track.stop()); }; }, []);

  async function pump(sessionId: string): Promise<void> {
    if (pumping.current) { await pumping.current.catch(() => undefined); return pump(sessionId); }
    pumping.current = (async () => {
      const outbox = await recordingOutbox();
      for (const chunk of [...memoryChunks.current].filter(item => item.sid === sessionId)) { await outbox.put(chunk); memoryChunks.current = memoryChunks.current.filter(item => item.id !== chunk.id); }
      setUnsavedAudio(memoryChunks.current.length > 0);
      let queue = await outbox.list(sessionId); if (activeSid.current === sessionId) setPending(queue.length);
      while (queue.length) {
        const chunk = queue[0];
        const form = new FormData(); form.append('audio', chunk.blob, chunk.blob.type.includes('mp4') ? 'chunk.mp4' : 'chunk.webm'); form.append('chunk_id', chunk.id);
        const result = await legacyApi<{ transcription_status?: string }>(`/api/sessions/${sessionId}/audio`, { method: 'POST', body: form, signal: AbortSignal.timeout(195000) });
        if (result.transcription_status === 'no_speech' && activeSid.current === sessionId) setStatus('本段未识别到有效语音，录音已保存；可以继续录音或返回工作台');
        await outbox.remove(chunk.id); queue = await outbox.list(sessionId); if (activeSid.current === sessionId) setPending(queue.length);
      }
    })();
    try { await pumping.current; } finally { pumping.current = null; }
  }
  useEffect(() => {
    if (!sid) return;
    let disposed = false;
    const refresh = async () => {
      try { const data = await legacyApi<Session>(`/api/sessions/${sid}`); if (!disposed) {
          setSession(data);
          if (!isRecording.current) setElapsed(Math.floor((data.recorded_ms || 0) / 1000));
          const state = currentProject.current;
          if (data.report_status === 'ready' && !syncingReport.current && state.artifacts.some(a => a.kind === 'material' && a.source_session === sid) && !state.artifacts.some(a => a.kind === 'session_report' && a.source_session === sid) && !['running','queued','retry'].includes(state.job?.status || '')) {
            syncingReport.current = true;
            try { await commandRef.current({ action: 'sync_session', session_id: sid }); } finally { syncingReport.current = false; }
          }
        } }
      catch (cause) { if (!disposed) setError((cause as Error).message); }
    };
    void refresh();
    void pump(sid).catch(cause => { if (!disposed) setError('录音片段已保留，请重试上传：' + cause.message); });
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => { disposed = true; clearInterval(timer); };
  }, [sid]);

  async function createSession() {
    setLocalBusy(true); setError('');
    try {
      const result = await commandRef.current({ action: 'start_interview', title: interviewTitle, selected_questions: selected, outline_version: outlineArtifact?.version, request_id: creation.current });
      if (result?.active_session_id) { setSession(null); setSid(result.active_session_id); setPhase('recording'); setConsent(false); setElapsed(0); setConfirmEnd(false); creation.current = requestId(); }
    } finally { setLocalBusy(false); }
  }
  function newInterview() {
    setPhase('outline'); setInterviewTitle(''); setSelected(questions); setError(''); setConfirmEnd(false);
    creation.current = requestId();
    setStatus('新建访谈不会覆盖已有录音；暂停中的访谈仍可在列表中继续。');
  }
  async function syncAll() {
    setLocalBusy(true); setError('');
    try {
      let latest = currentProject.current;
      const send = commandRef.current;
      for (const item of latest.interviews || []) {
        if (item.status !== 'done' || !item.utterance_count || item.synced) continue;
        const result = await send({ action: 'sync_session', session_id: item.id, revision: latest.revision });
        if (!result) return;
        latest = result;
      }
      setStatus('已将各场访谈的原始逐字稿同步到项目，可统一分析并生成整合报告。');
    } finally { setLocalBusy(false); }
  }
  async function selectInterview(id: string) {
    setLocalBusy(true); setError('');
    try {
      const result = await commandRef.current({ action: 'select_interview', session_id: id });
      if (result) { if (id !== sid) setSession(null); setSid(id); setPhase('recording'); setConsent(false); setConfirmEnd(false); }
    } finally { setLocalBusy(false); }
  }
  async function stop() {
    wantsRecording.current = false; clearTimeout(segmentTimer.current);
    const active = recorder.current; recorder.current = null;
    if (active && active.state !== 'inactive') active.stop();
    await stopWait.current; await persistWait.current.catch(cause => setError('本地分片保存未完成，请重试上传并保持页面打开：' + cause.message));
    stream.current?.getTracks().forEach(track => track.stop()); stream.current = null;
    setRecording(false); setStatus('已暂停；录音保留在本机，上传或转写未完成也可以返回工作台');
    if (sid && session?.status === 'live') {
      await request(`/api/sessions/${sid}/capture-state`, { state: 'paused' }).catch(cause => setError('录音已在本机暂停，状态同步失败，可重试：' + cause.message));
      setSession(previous => previous ? { ...previous, recording_state: 'paused' } : previous);
    }
    if (sid) void pump(sid).catch(cause => setError('分片已保留，可重试上传：' + cause.message));
  }
  async function start() {
    if (!sid || !consent || recording || localBusy) return;
    setLocalBusy(true); setError('');
    try {
      const unavailable = recordingUnavailableReason();
      if (unavailable) throw new Error(unavailable);
      const outbox = await recordingOutbox();
      void pump(sid).catch(cause => setError('待处理分片已保留，可稍后重试：' + cause.message));
      const media = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); if (!mounted.current) { media.getTracks().forEach(track => track.stop()); return; }
      stream.current = media;
      const mime = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm'].find(type => MediaRecorder.isTypeSupported(type));
      const active = new MediaRecorder(media, mime ? { mimeType: mime } : undefined); recorder.current = active; wantsRecording.current = true;
      await request(`/api/sessions/${sid}/capture-state`, { state: 'recording' });
      let stopped: () => void = () => {};
      const startSegment = () => { stopWait.current = new Promise<void>(resolve => { stopped = resolve; }); active.start(); segmentTimer.current = window.setTimeout(() => { if (wantsRecording.current && active.state === 'recording') active.stop(); }, 10000); };
      active.ondataavailable = event => {
        if (!event.data.size) return;
        const chunk = { id: requestId(), sid, blob: event.data, createdAt: Date.now(), order: Date.now() };
        memoryChunks.current.push(chunk); setUnsavedAudio(true); setPending(value => value + 1);
        persistWait.current = persistWait.current.catch(() => undefined).then(async () => { await outbox.put(chunk); memoryChunks.current = memoryChunks.current.filter(item => item.id !== chunk.id); setUnsavedAudio(memoryChunks.current.length > 0); });
        void persistWait.current.then(() => pump(sid)).catch(cause => setError(memoryChunks.current.length ? '分片尚未保存到本机，请保持页面打开并重试：' + cause.message : '分片已保留，上传或转写未完成；可返回工作台，稍后重试：' + cause.message));
      };
      active.onstop = () => { stopped(); if (wantsRecording.current && media.active && media.getTracks().every(track => track.readyState === 'live')) startSegment(); else { wantsRecording.current = false; setRecording(false); } };
      active.onerror = () => { void stop(); setError('浏览器中断录音，已保存分片会保留，请重新开始'); };
      media.getTracks().forEach(track => track.addEventListener('ended', () => { if (wantsRecording.current) { void stop(); setError('麦克风被系统中断，请确认后继续录音'); } }));
      startSegment(); setRecording(true); setStatus('录音中 · 每 10 秒保存独立音频并转写');
    } catch (cause) { wantsRecording.current = false; stream.current?.getTracks().forEach(track => track.stop()); setError(recordingFailureMessage(cause)); }
    finally { setLocalBusy(false); }
  }
  async function sync() {
    const result = await commandRef.current({ action: 'sync_session', session_id: sid });
    if (result) setStatus('原始逐字稿与已有纪要已同步到项目资料，可以返回继续分析');
    return result;
  }
  async function finish() {
    setLocalBusy(true); setError('');
    try {
      await stop(); await pump(sid);
      const remaining = await (await recordingOutbox()).list(sid);
      if (remaining.length) throw new Error('还有未上传分片，请重试上传后结束访谈');
      await legacyApi(`/api/sessions/${sid}/finish?background=true`, { method: 'POST' });
      const data = await legacyApi<Session>(`/api/sessions/${sid}`); setSession(data);
      setConfirmEnd(false);
      setStatus(data.utterances?.length ? '访谈已结束，原始逐字稿可同步；纪要在后台生成' : '录音已归档，未识别到有效语音；不会生成空纪要，可以返回工作台或重新选题开一场访谈');
      if (data.utterances?.length) await sync();
    } catch (cause) { setError((cause as Error).message); }
    finally { setLocalBusy(false); }
  }

  useEffect(() => {
    if (!importId) return;
    let disposed = false; let timer: number | undefined;
    const poll = async () => {
      try {
        const job = await legacyApi<ImportJob>(`/api/imports/${importId}`);
        if (disposed) return; setImportJob(job);
        if (['ready', 'failed', 'cancelled'].includes(job.status)) {
          if (job.session_id) {
            await legacyApi(`/api/sessions/${job.session_id}/project`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project_id: currentProject.current.id }) });
            if (disposed) return;
            setSid(job.session_id); setPhase('recording');
            await commandRef.current({ action: 'sync_session', session_id: job.session_id });
          }
          localStorage.removeItem(`research-import-${project.id}`); setImportId('');
          if (job.status !== 'ready') setError(job.session_id ? '原文已保留，纪要未完成，可在访谈页重试生成' : job.error || '材料处理未完成，请重试');
        } else timer = window.setTimeout(() => void poll(), 1500);
      } catch (cause) { if (!disposed) { setError('导入进度连接中断，将自动重连：' + (cause as Error).message); timer = window.setTimeout(() => void poll(), 4000); } }
    };
    void poll(); return () => { disposed = true; clearTimeout(timer); };
  }, [importId, project.id]);
  async function importFile(file: File) {
    if (importId || recording || localBusy) return;
    setLocalBusy(true); setError(''); setPhase('import');
    try {
      const isText = /\.(txt|md|docx|srt|vtt|csv|json|html)$/i.test(file.name);
      if (!/\.(mp3|m4a|wav|aac|ogg|flac|wma|webm|mp4|txt|md|docx|srt|vtt|csv|json|html)$/i.test(file.name)) throw new Error('请选择支持的音频或文字稿格式');
      if (file.size > (isText ? 20 : 200) * 1024 * 1024) throw new Error(isText ? '文字稿上限为 20 MB' : '音频上限为 200 MB');
      const form = new FormData(); form.append('material', file); form.append('subject', project.title); form.append('interviewee_type', project.scenario === 'memoir' ? '消费者' : project.scenario === 'enterprise' ? '企业高管' : '商户'); form.append('industry', '综合/不限'); form.append('focus', project.answers.goal?.join('、') || ''); form.append('location', JSON.stringify(project.location || {}));
      const job = await legacyApi<ImportJob>('/api/imports/analyze', { method: 'POST', body: form });
      localStorage.setItem(`research-import-${project.id}`, job.job_id); setImportJob(job); setImportId(job.job_id);
    } catch (cause) { setError((cause as Error).message); }
    finally { setLocalBusy(false); }
  }
  const blocked = busy || localBusy || recording || unsavedAudio;
  const resume = session?.recording_state === 'paused' || session?.recording_state === 'recording' || !!session?.recorded_ms;
  const captureLabel = session?.status === 'done' ? '本场已结束' : recording ? '正在录音' : session?.recording_state === 'recording' ? '录音已中断 · 可继续' : session?.recording_state === 'paused' ? '已暂停 · 本场未结束' : '等待开始录音';
  return <section className="interview-recorder">
    <div className="capture-manager"><div><strong>本项目访谈</strong><span>{project.interviews?.length || 0} 场 · 每场录音与逐字稿独立保存</span></div><button className="secondary" disabled={blocked} onClick={newInterview}><Plus size={16}/>新建访谈</button></div>
    {!!project.interviews?.length && <div className="capture-history" aria-label="本项目访谈列表">{project.interviews.map(item => <button className={sid === item.id && phase === 'recording' ? 'active' : ''} aria-pressed={sid === item.id && phase === 'recording'} disabled={blocked} key={item.id} onClick={() => void selectInterview(item.id)}><span className="interview-number">{String(item.number).padStart(2, '0')}</span><span><strong>{item.subject}</strong><small>{new Date(item.created_at * 1000).toLocaleDateString('zh-CN')} · {item.recorded_ms > 0 ? `${Math.floor(item.recorded_ms / 1000)} 秒` : item.has_audio ? '时长待确认' : '未录音'} · {item.utterance_count} 段转写</small></span><span className={`capture-status ${item.status === 'done' ? 'ended' : ''}`}>{item.status === 'done' ? '已结束' : item.recording_state === 'paused' ? '已暂停' : item.recording_state === 'recording' ? sid === item.id && recording ? '录音中' : '可恢复' : '待录音'}{item.status === 'done' && item.utterance_count > 0 && !item.synced ? ' · 待同步' : ''}</span></button>)}</div>}
    {project.interviews?.some(item => item.status === 'done' && item.utterance_count > 0 && !item.synced) && <p className="capture-sync-note">有已结束访谈尚未纳入工作台。<button className="text-button" disabled={blocked} onClick={() => void syncAll()}>同步已结束访谈</button></p>}
    <div className="recorder-kicker">{phase === 'outline' ? '03 · 调研提纲' : '04 · 现场采集'}</div>
    <div className="recorder-heading"><div><h2>{phase === 'outline' ? '确认本场要问的问题' : phase === 'import' ? '导入已有材料' : session?.subject || project.title}</h2><p>{phase === 'outline' ? '问题直接来自已生成的 Markdown 提纲，勾选后带到录音现场。' : status}</p></div>{phase === 'outline' && <span className="outline-count">已选 {selected.length} / {questions.length}</span>}</div>
    <nav className="capture-tabs" aria-label="采集方式"><button disabled={blocked} aria-pressed={phase === 'outline'} onClick={() => setPhase('outline')}>提纲勾选</button><button disabled={blocked || !sid} aria-pressed={phase === 'recording'} onClick={() => setPhase('recording')}>现场录音</button><button disabled={blocked} aria-pressed={phase === 'import'} onClick={() => setPhase('import')}>导入已有材料</button><button disabled={blocked} onClick={onPaste}>粘贴文字材料</button></nav>
    {error && <p className="recorder-error" role="alert"><WarningCircle size={16}/>{error}</p>}
    {phase === 'outline' ? <><label className="interview-title-input">访谈名称或受访对象<input aria-label="访谈名称或受访对象" value={interviewTitle} maxLength={80} placeholder={`例如：第${(project.interviews?.length || 0) + 1}场 · 张店主 / 上午补访`} onChange={event => setInterviewTitle(event.target.value)}/><small>用于区分各场资料；不填写时自动按场次命名。场次数不代表独立受访人数。</small></label><div className="outline-toolbar"><button className="text-button" disabled={blocked} onClick={() => setSelected(questions)}>全选</button><button className="text-button" disabled={blocked} onClick={() => setSelected([])}>清空</button>{outlineArtifact && <button className="secondary" disabled={blocked} onClick={() => onOpen(outlineArtifact.id)}>编辑 Markdown 提纲</button>}</div><div className="outline-checklist">{questions.map(question => <label className={`outline-check ${selected.includes(question) ? 'checked' : ''}`} key={question}><input type="checkbox" disabled={blocked} checked={selected.includes(question)} onChange={() => setSelected(current => current.includes(question) ? current.filter(q => q !== question) : [...current, question])}/><span><Check size={15}/></span><strong>{question}</strong></label>)}</div>{!questions.length && <p className="panel-note">当前提纲没有可识别的问题，请打开 Markdown 提纲，每个问题单独一行，以问号结尾。这里不会补入示例问题。</p>}<div className="recorder-next"><span>只将本次勾选的问题带入现场；不会自动开启麦克风。</span><button className="primary" disabled={blocked || !selected.length || !!outlineArtifact?.stale} onClick={() => void createSession()}>确认提纲，进入现场录音<Play size={16}/></button></div></>
    : phase === 'import' ? <div className="capture-import"><h3>提交已有录音或文字稿</h3><p>沿用当前项目主题，完成转写和纪要后同步到工作台。</p><label className="capture-drop" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); const file = event.dataTransfer.files[0]; if (file) void importFile(file); }}><FileArrowUp size={32}/><strong>选择文件，或拖到这里</strong><span>音频：MP3 / M4A / WAV / WEBM 等，上限 200 MB</span><span>文字：TXT / MD / DOCX / SRT / VTT / CSV / JSON / HTML，上限 20 MB</span><input ref={input} type="file" aria-label="提交已有录音或文字稿" disabled={blocked || !!importId} accept=".mp3,.m4a,.wav,.aac,.ogg,.flac,.wma,.webm,.mp4,.txt,.md,.docx,.srt,.vtt,.csv,.json,.html" onChange={event => { const file = event.target.files?.[0]; if (file) void importFile(file); event.target.value = ''; }}/></label>{importJob && <p role="status">{importJob.stage || '正在处理'} · {importJob.progress || 0}% {importId && <CircleNotch className="spin"/>}</p>}</div>
    : !session ? <p role="status" className="recorder-note">正在加载本场访谈…</p> : <>{session?.status === 'live' && session.suggestions?.filter(item => item.kind === 'playbook' && item.status === 'new').map(item => { const data = suggestionData(item); return <section className="recorder-opening" key={item.id}><h3>开场与录音授权提示</h3>{data.scripts?.slice(0, 2).map(script => <p key={script.text}>{script.text}<small>{script.why}</small></p>)}{data.consent?.map(script => <p key={script.text}>{script.text}</p>)}<button className="secondary" onClick={() => void legacyApi(`/api/suggestions/${item.id}/dismissed`, { method: 'POST' }).then(() => legacyApi<Session>(`/api/sessions/${sid}`)).then(setSession).catch(cause => setError(cause.message))}>已了解，继续访谈</button></section>; })}<div className="recording-state-label" role="status">{captureLabel}<small>{session?.status === 'done' ? '本场已归档；补充访谈请新建一场。' : '暂停或离开不会结束访谈；只有确认结束才会归档。'}</small></div><div className="recording-stage"><div className={`record-orb ${recording ? 'active' : ''}`}>{recording ? <Pause size={31}/> : <Microphone size={31}/>}</div><time>{String(Math.floor(elapsed / 60)).padStart(2, '0')}:{String(elapsed % 60).padStart(2, '0')}</time>{session?.status === 'live' && <><label className="record-consent"><input type="checkbox" checked={consent} disabled={recording} onChange={event => setConsent(event.target.checked)}/>已向受访者说明并取得录音及云端转写同意</label>{recording ? <button className="record-stop" onClick={() => void stop()}>暂停录音</button> : <button className="record-start" disabled={blocked || !consent} onClick={() => void start()}><Microphone size={18}/>{resume ? '继续本场录音' : '开始录音'}</button>}</>}<p>分片先保存在本机，再按顺序上传转写；暂停后可以离开，返回时继续处理。</p>{pending > 0 && <p role="status">{pending} 个分片待上传或转写 <button onClick={() => { setError(''); void pump(sid).catch(cause => setError(cause.message)); }}>重试上传 / 转写</button></p>}{session?.has_audio && <audio controls src={`/api/sessions/${sid}/recording`}/>}</div><div className="recording-outline"><div><strong>本场提纲 · 已问 {session?.coverage?.dims.filter(item => item.manual_asked).length || 0} / {session?.outline.length || 0}</strong><span>{session?.outline.length || 0} 个问题</span></div>{session?.outline.map((question, index) => <p key={question}><span>{index + 1}</span>{question}<button className="asked-question" disabled={session.coverage?.dims.find(item => item.item === question)?.manual_asked} onClick={() => void request(`/api/sessions/${sid}/outline_asked`, { item: question }).then(() => legacyApi<Session>(`/api/sessions/${sid}`)).then(setSession).catch(cause => setError(cause.message))}>{session.coverage?.dims.find(item => item.item === question)?.manual_asked ? '已问' : '标记已问'}</button></p>)}</div>{session?.suggestions?.filter(item => item.kind === 'follow_up' && item.status === 'new').slice(-3).map(item => <aside className="panel-note" key={item.id}><strong>追问建议</strong><p>{suggestionData(item).question}</p></aside>)}<div className="recording-transcript"><h3>实时逐字稿 · {session?.utterances?.length || 0} 段</h3>{session?.utterances?.map(row => <p key={row.id}>{row.text}</p>)}</div><div className="recording-footer">{session?.status === 'live' ? <button className="primary" disabled={blocked || pending > 0} onClick={() => setConfirmEnd(true)}>结束本场访谈</button> : <button className="primary" disabled={blocked || !session?.utterances?.length} onClick={() => void sync()}>同步逐字稿与纪要到工作台</button>}<button className="secondary" disabled={recording || unsavedAudio} onClick={onReturn}>返回工作台继续分析</button></div>{confirmEnd && <section className="capture-end-confirm" aria-label="结束访谈确认"><h3>结束本场访谈？</h3><p>结束后保存本场录音与逐字稿，并在后台生成纪要。若只是暂时离开，请取消并暂停录音；之后仍可继续同一场。</p><div><button className="secondary" disabled={localBusy} onClick={() => setConfirmEnd(false)}>取消，保留本场</button><button className="primary" disabled={blocked || pending > 0} onClick={() => void finish()}>确认结束本场</button></div></section>}{session?.status === 'done' && <p className="recorder-note">纪要状态：{!session.utterances?.length ? '无有效语音，未生成纪要；原录音可回听' : session.report_status === 'ready' ? '已生成，点击同步带回工作台' : session.report_status === 'failed' ? '未完成，可点击重试' : '后台处理中'}{session.report_status === 'failed' && <button onClick={() => void legacyApi(`/api/sessions/${sid}/finish?background=true`, { method: 'POST' }).catch(cause => setError(cause.message))}>重试生成纪要</button>}</p>}</>}
  </section>;
}
