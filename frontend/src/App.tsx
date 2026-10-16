import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { ArrowUp, ArrowUpRight, ArrowLeft, Plus, MagnifyingGlass, X, Check, Clock, CaretRight, DownloadSimple, ArrowClockwise, BookOpen, Buildings, Storefront, ChatsCircle, CirclesThree, Question, Sparkle, Sun, Moon, Cursor, FolderSimple, FlowArrow, FileText, Microphone, DotsThree, PencilSimple, FloppyDisk, CaretLeft, SidebarSimple, CheckCircle, WarningCircle } from '@phosphor-icons/react';
import { ReactFlow, Background, Controls, Handle, Position, type NodeProps, type Node, type Edge } from '@xyflow/react';
import { api, download, type Artifact, type Project, type ProjectSummary, type Scenario, type Scene } from './types';
import { RevealBackdrop } from './RevealBackdrop';

const sceneIcons = { market: Storefront, enterprise: Buildings, memoir: BookOpen };
const fallbackNames = { market: '市场调研', enterprise: '企业访谈', memoir: '口述史' };
const examples: { scene: Scene; prompt: string; label: string }[] = [
  { scene: 'market', prompt: '想了解锦州本地消费市场，准备一次实地调研', label: '去锦州，做一次市场调研' },
  { scene: 'enterprise', prompt: '准备一次制造业企业负责人访谈', label: '走进一家企业，听听它的故事' },
  { scene: 'memoir', prompt: '为爷爷整理一份口述史，留下他的经历', label: '和家人聊聊，留住人生记忆' },
];

function Brand({ compact = false, onClick }: { compact?: boolean; onClick: () => void }) {
  return <button className={`brand ${compact ? 'compact' : ''}`} onClick={onClick} aria-label="体察首页"><CirclesThree weight="duotone" size={31}/><span>体察</span>{!compact && <small>FIELDWORK</small>}</button>;
}

export function App() {
  const [scenarios, setScenarios] = useState<Record<Scene, Scenario> | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [booting, setBooting] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [modal, setModal] = useState<'history' | 'guide' | null>(null);
  const [query, setQuery] = useState('');
  const modalRef = useRef<HTMLElement>(null);
  const [theme, setTheme] = useState(() => localStorage.getItem('fieldwork-theme') || 'light');
  const creation = useRef<{ prompt: string; scene?: Scene; id: string } | null>(null);
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('fieldwork-theme', theme); }, [theme]);
  useEffect(() => {
    if (!modal) return;
    const previous = document.activeElement as HTMLElement | null;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setModal(null);
      if (event.key !== 'Tab') return;
      const targets = modalRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, a[href]');
      if (!targets?.length) return;
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => { document.removeEventListener('keydown', handleKey); previous?.focus(); };
  }, [modal]);
  const loadLists = async () => {
    const [specs, list] = await Promise.all([api<Record<Scene, Scenario>>('/scenarios'), api<ProjectSummary[]>('/projects')]);
    setScenarios(specs); setProjects(list);
  };
  useEffect(() => {
    let disposed = false;
    const boot = async () => {
      try {
        await loadLists();
        const id = new URLSearchParams(location.search).get('project');
        if (id) { const data = await api<Project>(`/projects/${id}`); if (!disposed) setProject(data); }
      } catch (e) { if (!disposed) setError((e as Error).message); }
      finally { if (!disposed) setBooting(false); }
    };
    void boot();
    const pop = () => { const id = new URLSearchParams(location.search).get('project'); if (id) void openProject(id, false); else setProject(null); };
    addEventListener('popstate', pop);
    return () => { disposed = true; removeEventListener('popstate', pop); };
  }, []);
  async function openProject(id: string, push = true) {
    setBusy(true); setError('');
    try {
      const data = await api<Project>(`/projects/${id}`);
      setProject(data); setModal(null);
      if (push) history.pushState({}, '', `/?project=${id}`);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function create(prompt: string, scene?: Scene) {
    if (!prompt.trim() || busy) return;
    setBusy(true); setError('');
    if (!creation.current || creation.current.prompt !== prompt || creation.current.scene !== scene) creation.current = { prompt, scene, id: crypto.randomUUID() };
    try {
      const data = await api<Project>('/projects', { method: 'POST', body: JSON.stringify({ prompt: prompt.trim(), scenario: scene, request_id: creation.current.id }) });
      setProject(data); setModal(null); history.pushState({}, '', `/?project=${data.id}`);
      creation.current = null;
      void loadLists().catch(() => {});
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function command(body: Record<string, unknown>) {
    if (!project || busy) return;
    setBusy(true); setError('');
    try {
      const result = await api<Project>(`/projects/${project.id}/commands`, { method: 'POST', body: JSON.stringify({ revision: project.revision, ...body }) });
      setProject(result); void loadLists().catch(() => {});
      return result;
    } catch (e) { setError((e as Error).message); throw e; }
    finally { setBusy(false); }
  }
  function home() { setProject(null); setError(''); history.pushState({}, '', '/'); void loadLists().catch(() => {}); }
  return <>
    {project && scenarios ? <Workspace key={project.id} project={project} scenarios={scenarios} busy={busy} command={command} onHome={home} onHistory={() => setModal('history')}/>
      : <Home busy={busy || booting} projects={projects} onCreate={create} onOpen={openProject} onHistory={() => setModal('history')} onGuide={() => setModal('guide')} theme={theme} onTheme={() => setTheme(theme === 'light' ? 'dark' : 'light')}/>}
    {error && <div className="error-toast" role="alert"><WarningCircle size={20}/><span>{error}</span>{project && <button onClick={() => location.reload()}>重新加载</button>}{!project && <button onClick={() => { setError(''); void loadLists().catch(e => setError(e.message)); }}>重试</button>}<button aria-label="关闭错误提示" onClick={() => setError('')}><X size={16}/></button></div>}
    {modal && <div className="modal-backdrop" onClick={() => setModal(null)}><section ref={modalRef} className="modal" role="dialog" aria-modal="true" aria-label={modal === 'history' ? '历史项目' : '使用指南'} onClick={e => e.stopPropagation()}><div className="modal-heading"><h2>{modal === 'history' ? '你的调研项目' : '从一个问题开始'}</h2><button className="icon-button" autoFocus aria-label="关闭" onClick={() => setModal(null)}><X size={20}/></button></div>
      {modal === 'history' ? <><label className="search-box"><MagnifyingGlass size={18}/><input autoFocus placeholder="搜索项目名称" value={query} onChange={e => setQuery(e.target.value)}/></label><div className="history-list">{projects.filter(p => p.title.includes(query)).map(p => <ProjectRow key={p.id} item={p} onClick={() => void openProject(p.id)}/>)}{!projects.filter(p => p.title.includes(query)).length && <div className="empty-state"><FolderSimple size={36}/><p>{query ? '没有找到相关项目' : '还没有项目，写下第一个调研问题吧'}</p></div>}</div></>
        : <div className="guide-content"><p>把想了解的人、企业或地方告诉体察。从市场调研、企业访谈、口述史中选择适合的流程。</p><ol><li>选一选，确认目标与范围。</li><li>沿着画布，查看准备计划与提纲。</li><li>打开成果修改，保存版本或导出。</li></ol><aside>当前为第一轮工作台：项目与编辑真实保存，研究与提纲为明确标注的流程示例。真实 AI、录音和手机采集将在下一轮接入。无需登录。</aside></div>}
    </section></div>}
  </>;
}

function ProjectRow({ item, onClick }: { item: ProjectSummary; onClick: () => void }) {
  const Icon = sceneIcons[item.scenario];
  return <button className="project-row" onClick={onClick}><span className="project-icon"><Icon size={21}/></span><span className="project-row-text"><strong>{item.title}</strong><small>{fallbackNames[item.scenario]} · {item.stage ? '进行中' : '待确认需求'}</small></span><time>{new Date(item.updated_at * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}</time><ArrowUpRight size={17}/></button>;
}

function Home({ busy, projects, onCreate, onOpen, onHistory, onGuide, theme, onTheme }: { busy: boolean; projects: ProjectSummary[]; onCreate: (p: string, s?: Scene) => void; onOpen: (id: string) => void; onHistory: () => void; onGuide: () => void; theme: string; onTheme: () => void }) {
  const [prompt, setPrompt] = useState('');
  const [scene, setScene] = useState<Scene | undefined>();
  const [revealed, setRevealed] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const submit = (e?: FormEvent) => { e?.preventDefault(); onCreate(prompt, scene); };
  return <div className="home">
    <header className="home-header"><Brand onClick={() => { setPrompt(''); setScene(undefined); }}/><nav><button onClick={onHistory}>我的项目 <CaretRight size={13}/></button><button onClick={onGuide}>使用指南</button><span className="nav-divider"/><button className="icon-button" onClick={onTheme} aria-label={theme === 'light' ? '切换深色模式' : '切换浅色模式'}>{theme === 'light' ? <Moon size={19}/> : <Sun size={19}/>}</button></nav></header>
    <section className="landing-hero">
      <RevealBackdrop revealed={revealed}/>
      <div className="hero-content">
        <div className="hero-heading"><div className="hero-wordmark">体察 <span>Fieldwork</span></div><h1>让每一次对话，都有迹可循。</h1></div>
        <form className="home-composer" onSubmit={submit}><label htmlFor="research-prompt">今天您想调研什么？</label><textarea ref={input} id="research-prompt" maxLength={2000} rows={2} placeholder="一个地方，一家企业，或一段值得留下的人生……" value={prompt} onChange={e => { setPrompt(e.target.value); setScene(undefined); }} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }}/><div className="composer-bottom"><span><Sparkle size={15}/>{scene ? `已选择${fallbackNames[scene]}` : '让体察帮你规划下一步'}</span><button type="submit" className="send-button" aria-label="开始调研" disabled={!prompt.trim() || busy}>{busy ? <ArrowClockwise className="spin" size={22}/> : <ArrowUp size={22} weight="bold"/>}</button></div></form>
        <div className="scenario-suggestions">{examples.map(({ scene: s, prompt: p, label }) => { const Icon = sceneIcons[s]; return <button key={s} onClick={() => { setPrompt(p); setScene(s); input.current?.focus(); }}><Icon size={17}/><span>{fallbackNames[s]}</span><span className="suggestion-tooltip">{label}</span><ArrowUpRight size={13}/></button>; })}</div>
      </div>
      <div className="hero-floor"><button className="backdrop-toggle" aria-pressed={revealed} onClick={() => setRevealed(!revealed)}><Cursor size={16}/>{revealed ? '恢复光标擦除' : '移动光标，探索底图'}<span>{revealed ? '收起' : '查看'}</span></button><span className="hero-note">问得深入，才能看得清楚。</span></div>
    </section>
    <section className="recent-section"><div className="section-heading"><h2><Clock size={18}/>最近的调研</h2><button onClick={onHistory}>全部项目 <ArrowUpRight size={15}/></button></div>{projects.length ? <div className="recent-grid">{projects.slice(0, 3).map(item => <ProjectRow key={item.id} item={item} onClick={() => onOpen(item.id)}/>)}</div> : <button className="first-project" onClick={() => input.current?.focus()}><Plus size={20}/><span>你的第一份调研，从上面的一个问题开始</span><ArrowUpRight size={16}/></button>}</section>
    <footer className="home-footer"><span>体察 · 调研工作台</span><button onClick={onGuide}><Question size={15}/>如何开始</button></footer>
  </div>;
}

type StepData = { title: string; index: number; state: string; artifacts: Artifact[]; selected: boolean };
type StepNode = Node<StepData, 'step'>;
function StepCard({ data }: NodeProps<StepNode>) {
  const Icon = [ChatsCircle, MagnifyingGlass, FileText, Microphone, CirclesThree, FolderSimple][data.index];
  return <div className={`flow-step ${data.state} ${data.selected ? 'selected' : ''}`}>
    <Handle type="target" position={Position.Left}/><div className="step-top"><span className="step-number">{String(data.index + 1).padStart(2, '0')}</span><span className="step-status">{data.state === 'done' ? <><CheckCircle size={13} weight="fill"/>已完成</> : data.state === 'active' ? '当前阶段' : '待开始'}</span></div><div className="step-name"><Icon size={22}/><strong>{data.title}</strong></div><p>{['明确目标与范围', '建立理解的起点', '带着好问题出发', '保存真实的声音', '从材料中寻找联系', '让成果有序沉淀'][data.index]}</p><div className="step-files">{data.artifacts.length ? data.artifacts.map(a => <span key={a.id}><FileText size={14}/>{a.title}<CaretRight size={12}/></span>) : <span className="no-artifact">{data.state === 'active' ? '等待补充与确认' : '成果将在这里汇集'}</span>}</div><Handle type="source" position={Position.Right}/>
  </div>;
}
const nodeTypes = { step: StepCard };

function Workspace({ project, scenarios, busy, command, onHome, onHistory }: { project: Project; scenarios: Record<Scene, Scenario>; busy: boolean; command: (body: Record<string, unknown>) => Promise<Project | undefined>; onHome: () => void; onHistory: () => void }) {
  const [tab, setTab] = useState('flow');
  const [selectedStep, setSelectedStep] = useState(project.stage);
  const [selectedArtifact, setSelectedArtifact] = useState<string | null>(null);
  const [chatVisible, setChatVisible] = useState(true);
  const [chat, setChat] = useState('');
  const [answers, setAnswers] = useState(project.answers);
  const [context, setContext] = useState(project.context);
  const [saved, setSaved] = useState(false);
  const [exportMenu, setExportMenu] = useState(false);
  const [editorDirty, setEditorDirty] = useState(false);
  const draftDirty = project.stage === 0 && (JSON.stringify(answers) !== JSON.stringify(project.answers) || context !== project.context);
  const canLeave = () => !(editorDirty || draftDirty) || confirm('还有未保存的修改，确定离开吗？');
  const messagesEnd = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (draftDirty || editorDirty) event.preventDefault(); };
    addEventListener('beforeunload', guard);
    return () => removeEventListener('beforeunload', guard);
  }, [draftDirty, editorDirty]);
  const current = scenarios[project.scenario];
  const artifact = project.artifacts.find(a => a.id === selectedArtifact);
  useEffect(() => { setSelectedStep(project.stage); }, [project.stage]);
  useEffect(() => { setAnswers(project.answers); setContext(project.context); }, [project.scenario]);
  useEffect(() => { messagesEnd.current?.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' }); }, [project.messages.length, busy]);
  useEffect(() => { if (saved) { const t = setTimeout(() => setSaved(false), 3000); return () => clearTimeout(t); } }, [saved]);
  const act = (body: Record<string, unknown>) => command(body).catch(() => undefined);
  const complete = current.fields.every(f => (answers[f.key] || []).length > 0);
  const nodes: StepNode[] = useMemo(() => current.steps.map((title, index) => ({ id: String(index), type: 'step', initialWidth: 242, initialHeight: 165, position: { x: (index % 3) * 300, y: Math.floor(index / 3) * 285 }, data: { title, index, state: index < project.stage ? 'done' : index === project.stage ? 'active' : 'pending', artifacts: project.artifacts.filter(a => a.step === index), selected: selectedStep === index }, draggable: false })), [current, project.stage, project.artifacts, selectedStep]);
  const edges: Edge[] = useMemo(() => current.steps.slice(1).map((_, i) => ({ id: `e${i}`, source: String(i), target: String(i + 1), type: i === 2 ? 'smoothstep' : 'default', animated: false, className: i < project.stage ? 'edge-done' : '' })), [current, project.stage]);
  const pick = (key: string, value: string, multiple: boolean) => setAnswers(previous => ({ ...previous, [key]: multiple ? (previous[key] || []).includes(value) ? previous[key].filter(v => v !== value) : [...(previous[key] || []), value] : [value] }));
  const selectNode = (index: number) => { if (!canLeave()) return; setSelectedStep(index); const first = project.artifacts.find(a => a.step === index); if (first) setSelectedArtifact(first.id); };
  const exportAll = () => { download(`${project.title}.md`, `# ${project.title}\n\n场景：${current.name}\n\n> 第一轮工作台示例成果，未执行真实研究。\n\n${project.artifacts.map(a => `${a.content}\n\n---\n`).join('\n')}`); setExportMenu(false); };
  return <div className={`workspace ${!chatVisible ? 'chat-hidden' : ''}`}>
    <aside className="sidebar"><Brand compact onClick={() => { if (canLeave()) onHome(); }}/><button className="new-project" onClick={() => { if (canLeave()) onHome(); }}><Plus size={19}/>新建调研</button><div className="sidebar-section">工作空间</div><nav>{[{ key: 'overview', name: '项目概览', Icon: CirclesThree }, { key: 'flow', name: '调研流程', Icon: FlowArrow }, { key: 'files', name: '项目资料', Icon: FolderSimple }, { key: 'interviews', name: '访谈记录', Icon: Microphone }].map(({ key, name, Icon }) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => { if (canLeave()) { setTab(key); setSelectedArtifact(null); } }}><Icon size={19}/><span>{name}</span>{key === 'files' && <small>{project.artifacts.length}</small>}</button>)}</nav><div className="sidebar-bottom"><button onClick={() => { if (canLeave()) onHistory(); }}><Clock size={18}/>历史项目</button><div className="local-workspace"><span className="workspace-avatar">体</span><span>本地工作空间<small>无需登录 · 本地存储</small></span></div></div></aside>
    <header className="workspace-header"><div className="breadcrumb"><button onClick={() => { if (canLeave()) onHistory(); }}>我的项目</button><CaretRight size={13}/><strong title={project.title}>{project.title}</strong></div><div className="workspace-actions"><span className="save-indicator"><Check size={13}/>{busy ? '正在保存' : draftDirty || editorDirty ? '有未保存修改' : '已保存'}</span><div className="export-wrapper"><button className="secondary small" onClick={() => setExportMenu(!exportMenu)}><DownloadSimple size={16}/>导出</button>{exportMenu && <div className="export-menu"><button disabled={!project.artifacts.length} onClick={exportAll}>导出全部成果 Markdown</button><button onClick={() => { download(`${project.title}.json`, JSON.stringify(project, null, 2), 'application/json'); setExportMenu(false); }}>导出项目快照 JSON</button></div>}</div><button className="icon-button" aria-label={chatVisible ? '收起对话' : '展开对话'} onClick={() => setChatVisible(!chatVisible)}><SidebarSimple size={20}/></button></div></header>
    <main className="workspace-main">
      <div className="work-heading"><div><span className="scene-label">{current.name}</span><h1>{tab === 'files' ? '项目资料' : tab === 'interviews' ? '访谈记录' : tab === 'overview' ? '项目概览' : '把思路，一步步展开。'}</h1><p>{tab === 'flow' ? '每一步有进展，每一份成果有来处。' : project.title}</p></div><span className="phase-badge">流程示例</span></div>
      {artifact ? <ArtifactEditor key={artifact.id} artifact={artifact} busy={busy} onDirty={setEditorDirty} onBack={() => setSelectedArtifact(null)} onSave={async content => { const result = await act({ action: 'artifact', artifact_id: artifact.id, content }); return !!result; }}/>
      : tab === 'flow' ? <div className="canvas-container"><div className="canvas-topline"><span><FlowArrow size={15}/>调研路径</span><span>{project.stage + 1} / {current.steps.length} 阶段</span></div><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodeClick={(_, n) => selectNode(Number(n.id))} fitView fitViewOptions={{ padding: .16 }} minZoom={.25} maxZoom={1.4} nodesConnectable={false} proOptions={{ hideAttribution: false }} aria-label="调研流程画布"><Background color="var(--dot)" gap={22} size={1}/><Controls showInteractive={false}/></ReactFlow><div className="canvas-footnote"><span><Cursor size={14}/>拖动画布 · 点击节点查看成果</span><button onClick={() => { setSelectedStep(project.stage); setChatVisible(true); }}>定位当前阶段 <ArrowUpRight size={13}/></button></div></div>
      : tab === 'files' ? <div className="files-panel">{project.artifacts.length ? project.artifacts.map(a => <button className="file-row" key={a.id} onClick={() => setSelectedArtifact(a.id)}><span className="file-type"><FileText size={23}/></span><span><strong>{a.title}</strong><small>{current.steps[a.step]} · v{a.version}{a.stale ? ' · 需复核' : ''}</small></span><span className="file-example">{a.demo ? '示例' : ''}</span><CaretRight size={16}/></button>) : <div className="empty-state"><FolderSimple size={42}/><h3>成果会在这里汇集</h3><p>先在右侧确认目标，生成第一份准备计划。</p><button className="secondary" onClick={() => { setTab('flow'); setChatVisible(true); }}>继续确认需求</button></div>}</div>
      : tab === 'interviews' ? <div className="empty-state interviews-empty"><Microphone size={45}/><h3>准备好问题，再开始倾听。</h3><p>这里将汇集每场访谈的录音、逐字稿与纪要。</p><aside>录音、材料导入和手机采集将在第二轮接入。</aside><button className="secondary" onClick={() => setTab('flow')}>查看调研准备 <ArrowUpRight size={16}/></button></div>
      : <div className="overview-panel"><section><h2>这次想了解什么</h2><p>{project.prompt}</p><dl><div><dt>研究方式</dt><dd>{current.name}</dd></div><div><dt>当前阶段</dt><dd>{current.steps[project.stage]}</dd></div><div><dt>已存成果</dt><dd>{project.artifacts.length} 份</dd></div></dl></section><section><h2>关注方向</h2>{current.fields.map(f => <p key={f.key}><span>{f.label}</span><strong>{project.answers[f.key]?.join('、') || '尚待确认'}</strong></p>)}</section><button className="primary" onClick={() => { setTab('flow'); setChatVisible(true); }}>继续调研 <ArrowUpRight size={16}/></button></div>}
      {!artifact && tab === 'flow' && <div className="current-step-bar"><div><span className="current-step-icon"><Sparkle size={19}/></span><span><strong>{current.steps[selectedStep]}</strong><small>{selectedStep === project.stage ? '在右侧与体察一起继续' : selectedStep < project.stage ? '已完成，可查看阶段成果' : '完成前面的准备后继续'}</small></span></div>{project.artifacts.some(a => a.step === selectedStep) ? <button className="secondary small" onClick={() => setSelectedArtifact(project.artifacts.find(a => a.step === selectedStep)!.id)}>查看成果 <CaretRight size={14}/></button> : <span className="muted">暂无附件</span>}</div>}
    </main>
    {chatVisible && <aside className="chat-panel"><div className="chat-header"><div><span className="agent-symbol"><Sparkle size={18} weight="fill"/></span><strong>体察助手</strong></div><button className="icon-button" aria-label="收起助手" onClick={() => setChatVisible(false)}><CaretRight size={18}/></button></div><div className="demo-notice"><span/>流程体验模式 · 尚未连接 AI 服务</div><div className="chat-scroll"><div className="chat-date">本次调研的起点</div>{project.messages.map((m, i) => <div key={m.id} className={`chat-message ${m.role}`}><div className="message-avatar">{m.role === 'assistant' ? <Sparkle size={15}/> : '我'}</div><div>{m.role === 'assistant' && <span className="message-author">体察</span>}<p className={i === project.messages.length - 1 ? 'latest-message' : ''}>{m.text}</p></div></div>)}
      {project.stage === 0 ? <div className="brief-form"><div className="scene-switch" aria-label="调研场景">{(Object.keys(scenarios) as Scene[]).map(s => <button key={s} disabled={busy} className={project.scenario === s ? 'selected' : ''} onClick={() => { if (s !== project.scenario) void act({ action: 'scenario', scenario: s }); }}>{scenarios[s].name}</button>)}</div>{current.fields.map((f, index) => <fieldset key={`${project.scenario}-${f.key}`}><legend><span>{index + 1}</span>{f.label}<small>{f.multiple ? '多选' : '单选'}</small></legend><div className="choice-options">{f.options.map(option => { const checked = (answers[f.key] || []).includes(option); return <button key={option} role={f.multiple ? 'checkbox' : 'radio'} aria-checked={checked} disabled={busy} className={checked ? 'checked' : ''} onClick={() => pick(f.key, option, f.multiple)}>{option}{checked && <Check size={12} weight="bold"/>}</button>; })}</div></fieldset>)}<label className="context-label">地点、人物或其他补充 <span>选填</span><input aria-label="补充信息" value={context} maxLength={1000} onChange={e => setContext(e.target.value)} placeholder={project.scenario === 'memoir' ? '例如：爷爷，1940 年出生于锦州' : '例如：锦州古塔区，计划访谈 5 位店主'}/></label><div className="brief-actions"><button className="secondary" disabled={busy} onClick={async () => { const result = await act({ action: 'draft', answers, context }); if (result) setSaved(true); }}><FloppyDisk size={15}/>{saved ? '草稿已保存' : '保存草稿'}</button><button className="primary" disabled={!complete || busy} onClick={() => void act({ action: 'brief', answers, context })}>{busy ? '正在整理…' : '确认，展开调研'}<ArrowUpRight size={16}/></button></div><p className="brief-footnote">选择可保存；示例成果可编辑，不进行真实联网研究。</p></div>
      : <div className="next-step-card"><div><CheckCircle size={18}/><strong>{current.steps[project.stage - 1]}已确认</strong></div><p>{project.stage < 3 ? '先查看并完善当前草稿，准备好了再继续。' : '提纲与准备清单已就绪。真实采集将在下一轮接入。'}</p>{project.stage < 3 ? <button className="primary" disabled={busy} onClick={() => void act({ action: 'advance' })}>{busy ? '正在整理…' : project.stage === 1 ? '确认计划，生成提纲示例' : '确认提纲，进入采集准备'}<ArrowUpRight size={15}/></button> : <button className="secondary" onClick={exportAll}><DownloadSimple size={16}/>导出当前准备成果</button>}</div>}
      {busy && <div className="chat-loading" role="status"><ArrowClockwise size={14} className="spin"/>正在保存项目进展…</div>}<div ref={messagesEnd}/></div><form className="chat-composer" onSubmit={e => { e.preventDefault(); if (chat.trim()) void act({ action: 'message', content: chat }).then(result => { if (result) setChat(''); }); }}><textarea aria-label="给体察助手补充信息" rows={2} maxLength={2000} value={chat} placeholder="补充你的想法…" onChange={e => setChat(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }}/><div><small>内容会保存到本项目</small><button className="send-button" disabled={!chat.trim() || busy} aria-label="发送补充信息"><ArrowUp size={18}/></button></div></form></aside>}
  </div>;
}

function ArtifactEditor({ artifact, busy, onBack, onSave, onDirty }: { artifact: Artifact; busy: boolean; onBack: () => void; onSave: (content: string) => Promise<boolean>; onDirty: (dirty: boolean) => void }) {
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(artifact.content);
  const [historyVisible, setHistoryVisible] = useState(false);
  const [saved, setSaved] = useState(false);
  const dirty = content !== artifact.content;
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  const back = () => { if (!dirty || confirm('还有未保存的修改，确定离开吗？')) onBack(); };
  useEffect(() => { const handler = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); }; addEventListener('beforeunload', handler); return () => removeEventListener('beforeunload', handler); }, [dirty]);
  return <section className="artifact-editor"><div className="artifact-toolbar"><button className="text-button" onClick={back}><ArrowLeft size={16}/>返回画布</button><div><button className="icon-button" aria-label="查看版本历史" onClick={() => setHistoryVisible(!historyVisible)}><Clock size={18}/></button><button className="icon-button" aria-label="下载成果" onClick={() => download(`${artifact.title}.md`, content)}><DownloadSimple size={18}/></button><button className="secondary small" onClick={() => setEditing(!editing)}><PencilSimple size={15}/>{editing ? '预览' : '编辑'}</button></div></div><div className="artifact-title"><span className="file-type"><FileText size={25}/></span><div><h2>{artifact.title}</h2><p>v{artifact.version} · {artifact.demo ? '流程示例' : '人工整理'}{artifact.stale ? ' · 上游已修改，请复核' : ''}</p></div></div>{artifact.demo && <div className="artifact-disclaimer"><Sparkle size={15}/>示例草稿，用于体验流程。未执行真实检索或模型分析。</div>}{historyVisible && <div className="version-history"><strong>版本历史</strong>{artifact.versions.length ? artifact.versions.slice().reverse().map(v => <button key={v.version} onClick={() => { if (!dirty || confirm('确定用此历史版本替换当前编辑内容？')) { setContent(v.content); setEditing(true); setHistoryVisible(false); } }}>v{v.version}<span>载入到编辑器</span></button>) : <p>首次编辑后会保留历史版本。</p>}</div>}
    {editing ? <textarea className="artifact-textarea" aria-label="成果正文" value={content} maxLength={100000} onChange={e => { setContent(e.target.value); setSaved(false); }}/>
      : <article className="markdown-document">{content.split('\n').map((line, i) => line.startsWith('### ') ? <h3 key={i}>{line.slice(4)}</h3> : line.startsWith('## ') ? <h2 key={i}>{line.slice(3)}</h2> : line.startsWith('# ') ? <h1 key={i}>{line.slice(2)}</h1> : line.startsWith('> ') ? <blockquote key={i}>{line.slice(2)}</blockquote> : line.startsWith('- ') ? <p className="md-list" key={i}>{line.slice(2)}</p> : line.trim() ? <p key={i}>{line}</p> : null)}</article>}
    <div className="artifact-footer"><span>{dirty ? '有未保存的修改' : saved ? '已保存新版本' : '修改将保留版本记录'}</span><button className="primary" disabled={!dirty || busy || !content.trim()} onClick={async () => { if (await onSave(content)) { setSaved(true); setEditing(false); } }}><FloppyDisk size={16}/>{busy ? '保存中…' : '保存修改'}</button></div>
  </section>;
}
