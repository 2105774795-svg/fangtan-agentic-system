import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowUp, ArrowUpRight, Plus, MagnifyingGlass, X, Clock, CaretRight, ArrowClockwise, BookOpen, Buildings, Storefront, CirclesThree, Question, Sparkle, Sun, Moon, Cursor, FolderSimple, WarningCircle, Trash } from '@phosphor-icons/react';
import { api, type Layout, type Project, type ProjectSummary, type Scenario, type Scene } from './types';
import { RevealBackdrop } from './RevealBackdrop';
import { Workspace } from './Workspace';
import { InterviewPreparation, prepApi, type Preparation } from './InterviewPreparation';
import { TypewriterSubtitle } from './TypewriterSubtitle';
import { requestId } from './requestId';
import { MemoirExperience } from './MemoirExperience';
import { interviewPreferences } from './interviewPreferences';

const sceneIcons = { market: Storefront, enterprise: Buildings, memoir: BookOpen, interview: Question };
const fallbackNames = { market: '市场调研', enterprise: '企业访谈', memoir: '口述史', interview: '面试准备' };
const examples: { scene: Scene; prompt: string; label: string }[] = [
  { scene: 'interview', prompt: '准备一场求职面试，围绕我的简历进行录音模拟', label: '模拟提问，录音回答，复盘重练' },
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
  const [prepId, setPrepId] = useState<string | null>(null);
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
    const prep = await prepApi<{ id: string; title: string; updated_at: number; stage: number }[]>('/projects').catch(() => []);
    const prepList: ProjectSummary[] = Array.isArray(prep) ? prep.map(p => ({ ...p, scenario: 'interview', stage: p.stage || 0, mode: 'ai' })) : [];
    setScenarios(specs); setProjects([...list, ...prepList].sort((a,b) => b.updated_at-a.updated_at));
  };
  useEffect(() => {
    let disposed = false;
    const boot = async () => {
      try {
        await loadLists();
        const prep = new URLSearchParams(location.search).get('prep');
        if (prep && !disposed) setPrepId(prep);
        const id = new URLSearchParams(location.search).get('project');
        if (id) { const data = await api<Project>(`/projects/${id}`); if (!disposed) setProject(data); }
      } catch (e) { if (!disposed) setError((e as Error).message); }
      finally { if (!disposed) setBooting(false); }
    };
    void boot();
    const pop = () => { const prep = new URLSearchParams(location.search).get('prep'); setPrepId(prep); const id = new URLSearchParams(location.search).get('project'); if (id) void openProject(id, false); else setProject(null); };
    addEventListener('popstate', pop);
    return () => { disposed = true; removeEventListener('popstate', pop); };
  }, []);
  async function openProject(id: string, push = true) {
    setBusy(true); setError('');
    try {
      if (id.startsWith('ip_')) { setPrepId(id); setProject(null); setModal(null); if (push) history.pushState({}, '', `/?prep=${id}`); return; }
      setPrepId(null);
      const data = await api<Project>(`/projects/${id}`);
      setProject(data); setModal(null);
      if (push) history.pushState({}, '', `/?project=${id}`);
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function create(prompt: string, scene?: Scene, sourceId?: string) {
    if (!prompt.trim() || busy) return;
    if (!scene && /面试|答辩|求职/.test(prompt) && !/调研|访谈研究/.test(prompt)) scene = 'interview';
    setBusy(true); setError('');
    if (!creation.current || creation.current.prompt !== prompt || creation.current.scene !== scene || creation.current.sourceId !== sourceId) creation.current = { prompt, scene, sourceId, id: requestId() };
    try {
      if (scene === 'interview') {
        const data = await prepApi<Preparation>('/projects', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({title:prompt.trim().slice(0,120),request_id:creation.current.id,config:{template:/论文|开题|毕业|硕士|博士/.test(prompt)?'thesis':'job',goal:prompt.trim(),language:'中文',practice_mode:'coaching',question_count:5,followup_mode:'adaptive',include_candidate_questions:true,max_followups:1,duration_minutes:20,...interviewPreferences(prompt)}}) });
        setPrepId(data.id); setProject(null); setModal(null); history.pushState({},'',`/?prep=${data.id}`); creation.current=null; void loadLists().catch(()=>{}); return;
      }
      const data = await api<Project>('/projects', { method: 'POST', body: JSON.stringify({ prompt: prompt.trim(), scenario: scene, mode: 'ai', source_project_id: sourceId, request_id: creation.current.id }) });
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
      setProject(previous => previous?.id === result.id && previous.revision > result.revision ? previous : result); void loadLists().catch(() => {});
      return result;
    } catch (e) { setError((e as Error).message); throw e; }
    finally { setBusy(false); }
  }
  useEffect(() => {
    if (!project?.id || (project.scenario === 'memoir' && project.mode === 'ai')) return;
    // The conversation surface owns its SSE stream; avoid duplicate long-lived
    // HTTP/1 connections that can otherwise starve microphone uploads across tabs.
    const id = project.id;
    const stream = new EventSource(`/api/platform/projects/${id}/events`);
    stream.addEventListener('snapshot', event => {
      try {
        const data = JSON.parse((event as MessageEvent).data) as Project;
        setProject(previous => previous?.id === id && data.revision >= previous.revision ? { ...data, connection_lost: false } : previous);
      } catch { /* An incomplete SSE frame is ignored; reconnect restores a full snapshot. */ }
    });
    stream.addEventListener('deleted', () => { stream.close(); setProject(previous => previous?.id === id ? null : previous); setProjects(previous => previous.filter(item => item.id !== id)); history.replaceState({}, '', '/'); });
    stream.onerror = () => setProject(previous => previous?.id === id ? { ...previous, connection_lost: true } : previous);
    return () => stream.close();
  }, [project?.id, project?.scenario, project?.mode]);
  async function saveLayout(positions: Layout['positions']) {
    if (!project) return;
    try {
      const layout = await api<Layout>(`/projects/${project.id}/layout`, { method: 'PUT', body: JSON.stringify({ version: project.layout?.version || 0, positions }) });
      setProject(previous => previous?.id === project.id ? { ...previous, layout: (previous.layout?.version || 0) > layout.version ? previous.layout : layout } : previous);
    } catch (e) { setError((e as Error).message); }
  }
  async function deleteProject() {
    if (!deleteTarget || deleting) return;
    const id = deleteTarget.id;
    setDeleting(true); setError('');
    try {
      if (deleteTarget.scenario === 'interview') await prepApi(`/projects/${id}`, { method:'DELETE' });
      else await api(`/projects/${id}`, { method: 'DELETE' });
      if (prepId === id) { setPrepId(null); history.replaceState({}, '', '/'); }
      setProjects(previous => previous.filter(item => item.id !== id));
      if (project?.id === id) { setProject(null); history.replaceState({}, '', '/'); }
      setDeleteTarget(null);
      void loadLists().catch(() => {});
    } catch (cause) { setError((cause as Error).message); }
    finally { setDeleting(false); }
  }
  function closeModal() { if (deleting) return; if (deleteTarget) setDeleteTarget(null); else setModal(null); }
  function home() { setPrepId(null); setProject(null); setError(''); history.pushState({}, '', '/'); void loadLists().catch(() => {}); }
  return <>
    {prepId ? <InterviewPreparation key={prepId} id={prepId} onHome={home} onHistory={() => setModal('history')}/> : project && scenarios ? project.scenario === 'memoir' && project.mode === 'ai' ? <MemoirExperience key={project.id} project={project} onHome={home} onHistory={() => setModal('history')}/> : <Workspace key={project.id} project={project} scenarios={scenarios} busy={busy} command={command} onLayout={saveLayout} onStartAI={() => void create(project.prompt, project.scenario, project.id)} onHome={home} onHistory={() => setModal('history')}/>
      : <Home busy={busy || booting} projects={projects} onCreate={create} onOpen={openProject} onHistory={() => setModal('history')} onGuide={() => setModal('guide')} theme={theme} onTheme={() => setTheme(theme === 'light' ? 'dark' : 'light')}/>}
    {!project && !prepId && /登录|账号/.test(error) && <section className="prep-login"><h2>登录工作空间</h2><form onSubmit={async e => {e.preventDefault();setBusy(true);try{const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:loginName,password:loginPassword})});const body=await response.json();if(!response.ok)throw new Error(body.detail||'登录失败');setLoginPassword('');setError('');location.reload();}catch(cause){setError((cause as Error).message);}finally{setBusy(false);}}}><input aria-label="员工姓名" autoComplete="username" placeholder="员工姓名" value={loginName} onChange={e=>setLoginName(e.target.value)}/><input aria-label="密码" type="password" autoComplete="current-password" placeholder="密码" value={loginPassword} onChange={e=>setLoginPassword(e.target.value)}/><button className="primary" disabled={busy}>登录</button></form><p>使用已配置的员工中心账号。仅在本机体验时，可按安装文档显式启用单人模式。</p></section>}
    {error && <div className="error-toast" role="alert"><WarningCircle size={20}/><span>{error}</span>{project && <button onClick={() => location.reload()}>重新加载</button>}{!project && <button onClick={() => { setError(''); void loadLists().catch(e => setError(e.message)); }}>重试</button>}<button aria-label="关闭错误提示" onClick={() => setError('')}><X size={16}/></button></div>}
    {modal && <div className="modal-backdrop" onClick={closeModal}><section ref={modalRef} className="modal" role="dialog" aria-modal="true" aria-label={deleteTarget ? '删除项目' : modal === 'history' ? '历史项目' : '使用指南'} onClick={e => e.stopPropagation()}><div className="modal-heading"><h2>{deleteTarget ? '删除这个项目？' : modal === 'history' ? '你的调研项目' : '从一个问题开始'}</h2><button className="icon-button" autoFocus aria-label="关闭" disabled={deleting} onClick={closeModal}><X size={20}/></button></div>
      {deleteTarget ? <div className="project-delete-confirm"><div className="delete-project-name"><FolderSimple size={22}/><strong>{deleteTarget.title}</strong></div><p>删除后，项目及其对话、文档将从工作台移除，无法在这里恢复。</p><p className="muted">该项目正在运行的 AI 任务会停止。原始访谈录音保留，不会同时删除。</p><div className="delete-actions"><button className="secondary" autoFocus disabled={deleting} onClick={() => setDeleteTarget(null)}>取消</button><button className="danger-button" disabled={deleting} onClick={() => void deleteProject()}>{deleting ? '正在删除…' : '删除项目'}</button></div></div> : modal === 'history' ? <><label className="search-box"><MagnifyingGlass size={18}/><input autoFocus placeholder="搜索项目名称" value={query} onChange={e => setQuery(e.target.value)}/></label><div className="history-list">{projects.filter(p => p.title.includes(query)).map(p => <ProjectRow key={p.id} item={p} onClick={() => void openProject(p.id)} onDelete={() => setDeleteTarget(p)}/>)}{!projects.filter(p => p.title.includes(query)).length && <div className="empty-state"><FolderSimple size={36}/><p>{query ? '没有找到相关项目' : '还没有项目，写下第一个调研问题吧'}</p></div>}</div></>
        : <div className="guide-content"><p>把想了解的人、企业或地方告诉体察。面试准备通过对话确认目标，进入录音模拟与复盘重练；市场调研与企业访谈进入工作台；口述史直接进入对谈，聊天结束后整理到人生档案。</p><ol><li>选一选，确认目标与范围。</li><li>沿着画布，查看准备计划与提纲。</li><li>打开成果修改，保存版本或导出。</li></ol><aside>新建项目已接入真实 AI：确认需求后，生成背景与提纲，勾选提纲中的问题进入录音，也可导入已有录音或文字稿，再进行分析和报告整理。生成内容需核对来源与原文。手机接续尚未接入，当前无需登录。</aside></div>}
    </section></div>}
  </>;
}

function ProjectRow({ item, onClick, onDelete }: { item: ProjectSummary; onClick: () => void; onDelete?: () => void }) {
  const Icon = sceneIcons[item.scenario];
  return <div className="project-row" data-project-id={item.id}><button className="project-row-open" onClick={onClick}><span className="project-icon"><Icon size={21}/></span><span className="project-row-text"><strong>{item.title}</strong><small>{item.scenario === 'interview' ? '录音陪练' : item.mode === 'ai' ? 'AI 调研' : '历史示例'} · {fallbackNames[item.scenario]} · {item.scenario === 'memoir' && item.mode === 'ai' ? '对谈与人生档案' : item.stage ? '进行中' : '待确认需求'}</small></span><time>{new Date(item.updated_at * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}</time><ArrowUpRight size={17}/></button>{onDelete && <button className="project-row-delete" aria-label={`删除项目：${item.title}`} title="删除项目" onClick={onDelete}><Trash size={17}/></button>}</div>;
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
        <div className="hero-heading"><div className="hero-wordmark" aria-label="体察 Fieldwork"><span className="wordmark-cn">体察</span><span className="wordmark-en">Fieldwork</span></div><TypewriterSubtitle text="让每一次对话，都有迹可循。"/></div>
        <form className="home-composer" onSubmit={submit}><label htmlFor="research-prompt">今天想准备哪一次对话？</label><textarea ref={input} id="research-prompt" maxLength={2000} rows={2} placeholder="一场面试、一篇论文答辩，或一次值得深入的访谈……" value={prompt} onChange={e => { setPrompt(e.target.value); setScene(undefined); }} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }}/><div className="composer-bottom"><span><Sparkle size={15}/>{scene ? `已选择${fallbackNames[scene]}` : '让体察帮你规划下一步'}</span><button type="submit" className="send-button" aria-label="开始调研" disabled={!prompt.trim() || busy}>{busy ? <ArrowClockwise className="spin" size={22}/> : <ArrowUp size={22} weight="bold"/>}</button></div></form>
        <div className="scenario-suggestions">{examples.map(({ scene: s, prompt: p, label }) => { const Icon = sceneIcons[s]; return <button key={s} onClick={() => { setPrompt(p); setScene(s); input.current?.focus(); }}><Icon size={17}/><span>{fallbackNames[s]}</span><span className="suggestion-tooltip">{label}</span><ArrowUpRight size={13}/></button>; })}</div>
      </div>
      <div className="hero-floor"><button className="backdrop-toggle" aria-pressed={revealed} onClick={() => setRevealed(!revealed)}><Cursor size={16}/>{revealed ? '恢复光标擦除' : '移动光标，探索底图'}<span>{revealed ? '收起' : '查看'}</span></button><span className="hero-note">问得深入，才能看得清楚。</span></div>
    </section>
    <section className="recent-section"><div className="section-heading"><h2><Clock size={18}/>最近的项目</h2><button onClick={onHistory}>全部项目 <ArrowUpRight size={15}/></button></div>{projects.length ? <div className="recent-grid">{projects.slice(0, 3).map(item => <ProjectRow key={item.id} item={item} onClick={() => onOpen(item.id)}/>)}</div> : <button className="first-project" onClick={() => input.current?.focus()}><Plus size={20}/><span>你的第一份调研，从上面的一个问题开始</span><ArrowUpRight size={16}/></button>}</section>
    <footer className="home-footer"><span>体察 · 调研工作台</span><button onClick={onGuide}><Question size={15}/>如何开始</button></footer>
  </div>;
}
