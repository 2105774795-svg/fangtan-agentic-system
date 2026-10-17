import { useEffect, useState } from 'react';
import { ArrowLeft, BookOpen, DownloadSimple, Microphone } from '@phosphor-icons/react';
import type { Project, Artifact } from './types';
import { download } from './types';
import { MarkdownContent } from './MarkdownContent';
import { ConversationRoom } from './conversation/ConversationRoom';
import { conversationRequest, type Conversation, type Evidence } from './conversation/types';

/** Memoir presentation/policy only. Voice/turn handling lives in conversation/. */
export function MemoirExperience({ project, onHome, onHistory }: { project: Project; onHome: () => void; onHistory: () => void }) {
  const [archiveVisible, setArchiveVisible] = useState(new URLSearchParams(location.search).get('view') === 'life-archive');
  const [startNew, setStartNew] = useState(false);
  const [sessions, setSessions] = useState<Conversation[]>([]);
  const [legacy, setLegacy] = useState<Artifact[]>([]);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<{ sid: string; index: number; text: string; revision: number } | null>(null);
  const [evidence, setEvidence] = useState<{ session: Conversation; items: Evidence[] } | null>(null);
  const endpoint = `/api/platform/projects/${project.id}`;
  function changeView(value: boolean) { if (!value) setStartNew(true); setArchiveVisible(value); const url = new URL(location.href); if (value) url.searchParams.set('view', 'life-archive'); else url.searchParams.delete('view'); history.replaceState({}, '', url); }
  useEffect(() => {
    if (!archiveVisible) return;
    let active = true;
    async function load() { try { const value = await conversationRequest<{ sessions: Conversation[]; legacy_artifacts: Artifact[] }>(endpoint + '/conversation-archive'); if (active) { setSessions(value.sessions); setLegacy(value.legacy_artifacts); setError(''); } } catch (cause) { if (active) setError((cause as Error).message); } }
    void load(); const timer = window.setInterval(() => void load(), 2500);
    return () => { active = false; clearInterval(timer); };
  }, [endpoint, archiveVisible]);
  async function review(session: Conversation, body: Record<string, unknown>) {
    try { const updated = await conversationRequest<Conversation>(endpoint + `/conversations/${session.id}/archive-review`, { revision: session.archive?.revision || 1, ...body }); setSessions(previous => previous.map(s => s.id === updated.id ? updated : s)); setEditing(null); setError(''); }
    catch (cause) { setError((cause as Error).message); }
  }
  const stories = sessions.filter(s => s.archive);
  const exportStory = () => download(project.title + ' · 人生故事.md', '# ' + project.title + '\n\n> AI 整理草稿，依据本人原话，尚未核验。\n\n' + [...stories].reverse().map(s => `## ${new Date(s.created_at * 1000).toLocaleDateString('zh-CN')}的讲述\n\n` + s.archive!.paragraphs.map(p => p.text + '\n\n' + p.evidence.map(e => `> 原话：${e.quote}`).join('\n')).join('\n\n')).join('\n\n---\n\n'));
  if (!archiveVisible) return <ConversationRoom copy={{ archiveLabel: '我的档案', eyebrow: '听您慢慢讲', finishedTitle: '谢谢您，把这些故事留了下来。', archiveReady: '今天的故事已经整理好，作为草稿存进了您的档案。', archivePending: '正在后台整理今天的故事，您可以先休息，稍后回来查看。' }} startNew={startNew} endpoint={endpoint} kind="memoir" title={project.title} onHome={onHome} onHistory={onHistory} onArchive={() => changeView(true)}/>;
  return <div className="life-archive"><header className="conversation-header"><button onClick={onHome}><ArrowLeft size={21}/>返回首页</button><span>我的人生故事</span><button onClick={() => changeView(false)}><Microphone size={21}/>继续聊聊</button></header><main>
    <div className="life-archive-heading"><p className="conversation-eyebrow">一段一段，留住您的人生</p><h1>{project.title}</h1><p>每次聊天都保留原始讲述。故事由 AI 整理成草稿，可以回看原话、回听录音，记不清的地方不急着下结论。</p><button onClick={exportStory} disabled={!stories.length}><DownloadSimple size={20}/>导出故事</button></div>
    {error && <p role="alert" className="conversation-error">{error}</p>}
    {!sessions.length && <section className="life-empty"><BookOpen size={40}/><h2>故事从一次聊天开始</h2><button className="conversation-primary" onClick={() => changeView(false)}>开始聊聊</button></section>}
    <section className="life-stories" aria-label="讲述档案">{sessions.map((session, index) => <article className="life-story" key={session.id}><div className="life-story-title"><span>第 {sessions.length - index} 次讲述 · {new Date(session.created_at * 1000).toLocaleDateString('zh-CN')}</span><small>{session.status === 'live' ? '还可以继续讲' : session.archive ? session.archive.status === 'reviewed' ? '已审阅 · 保留原话依据' : '整理草稿 · 待核对' : session.archive_job?.status === 'failed' ? '整理暂未完成' : session.archive_job ? '正在后台整理' : '暂无故事正文'}</small></div>
      {session.archive?.paragraphs.map((p, n) => <div className="life-paragraph" key={n}>{editing?.sid === session.id && editing.index === n ? <form onSubmit={e => { e.preventDefault(); void review(session, { action: 'edit', paragraph_index: n, text: editing.text, revision: editing.revision }); }}><label>修改故事整理稿（原始讲述保持不变）<textarea value={editing.text} maxLength={5000} onChange={e => setEditing({ ...editing, text: e.target.value })}/></label><button type="submit">保存修改</button><button type="button" onClick={() => setEditing(null)}>取消修改</button></form> : <p>{p.text}</p>}{p.edited && <small>本段经人工修改，原话依据保留供对照。</small>}<button onClick={() => setEvidence({ session, items: p.evidence })}>查看这段的原话与录音</button><button onClick={() => setEditing({ sid: session.id, index: n, text: p.text, revision: session.archive?.revision || 1 })}>修改这段故事</button></div>)}
      {session.archive && session.archive.status !== 'reviewed' && <button className="life-review" onClick={() => void review(session, { action: 'review' })}>这次故事我看过了</button>}
      {!!session.archive?.events.length && <details className="life-events"><summary>这次留下的人生线索</summary>{session.archive.events.map((event, n) => <div key={n}><span>{event.date_text === '未提及' ? '时间待补充' : event.date_text}</span><h3>{event.title}</h3><p>{event.description}</p><small>{event.status === 'uncertain' ? '有记不清的细节，留待确认' : '根据本人讲述整理，待核对'}</small><button onClick={() => setEvidence({ session, items: event.evidence })}>查看原话</button></div>)}</details>}
      {session.archive_job?.status === 'failed' && <button onClick={() => void conversationRequest(endpoint + `/conversations/${session.id}/retry`, {}).catch(cause => setError(cause.message))}>原话已保存，重试整理</button>}
      <details className="life-original"><summary>查看本次完整对话与原始录音</summary>{session.turns.filter(t => t.text).map(t => <div key={t.id}><strong>{t.role === 'human' ? '本人讲述' : '体察回应'}</strong><p>{t.text}</p>{t.audio_ids.map(id => <audio key={id} controls preload="none" src={endpoint + `/conversations/${session.id}/audio/${id}`}/>)}</div>)}{session.audio.filter(a => !session.turns.some(t => t.client_id === a.turn_key && t.role === 'human')).map(a => <div key={a.id}><p>{a.transcript || '尚未识别到文字，原音保留'}</p><audio controls preload="none" src={endpoint + `/conversations/${session.id}/audio/${a.id}`}/></div>)}</details>
    </article>)}</section>
    {!!legacy.length && <details className="life-legacy"><summary>原项目中的已有资料（不自动作为本人讲述）</summary>{legacy.map(file => <details key={file.id}><summary>{file.title}</summary><MarkdownContent text={file.content}/></details>)}</details>}
    {evidence && <section className="life-evidence" role="region" aria-label="原话依据"><button onClick={() => setEvidence(null)}>收起原话</button><h2>这段故事来自您的讲述</h2>{evidence.items.map((item, index) => { const turn = evidence.session.turns.find(t => t.id === item.turn_id); return <div key={index}><blockquote>{item.quote}</blockquote>{turn?.audio_ids.map(id => <audio controls preload="none" key={id} src={endpoint + `/conversations/${evidence.session.id}/audio/${id}`}/>)}</div>; })}</section>}
  </main></div>;
}
