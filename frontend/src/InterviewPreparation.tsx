import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, Microphone, Play, Pause, Check, ArrowClockwise, DownloadSimple, FileText, Plus, X } from '@phosphor-icons/react';
import { MarkdownContent } from './MarkdownContent';
import { download } from './types';
import { useAnswerRecorder } from './useAnswerRecorder';
import './interview.css';
import { interviewPreferences } from './interviewPreferences';

type Config = { preparation_step?: 'goal'|'background'|'closing'|'ready'; template: 'job' | 'thesis'; goal: string; language: '中文' | 'English'; practice_mode: 'coaching' | 'simulation'; question_count: number; max_followups: number; duration_minutes: number; followup_mode: 'adaptive'|'fixed'; include_candidate_questions: boolean; company_name: string; role_name: string; company_background: string; job_requirements: string; background_source: string; interview_stage: string };
type SuggestedQuestion = { question:string; why:string; timing:string };
type CandidateReviewData = { summary:string; strengths:string[]; improvements:string[]; recommended_questions:SuggestedQuestion[] };
type BankQuestion = { id:string; question:string; previous_answer:string; context:string; analysis_stale:boolean; job:Job; analysis:{intent:string;answer_outline:string[];previous_answer_feedback:string[];followup_angles:string[];preparation_actions:string[];missing_information:string[]}|null };
type Question = { question: string; target: string };
type Finding = { dimension: string; observation: string; suggestion: string; answer_id: string; quote: string; segment_ids: number[] };
type Segment = { id: number; start_ms: number; end_ms: number; text: string };
type Answer = { id: string; session_id: string; status: string; transcript: string; version: number; original_transcript: string; segments: Segment[]; chunk_ids: string[] };
type Turn = { id: string; position: number; question: string; target: string; parent_id: string | null; status: string; feedback: Finding[] | null; feedback_notice?: string; turn_kind?: 'question'|'candidate_questions'; decision_reason?: string; answers: Answer[] };
type Job = { id: string; status: string; stage: string; error?: string } | null;
type Practice = { candidate_review?:CandidateReviewData|null; id: string; status: string; config: Config; job: Job; created_at: number; source_practice_id: string | null; review_stale: boolean; review: { summary: string; findings: Finding[]; retry_questions: Question[]; candidate_questions_review?:CandidateReviewData|null } | null; turns: Turn[] };
type Material = { id: string; title: string; kind: string; content: string; version: number };
export type Preparation = { id: string; title: string; revision: number; config: Config; plan: { summary: string; preparation_tips: string[]; questions: Question[]; company_and_role_focus?:string[]; candidate_question_suggestions?:SuggestedQuestion[] } | null; plan_stale: boolean; materials: Material[]; question_bank:BankQuestion[]; practices: Practice[]; job: Job };
const active = (job: Job) => !!job && ['queued','running','retry'].includes(job.status);
const clock = (seconds: number) => `${Math.floor(seconds / 60).toString().padStart(2,'0')}:${Math.floor(seconds % 60).toString().padStart(2,'0')}`;
export async function prepApi<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/interview-prep${path}`, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : '提交内容无效，请检查必填项或重新加载');
  }
  return response.json();
}

export function InterviewPreparation({ id, onHome, onHistory }: { id: string; onHome: () => void; onHistory: () => void }) {
  const [project, setProject] = useState<Preparation | null>(null);
  const [tab, setTab] = useState<'prepare'|'practice'|'review'|'history'>('prepare');
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(true);
  const [captureBusy, setCaptureBusy] = useState(false);
  const [draftDirty,setDraftDirty] = useState(false);
  const [setupReady,setSetupReady] = useState(false);
  const [materialDirty,setMaterialDirty] = useState(false);
  const [questionDirty,setQuestionDirty] = useState(false);
  const [capabilities, setCapabilities] = useState({ model_configured: false, transcription_configured: false, local_mode: true });
  const current = useRef<Preparation | null>(null);
  const apply = (data: Preparation) => { if (!current.current || current.current.revision <= data.revision) { current.current = data; setProject(data); } };
  const refresh = async () => { const data = await prepApi<Preparation>(`/projects/${id}`); apply(data); return data; };
  useEffect(() => {
    void refresh().catch(e => setError(e.message));
    void prepApi<typeof capabilities>('/capabilities').then(setCapabilities).catch(() => {});
    const source = new EventSource(`/api/interview-prep/projects/${id}/events`);
    source.addEventListener('snapshot', event => { apply(JSON.parse((event as MessageEvent).data)); setConnected(true); });
    source.addEventListener('unavailable', () => { source.close(); setConnected(false); setError('项目已删除或登录失效，请返回首页重新登录'); });
    source.onerror = () => setConnected(false);
    return () => { source.close(); window.speechSynthesis?.cancel(); };
  }, [id]);
  const practice = project?.practices.find(p => p.id === selected) || project?.practices[0];
  useEffect(() => { if (project?.practices.length && !selected) setSelected(project.practices[0].id); }, [project?.practices.length]);
  const act = async (body: Record<string, unknown>) => {
    if (!current.current || busy) return undefined;
    setBusy(true); setError('');
    try {
      const result = await prepApi<Preparation>(`/projects/${id}/commands`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: current.current.revision, request_id: crypto.randomUUID(), ...body }) });
      apply(result); return result;
    } catch (e) { setError((e as Error).message); await refresh().catch(() => undefined); return undefined; }
    finally { setBusy(false); }
  };
  const leave = (action: () => void) => { if (captureBusy) { setError('请先暂停录音，等待片段保存到本机后再离开'); return; } if ((draftDirty || materialDirty || questionDirty) && !confirm('还有未保存的目标或材料，确定离开吗？')) return; action(); };
  const changeTab = (next: typeof tab) => leave(() => setTab(next));
  const startPractice = async (source?: string, questionId?:string) => {
    const result = await act({ action: questionId ? 'practice_question' : source ? 'retry_practice' : 'start', practice_id: source, question_id:questionId });
    if (result) { setSelected(result.practices[0].id); setTab('practice'); }
  };
  const exportPractice = () => {
    if (!practice || !project) return;
    const lines = [`# ${project.title}`, `练习时间：${new Date(practice.created_at * 1000).toLocaleString('zh-CN')}`, `模式：${practice.config.practice_mode === 'simulation' ? '模拟' : '辅导'}`, '\n> AI 练习反馈，需结合原音审阅。'];
    practice.turns.forEach(t => {
      lines.push(`\n## ${t.position + 1}. ${t.question}`);
      t.answers.filter(a => a.status === 'ready').forEach(a => lines.push(a.transcript));
      (t.feedback || []).forEach(f => lines.push(`\n- ${f.dimension}：${f.observation}\n  原话：${f.quote}\n  建议：${f.suggestion}`));
    });
    if (practice.review) lines.push(`\n## 整场复盘${practice.review_stale ? '（原文已更新，需重生成）' : ''}\n${practice.review.summary}`, ...practice.review.findings.map(f => `- ${f.observation}\n  原话：${f.quote}\n  建议：${f.suggestion}`));
    if(practice.candidate_review){const c=practice.candidate_review;lines.push('\n## 最后反问点评',c.summary,...c.improvements.map(v=>'- '+v),...c.recommended_questions.map(q=>`- ${q.question}\n  原因：${q.why}\n  时机：${q.timing}`));}
    download(`${project.title}-练习复盘.md`, lines.join('\n\n'));
  };
  if (!project) return <main className="prep-loading"><button onClick={onHome}>返回首页</button><p role="status">{error || '正在读取面试准备…'}</p></main>;
  const steps = ['对话准备','录音面试','复盘与重练'];
  const stage = practice?.status === 'done' ? 2 : practice ? 1 : 0;
  return <div className="prep-shell">
    <header className="prep-top"><button className="text-button" onClick={() => leave(onHome)}><ArrowLeft/>体察</button><div><small>面试与答辩 · 个人准备</small><strong>{project.title}</strong></div><span className="prep-save">{captureBusy ? '录音中 / 本机保存中' : !connected ? '连接中断，正在重连' : busy ? '保存中' : draftDirty || materialDirty || questionDirty ? '修改待保存' : '已保存'}</span><button className="secondary" onClick={() => leave(onHistory)}>我的项目</button></header>
    <nav className="prep-steps" aria-label="准备流程">{steps.map((s,i) => <button key={s} className={i === stage ? 'current' : i < stage ? 'complete' : ''} onClick={() => changeTab(i === 0 ? 'prepare' : i === 1 ? 'practice' : 'review')}><span>{i < stage ? <Check size={13}/> : i+1}</span>{s}</button>)}</nav>
    <div className="prep-body"><aside className="prep-nav">{([['prepare','准备与材料'],['practice','录音练习'],['review','复盘与重练'],['history','练习记录']] as const).map(([key,label]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => changeTab(key)}>{label}</button>)}<p>先把经历讲清楚，再面对真正的提问。</p><small>{capabilities.local_mode ? '本机单人工作空间' : '个人账号工作空间'}</small></aside>
    <main className="prep-main">
      {error && <div className="prep-error" role="alert">{error}<button aria-label="关闭错误" onClick={() => setError('')}><X size={16}/></button></div>}
      {!capabilities.model_configured && <div className="prep-notice">可先保存目标与材料。生成方案、追问和复盘前，请在后端配置 KIMI_API_KEY 并启动 Worker。</div>}
      {tab === 'prepare' && <><PreparationForm key={project.id} project={project} busy={busy} act={act} onDirty={setDraftDirty} onReady={setSetupReady}/>{setupReady && <details className="prep-optional-materials"><summary>补充简历、岗位资料或论文（可选）{project.materials.length ? ` · 已保存 ${project.materials.length} 份` : ''}</summary><Materials project={project} busy={busy} act={act} onDirty={setMaterialDirty} onUpload={async (file, kind) => { setBusy(true); setError(''); try { const form = new FormData(); form.append('file',file); form.append('kind',kind); form.append('revision',String(current.current!.revision)); form.append('request_id',crypto.randomUUID()); apply(await prepApi<Preparation>(`/projects/${id}/materials`, { method:'POST',body:form })); } catch(e) { setError((e as Error).message); } finally { setBusy(false); } }}/></details>}
        {setupReady && <section className="prep-card"><div className="prep-heading"><h2>练习计划</h2><button className="primary" disabled={busy || active(project.job) || !project.config.goal.trim() || draftDirty || materialDirty || !capabilities.model_configured} onClick={() => void act({ action:'prepare' })}>{project.plan ? '重新生成方案' : '生成准备方案'}</button></div><JobStatus job={project.job}/>{project.plan_stale && project.plan && <p className="prep-notice">准备材料或目标已更新，请重新生成方案。已有练习仍使用当时的材料版本。</p>}{project.plan ? <><MarkdownContent text={project.plan.summary}/><ul>{project.plan.preparation_tips.map((t,i) => <li key={i}>{t}</li>)}</ul>{!!project.plan.company_and_role_focus?.length&&<><h3>公司与岗位可能考查的重点</h3><ul>{project.plan.company_and_role_focus.map((item,i)=><li key={i}>{item}</li>)}</ul></>}<ol>{project.plan.questions.map((q,i) => <li key={i}><strong>{q.question}</strong><small>{q.target}</small></li>)}</ol>{!!project.plan.candidate_question_suggestions?.length&&<><h3>最后反问的准备建议</h3><QuestionSuggestions questions={project.plan.candidate_question_suggestions}/></>}<button className="primary" disabled={busy || !!project.plan_stale || !capabilities.transcription_configured || project.practices.some(p => p.status === 'active')} onClick={() => void startPractice()}><Microphone/>开始录音练习</button>{!capabilities.transcription_configured && <p>录音转写需要配置 DASHSCOPE_API_KEY。</p>}</> : <p className="muted">已了解你的目标。可以直接生成练习计划，也可以先补充材料，让提问更贴近你的经历。</p>}</section>}</>}
      {tab === 'practice' && (practice ? <MockInterview key={practice.id} practice={practice} busy={busy} act={act} onBusy={setCaptureBusy} onReview={() => setTab('review')}/> : <Empty title="还没有开始练习" text="先添加准备材料并生成练习计划。" onClick={() => setTab('prepare')}/>)}
      {tab === 'review' && (practice ? <><section className="prep-card"><div className="prep-heading"><h1>回答复盘</h1><button className="secondary" onClick={exportPractice}><DownloadSimple/>导出 Markdown</button></div><JobStatus job={practice.job}/>{practice.status !== 'done' ? <p>请先结束当前练习；模拟模式在结束前不展示反馈。</p> : <><button className="primary" disabled={busy || active(practice.job) || !practice.turns.some(t => t.answers.some(a => a.status === 'ready'))} onClick={() => void act({ action:'review',practice_id:practice.id })}>{practice.review ? '重新生成复盘' : '生成整场复盘'}</button>{practice.review_stale && <p className="prep-notice">转写已校对，旧复盘需要重新生成。</p>}{practice.review && <><MarkdownContent text={practice.review.summary}/>{(practice.review.candidate_questions_review||practice.candidate_review)&&<CandidateQuestionsFeedback review={(practice.review.candidate_questions_review||practice.candidate_review)!}/>}<Findings findings={practice.review.findings} practice={practice}/><h3>专项重练</h3><ol>{practice.review.retry_questions.map((q,i) => <li key={i}>{q.question}</li>)}</ol><button className="primary" disabled={busy || !!practice.review_stale || project.practices.some(p => p.status === 'active')} onClick={() => void startPractice(practice.id)}>结合复盘与补充问题，再练一轮</button></>}</>}</section>{practice.status === 'done' && <HistoricalQuestions key={practice.id} project={project} busy={busy} act={act} onDirty={setQuestionDirty} onPractice={qid=>startPractice(undefined,qid)}/>}{practice.turns.map(t => <section className="prep-card" key={t.id}><h3>{t.position+1}. {t.question}</h3>{t.answers.filter(a => a.status === 'ready').map(a => <AnswerReview key={a.id} answer={a} editable={practice.status === 'done'} busy={busy} onSave={content => act({ action:'correct',practice_id:practice.id,answer_id:a.id,content })}/>)}{t.feedback && <Findings findings={t.feedback} practice={practice}/>}</section>)}</> : <Empty title="先完成一次练习" text="复盘会关联你的真实回答和录音。" onClick={() => setTab('prepare')}/>)}
      {tab === 'history' && <section className="prep-card"><h1>练习记录</h1><PracticeComparison practices={project.practices}/><p>每次练习保留当时的材料、计划与模式；同题可在复盘页对照回听。</p>{project.practices.map((p,i) => <button className="prep-history-row" key={p.id} onClick={() => { setSelected(p.id); setTab(p.status === 'active' ? 'practice' : 'review'); }}><span>第 {project.practices.length-i} 次{p.source_practice_id ? ' · 专项重练' : ''}<small>{new Date(p.created_at*1000).toLocaleString('zh-CN')} · {p.config.practice_mode === 'simulation' ? '模拟' : '辅导'}</small></span><span>{p.status === 'done' ? '已结束' : '继续练习'} →</span></button>)}{!project.practices.length && <p>还没有练习记录。</p>}</section>}
    </main></div>
  </div>;
}

type Act = (body: Record<string,unknown>) => Promise<Preparation | undefined>;
function PreparationForm({project,busy,act,onDirty,onReady}:{project:Preparation;busy:boolean;act:Act;onDirty:(dirty:boolean)=>void;onReady:(ready:boolean)=>void}) {
  const stored = project.config.preparation_step || (project.plan || project.materials.length ? 'ready' : 'goal');
  const [editing,setEditing] = useState(false);
  const genericGoal = /^(准备一场求职面试，围绕我的简历进行录音模拟|面试准备|求职面试|模拟面试)$/.test(project.config.goal.trim());
  const step = editing ? 'goal' : stored === 'goal' ? project.plan ? 'ready' : !genericGoal && project.config.goal.trim().length >= 12 ? 'background' : 'goal' : stored;
  const [text,setText] = useState('');
  const [customClosing,setCustomClosing] = useState(false);
  useEffect(()=>{onDirty(!!text.trim());return()=>onDirty(false);},[text,onDirty]);
  useEffect(()=>{onReady(step==='ready');},[step,onReady]);
  const save = async (patch:Partial<Config>) => {
    const result = await act({action:'configure',config:{...project.config,...patch,followup_mode:'adaptive'}});
    if(result){setText('');setEditing(false);setCustomClosing(false);}
  };
  const goal = async () => {
    const value=text.trim(); if(!value)return;
    const preferences=interviewPreferences(value);
    const template=preferences.template || project.config.template;
    await save({goal:value,...preferences,template,
      preparation_step:project.materials.length || project.config.company_background || project.config.job_requirements ? template==='job'?'closing':'ready' : 'background'});
  };
  const background = () => save({...(text.trim()?{company_background:text.trim()}:{}),preparation_step:project.config.template==='job'?'closing':'ready'});
  return <section className="prep-card prep-conversation"><h1>一起准备这场面试</h1><p>说说你的目标，我会一步步带你准备。只确认会影响这次练习的事情。</p>
    <div className="prep-dialogue" aria-live="polite">
      <div className="prep-chat-agent">{step==='goal'?'这次要准备什么面试？告诉我应聘方向或答辩主题，以及你最想练好的地方。有公司、岗位或轮次信息，也可以一起说。':'收到，这次就围绕这个目标准备。'}</div>
      {step!=='goal'&&<div className="prep-chat-user">{project.config.goal}</div>}
      {step==='goal'&&<form onSubmit={e=>{e.preventDefault();void goal();}}><textarea aria-label="告诉我面试目标" autoFocus rows={3} maxLength={2000} disabled={busy} placeholder="例如：准备产品经理的业务面，想练习怎么讲项目贡献，面试官可能会追问结果。" value={text} onChange={e=>setText(e.target.value)}/><button className="primary" disabled={busy||!text.trim()} type="submit">继续</button></form>}
      {step==='background'&&<><div className="prep-chat-agent">还有什么背景能帮我更了解这次面试？比如你的经历、岗位要求或论文内容。直接粘贴即可；也可以先跳过，稍后上传文件。</div><textarea aria-label="补充面试背景" rows={4} maxLength={6000} disabled={busy} value={text} onChange={e=>setText(e.target.value)} placeholder="把已知信息放在这里，不需要逐项填写。"/><div className="prep-inline"><button className="primary" disabled={busy||!text.trim()} onClick={()=>void background()}>补充并继续</button><button className="text-button" disabled={busy} onClick={()=>void save({preparation_step:project.config.template==='job'?'closing':'ready'})}>先跳过</button></div></>}
      {(step==='closing'||step==='ready')&&project.config.company_background&&<div className="prep-chat-user">{project.config.company_background}</div>}
      {step==='closing'&&<><div className="prep-chat-agent">要一起练习最后向面试官反问的环节吗？我会结合你的提问，帮你判断问得是否合适、还能怎么问。</div><div className="prep-inline"><button className="primary" disabled={busy} onClick={()=>void save({include_candidate_questions:true,preparation_step:'ready'})}>一起练习反问</button><button className="secondary" disabled={busy} onClick={()=>void save({include_candidate_questions:false,preparation_step:'ready'})}>这次先不练</button><button className="text-button" disabled={busy} onClick={()=>setCustomClosing(true)}>我想补充自己的要求</button></div>{customClosing&&<><textarea aria-label="反问环节的要求" maxLength={1000} rows={2} disabled={busy} value={text} onChange={e=>setText(e.target.value)} placeholder="例如：想重点练习问团队协作和入职目标。"/><button className="primary" disabled={busy||!text.trim()} onClick={()=>void save({goal:(project.config.goal+'；反问要求：'+text.trim()).slice(0,2000),include_candidate_questions:true,preparation_step:'ready'})}>按这个要求练习反问</button></>}</>}
      {step==='ready'&&<><div className="prep-chat-agent">准备好了。我会围绕你的目标安排问题，根据每次回答决定是否继续追问。{project.config.template==='job'?(project.config.include_candidate_questions?'最后会练习反问。':'这次先不练习反问。'):''}录音后先核对转写，再继续下一题。</div><small>本轮沿用{project.config.language} · 约 {project.config.duration_minutes} 分钟 · {project.config.practice_mode==='simulation'?'结束后统一复盘':'每题反馈'}。需要调整时，直接在目标中说明。</small><button className="text-button" disabled={busy} onClick={()=>{setText(project.config.goal);setEditing(true);}}>补充或调整这次的要求</button></>}
    </div></section>;
}

function Materials({project,busy,act,onUpload,onDirty}:{project:Preparation;busy:boolean;act:Act;onDirty:(dirty:boolean)=>void;onUpload:(file:File,kind:string)=>Promise<void>}) {
  const kind = 'background'; const [text,setText] = useState(''); const [open,setOpen] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const [editing,setEditing] = useState(''); const [revisionText,setRevisionText] = useState('');
  useEffect(()=>{onDirty(!!text.trim()||!!editing);return()=>onDirty(false);},[text,editing,onDirty]);
  return <section className="prep-card"><h2>准备材料</h2><p>材料是提问背景，不会被当成你已经说过的回答。单份最多 5 万字，项目合计 10 万字。</p><div className="prep-inline"><button className="secondary" disabled={busy} onClick={()=>input.current?.click()}><Plus/>上传文件</button><input ref={input} hidden type="file" accept=".txt,.md,.docx,.pdf,.pptx" onChange={e=>{ const file=e.target.files?.[0]; if(file) void onUpload(file,kind); e.target.value=''; }}/></div><textarea aria-label="材料正文" disabled={busy} rows={6} maxLength={50000} placeholder="粘贴真实简历、岗位要求或论文内容……" value={text} onChange={e=>setText(e.target.value)}/><button className="secondary" disabled={busy || !text.trim()} onClick={async()=>{ if(await act({action:'material',title:text.trim().split('\n')[0].slice(0,40),kind,content:text})){setText('');} }}><FileText/>保存材料</button><small>支持 TXT、MD、DOCX、文本 PDF、PPTX。扫描 PDF 需先 OCR。图片与图表不参与本版分析。</small><div className="prep-material-list">{project.materials.map(m=><details key={m.id} open={open===m.id} onToggle={e=>{if(e.currentTarget.open)setOpen(m.id);}}><summary><span>{m.title} <small>v{m.version} · {m.content.length} 字</small></span><button className="prep-material-delete" aria-label={`删除材料：${m.title}`} disabled={busy} onClick={async e=>{e.preventDefault();e.stopPropagation();if(!confirm(`删除“${m.title}”？需要重新生成准备方案；已有练习保留当时的材料快照。`))return;if(await act({action:'delete_material',material_id:m.id})){if(editing===m.id)setEditing('');if(open===m.id)setOpen('');}}}>删除</button></summary>{editing===m.id?<><textarea aria-label="修订材料正文" rows={8} maxLength={50000} value={revisionText} onChange={e=>setRevisionText(e.target.value)}/><button className="secondary" disabled={busy||!revisionText.trim()} onClick={async()=>{if(await act({action:'revise_material',material_id:m.id,content:revisionText}))setEditing('');}}>保存材料新版本</button><button className="text-button" onClick={()=>setEditing('')}>取消修订</button></>:<><pre>{m.content}</pre><button className="text-button" onClick={()=>{setEditing(m.id);setRevisionText(m.content);}}>修订材料</button></>}</details>)}</div></section>;
}
function JobStatus({job}:{job:Job}) { if(!job)return null; return <p role="status" className={job.status==='failed'?'prep-error':'prep-job'}>{job.status==='queued'?'已排队，等待后台 Worker':job.status==='running'?job.stage:job.status==='failed'?job.error:job.status==='cancelled'?'任务已取消，保存内容保留':null}</p>; }
function Empty({title,text,onClick}:{title:string;text:string;onClick:()=>void}) {return <section className="prep-card prep-empty"><Microphone size={42}/><h2>{title}</h2><p>{text}</p><button className="primary" onClick={onClick}>查看准备</button></section>;}

function MockInterview({practice,busy,act,onBusy,onReview}:{practice:Practice;busy:boolean;act:Act;onBusy:(b:boolean)=>void;onReview:()=>void}) {
  const turn=practice.turns.find(t=>t.status==='waiting');
  const answer=turn?.answers.find(a=>a.status==='recording'||a.status==='confirming');
  const [consent,setConsent]=useState(false);
  const previous=[...practice.turns].reverse().find(t=>t.feedback?.length||t.feedback_notice);
  return <><section className="prep-card"><div className="prep-heading"><div><small>{practice.config.practice_mode==='simulation'?'模拟模式 · 结束后统一反馈':'辅导模式 · 每题反馈'}</small><h1>{practice.status==='done'?'本轮练习已结束':'模拟面试'}</h1></div><span>{practice.turns.filter(t=>t.status==='answered').length} 轮已回答</span></div><JobStatus job={practice.job}/>{active(practice.job)&&<button className="text-button" disabled={busy} onClick={()=>void act({action:'cancel',practice_id:practice.id})}>取消当前生成</button>}{practice.status==='done'?<button className="primary" onClick={onReview}>查看回答与复盘</button>:turn?<><div className="prep-question"><small>{turn.turn_kind==='candidate_questions'?'最后反问':turn.parent_id?'深入追问':'计划问题'} · {turn.target}</small><h2>{turn.question}</h2>{turn.turn_kind==='candidate_questions'?<p>请说出你真正想向公司了解的问题。系统会点评提问质量并建议怎么问，不代表公司作答。</p>:turn.decision_reason&&<p className="prep-followup-reason">为什么继续问：{turn.decision_reason}</p>}<button className="text-button" disabled={!!answer} onClick={()=>{if(!window.speechSynthesis)return;speechSynthesis.cancel();const speech=new SpeechSynthesisUtterance(turn.question);speech.lang=practice.config.language==='English'?'en-US':'zh-CN';speechSynthesis.speak(speech);}}><Play size={16}/>朗读问题</button></div>{answer?.status==='confirming'?<TranscriptConfirmation key={answer.id} answer={answer} busy={busy} onConfirm={content=>act({action:'confirm_answer',practice_id:practice.id,answer_id:answer.id,content})} onDiscard={()=>act({action:'discard_answer',practice_id:practice.id,answer_id:answer.id})}/>:answer?<AnswerRecorder key={answer.id} answer={answer} busy={busy} onBusy={onBusy} onComplete={chunk_ids=>act({action:'prepare_answer',practice_id:practice.id,answer_id:answer.id,chunk_ids})} onDiscard={()=>act({action:'discard_answer',practice_id:practice.id,answer_id:answer.id})}/>:<><label className="prep-consent"><input type="checkbox" checked={consent} onChange={e=>setConsent(e.target.checked)}/>我同意录音及云端转写、分析我的练习内容</label><button className="primary" disabled={!consent||busy||active(practice.job)} onClick={()=>void act({action:'answer',practice_id:practice.id,turn_id:turn.id})}><Microphone/>准备录音回答</button></>}</>:<div><p>回答已保存。{practice.job?.status==='failed'?'下一步生成失败，可以重试。':'等待提问完成，或继续生成下一步。'}</p><button className="primary" disabled={busy||active(practice.job)} onClick={()=>void act({action:'next',practice_id:practice.id})}><ArrowClockwise/>继续 / 重试下一题</button></div>}{practice.status==='active'&&!answer&&practice.config.template==='job'&&!practice.turns.some(t=>t.turn_kind==='candidate_questions')&&<button className="secondary" disabled={busy} onClick={()=>void act({action:'candidate_questions',practice_id:practice.id})}>进入最后反问</button>}{practice.status==='active'&&!answer&&<button className="text-button" disabled={busy} onClick={async()=>{if(await act({action:'finish',practice_id:practice.id}))onReview();}}>结束本轮练习</button>}</section>{practice.candidate_review&&<section className="prep-card"><CandidateQuestionsFeedback review={practice.candidate_review}/></section>}{practice.turns.some(t=>t.answers.some(a=>a.status==='ready'))&&<section className="prep-card prep-submitted"><h2>已提交的回答与转写</h2>{practice.turns.filter(t=>t.answers.some(a=>a.status==='ready')).map(t=><section key={t.id}><h3>{t.position+1}. {t.question}</h3>{t.answers.filter(a=>a.status==='ready').map(a=><AnswerReview key={a.id} answer={a} editable={false} busy={busy} onSave={async()=>undefined}/>)}</section>)}</section>}{previous&&<section className="prep-card"><h2>上一题反馈</h2>{previous.feedback_notice&&<p className="prep-notice">{previous.feedback_notice}</p>}{!!previous.feedback?.length&&<Findings findings={previous.feedback} practice={practice}/>}</section>}</>;
}
function AnswerRecorder({answer,busy,onBusy,onComplete,onDiscard}:{answer:Answer;busy:boolean;onBusy:(b:boolean)=>void;onComplete:(ids:string[])=>Promise<Preparation|undefined>;onDiscard:()=>Promise<Preparation|undefined>}) {
  const rec=useAnswerRecorder(answer.id,answer.session_id,answer.chunk_ids);
  const [sending,setSending]=useState(false);const [error,setError]=useState('');
  useEffect(()=>{onBusy(rec.recording||rec.unsaved);return()=>onBusy(false);},[rec.recording,rec.unsaved,onBusy]);
  const complete=async()=>{setSending(true);setError('');try{const ids=await rec.flush();await onComplete(ids);}catch(e){setError((e as Error).message);}finally{setSending(false);}};
  return <div className="prep-recorder"><div className="prep-timer"><span className={rec.recording?'record-dot live':'record-dot'}/>{clock(rec.elapsed)}</div><p>{rec.recording?'正在录音，每 10 秒保存分片':rec.pending?'录音保留在本机，等待上传与转写':'准备好后开始回答，可以暂停后继续'}</p>{(rec.error||error)&&<p role="alert" className="prep-error">{error||rec.error}</p>}<div className="prep-inline"><button className="primary" disabled={busy||sending} onClick={()=>void (rec.recording?rec.stop():rec.start()).catch(e=>setError(e.message))}>{rec.recording?<Pause/>:<Microphone/>}{rec.recording?'暂停录音':'开始 / 继续录音'}</button><button className="secondary" disabled={busy||sending} onClick={()=>void complete()}>{sending?'正在保存、上传与转写…':'回答完成'}</button><button className="text-button" disabled={busy||sending||rec.uploading} onClick={()=>void rec.retry().catch(e=>setError(e.message))}>重试上传（{rec.pending}）</button></div><section className="prep-live-transcript" aria-live="polite"><h3>本题实时转写</h3>{answer.segments.length?answer.segments.map(s=><p key={s.id}>{s.text}</p>):<p>语音分片上传后会显示文字；回答完成后，可回听、校对，再确认提交。</p>}</section><button className="text-button" disabled={busy||sending} onClick={async()=>{if(!confirm('放弃这次未提交回答？已上传录音仍保留在本机服务数据目录。'))return;setSending(true);try{await rec.stop();if(await onDiscard())await rec.discard();}catch(e){setError((e as Error).message);}finally{setSending(false);}}}>放弃本次回答并重录</button></div>;
}
function TranscriptConfirmation({answer,busy,onConfirm,onDiscard}:{answer:Answer;busy:boolean;onConfirm:(content:string)=>Promise<unknown>;onDiscard:()=>Promise<unknown>}) {
  const [text,setText]=useState(answer.transcript);
  return <section className="prep-confirm-transcript"><h2>核对本题语音转文字</h2><p>录音与识别稿已保存。请回听并核对文字，识别有误可以直接修改。确认后才会生成追问或进入下一题。</p><audio controls preload="metadata" src={`/api/interview-prep/answers/${answer.id}/recording`}/><label className="prep-label">本题回答转写<textarea aria-label="本题回答转写" rows={7} maxLength={20000} value={text} onChange={e=>setText(e.target.value)}/></label>{text!==answer.original_transcript&&<details><summary>查看原始识别稿</summary><p className="prep-transcript">{answer.original_transcript}</p></details>}<div className="prep-inline"><button className="primary" disabled={busy||!text.trim()} onClick={()=>void onConfirm(text)}>确认转写并继续</button><button className="secondary" disabled={busy} onClick={()=>{if(confirm('放弃本次回答并重新录制？已保存的录音仍保留在本机。'))void onDiscard();}}>重新录制本题</button></div></section>;
}
function AnswerReview({answer,editable,busy,onSave}:{answer:Answer;editable:boolean;busy:boolean;onSave:(text:string)=>Promise<unknown>}) {
  const [text,setText]=useState(answer.transcript);const [editing,setEditing]=useState(false);
  return <div className="prep-answer"><audio controls preload="none" src={`/api/interview-prep/answers/${answer.id}/recording`}/><small>答案 v{answer.version} · 原音约 {Math.round(Math.max(...answer.segments.map(s=>s.end_ms),0)/1000)} 秒 · 转写可能有误，可结合原音校对</small>{editing?<><textarea rows={6} maxLength={20000} value={text} onChange={e=>setText(e.target.value)}/><button className="secondary" disabled={busy||!text.trim()} onClick={async()=>{await onSave(text);setEditing(false);}}>保存校对稿</button><button className="text-button" onClick={()=>setEditing(false)}>取消</button></>:<><p className="prep-transcript">{answer.transcript}</p>{editable&&<button className="text-button" onClick={()=>{setText(answer.transcript);setEditing(true);}}>校对转写</button>}</>}{answer.version>1&&<details><summary>原始识别稿</summary><p>{answer.original_transcript}</p></details>}</div>;
}
function Findings({findings,practice}:{findings:Finding[];practice:Practice}) {
  const [playing,setPlaying]=useState('');
  const audio=useRef<HTMLAudioElement>(null);
  const all=practice.turns.flatMap(t=>t.answers);
  const evidence=all.find(a=>a.id===playing.split(':')[0]);
  const selected=findings.find(f=>`${f.answer_id}:${findings.indexOf(f)}`===playing);
  const end=selected&&evidence?Math.max(...evidence.segments.filter(s=>selected.segment_ids.includes(s.id)).map(s=>s.end_ms))/1000:0;
  return <div className="prep-findings">{findings.map((f,i)=>{const a=all.find(a=>a.id===f.answer_id);const segments=a?.segments.filter(s=>f.segment_ids.includes(s.id))||[];return <article key={i}><strong>{f.dimension}</strong><p>{f.observation}</p><blockquote>{f.quote}</blockquote><p><b>下一次：</b>{f.suggestion}</p><button className="text-button" onClick={()=>{setPlaying(`${f.answer_id}:${i}`);}}><Play size={15}/>{segments.length?`回听 ${clock(Math.min(...segments.map(s=>s.start_ms))/1000)}–${clock(Math.max(...segments.map(s=>s.end_ms))/1000)}`:'回听本题原音（按全文核对）'}</button></article>;})}{evidence&&<audio key={playing} ref={audio} controls autoPlay src={`/api/interview-prep/answers/${evidence.id}/recording`} onLoadedMetadata={()=>{const segments=evidence.segments.filter(s=>selected?.segment_ids.includes(s.id));if(audio.current&&segments.length)audio.current.currentTime=Math.min(...segments.map(s=>s.start_ms))/1000;}} onTimeUpdate={()=>{if(end&&audio.current&&audio.current.currentTime>=end)audio.current.pause();}}/>}</div>;
}

function PracticeComparison({practices}:{practices:Practice[]}) {
  const [question,setQuestion]=useState('');
  const groups=new Map<string,{practice:Practice;turn:Turn;answer:Answer}[]>();
  for(const practice of practices)for(const turn of practice.turns)for(const answer of turn.answers.filter(a=>a.status==='ready')){
    const key=turn.question.trim();const rows=groups.get(key)||[];rows.push({practice,turn,answer});groups.set(key,rows);
  }
  const repeated=[...groups.entries()].filter(([,rows])=>rows.length>1);
  const current=repeated.find(([key])=>key===question)||repeated[0];
  if(!current)return <p className="muted">同一道问题完成两次回答后，这里会出现原文与原音对比。不同问题不直接比较分数。</p>;
  return <section className="prep-comparison"><h2>同题练习对比</h2><select aria-label="选择对比问题" value={current[0]} onChange={e=>setQuestion(e.target.value)}>{repeated.map(([q])=><option key={q}>{q}</option>)}</select><div className="prep-compare-grid">{current[1].map(row=><article key={row.answer.id}><small>{new Date(row.practice.created_at*1000).toLocaleString('zh-CN')} · {row.practice.config.practice_mode==='simulation'?'模拟':'辅导'}</small><p className="prep-transcript">{row.answer.transcript}</p><audio controls preload="none" src={`/api/interview-prep/answers/${row.answer.id}/recording`}/><small>{Math.round(Math.max(...row.answer.segments.map(s=>s.end_ms),0)/1000)} 秒 · 转写 v{row.answer.version}</small></article>)}</div></section>;
}

function QuestionSuggestions({questions}:{questions:SuggestedQuestion[]}) {
  return <div className="prep-question-suggestions">{questions.map((q,i)=><article key={i}><strong>{q.question}</strong><p>{q.why}</p><small>适合时机：{q.timing}</small></article>)}</div>;
}
function CandidateQuestionsFeedback({review}:{review:CandidateReviewData}) {
  return <section className="prep-candidate-feedback"><h2>最后反问点评</h2><MarkdownContent text={review.summary}/>{!!review.strengths.length&&<><h3>问得好的地方</h3><ul>{review.strengths.map((item,i)=><li key={i}>{item}</li>)}</ul></>}<h3>可以改进的地方</h3><ul>{review.improvements.map((item,i)=><li key={i}>{item}</li>)}</ul><h3>结合公司与岗位，还可以这样问</h3><QuestionSuggestions questions={review.recommended_questions}/><small>建议是练习参考，不代表公司已确认的情况；根据面试轮次、对方职责及已聊过的内容选择。</small></section>;
}
function HistoricalQuestions({project,busy,act,onPractice,onDirty}:{project:Preparation;busy:boolean;act:Act;onPractice:(id:string)=>Promise<void>;onDirty:(dirty:boolean)=>void}) {
  const [expanded,setExpanded]=useState(false);
  const [question,setQuestion]=useState('');const [previous,setPrevious]=useState('');const [context,setContext]=useState('');
  useEffect(()=>{onDirty(!!question.trim()||!!previous.trim()||!!context.trim());return()=>onDirty(false);},[question,previous,context,onDirty]);
  return <section className="prep-card"><h2>还有以前被问过、想再练一次的问题吗？</h2><p>现在想起的真实面试问题，可以补充进来。下一轮会一起问，也可以直接录音重答。</p>{!expanded?<div className="prep-inline"><button className="secondary" onClick={()=>setExpanded(true)}>有，我来补充</button><span className="muted">没有也可以直接继续复盘。</span></div>:<><label className="prep-label">历史面试问题<textarea aria-label="历史面试问题" disabled={busy} maxLength={500} rows={2} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="例如：你为什么选择我们公司？"/></label><details><summary>补充上次的回答与背景（可选）</summary><label className="prep-label">上次怎么回答的（可选）<textarea aria-label="上次回答" disabled={busy} maxLength={10000} rows={3} value={previous} onChange={e=>setPrevious(e.target.value)} placeholder="可以粘贴上次回答或凭记忆记录；不作为本次录音回答"/></label><label className="prep-label">当时的背景（可选）<input aria-label="历史问题背景" disabled={busy} maxLength={2000} value={context} onChange={e=>setContext(e.target.value)} placeholder="哪家公司、岗位、轮次，或面试官继续追问了什么"/></label></details><button className="secondary" disabled={busy||!question.trim()} onClick={async()=>{if(await act({action:'add_question',question,previous_answer:previous,question_context:context})){setQuestion('');setPrevious('');setContext('');}}}>加入下一轮的问题</button></>}<div className="prep-history-questions">{(project.question_bank||[]).map(q=><article key={q.id}><h3>{q.question}</h3>{q.context&&<small>{q.context}</small>}{q.previous_answer&&<details><summary>上次的回答</summary><p className="prep-transcript">{q.previous_answer}</p></details>}<div className="prep-inline"><button className="secondary" disabled={busy||active(project.job)} onClick={()=>void act({action:'analyze_question',question_id:q.id})}>{q.analysis?'重新分析这道题':'分析这道题'}</button><button className="primary" disabled={busy||project.practices.some(p=>p.status==='active')} onClick={()=>void onPractice(q.id)}>重新录音回答这道题</button><button className="text-button" disabled={busy} onClick={()=>{if(confirm('删除这道历史问题？已有练习仍保留当时的题目。'))void act({action:'delete_question',question_id:q.id});}}>删除问题</button></div><JobStatus job={q.job}/>{q.analysis_stale&&q.analysis&&<p className="prep-notice">公司、岗位或准备材料已变更，建议重新分析。</p>}{q.analysis&&<div className="prep-question-analysis"><h4>这道题想考查什么</h4><p>{q.analysis.intent}</p>{([['回答结构',q.analysis.answer_outline],['对上次回答的分析',q.analysis.previous_answer_feedback],['可能继续追问',q.analysis.followup_angles],['再次回答前的准备',q.analysis.preparation_actions],['还需要补充',q.analysis.missing_information]] as [string,string[]][]).filter(([,items])=>items.length>0).map(([label,items])=><section key={label}><h4>{label}</h4><ul>{items.map((item,i)=><li key={i}>{item}</li>)}</ul></section>)}</div>}</article>)}</div><small>补充的问题会带入复盘后的下一轮，也可随时逐题重练。</small></section>;
}
