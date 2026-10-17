import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, FileText, FolderSimple, Microphone } from '@phosphor-icons/react';
import { WorkspaceShell } from '../WorkspaceShell';
import { ResearchMap } from '../ResearchMap';
import { AssistantPanel, type AssistantTab } from '../AssistantPanel';
import { MarkdownContent } from '../MarkdownContent';
import { api, download, type Layout, type Project } from '../types';
import { requestId } from '../requestId';
import { interviewScenario } from '../sceneRegistry';
import { active, Materials, JobStatus, MockInterview, AnswerReview, Findings, PracticeComparison, CandidateQuestionsFeedback, HistoricalQuestions, QuestionSuggestions, type Preparation, type Config, type Practice, type TranscriptDirty } from './InterviewPanels';
import './workspace.css';

type Props = { project: Project; busy: boolean; command: (body: Record<string, unknown>) => Promise<Project | undefined>; onLayout: (positions: Layout['positions']) => void; onHome: () => void; onHistory: () => void; onProjectUpdated: (project: Project) => void };

export function InterviewWorkspace(props: Props) {
  const { project } = props;
  const [view, setView] = useState('flow');
  const [step, setStep] = useState(project.stage);
  const [tab, setTab] = useState<AssistantTab>('chat');
  const [chatVisible, setChatVisible] = useState(true);
  const [menu, setMenu] = useState(false);
  const [data, setData] = useState<Preparation | null>(null);
  const current = useRef<Preparation | null>(null);
  const mounted = useRef(true);
  const [selected, setSelected] = useState('');
  const [captureBusy, setCaptureBusy] = useState(false);
  const [drafts, setDrafts] = useState({ setup: false, materials: false, questions: false });
  const [transcriptDrafts, setTranscriptDrafts] = useState<Record<string, boolean>>({});
  const transcriptDirty = Object.values(transcriptDrafts).some(Boolean);
  const dirty = Object.values(drafts).some(Boolean) || transcriptDirty;
  const onTranscriptDirty = useCallback<TranscriptDirty>((id, value) => setTranscriptDrafts(old => !!old[id] === value ? old : { ...old, [id]: value }), []);
  const setDirty = () => setDrafts({ setup: false, materials: false, questions: false });
  const setupDirty = useCallback((value: boolean) => setDrafts(old => old.setup === value ? old : { ...old, setup: value }), []);
  const materialsDirty = useCallback((value: boolean) => setDrafts(old => old.materials === value ? old : { ...old, materials: value }), []);
  const questionsDirty = useCallback((value: boolean) => setDrafts(old => old.questions === value ? old : { ...old, questions: value }), []);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const uploadLock = useRef(false);
  const uploadRequest = useRef<{ file: File; kind: string; id: string } | null>(null);
  const request = useRef<{ key: string; id: string } | null>(null);
  const endpoint = `/projects/${project.id}`;
  const apply = useCallback((value: Preparation) => {
    if (!mounted.current || current.current && current.current.revision > value.revision) return;
    current.current = value; setData(value);
  }, []);
  const refresh = useCallback(async () => { const value = await api<Preparation>(`${endpoint}/interview`); apply(value); return value; }, [endpoint, apply]);
  const jobState = JSON.stringify(project.tasks?.filter(t => t.kind !== 'interview_chat').map(t => [t.id, t.status, t.stage]));
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; speechSynthesis?.cancel(); }; }, []);
  useEffect(() => { void refresh().catch(cause => { if (mounted.current) setError(cause.message); }); }, [refresh, project.scene_state?.domain_revision, project.scene_state?.capture_version, jobState]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (captureBusy || dirty || uploading) event.preventDefault(); };
    addEventListener('beforeunload', guard); return () => removeEventListener('beforeunload', guard);
  }, [captureBusy, dirty, uploading]);
  const allowLeave = useCallback(() => {
    if (uploading) { alert('简历或材料正在上传解析，请完成后再离开。'); return false; }
    if (captureBusy) { alert('请先暂停录音并等待最后分片保存到本机。'); return false; }
    return !dirty || confirm('还有未保存的修改，确定离开吗？');
  }, [captureBusy, dirty, uploading]);
  useEffect(() => {
    const guard = (event: Event) => { if (!allowLeave()) event.preventDefault(); };
    addEventListener('fieldwork:before-navigate', guard);
    return () => removeEventListener('fieldwork:before-navigate', guard);
  }, [allowLeave]);
  const navigate = (next: string) => { if (allowLeave()) { setView(next); setDirty(); } };
  const openStep = (index: number) => { if (allowLeave()) { setStep(index); setView('step'); setDirty(); } };
  const act = async (body: Record<string, unknown>) => {
    if (!current.current || saving || uploadLock.current) return;
    if (transcriptDirty && ['prepare', 'review', 'finish', 'next', 'candidate_questions'].includes(String(body.action))) {
      setError('请先保存或取消当前转写修改，再继续生成或推进练习。'); return;
    }
    const key = JSON.stringify(body);
    if (request.current?.key !== key) request.current = { key, id: requestId() };
    setSaving(true); setError('');
    try {
      const result = await props.command({ action: 'scene', scene_command: { ...body, revision: current.current.revision, request_id: request.current.id } });
      if (!result) return;
      request.current = null;
      return await refresh();
    } catch (cause) { if (mounted.current) setError((cause as Error).message); }
    finally { if (mounted.current) setSaving(false); }
  };
  const practice = data?.practices.find(p => p.id === selected) || data?.practices[0];
  const openPractice = (id: string, review = false) => { if (allowLeave()) { setSelected(id); setStep(review ? 4 : 3); setView('step'); } };
  const start = async (body: Record<string, unknown> = { action: 'start' }) => {
    if (!allowLeave()) return;
    const result = await act(body);
    if (result?.practices[0]) { setSelected(result.practices[0].id); setStep(3); setView('step'); }
  };
  const openArtifact = useCallback((id: string) => {
    const artifact = project.artifacts.find(a => a.id === id);
    if (artifact?.resource?.practice_id) openPractice(artifact.resource.practice_id, artifact.step === 4);
    else if (artifact) openStep(artifact.step);
  }, [project.artifacts, captureBusy, dirty, uploading]);
  const exportPractice = () => { if (data && practice) download(`${data.title}-本场练习.md`, practiceMarkdown(data, practice)); setMenu(false); };
  const busy = props.busy || saving || uploading;
  const uploadMaterial = async (file: File, kind: string) => {
    if (uploadLock.current || saving || props.busy) throw new Error('请等待当前保存完成，再上传材料。');
    if (captureBusy) throw new Error('请先暂停录音并保存本次回答，再上传简历。');
    if (active(project.job ?? null)) throw new Error('请等待当前回复完成，或先停止生成，再上传简历。');
    if (!current.current) throw new Error('面试资料尚未加载完成，请稍后重试。');
    uploadLock.current = true; setUploading(true);
    if (!uploadRequest.current || uploadRequest.current.file !== file || uploadRequest.current.kind !== kind) uploadRequest.current = { file, kind, id: requestId() };
    try {
      const form = new FormData(); form.append('file', file); form.append('kind', kind);
      form.append('revision', String(current.current.revision)); form.append('request_id', uploadRequest.current.id);
      const updated = await api<Project>(`${endpoint}/materials`, { method: 'POST', body: form });
      props.onProjectUpdated(updated);
      await refresh();
      uploadRequest.current = null;
    } finally { uploadLock.current = false; if (mounted.current) setUploading(false); }
  };
  const chatAct = (body: Record<string, unknown>) => {
    if (uploadLock.current) return Promise.resolve(undefined);
    if (body.action === 'archive' && dirty) { setError('请先保存或取消未保存的修改，再确认成果归档。'); return Promise.resolve(undefined); }
    return props.command(body).catch(() => undefined);
  };
  const heading = (title: string, description?: string) => <div className="studio-page-heading"><span>面试准备 · 项目工作空间</span><h1>{title}</h1>{description && <p>{description}</p>}</div>;
  const review = practice ? <InterviewReview project={data!} practice={practice} busy={busy} act={act} onDirty={questionsDirty} onTranscriptDirty={onTranscriptDirty} onPractice={id => start({ action: 'practice_question', question_id: id })} onRetry={() => start({ action: 'retry_practice', practice_id: practice.id })}/> : <p>完成一场练习后，可在这里核对回答、查看复盘并重练。</p>;
  const plans = data && <>
    <InterviewSetup key={data.id} project={data} busy={busy} act={act} onDirty={setupDirty}/>
    <section className="prep-card"><div className="prep-heading"><h2>问题与准备策略</h2><button className="primary" disabled={busy || active(data.job) || dirty} onClick={() => void act({ action: 'prepare' })}>{data.plan ? '重新生成方案' : '生成准备方案'}</button></div><JobStatus job={data.job}/>{data.plan_stale && <p className="prep-notice">准备资料已变化，请重新生成方案；已开始的练习仍使用当时的资料版本。</p>}{data.plan ? <><MarkdownContent text={data.plan.summary}/><ul>{data.plan.preparation_tips.map((text, i) => <li key={i}>{text}</li>)}</ul><ol>{data.plan.questions.map((q, i) => <li key={i}>{q.question}<small> · {q.target}</small></li>)}</ol>{data.plan.company_and_role_focus?.map((text, i) => <p key={i}>{text}</p>)}{!!data.plan.candidate_question_suggestions?.length && <QuestionSuggestions questions={data.plan.candidate_question_suggestions}/>}<button className="primary" disabled={busy || data.plan_stale || data.practices.some(p => p.status === 'active')} onClick={() => void start()}><Microphone/>开始录音练习</button></> : <p>可先在右侧讨论目标，也可以直接生成计划；材料可以稍后补充。</p>}</section>
    <HistoricalQuestions project={data} busy={busy} act={act} onDirty={questionsDirty} onPractice={id => start({ action: 'practice_question', question_id: id })}/>
  </>;
  const materials = data && <Materials project={data} busy={busy} act={act} onDirty={materialsDirty} onUpload={async (file, kind) => {
    setError('');
    try {
      await uploadMaterial(file, kind);
    } catch (cause) { setError((cause as Error).message); }
  }}/>;
  return <WorkspaceShell interview title={project.title} saved={captureBusy ? '正在录音 / 本机保存中' : project.connection_lost ? '连接中断，正在重连' : busy ? '保存中' : dirty ? '修改待保存' : '已保存'} view={view} fileCount={project.artifacts.length} chatVisible={chatVisible} onChatVisible={setChatVisible} onNavigate={navigate} onHome={props.onHome} onHistory={props.onHistory} onToggleExport={() => setMenu(!menu)} exportMenu={menu && <div className="export-menu"><button disabled={!practice} onClick={exportPractice}>导出本场练习与复盘</button><button disabled={!data} onClick={() => { if (data) download(`${data.title}.md`, `# ${data.title}\n\n${data.materials.map(m => `## ${m.title}\n${m.content}`).join('\n\n')}\n\n${data.practices.map(p => practiceMarkdown(data, p)).join('\n\n---\n\n')}`); setMenu(false); }}>导出全部面试资料 Markdown</button></div>}>
    <main className={`studio-main ${view === 'flow' ? 'map-view' : 'document-view'} interview-main`}>
      {error && <div role="alert" className="prep-error">{error}<button onClick={() => setError('')}>关闭</button></div>}
      {view === 'flow' ? <><ResearchMap project={project} current={interviewScenario} selectedStep={step} onSelect={setStep} onOpen={openArtifact} onTasks={() => { setChatVisible(true); setTab('tasks'); }} onLayout={props.onLayout}/><div className="selected-branch-chip"><span>{interviewScenario.steps[step]}</span><button onClick={() => openStep(step)}>打开此阶段<ArrowUpRight size={12}/></button></div></>
        : !data ? <p role="status">正在读取面试资料…</p>
        : view === 'files' ? <>{heading('项目资料', '准备材料与生成成果统一保存在这里，原始资料可以按版本修订。')}{materials}<div className="studio-file-grid">{project.artifacts.filter(a => a.kind !== 'material').map(a => <button className="studio-file-card" key={a.id} onClick={() => openArtifact(a.id)}><FileText size={24}/><strong>{a.title}</strong><small>{interviewScenario.steps[a.step]}{a.stale ? ' · 需复核' : ''}</small></button>)}</div></>
          : view === 'overview' ? <>{heading('项目概览', project.prompt)}<section className="prep-card"><p>当前阶段：{interviewScenario.steps[project.stage]}</p><p>{data.materials.length} 份准备材料 · {data.practices.length} 场练习</p><button className="secondary" onClick={() => openStep(0)}>调整面试目标与设置</button></section>{data.practices.map(p => <button className="prep-history-item" key={p.id} onClick={() => openPractice(p.id, p.status === 'done')}><strong>{new Date(p.created_at * 1000).toLocaleString('zh-CN')}</strong><small>{p.status === 'active' ? '练习中' : '已结束'}{p.source_practice_id ? ' · 从复盘重练' : ''}</small></button>)}<PracticeComparison practices={data.practices}/></>
            : view === 'interviews' || step === 3 ? <>{heading('模拟练习', '本题作答在这里录音并核对；右侧输入框用于场外讨论，不提交答案。')}{data.practices.length > 1 && <label>选择练习<select aria-label="选择练习" value={practice?.id || ''} disabled={captureBusy} onChange={e => { if (allowLeave()) setSelected(e.target.value); }}>{data.practices.map(p => <option key={p.id} value={p.id}>{new Date(p.created_at * 1000).toLocaleString('zh-CN')} · {p.status === 'active' ? '进行中' : '已结束'}</option>)}</select></label>}{practice ? <MockInterview key={practice.id} practice={practice} busy={busy} act={act} onBusy={setCaptureBusy} onTranscriptDirty={onTranscriptDirty} onReview={() => openStep(4)}/> : <section className="prep-card"><p>先确认问题计划，再开始练习。</p><button className="primary" onClick={() => openStep(2)}>查看问题与策略</button></section>}</>
              : step === 1 ? <>{heading('背景与材料')}{materials}</>
                : step === 4 ? <>{heading('复盘与改进')}{review}{practice?.review && !practice.review_stale && <button className="secondary" onClick={() => openStep(5)}>审阅完成，进入成果归档</button>}</>
                  : step === 5 ? <>{heading('成果归档')}{review}<section className="prep-card"><button className="primary" disabled={busy || project.completed} onClick={() => void chatAct({ action: 'archive' })}>{project.completed ? '本次成果已归档' : '确认成果归档'}</button><button className="secondary" disabled={!practice} onClick={exportPractice}>导出本场练习与复盘</button></section></>
                    : <>{heading(step === 0 ? '面试目标' : '问题与策略')}{plans}</>}
    </main>
    {chatVisible && <AssistantPanel project={project} current={interviewScenario} selectedStep={step} busy={busy} tab={tab} onTab={setTab} onClose={() => setChatVisible(false)} answers={project.answers} customAnswers={{}} onCustom={() => {}} context="" location={{}} onLocation={() => {}} onStartAI={() => {}} onCapture={() => navigate('interviews')} onPick={() => {}} onContext={() => {}} act={chatAct} onOpen={openArtifact} onSelect={openStep} onExport={exportPractice} onUploadResume={file => uploadMaterial(file, 'resume')}/>}
  </WorkspaceShell>;
}

function InterviewSetup({ project, busy, act, onDirty }: { project: Preparation; busy: boolean; act: (body: Record<string, unknown>) => Promise<Preparation | undefined>; onDirty: (dirty: boolean) => void }) {
  const [cfg, setConfig] = useState<Config>(project.config);
  const [editing, setEditing] = useState(false);
  const dirty = editing && JSON.stringify(cfg) !== JSON.stringify(project.config);
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  useEffect(() => { if (!editing) setConfig(project.config); }, [project.revision, editing]);
  return <section className="prep-card"><h2>这次的准备目标</h2>{editing ? <><label className="prep-label">面试目标<textarea aria-label="面试目标" value={cfg.goal} maxLength={2000} onChange={e => setConfig({ ...cfg, goal: e.target.value })}/></label><label className="prep-label">公司、岗位或论文背景<textarea aria-label="公司岗位背景" value={cfg.company_background || ''} maxLength={6000} onChange={e => setConfig({ ...cfg, company_background: e.target.value })}/></label><div className="prep-inline"><label>准备类型<select aria-label="准备类型" value={cfg.template} onChange={e => setConfig({ ...cfg, template: e.target.value as Config['template'], include_candidate_questions: e.target.value === 'thesis' ? false : cfg.include_candidate_questions })}><option value="job">求职面试</option><option value="thesis">论文答辩</option></select></label><label>语言<select aria-label="面试语言" value={cfg.language} onChange={e => setConfig({ ...cfg, language: e.target.value as Config['language'] })}><option>中文</option><option>English</option></select></label><label>练习方式<select aria-label="练习方式" value={cfg.practice_mode} onChange={e => setConfig({ ...cfg, practice_mode: e.target.value as Config['practice_mode'] })}><option value="coaching">辅导：每题反馈</option><option value="simulation">模拟：结束后复盘</option></select></label><label>计划题数<input aria-label="计划题数" type="number" min={2} max={12} value={cfg.question_count} onChange={e => setConfig({ ...cfg, question_count: Number(e.target.value) })}/></label><label>预计分钟<input aria-label="练习分钟" type="number" min={3} max={90} value={cfg.duration_minutes} onChange={e => setConfig({ ...cfg, duration_minutes: Number(e.target.value) })}/></label></div><label><input type="checkbox" checked={cfg.include_candidate_questions} onChange={e => setConfig({ ...cfg, include_candidate_questions: e.target.checked })}/>练习最后反问</label><div className="prep-inline"><button className="primary" disabled={busy || !cfg.goal.trim()} onClick={async () => { if (await act({ action: 'configure', config: { ...cfg, preparation_step: 'ready' } })) setEditing(false); }}>保存准备要求</button><button className="secondary" onClick={() => { setConfig(project.config); setEditing(false); }}>取消修改</button></div></> : <><p>{project.config.goal}</p><small>{project.config.language} · {project.config.duration_minutes} 分钟 · {project.config.practice_mode === 'simulation' ? '模拟' : '辅导'}</small><p>{project.config.company_background}</p><button className="secondary" onClick={() => setEditing(true)}>调整准备要求</button></>}</section>;
}

function InterviewReview({ project, practice, busy, act, onDirty, onTranscriptDirty, onPractice, onRetry }: { project: Preparation; practice: Practice; busy: boolean; act: (body: Record<string, unknown>) => Promise<Preparation | undefined>; onDirty: (dirty: boolean) => void; onTranscriptDirty: TranscriptDirty; onPractice: (id: string) => Promise<void>; onRetry: () => Promise<void> }) {
  if (practice.status !== 'done') return <p>结束当前练习后，在这里统一复盘；模拟模式不提前展示点评。</p>;
  return <><section className="prep-card"><div className="prep-heading"><h2>整场复盘</h2><button className="primary" disabled={busy || active(practice.job)} onClick={() => void act({ action: 'review', practice_id: practice.id })}>{practice.review ? '重新生成复盘' : '生成整场复盘'}</button></div><JobStatus job={practice.job}/>{practice.review_stale && <p className="prep-notice">回答已校对，旧复盘需要重新生成。</p>}{practice.review && <><MarkdownContent text={practice.review.summary}/><Findings findings={practice.review.findings} practice={practice}/>{practice.review.candidate_questions_review && <CandidateQuestionsFeedback review={practice.review.candidate_questions_review}/>}<h3>下一轮重练的问题</h3><ul>{practice.review.retry_questions.map((q, i) => <li key={i}>{q.question}</li>)}</ul><button className="primary" disabled={busy || practice.review_stale} onClick={() => void onRetry()}>按复盘建议开始重练</button></>}</section>{practice.turns.map(t => <section className="prep-card" key={t.id}><h3>{t.question}</h3>{t.answers.filter(a => a.status === 'ready').map(a => <AnswerReview key={a.id} answer={a} editable busy={busy} onTranscriptDirty={onTranscriptDirty} onSave={content => act({ action: 'correct', practice_id: practice.id, answer_id: a.id, content })}/>)}</section>)}<HistoricalQuestions project={project} busy={busy} act={act} onPractice={onPractice} onDirty={onDirty}/><PracticeComparison practices={project.practices}/></>;
}

function practiceMarkdown(project: Preparation, practice: Practice) {
  const lines = [`# ${project.title}`, `练习时间：${new Date(practice.created_at * 1000).toLocaleString('zh-CN')}`, '> 面试练习资料，个人陈述与 AI 建议需要结合原音核对。'];
  for (const turn of practice.turns) { lines.push(`\n## ${turn.question}`); for (const answer of turn.answers.filter(a => a.status === 'ready')) lines.push(`回答 v${answer.version}\n${answer.transcript}`); for (const f of turn.feedback || []) lines.push(`${f.observation}\n原话：${f.quote}\n建议：${f.suggestion}`); }
  if (practice.review) lines.push(`\n## 整场复盘${practice.review_stale ? '（需要更新）' : ''}\n${practice.review.summary}`, ...practice.review.findings.map(f => `${f.observation}\n原话：${f.quote}\n建议：${f.suggestion}`));
  const closing = practice.candidate_review || practice.review?.candidate_questions_review;
  if (closing) lines.push(`\n## 最后反问\n${closing.summary}`, ...closing.recommended_questions.map(q => `${q.question}\n${q.why}\n${q.timing}`));
  return lines.join('\n\n');
}
