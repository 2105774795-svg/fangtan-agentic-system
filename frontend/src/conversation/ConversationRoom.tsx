import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, BookOpen, CircleNotch, Microphone, Pause, Play, SpeakerHigh, Stop, TextT, WarningCircle } from '@phosphor-icons/react';
import { requestId } from '../requestId';
import { recordingOutbox } from '../recordingOutbox';
import { conversationRequest, type Conversation } from './types';
import { useConversationAudio } from './useConversationAudio';
import './conversation.css';

type Props = { endpoint: string; kind: string; title: string; onHome: () => void; onHistory: () => void; onArchive: () => void; startNew?: boolean; copy?: { archiveLabel?: string; eyebrow?: string; finishedTitle?: string; archiveReady?: string; archivePending?: string } };

/** Scenario-neutral, reusable voice/text conversation surface. */
export function ConversationRoom(props: Props) {
  const copy = { archiveLabel: '对谈记录', eyebrow: '一次聊一个问题', finishedTitle: '本次对谈已经保存。', archiveReady: '本次对谈已整理为草稿，可以查看原话和整理结果。', archivePending: '正在后台整理本次对谈，您可以先离开，稍后回来查看。', ...props.copy };
  const [session, setSession] = useState<Conversation | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [sending, setSending] = useState(false);
  const [text, setText] = useState('');
  const [textVisible, setTextVisible] = useState(false);
  const [historyVisible, setHistoryVisible] = useState(false);
  const [silenceMs, setSilenceMs] = useState(5000);
  const [consent, setConsent] = useState(false);
  const [handsfree, setHandsfree] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [booting, setBooting] = useState(true);
  const active = useRef(true);
  const auto = useRef(false);
  const generation = useRef(0);
  const speechDone = useRef<(() => void) | null>(null);
  const spoken = useRef(new Set<string>());
  const submitId = useRef<string | null>(null);
  const sendText = useRef('');
  const createId = useRef(requestId());
  const endpoint = session ? `${props.endpoint}/conversations/${session.id}` : '';
  auto.current = handsfree;
  const voice = useConversationAudio({
    sessionId: session?.id || 'loading',
    silenceMs,
    completedTurnIds: (session?.turns || []).filter(t => t.role === 'human').map(t => t.client_id),
    upload: async (chunk, turnId) => {
      const form = new FormData(); form.append('audio', chunk.blob, 'voice.webm'); form.append('chunk_id', chunk.id); form.append('turn_key', turnId); form.append('part_index', String(chunk.order));
      const r = await fetch(endpoint + '/audio', { method: 'POST', body: form, signal: AbortSignal.timeout(195000) });
      if (!r.ok) { const data = await r.json().catch(() => ({})); throw new Error(typeof data.detail === 'string' ? data.detail : '录音已保留，转写暂未完成，请稍后重试'); }
      return r.json();
    },
    submit: async body => {
      const value = await conversationRequest<Conversation>(endpoint + '/turns', body);
      setSession(value);
      if (value.no_speech) { setHandsfree(false); setNotice('刚才没有听到有效语音，录音已保留。准备好后可以继续说。'); }
    },
  });
  const audioRef = useRef(voice); audioRef.current = voice;
  const latest = session?.turns.at(-1);
  const generating = !!latest?.job && ['queued', 'running', 'retry'].includes(latest.job.status);
  const failed = latest?.job?.status === 'failed';

  async function open(newSession = false) {
    setBooting(true); setError('');
    try {
      const cacheKey = 'conversation-last-' + props.endpoint;
      const savedId = localStorage.getItem(cacheKey);
      if (!newSession && savedId) {
        const prior = await fetch(props.endpoint + '/conversations/' + savedId);
        if (prior.ok) {
          const value = await prior.json() as Conversation;
          if (!props.startNew || value.status === 'live') { if (active.current) setSession(value); return; }
        } else if (prior.status !== 404) throw new Error('暂时无法恢复上次对谈，请稍后重试');
      }
      const value = await conversationRequest<Conversation>(props.endpoint + '/conversations', { kind: props.kind, request_id: createId.current, new_session: newSession });
      localStorage.setItem(cacheKey, value.id);
      if (active.current) setSession(value);
    }
    catch (cause) { if (active.current) setError((cause as Error).message); }
    finally { if (active.current) setBooting(false); }
  }
  useEffect(() => { active.current = true; void open(); return () => { active.current = false; auto.current = false; generation.current++; speechDone.current?.(); if ('speechSynthesis' in window) window.speechSynthesis.cancel(); }; }, [props.endpoint]);
  useEffect(() => {
    if (!session?.id) return;
    const events = new EventSource(`${props.endpoint}/conversations/${session.id}/events`);
    events.addEventListener('snapshot', event => { try { setSession(JSON.parse((event as MessageEvent).data)); } catch { /* Reconnect delivers complete snapshots. */ } });
    events.addEventListener('deleted', () => { events.close(); void audioRef.current.pause().catch(() => {}); setError('这个项目已经删除，请返回首页'); setHandsfree(false); });
    events.onerror = () => { setNotice('连接暂时中断，已保存的讲述不会丢失，正在重连。'); };
    return () => events.close();
  }, [session?.id, props.endpoint]);

  function cancelSpeech() { generation.current++; speechDone.current?.(); speechDone.current = null; if ('speechSynthesis' in window) window.speechSynthesis.cancel(); setSpeaking(false); }
  async function speak(value: string) {
    if (!('speechSynthesis' in window) || !window.SpeechSynthesisUtterance) { setNotice('当前设备不能朗读，您可以看文字继续讲。'); return; }
    const available = speechSynthesis.getVoices().find(v => /^zh/i.test(v.lang) && v.localService);
    if (!available) { setNotice('当前设备没有可用的本地中文声音，暂时显示文字；安装中文语音后可朗读。'); return; }
    setSpeaking(true);
    await new Promise<void>(resolve => {
      const utterance = new SpeechSynthesisUtterance(value); utterance.voice = available; utterance.lang = available.lang; utterance.rate = .88;
      let timer: number;
      const finish = () => { clearTimeout(timer); speechDone.current = null; resolve(); };
      speechDone.current = finish;
      utterance.onend = finish; utterance.onerror = () => { setNotice('朗读暂未完成，可以点击重新听或看文字继续讲。'); finish(); };
      timer = window.setTimeout(() => { speechSynthesis.cancel(); finish(); }, 90000);
      speechSynthesis.speak(utterance);
    });
    if (active.current) setSpeaking(false);
  }
  useEffect(() => {
    if (!handsfree || !session || session.status !== 'live' || !latest || latest.role !== 'assistant' || !latest.text || generating || failed || spoken.current.has(latest.id)) return;
    spoken.current.add(latest.id);
    if (latest.control) { setHandsfree(false); return; }
    const token = ++generation.current;
    void (async () => { await speak(latest.text); if (active.current && auto.current && generation.current === token) await audioRef.current.start(); })();
  }, [latest?.id, latest?.text, latest?.job?.status, handsfree, session?.status]);
  useEffect(() => { if (session?.status === 'done') { auto.current = false; setHandsfree(false); cancelSpeech(); } }, [session?.status]);

  async function pause() {
    auto.current = false; setHandsfree(false); cancelSpeech();
    try { await voice.pause(); } catch { setError('最后一段录音尚未保存到本机，请留在这里并重试'); throw new Error('unsaved audio'); }
  }
  async function leave(callback: () => void) { if (text.trim() && !confirm('还有没发送的文字，确定离开吗？')) return; try { await pause(); callback(); } catch { /* Unsaved memory stays on this page. */ } }
  async function start() {
    if (!consent) { setError('请先确认录音和云端处理说明'); return; }
    setError(''); setNotice(''); await recordingOutbox();
    if (voice.pending) { setHandsfree(true); await voice.start(); return; }
    if (latest && spoken.current.has(latest.id)) { auto.current = true; setHandsfree(true); await voice.start(); }
    else { auto.current = true; setHandsfree(true); }
  }
  async function typed() {
    if (!text.trim() || sending || generating || voice.pending || voice.unsaved) return;
    if (!consent) { setError('请先确认云端处理说明'); return; }
    await pause(); setSending(true); setError('');
    if (sendText.current !== text.trim()) { submitId.current = requestId(); sendText.current = text.trim(); }
    try { setSession(await conversationRequest<Conversation>(endpoint + '/turns', { client_id: submitId.current, text: text.trim() })); setText(''); submitId.current = null; }
    catch (cause) { setError((cause as Error).message); }
    finally { setSending(false); }
  }
  async function finish() {
    try {
      await pause(); setError('');
      if (voice.pending && !await voice.complete()) return;
      setSending(true); setSession(await conversationRequest<Conversation>(endpoint + '/finish', {}));
    } catch (cause) { setError((cause as Error).message); }
    finally { setSending(false); }
  }
  const currentAssistant = [...(session?.turns || [])].reverse().find(t => t.role === 'assistant');
  const lastHuman = [...(session?.turns || [])].reverse().find(t => t.role === 'human');
  const state = booting ? '正在准备' : session?.status === 'done' ? '今天的讲述已保存' : speaking ? '我在说，您慢慢听' : voice.listening ? '正在听您说，不着急' : voice.processing || generating ? '正在理解您的讲述' : '准备好了就开始聊';
  return <div className="conversation-room">
    <header className="conversation-header"><button onClick={() => void leave(props.onHome)}><ArrowLeft size={21}/>返回首页</button><span>{props.title}</span><div><button onClick={() => void leave(props.onArchive)}><BookOpen size={20}/>{copy.archiveLabel}</button><button onClick={() => void leave(props.onHistory)}>历史项目</button></div></header>
    <main className="conversation-main" aria-busy={booting}>
      <p className="conversation-eyebrow">体察 · {copy.eyebrow}</p>
      <div className={`conversation-orb ${voice.listening ? 'listening' : ''} ${speaking ? 'speaking' : ''}`} aria-hidden="true"><span/><span/><span/></div>
      <p className="conversation-state" role="status">{(voice.processing || generating) && <CircleNotch className="spin" size={20}/>} {state}</p>
      <div className="conversation-current">{currentAssistant?.output && generating ? currentAssistant.output : currentAssistant?.text || (booting ? '请稍等，我们正在准备这次聊天。' : '很高兴在这里听您讲述。')}</div>
      {voice.transcript ? <p className="conversation-user">您刚才说：{voice.transcript}</p> : lastHuman && <p className="conversation-user">您：{lastHuman.text}</p>}
      {(error || voice.error) && <div className="conversation-error" role="alert"><WarningCircle size={23}/><span>{error || voice.error}</span>{booting === false && !session && <button onClick={() => void open()}>重试连接</button>}</div>}
      {notice && <p className="conversation-notice">{notice}</p>}
      {session?.status !== 'done' ? <>
        <label className="conversation-consent"><input type="checkbox" checked={consent} disabled={voice.listening || voice.processing} onChange={e => setConsent(e.target.checked)}/><span>我同意保存录音和讲述，并使用云端 AI 转写、回应和整理。</span></label>
        <div className="conversation-controls">
          {voice.listening ? <><button className="conversation-primary" onClick={() => void voice.complete()}><Microphone size={24}/>我说完了</button><button onClick={() => void pause().catch(() => {})}><Pause size={22}/>暂停一下</button></> : speaking ? <button className="conversation-primary" onClick={() => { cancelSpeech(); void voice.start(); }}><Microphone size={24}/>我来说</button> : <button className="conversation-primary" disabled={booting || !session || !consent || generating || sending || voice.processing || failed} onClick={() => void start().catch(cause => setError(cause.message))}><Play size={23}/>{voice.pending ? '继续刚才的讲述' : '开始聊天'}</button>}
          <button className="conversation-end" disabled={!session || sending || voice.processing || voice.unsaved} onClick={() => void finish()}><Stop size={20}/>今天先聊到这里</button>
        </div>
        {voice.pending && !voice.listening && <div className="conversation-recovery"><p>这一段讲述已保留，您可以继续说，或重试转写并发送；也可以返回首页，稍后继续。</p><button disabled={voice.processing} onClick={() => void voice.complete()}>重试转写并发送</button></div>}
        {failed && <button className="conversation-retry" onClick={() => void conversationRequest<Conversation>(endpoint + '/retry', {}).then(setSession).catch(cause => setError(cause.message))}>原话已保存，重试 AI 回应</button>}
        <div className="conversation-secondary"><button onClick={() => { void pause().catch(() => {}); setTextVisible(!textVisible); }}><TextT size={20}/>用文字说</button><button disabled={!currentAssistant?.text || voice.listening || speaking} onClick={() => void speak(currentAssistant?.text || '')}><SpeakerHigh size={20}/>再听一遍</button><button onClick={() => setHistoryVisible(!historyVisible)}>查看对话</button></div>
        <details className="conversation-preferences"><summary>聊天设置</summary><label>说话时可以停顿多久<select aria-label="说话停顿等待" value={silenceMs} onChange={e => setSilenceMs(Number(e.target.value))}><option value={3000}>3 秒</option><option value={5000}>5 秒（推荐）</option><option value={8000}>8 秒，慢慢说</option></select></label><p>有连续说话声后才会自动回应，安静时不会催促；也可以随时点“我说完了”。</p></details>
        {textVisible && <form className="conversation-text" onSubmit={e => { e.preventDefault(); void typed(); }}><label htmlFor="conversation-input">写下您想讲的内容</label><textarea id="conversation-input" value={text} maxLength={12000} onChange={e => setText(e.target.value)} placeholder="您也可以打字，或请家人帮忙输入……"/><button disabled={!text.trim() || !consent || generating || sending || voice.pending} type="submit">{sending ? '正在发送' : '发送这段话'}</button></form>}
      </> : <section className="conversation-finished"><h2>{copy.finishedTitle}</h2><p>{session.archive ? copy.archiveReady : session.archive_job?.status === 'failed' ? '原始讲述已保存，故事整理暂未完成，可以稍后重试。' : session.archive_job ? copy.archivePending : '本次没有需要整理的讲述，您可以下次再聊。'}</p><button className="conversation-primary" onClick={props.onArchive}><BookOpen size={23}/>看看{copy.archiveLabel}</button><button onClick={() => { createId.current = requestId(); spoken.current.clear(); void open(true); }}>再聊一会</button></section>}
      {historyVisible && <section className="conversation-history" aria-label="对话记录">{session?.turns.filter(t => t.text).map(turn => <article key={turn.id}><strong>{turn.role === 'human' ? '您说' : '体察'}</strong><p>{turn.text}</p>{turn.audio_ids.map(id => <audio key={id} controls preload="none" src={endpoint + '/audio/' + id}/>)}</article>)}</section>}
    </main>
  </div>;
}
