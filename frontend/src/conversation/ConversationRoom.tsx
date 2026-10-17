import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, BookOpen, CircleNotch, Microphone, Pause, Play, SpeakerHigh, Stop, TextT, WarningCircle } from '@phosphor-icons/react';
import { requestId } from '../requestId';
import { recordingOutbox } from '../recordingOutbox';
import { conversationRequest, type Conversation } from './types';
import { useConversationAudio } from './useConversationAudio';
import { useConversationSpeech } from './useConversationSpeech';
import { useAutoConversation } from './useAutoConversation';
import { ConversationHttpError } from './retry';
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
  const [silenceMs, setSilenceMs] = useState(() => {
    const saved = Number(localStorage.getItem('conversation-silence-ms'));
    return [3000, 5000, 8000].includes(saved) ? saved : 5000;
  });
  const [consent, setConsent] = useState(false);
  const [booting, setBooting] = useState(true);
  const active = useRef(true);
  const submitId = useRef<string | null>(null);
  const sendText = useRef('');
  const createId = useRef(requestId());
  const endpoint = session ? `${props.endpoint}/conversations/${session.id}` : '';
  const voice = useConversationAudio({
    sessionId: session?.id || 'loading',
    silenceMs,
    completedTurnIds: (session?.turns || []).filter(t => t.role === 'human').map(t => t.client_id),
    upload: async (chunk, turnId) => {
      const form = new FormData(); form.append('audio', chunk.blob, 'voice.webm'); form.append('chunk_id', chunk.id); form.append('turn_key', turnId); form.append('part_index', String(chunk.order));
      const r = await fetch(endpoint + '/audio', { method: 'POST', body: form, signal: AbortSignal.timeout(195000) });
      if (!r.ok) { const data = await r.json().catch(() => ({})); throw new ConversationHttpError(typeof data.detail === 'string' ? data.detail : '录音已保留，转写暂未完成，请稍后重试', r.status); }
      return r.json();
    },
    submit: async body => {
      const value = await conversationRequest<Conversation>(endpoint + '/turns', body);
      setSession(value);
      if (value.no_speech) setNotice('刚才没有听清，原音已保留，自动对谈会继续听您说。');
    },
  });
  const speech = useConversationSpeech();
  const conversation = useAutoConversation({ session, voice, speech });
  const conversationRef = useRef(conversation); conversationRef.current = conversation;
  const speaking = speech.speaking;
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
  useEffect(() => { active.current = true; void open(); return () => { active.current = false; }; }, [props.endpoint]);
  useEffect(() => {
    if (!session?.id) return;
    const events = new EventSource(`${props.endpoint}/conversations/${session.id}/events`);
    events.addEventListener('snapshot', event => { try { setSession(JSON.parse((event as MessageEvent).data)); } catch { /* Reconnect delivers complete snapshots. */ } });
    events.addEventListener('deleted', () => { events.close(); void conversationRef.current.pause().catch(() => {}); setError('这个项目已经删除，请返回首页'); });
    events.onerror = () => { setNotice('连接暂时中断，已保存的讲述不会丢失，正在重连。'); };
    return () => events.close();
  }, [session?.id, props.endpoint]);

  async function pause() {
    try { await conversation.pause(); } catch { setError('最后一段录音尚未保存到本机，请留在这里并重试'); throw new Error('unsaved audio'); }
  }
  async function leave(callback: () => void) { if (text.trim() && !confirm('还有没发送的文字，确定离开吗？')) return; try { await pause(); callback(); } catch { /* Unsaved memory stays on this page. */ } }
  async function start() {
    if (!consent) { setError('请先确认录音和云端处理说明'); return; }
    setError(''); setNotice(''); await recordingOutbox();
    await conversation.start();
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
  const state = booting ? '正在准备' : session?.status === 'done' ? '今天的讲述已保存' : conversation.phase === 'preparing' ? '正在准备麦克风和人声检测' : speaking ? '我在说，您慢慢听' : voice.listening ? voice.countdown !== null ? `等待您继续说，${voice.countdown} 秒后自动回应` : '正在听您说，不着急' : voice.retryAttempt ? `连接暂时中断，正在第 ${voice.retryAttempt} 次重试` : latest?.job?.status === 'retry' ? 'AI 连接暂时中断，正在自动重试，您的讲述已保存' : failed ? 'AI 回应暂未完成，您的讲述已保存' : voice.processing || generating ? '正在理解您的讲述' : conversation.phase === 'error' ? '对谈暂时中断，讲述已保留' : conversation.enabled ? '正在接续，马上继续听您说' : conversation.phase === 'paused' ? '对谈已暂停，麦克风已关闭' : '准备好了就开始自动对谈';
  return <div className="conversation-room">
    <header className="conversation-header"><button onClick={() => void leave(props.onHome)}><ArrowLeft size={21}/>返回首页</button><span>{props.title}</span><div><button onClick={() => void leave(props.onArchive)}><BookOpen size={20}/>{copy.archiveLabel}</button><button onClick={() => void leave(props.onHistory)}>历史项目</button></div></header>
    <main className="conversation-main" aria-busy={booting}>
      <p className="conversation-eyebrow">体察 · {copy.eyebrow}</p>
      <div className={`conversation-orb ${voice.listening ? 'listening' : ''} ${speaking ? 'speaking' : ''}`} aria-hidden="true"><span/><span/><span/></div>
      <p className="conversation-state" role="status" data-phase={conversation.phase}>{(voice.processing || generating || conversation.phase === 'preparing') && <CircleNotch className="spin" size={20}/>} {state}</p>
      {conversation.enabled && <p className="conversation-auto-badge">自动对谈已开启 · 停顿后自动接话</p>}
      <div className="conversation-current">{currentAssistant?.output && generating ? currentAssistant.output : currentAssistant?.text || (booting ? '请稍等，我们正在准备这次聊天。' : '很高兴在这里听您讲述。')}</div>
      {voice.transcript ? <p className="conversation-user">您刚才说：{voice.transcript}</p> : lastHuman && <p className="conversation-user">您：{lastHuman.text}</p>}
      {(error || conversation.error) && <div className="conversation-error" role="alert"><WarningCircle size={23}/><span>{error || conversation.error}</span>{booting === false && !session && <button onClick={() => void open()}>重试连接</button>}{conversation.phase === 'error' && !voice.pending && !failed && <button onClick={() => void start().catch(cause => setError(cause.message))}>重试自动对谈</button>}</div>}
      {(notice || speech.notice || voice.notice || latest?.job?.notice) && <p className="conversation-notice">{[notice, speech.notice, voice.notice, latest?.job?.notice].filter(Boolean).join(' ')}</p>}
      {failed && <p className="conversation-error" role="alert">{latest?.job?.error || 'AI 回应暂未完成，原话已保存，可以重试。'}</p>}
      {session?.status !== 'done' ? <>
        <label className="conversation-consent"><input type="checkbox" checked={consent} disabled={conversation.enabled || voice.listening || voice.processing} onChange={e => setConsent(e.target.checked)}/><span>我同意保存录音和讲述，并使用云端 AI 转写、回应和整理。</span></label>
        <div className="conversation-controls">
          {conversation.enabled ? <><button className="conversation-primary" onClick={() => void pause().catch(() => {})}><Pause size={22}/>暂停对谈</button>{voice.listening && <button onClick={() => void voice.complete()}><Microphone size={24}/>我说完了</button>}{speaking && <button onClick={() => void conversation.interrupt().catch(cause => setError(cause.message))}><Microphone size={24}/>我来说</button>}</> : <button className="conversation-primary" disabled={booting || !session || !consent || generating || sending || voice.processing || failed} onClick={() => void start().catch(cause => setError(cause.message))}><Play size={23}/>{voice.pending ? '继续刚才的讲述' : conversation.phase === 'paused' ? '继续自动对谈' : '开始自动对谈'}</button>}
          <button className="conversation-end" disabled={!session || sending || voice.processing || voice.unsaved} onClick={() => void finish()}><Stop size={20}/>今天先聊到这里</button>
        </div>
        {voice.pending && !voice.listening && !voice.processing && <div className="conversation-recovery"><p>这一段讲述已保留，您可以继续说，或重试转写并发送；也可以返回首页，稍后继续。</p><button disabled={voice.processing} onClick={() => void voice.complete()}>重试转写并发送</button></div>}
        {failed && <button className="conversation-retry" onClick={() => void conversationRequest<Conversation>(endpoint + '/retry', {}).then(setSession).catch(cause => setError(cause.message))}>原话已保存，重试 AI 回应</button>}
        <div className="conversation-secondary"><button onClick={() => { void pause().then(() => setTextVisible(!textVisible)).catch(() => {}); }}><TextT size={20}/>用文字说</button><button disabled={!currentAssistant?.text || conversation.enabled || voice.processing || speaking} onClick={() => void speech.speak(currentAssistant?.text || '')}><SpeakerHigh size={20}/>再听一遍</button><button onClick={() => setHistoryVisible(!historyVisible)}>查看对话</button></div>
        <details className="conversation-preferences"><summary>聊天设置</summary><label>说话时可以停顿多久<select aria-label="说话停顿等待" value={silenceMs} onChange={e => { const value = Number(e.target.value); setSilenceMs(value); localStorage.setItem('conversation-silence-ms', String(value)); }}><option value={3000}>3 秒</option><option value={5000}>5 秒（推荐）</option><option value={8000}>8 秒，慢慢说</option></select></label><p>设置从下一轮收音开始生效。停顿期间继续说，仍算同一轮；安静时不会催促。也可以点“我说完了”提前回应。</p></details>
        {textVisible && <form className="conversation-text" onSubmit={e => { e.preventDefault(); void typed(); }}><label htmlFor="conversation-input">写下您想讲的内容</label><p>文字输入会暂停自动收音；发送后可点击“继续自动对谈”。</p><textarea id="conversation-input" value={text} maxLength={12000} onChange={e => setText(e.target.value)} placeholder="您也可以打字，或请家人帮忙输入……"/><button disabled={!text.trim() || !consent || generating || sending || voice.pending} type="submit">{sending ? '正在发送' : '发送这段话'}</button></form>}
      </> : <section className="conversation-finished"><h2>{copy.finishedTitle}</h2><p>{session.archive ? copy.archiveReady : session.archive_job?.status === 'failed' ? '原始讲述已保存，故事整理暂未完成，可以稍后重试。' : session.archive_job ? copy.archivePending : '本次没有需要整理的讲述，您可以下次再聊。'}</p><button className="conversation-primary" onClick={props.onArchive}><BookOpen size={23}/>看看{copy.archiveLabel}</button><button onClick={() => { createId.current = requestId(); void open(true); }}>再聊一会</button></section>}
      {historyVisible && <section className="conversation-history" aria-label="对话记录">{session?.turns.filter(t => t.text).map(turn => <article key={turn.id}><strong>{turn.role === 'human' ? '您说' : '体察'}</strong><p>{turn.text}</p>{turn.audio_ids.map(id => <audio key={id} controls preload="none" src={endpoint + '/audio/' + id}/>)}</article>)}</section>}
    </main>
  </div>;
}
