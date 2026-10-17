import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowUp, ArrowUpRight, Plus, MagnifyingGlass, X, Clock, CaretRight, ArrowClockwise, BookOpen, Buildings, Storefront, CirclesThree, Question, Sparkle, Sun, Moon, Cursor, FolderSimple, WarningCircle, Trash } from '@phosphor-icons/react';
import { api, type Layout, type Project, type ProjectSummary, type Scenario, type Scene } from './types';
import { RevealBackdrop } from './RevealBackdrop';
import { Workspace } from './Workspace';
import { prepApi } from './InterviewPreparation';
import { InterviewWorkspace } from './interview/InterviewWorkspace';
import { useProjectEvents } from './useProjectEvents';
import { useProjectCommands } from './useProjectCommands';
import { TypewriterSubtitle } from './TypewriterSubtitle';
import { requestId } from './requestId';
import { MemoirExperience } from './MemoirExperience';
import { interviewPreferences } from './interviewPreferences';

const sceneIcons = { market: Storefront, enterprise: Buildings, memoir: BookOpen, interview: Question };
const fallbackNames = { market: '用户与市场调研', enterprise: '企业访谈', memoir: '口述史', interview: '面试准备' };
const examples: { scene: Scene; prompt: string; label: string }[] = [
  { scene: 'interview', prompt: '准备一场求职面试，围绕我的简历进行录音模拟', label: '模拟提问，录音回答，复盘重练' },
  { scene: 'market', prompt: '想了解目标用户的使用场景、现有做法与痛点，识别需要验证的需求机会', label: '理解使用场景与用户痛点' },
  { scene: 'enterprise', prompt: '调研企业客户的业务流程、关键角色与采购决策，形成待验证的需求假设', label: '梳理业务流程与决策依据' },
  { scene: 'memoir', prompt: '为爷爷整理一份口述史，留下他的经历', label: '和家人聊聊，留住人生记忆' },
];

function Brand({ compact = false, onClick }: { compact?: boolean; onClick: () => void }) {
  return <button className={`brand ${compact ? 'compact' : ''}`} onClick={onClick} aria-label="体察首页"><CirclesThree weight="duotone" size={31}/><span>体察</span>{!compact && <small>FIELDWORK</small>}</button>;
}

export function App() {
  const [scenarios, setScenarios] = useState<Record<Scene, Scenario> | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [loginName, setLoginName] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [booting, setBooting] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [modal, setModal] = useState<'history' | 'guide' | null>(null);
  const [query, setQuery] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<ProjectSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const modalRef = useRef<HTMLElement>(null);
  const [theme, setTheme] = useState(() => localStorage.getItem('fieldwork-theme') || 'light');
  const creation = useRef<{ prompt: string; scene?: Scene; sourceId?: string; id: string } | null>(null);
  const activeProject = useRef(project?.id);
  const activeLocation = useRef(location.pathname + location.search + location.hash);
  activeProject.current = project?.id;
  const allowNavigation = () => dispatchEvent(new Event('fieldwork:before-navigate', { cancelable: true }));
  const rememberLocation = () => { activeLocation.current = location.pathname + location.search + location.hash; };
  useEffect(() => { document.documentElement.dataset.theme = theme; localStorage.setItem('fieldwork-theme', theme); }, [theme]);
  useEffect(() => {
    if (!modal) return;
    const previous = document.activeElement as HTMLElement | null;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { if (deleting) return; if (deleteTarget) setDeleteTarget(null); else setModal(null); }
      if (event.key !== 'Tab') return;
      const targets = modalRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input, a[href]');
      if (!targets?.length) return;
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => { document.removeEventListener('keydown', handleKey); previous?.focus(); };
  }, [modal, deleteTarget, deleting]);
  const loadLists = async () => {
    const [specs, list] = await Promise.all([api<Record<Scene, Scenario>>('/scenarios'), api<ProjectSummary[]>('/projects')]);
    setScenarios(specs); setProjects(list);
  };
  useEffect(() => {
    let disposed = false;
    const boot = async () => {
      try {
        await loadLists();
        const prep = new URLSearchParams(location.search).get('prep');
        if (prep && !disposed) { const legacy = await prepApi<{ workspace_project_id: string }>(`/projects/${prep}`); const data = await api<Project>(`/projects/${legacy.workspace_project_id}`); if (!disposed) { setProject(data); history.replaceState({}, '', `/?project=${data.id}`); rememberLocation(); } }
        const id = new URLSearchParams(location.search).get('project');
        if (id) { const data = await api<Project>(`/projects/${id}`); if (!disposed) setProject(data); }
      } catch (e) { if (!disposed) setError((e as Error).message); }
      finally { if (!disposed) setBooting(false); }
    };
    void boot();
    const pop = () => {
      const id = new URLSearchParams(location.search).get('project');
      if (id === activeProject.current) { rememberLocation(); return; }
      if (!allowNavigation()) { history.pushState({}, '', activeLocation.current); return; }
      rememberLocation();
      if (id) void openProject(id, false, true); else setProject(null);
    };
    addEventListener('popstate', pop);
    return () => { disposed = true; removeEventListener('popstate', pop); };
  }, []);
  async function openProject(id: string, push = true, approved = false) {
    if (id !== activeProject.current && !approved && !allowNavigation()) return;
    setBusy(true); setError('');
    try {
      const data = await api<Project>(`/projects/${id}`);
      setProject(data); setModal(null);
      if (push) history.pushState({}, '', `/?project=${id}`);
      rememberLocation();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function create(prompt: string, scene?: Scene, sourceId?: string) {
    if (!prompt.trim() || busy) return;
    if (!allowNavigation()) return;
    if (!scene && /面试|答辩|求职/.test(prompt) && !/调研|访谈研究/.test(prompt)) scene = 'interview';
    setBusy(true); setError('');
    if (!creation.current || creation.current.prompt !== prompt || creation.current.scene !== scene || creation.current.sourceId !== sourceId) creation.current = { prompt, scene, sourceId, id: requestId() };
    try {
      const data = await api<Project>('/projects', { method: 'POST', body: JSON.stringify({ prompt: prompt.trim(), scenario: scene, mode: 'ai', source_project_id: sourceId, request_id: creation.current.id, ...(scene === 'interview' ? { interview_config: { goal: prompt.trim(), template: /论文|开题|毕业|硕士|博士/.test(prompt) ? 'thesis' : 'job', ...interviewPreferences(prompt) } } : {}) }) });
      setProject(data); setModal(null); history.pushState({}, '', `/?project=${data.id}`);
      rememberLocation();
      creation.current = null;
      void loadLists().catch(() => {});
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const command = useProjectCommands({ project, setProject, setBusy, onError: setError, onChanged: () => { void loadLists().catch(() => {}); } });
  useProjectEvents(project, setProject, id => { setProject(previous => previous?.id === id ? null : previous); setProjects(previous => previous.filter(item => item.id !== id)); history.replaceState({}, '', '/'); });
  async function saveLayout(positions: Layout['positions']) {
    if (!project) return;
    try {
      const layout = await api<Layout>(`/projects/${project.id}/layout`, { method: 'PUT', body: JSON.stringify({ version: project.layout?.version || 0, positions }) });
      setProject(previous => previous?.id === project.id ? { ...previous, layout: (previous.layout?.version || 0) > layout.version ? previous.layout : layout } : previous);
    } catch (e) { setError((e as Error).message); }
  }
  async function deleteProject() {
    if (!deleteTarget || deleting || deleteTarget.id === project?.id && !allowNavigation()) return;
    const id = deleteTarget.id;
    setDeleting(true); setError('');
    try {
      await api(`/projects/${id}`, { method: 'DELETE' });
      setProjects(previous => previous.filter(item => item.id !== id));
      if (project?.id === id) { setProject(null); history.replaceState({}, '', '/'); rememberLocation(); }
      setDeleteTarget(null);
      void loadLists().catch(() => {});
    } catch (cause) { setError((cause as Error).message); }
    finally { setDeleting(false); }
  }
  function closeModal() { if (deleting) return; if (deleteTarget) setDeleteTarget(null); else setModal(null); }
  function home() { if (!allowNavigation()) return; setProject(null); setError(''); history.pushState({}, '', '/'); rememberLocation(); void loadLists().catch(() => {}); }
  return <>
    {project && scenarios ? project.scenario === 'memoir' && project.mode === 'ai' ? <MemoirExperience key={project.id} project={project} onHome={home} onHistory={() => setModal('history')}/> : project.scenario === 'interview' ? <InterviewWorkspace key={project.id} project={project} onProjectUpdated={value => setProject(previous => previous?.id === value.id && value.revision >= previous.revision && (value.event_cursor ?? 0) >= (previous.event_cursor ?? 0) ? value : previous)} busy={busy} command={command} onLayout={saveLayout} onHome={home} onHistory={() => setModal('history')}/> : <Workspace key={project.id} project={project} scenarios={scenarios} busy={busy} command={command} onLayout={saveLayout} onStartAI={() => void create(project.prompt, project.scenario, project.id)} onHome={home} onHistory={() => setModal('history')}/>
      : <Home busy={busy || booting} projects={projects} onCreate={create} onOpen={openProject} onHistory={() => setModal('history')} onGuide={() => setModal('guide')} theme={theme} onTheme={() => setTheme(theme === 'light' ? 'dark' : 'light')}/>}
    {!project && /登录|账号/.test(error) && <section className="prep-login"><h2>登录工作空间</h2><form onSubmit={async e => {e.preventDefault();setBusy(true);try{const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:loginName,password:loginPassword})});const body=await response.json();if(!response.ok)throw new Error(body.detail||'登录失败');setLoginPassword('');setError('');location.reload();}catch(cause){setError((cause as Error).message);}finally{setBusy(false);}}}><input aria-label="员工姓名" autoComplete="username" placeholder="员工姓名" value={loginName} onChange={e=>setLoginName(e.target.value)}/><input aria-label="密码" type="password" autoComplete="current-password" placeholder="密码" value={loginPassword} onChange={e=>setLoginPassword(e.target.value)}/><button className="primary" disabled={busy}>登录</button></form><p>使用已配置的员工中心账号。仅在本机体验时，可按安装文档显式启用单人模式。</p></section>}
    {error && <div className="error-toast" role="alert"><WarningCircle size={20}/><span>{error}</span>{project && <button onClick={() => location.reload()}>重新加载</button>}{!project && <button onClick={() => { setError(''); void loadLists().catch(e => setError(e.message)); }}>重试</button>}<button aria-label="关闭错误提示" onClick={() => setError('')}><X size={16}/></button></div>}
    {modal && <div className="modal-backdrop" onClick={closeModal}><section ref={modalRef} className="modal" role="dialog" aria-modal="true" aria-label={deleteTarget ? '删除项目' : modal === 'history' ? '历史项目' : '使用指南'} onClick={e => e.stopPropagation()}><div className="modal-heading"><h2>{deleteTarget ? '删除这个项目？' : modal === 'history' ? '你的研究项目' : '从一个问题开始'}</h2><button className="icon-button" autoFocus aria-label="关闭" disabled={deleting} onClick={closeModal}><X size={20}/></button></div>
      {deleteTarget ? <div className="project-delete-confirm"><div className="delete-project-name"><FolderSimple size={22}/><strong>{deleteTarget.title}</strong></div><p>删除后，项目及其对话、文档将从工作台移除，无法在这里恢复。</p><p className="muted">该项目正在运行的 AI 任务会停止。原始访谈录音保留，不会同时删除。</p><div className="delete-actions"><button className="secondary" autoFocus disabled={deleting} onClick={() => setDeleteTarget(null)}>取消</button><button className="danger-button" disabled={deleting} onClick={() => void deleteProject()}>{deleting ? '正在删除…' : '删除项目'}</button></div></div> : modal === 'history' ? <><label className="search-box"><MagnifyingGlass size={18}/><input autoFocus placeholder="搜索项目名称" value={query} onChange={e => setQuery(e.target.value)}/></label><div className="history-list">{projects.filter(p => p.title.includes(query)).map(p => <ProjectRow key={p.id} item={p} onClick={() => void openProject(p.id)} onDelete={() => setDeleteTarget(p)}/>)}{!projects.filter(p => p.title.includes(query)).length && <div className="empty-state"><FolderSimple size={36}/><p>{query ? '没有找到相关项目' : '还没有项目，从一个研究问题开始吧'}</p></div>}</div></>
        : <div className="guide-content"><p>把想了解的人、企业或地方告诉体察。面试准备、市场调研与企业访谈共用工作台；面试可在画布中准备、录音模拟与复盘重练；口述史直接进入对谈，聊天结束后整理到人生档案。</p><ol><li>定义调研目标、研究问题与目标人群。</li><li>审阅研究方案与访谈提纲，开展访谈并整理原始证据。</li><li>提炼研究发现与洞察，形成需求建议和后续验证方向；审阅后导出研究成果。</li></ol><aside>新建项目已接入真实 AI：确认调研目标后，生成研究方案与访谈提纲，勾选提纲中的问题进入录音，也可导入已有录音或文字稿，再进行洞察分析和研究报告整理。生成内容需核对来源与原文。功能与访问方式以当前工作空间配置为准。</aside></div>}
    </section></div>}
  </>;
}

function ProjectRow({ item, onClick, onDelete }: { item: ProjectSummary; onClick: () => void; onDelete?: () => void }) {
  const Icon = sceneIcons[item.scenario];
  return <div className="project-row" data-project-id={item.id}><button className="project-row-open" onClick={onClick}><span className="project-icon"><Icon size={21}/></span><span className="project-row-text"><strong>{item.title}</strong><small>{item.scenario === 'interview' ? '录音陪练' : item.mode === 'ai' ? 'AI 辅助研究' : '历史示例'} · {fallbackNames[item.scenario]} · {item.scenario === 'memoir' && item.mode === 'ai' ? '对谈与人生档案' : item.stage ? '进行中' : item.scenario === 'interview' ? '待确认面试目标' : '待定义研究目标'}</small></span><time>{new Date(item.updated_at * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}</time><ArrowUpRight size={17}/></button>{onDelete && <button className="project-row-delete" aria-label={`删除项目：${item.title}`} title="删除项目" onClick={onDelete}><Trash size={17}/></button>}</div>;
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
        <div className="hero-heading"><div className="hero-wordmark" aria-label="体察 Fieldwork"><span className="wordmark-cn">体察</span><span className="wordmark-en">Fieldwork</span></div><TypewriterSubtitle text="以访谈，唤出知识与经验。"/></div>
        <form className="home-composer" onSubmit={submit}><label htmlFor="research-prompt">这次想研究什么问题？</label><textarea ref={input} id="research-prompt" maxLength={2000} rows={2} placeholder="描述你的调研目标、目标用户或待验证假设，也可以准备面试、答辩与口述史……" value={prompt} onChange={e => { setPrompt(e.target.value); setScene(undefined); }} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }}/><div className="composer-bottom"><span><Sparkle size={15}/>{scene ? `已选择${fallbackNames[scene]}` : '明确目标，形成研究方案'}</span><button type="submit" className="send-button" aria-label="创建研究项目" disabled={!prompt.trim() || busy}>{busy ? <ArrowClockwise className="spin" size={22}/> : <ArrowUp size={22} weight="bold"/>}</button></div></form>
        <div className="scenario-suggestions">{examples.map(({ scene: s, prompt: p, label }) => { const Icon = sceneIcons[s]; return <button key={s} onClick={() => { setPrompt(p); setScene(s); input.current?.focus(); }}><Icon size={17}/><span>{fallbackNames[s]}</span><span className="suggestion-tooltip">{label}</span><ArrowUpRight size={13}/></button>; })}</div>
      </div>
      <div className="hero-floor"><button className="backdrop-toggle" aria-pressed={revealed} onClick={() => setRevealed(!revealed)}><Cursor size={16}/>{revealed ? '恢复光标擦除' : '移动光标，探索底图'}<span>{revealed ? '收起' : '查看'}</span></button><span className="hero-note">问得深入，才能看得清楚。</span></div>
    </section>
    <section className="recent-section"><div className="section-heading"><h2><Clock size={18}/>最近的项目</h2><button onClick={onHistory}>全部项目 <ArrowUpRight size={15}/></button></div>{projects.length ? <div className="recent-grid">{projects.slice(0, 3).map(item => <ProjectRow key={item.id} item={item} onClick={() => onOpen(item.id)}/>)}</div> : <button className="first-project" onClick={() => input.current?.focus()}><Plus size={20}/><span>你的第一个研究项目，从明确问题开始</span><ArrowUpRight size={16}/></button>}</section>
    <footer className="home-footer"><span>体察 · 访谈与研究工作台</span><button onClick={onGuide}><Question size={15}/>如何开始</button></footer>
  </div>;
}
