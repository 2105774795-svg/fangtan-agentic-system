import { useEffect, useState } from 'react';
import { conversationRequest, type Evidence, type LifeBook, type LifeMemory, type LifeNotebook } from './conversation/types';
import { download } from './types';

const statusNames: Record<string, string> = { candidate: '待核对', uncertain: '有不确定细节', confirmed: '本人已确认', disputed: '本人提出异议', superseded: '已有更正', dismissed: '不采用' };
type Props = { endpoint: string; title: string; data: LifeNotebook; view?: 'book' | 'gaps' | 'sources'; onContinue?: () => void; onChanged: () => void; onEvidence: (items: Evidence[]) => void };

/** 连续书稿是阅读主体；原话和结构化线索留在审校视图。 */
export function MemoirNotebook({ endpoint, title, data, view = 'book', onContinue, onChanged, onEvidence }: Props) {
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ chapter: number; paragraph: number; text: string; revision: number } | null>(null);
  const [replacements, setReplacements] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [versions, setVersions] = useState<{ revision: number; created_at: number; document: LifeBook }[] | null>(null);
  const [proof, setProof] = useState(false);
  const [feedback, setFeedback] = useState<{ chapter: string; kind: string; note: string } | null>(null);
  const prefs = data.book_settings;
  const [bookTitle, setBookTitle] = useState(prefs?.title || '');
  const [voiceNote, setVoiceNote] = useState(prefs?.voice_note || '');
  useEffect(() => { setBookTitle(prefs?.title || ''); setVoiceNote(prefs?.voice_note || ''); }, [prefs?.revision]);
  const { book, book_job: job } = data;
  const queued = !!job && ['queued', 'running', 'retry'].includes(job.status);
  const gaps = data.book_gaps || [];
  const blocked = busy || queued;
  async function mutate(path: string, body: unknown) {
    setBusy(true); setError('');
    try { await conversationRequest(endpoint + path, body); onChanged(); return true; }
    catch (cause) { setError((cause as Error).message); return false; }
    finally { setBusy(false); }
  }
  const settings = (body: Record<string, unknown>) => mutate('/conversation-book/settings', { revision: prefs?.revision || 0, ...body });
  const review = (body: Record<string, unknown>) => mutate('/conversation-book/review', { revision: book?.revision, ...body });
  async function memoryReview(memory: LifeMemory, action: string) {
    await mutate(`/conversation-memories/${memory.id}/review`, { revision: memory.revision, action, note: notes[memory.id] || '', ...(action === 'confirm' && replacements[memory.id] ? { replaces: replacements[memory.id] } : {}) });
  }
  async function saveParagraph() {
    if (editing && await mutate('/conversation-book/review', { revision: editing.revision, action: 'edit', chapter_index: editing.chapter, paragraph_index: editing.paragraph, text: editing.text })) setEditing(null);
  }
  function exportBook(withSources = false) {
    if (!book) return;
    const preface = withSources ? '> 审校稿：正文依据本人讲述写作，引用用于回查，不代表独立事实核验。\n\n' : '';
    download((book.title || title) + (withSources ? ' · 审校版.md' : ' · 人生传记.md'), '# ' + (book.title || title) + '\n\n' + preface +
      book.chapters.map(c => '## ' + c.title + '\n\n' + c.paragraphs.map(p => p.text + (withSources ? '\n\n' + p.evidence.map(e => '> 原话：' + e.quote).join('\n') : '')).join('\n\n')).join('\n\n'));
  }
  async function readVersions() {
    try { const value = await conversationRequest<{ versions: { revision: number; created_at: number; document: LifeBook }[] }>(endpoint + '/conversation-book/versions'); setVersions(value.versions); }
    catch (cause) { setError((cause as Error).message); }
  }
  const errorBox = error && <p role="alert" className="conversation-error">{error}</p>;
  if (view === 'gaps') return <section className="memoir-notebook" aria-label="待补充的往事"><h2>待补充的往事</h2><p>可以挑一段愿意聊的，也可以随心讲起。问题只是邀请，不必逐项完成。</p>{errorBox}
    {!gaps.some(g => g.status === 'open') && <p>暂时没有待展开的问题。继续讲述后，我们会慢慢发现值得写下的往事。</p>}
    {gaps.filter(g => g.status === 'open').map(gap => <article className="biography-gap" key={gap.id}><h3>{gap.title}</h3><p>{gap.question}</p>{gap.note && <p>您对书稿的意见：{gap.note}</p>}<div className="notebook-actions"><button disabled={blocked} onClick={async () => { if (await settings({ action: 'focus_gap', gap_id: gap.id })) onContinue?.(); }}>{gap.focused ? '接着聊这段往事' : '今天聊这一段'}</button><button disabled={blocked} onClick={() => void settings({ action: 'skip_gap', gap_id: gap.id })}>这段先不聊</button><button disabled={blocked} onClick={() => void settings({ action: 'resolve_gap', gap_id: gap.id })}>这段已讲清</button></div></article>)}
    {!!gaps.filter(g => g.status === 'skipped').length && <details><summary>暂时跳过的往事</summary>{gaps.filter(g => g.status === 'skipped').map(gap => <p key={gap.id}>{gap.question}<button disabled={blocked} onClick={async () => { if (await settings({ action: 'focus_gap', gap_id: gap.id })) onContinue?.(); }}>现在愿意聊</button></p>)}</details>}
    {!!gaps.filter(g => g.status === 'resolved').length && <details><summary>已经讲清的往事</summary>{gaps.filter(g => g.status === 'resolved').map(gap => <p key={gap.id}>{gap.question}<button disabled={blocked} onClick={async () => { if (await settings({ action: 'focus_gap', gap_id: gap.id })) onContinue?.(); }}>再补充一点</button></p>)}</details>}
  </section>;
  if (view === 'sources') return <section className="memoir-notebook" aria-label="资料与核对"><h2>人物与经历资料</h2><p>这些资料帮助核对书稿；最终正文在“我的传记”里阅读。</p>{errorBox}
    <details className="notebook-memories" open><summary>人生线索 · {data.memories.filter(m => !['dismissed', 'superseded'].includes(m.status)).length} 条</summary><p>确认、异议和更正分别留存，不把不确定的记忆写成定论。</p>
      {data.memories.map(memory => <article key={memory.id}><h3>{memory.title}</h3><small>{statusNames[memory.status] || memory.status} · {memory.date_text === '未提及' ? '时间待补充' : memory.date_text}</small><p>{memory.description}</p>{memory.note && <p>本人备注：{memory.note}</p>}<button onClick={() => onEvidence(memory.evidence)}>查看线索原话</button>
        {!['superseded', 'dismissed'].includes(memory.status) && <><label>备注或异议<input maxLength={1000} value={notes[memory.id] || ''} onChange={e => setNotes({ ...notes, [memory.id]: e.target.value })}/></label><label>确认时更正旧线索<select value={replacements[memory.id] || ''} onChange={e => setReplacements({ ...replacements, [memory.id]: e.target.value })}><option value="">不替代其他线索</option>{data.memories.filter(m => m.id !== memory.id && !['dismissed', 'superseded'].includes(m.status)).map(m => <option key={m.id} value={m.id}>{m.title} · {m.date_text}</option>)}</select></label><div className="notebook-actions"><button disabled={busy} onClick={() => void memoryReview(memory, 'confirm')}>确认这条线索</button><button disabled={busy} onClick={() => void memoryReview(memory, 'dispute')}>这条有出入</button><button disabled={busy} onClick={() => void memoryReview(memory, 'dismiss')}>不采用这条</button></div></>}
      </article>)}
    </details></section>;
  return <section className="memoir-notebook biography-reader" aria-label="我的传记">
    <header className="biography-cover"><p className="conversation-eyebrow">一本慢慢写成的书</p><h2>{book?.title || prefs?.title || '我的人生自传'}</h2><p>从一次次讲述里，写下属于您的生活。以朴素的第一人称回忆，保留人物、地方和事情本来的样子。</p></header>
    {errorBox}
    <details className="biography-contract"><summary>书名与写作约定</summary><form onSubmit={async e => { e.preventDefault(); await settings({ action: 'settings', title: bookTitle, voice_note: voiceNote }); }}><label>这本书的名字<input value={bookTitle} maxLength={120} placeholder="可以先用一个暂定书名" onChange={e => setBookTitle(e.target.value)}/></label><p>{prefs?.style.name || '参考沈从文《从文自传》的朴素回忆体'}</p><ul>{(prefs?.style.principles || ['通过具体的人物、动作与地方生活展开', '情感节制，保留传主自己的口吻']).map(rule => <li key={rule}>{rule}</li>)}</ul><label>您希望这本书怎样说话<textarea value={voiceNote} maxLength={2000} placeholder="例如：少一点抒情，保留我平时说话的直爽。" onChange={e => setVoiceNote(e.target.value)}/></label><p>没有讲过的天气、对白和心情不会补造；材料不足的地方，我们继续聊。</p><button disabled={blocked} type="submit">保存写作约定</button></form></details>
    <div className="notebook-actions"><button disabled={blocked} onClick={() => void mutate('/conversation-book', { mode: book ? 'continue' : 'sample' })}>{queued ? job?.stage || '正在写作…' : book ? prefs?.style_approved ? '根据新讲述续写' : '更新样章' : '写一章样稿'}</button>{book && <><button onClick={() => exportBook()}>导出传记正文</button><button onClick={() => exportBook(true)}>导出审校版</button></>}</div>
    {job?.status === 'failed' && <p role="status">这次写作未完成，已有书稿与原话保留，可以重试。</p>}
    {!book && <div className="biography-empty"><h3>先从一段清楚的往事写起</h3><p>不必一次讲完一生。聊到足够的材料后，先读一章样稿，再慢慢确定整本书的声音。</p><button onClick={onContinue}>继续讲述</button></div>}
    {book && <>
      <p role="status">{book.stage === 'sample' ? '样章 · 先确定写法' : book.stage === 'polished' ? '已完成本次统稿检查' : book.stage === 'collecting' ? '正在积累写作材料' : '传记书稿'} · 第 {book.revision} 版{book.stale ? ' · 有新讲述或编辑意见，可继续补写' : ''}</p>
      {!prefs?.style_approved && !!book.chapters.length && <div className="notebook-proposal"><p>先读读这章，看看是不是您希望的写法。也可以在章末告诉我们哪里不像您。</p><button disabled={blocked || book.stale} onClick={async () => { if (await settings({ action: 'approve_style' })) await mutate('/conversation-book', { mode: 'continue' }); }}>认可这个写法，继续写</button></div>}
      <nav className="biography-toc" aria-label="传记目录"><h3>目录</h3><ol>{(book.outline || book.chapters).map(c => <li key={c.id}>{book.chapters.some(w => w.id === c.id) ? <a href={'#biography-' + c.id}>{c.title}</a> : <span>{c.title} <small>待写</small></span>}</li>)}</ol></nav>
      <button className="biography-proof-toggle" aria-pressed={proof} onClick={() => setProof(!proof)}>{proof ? '返回连贯阅读' : '核对与编辑'}</button>
      {book.pending_draft && <details className="notebook-proposal"><summary>查看整份修订方案（当前正文保留）</summary><p>可以先对照各章，再决定是否采用；已定稿章节不会自行替换。</p>{book.pending_draft.chapters.map(c => <section key={c.id}><h3>{c.title}</h3>{c.paragraphs.map((p, i) => <p key={i}>{p.text}</p>)}</section>)}<button disabled={blocked || !!editing} onClick={() => { if (confirm('采用整份修订稿吗？当前版本会保存在历史记录中。')) void review({ action: 'adopt' }); }}>采用整份修订稿</button></details>}
      {book.chapters.map((chapter, ci) => <article className="notebook-chapter" id={'biography-' + chapter.id} key={chapter.id}><header><h3>{chapter.title}</h3>{chapter.locked && <small>本章已定稿保护</small>}{chapter.needs_review && <small>有待核对的修订</small>}</header>
        {chapter.paragraphs.map((p, pi) => <div className="life-paragraph" key={pi}>{editing?.chapter === ci && editing.paragraph === pi ? <form onSubmit={e => { e.preventDefault(); void saveParagraph(); }}><label>修改传记正文<textarea maxLength={5000} value={editing.text} onChange={e => setEditing({ ...editing, text: e.target.value })}/></label><button disabled={busy} type="submit">保存正文修改</button><button type="button" onClick={() => setEditing(null)}>取消</button></form> : <p>{p.text}</p>}
          {proof && <div className="biography-paragraph-tools">{p.edited && <small>本人修改过的文字</small>}<button onClick={() => onEvidence(p.evidence)}>核对这段原话</button><button disabled={busy || chapter.locked} onClick={() => setEditing({ chapter: ci, paragraph: pi, text: p.text, revision: book.revision })}>修改这一段</button></div>}
        </div>)}
        {chapter.pending_revision && <details className="biography-comparison"><summary>对照本章修订稿</summary><div><section aria-label="当前正文"><h4>当前正文</h4>{chapter.paragraphs.map((p, i) => <p key={i}>{p.text}</p>)}</section><section aria-label="本章修订稿"><h4>修订稿</h4>{chapter.pending_revision.paragraphs.map((p, i) => <p key={i}>{p.text}</p>)}</section></div><button disabled={blocked || !!editing} onClick={() => void review({ action: 'adopt_chapter', chapter_id: chapter.id })}>采用本章修订稿</button></details>}
        <div className="notebook-actions biography-feedback-actions"><button onClick={() => setFeedback({ chapter: chapter.id, kind: 'voice', note: '' })}>这里写得不像我</button><button onClick={() => setFeedback({ chapter: chapter.id, kind: 'fact', note: '' })}>这件事有出入</button><button onClick={() => setFeedback({ chapter: chapter.id, kind: 'expand', note: '' })}>这一段再多写一点</button><button disabled={blocked || !!editing || (!chapter.locked && (book.stale || !!chapter.pending_revision))} onClick={() => void review({ action: chapter.locked ? 'unlock_chapter' : 'lock_chapter', chapter_id: chapter.id })}>{chapter.locked ? '解除本章定稿保护' : '这一章先定下来'}</button></div>
        {feedback?.chapter === chapter.id && <form className="biography-feedback" onSubmit={async e => { e.preventDefault(); if (await settings({ action: 'feedback', chapter_id: chapter.id, kind: feedback.kind, note: feedback.note })) setFeedback(null); }}><label>告诉我们具体想改哪里<textarea maxLength={1000} value={feedback.note} onChange={e => setFeedback({ ...feedback, note: e.target.value })}/></label><p>{feedback.kind === 'voice' ? '写法意见会用于下一次修订。' : '已记录的意见会成为后续采访线索；补充的事实以您的新讲述为准。'}</p><button type="submit" disabled={blocked || !feedback.note.trim()}>保存这条意见</button><button type="button" onClick={() => setFeedback(null)}>取消</button></form>}
      </article>)}
      {!!book.editorial_notes?.length && <aside className="notebook-proposal"><h3>统稿时需要再看看的地方</h3>{book.editorial_notes.map((note, i) => <p key={i}>{book.chapters.find(c => c.id === note.chapter_id)?.title}：{note.message}</p>)}</aside>}
      <div className="notebook-actions"><button disabled={blocked || !prefs?.style_approved || !book.chapters.length} onClick={() => void mutate('/conversation-book', { mode: 'polish' })}>做一次全书统稿</button><button disabled={busy || !!editing || book.stale || book.chapters.some(c => !!c.pending_revision)} onClick={() => void review({ action: 'review' })}>这份书稿我看过了</button><button onClick={() => void readVersions()}>查看书稿版本</button></div>
      <p className="notebook-note">书稿逐步补写，不以字数填满一本书。原始录音、讲述和人物线索在“资料与核对”中保留。</p>
      {proof && <p>{book.coverage.notice}{book.coverage.uncited_turn_ids.length ? ` 还有 ${book.coverage.uncited_turn_ids.length} 段讲述尚未进入正文，可继续补写。` : ''}</p>}
      {versions && <ul aria-label="书稿版本记录">{versions.length ? versions.map(v => <li key={v.revision}><details><summary>第 {v.revision} 版 · {new Date(v.created_at * 1000).toLocaleString('zh-CN')}</summary>{v.document.chapters.map(c => <section key={c.id}><h3>{c.title}</h3>{c.paragraphs.map((p, pi) => <p key={pi}>{p.text}</p>)}</section>)}</details></li>) : <li>尚无旧版本</li>}</ul>}
    </>}
  </section>;
}
