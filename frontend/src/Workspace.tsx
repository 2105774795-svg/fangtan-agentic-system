import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpRight, CaretDown, CaretRight, Check, CirclesThree, Clock, DownloadSimple, FileText, FlowArrow, FolderSimple, Microphone, Plus, SidebarSimple } from '@phosphor-icons/react';
import { ArtifactEditor } from './ArtifactEditor';
import { AssistantPanel, type AssistantTab } from './AssistantPanel';
import { ResearchMap } from './ResearchMap';
import { MaterialCapture } from './MaterialCapture';
import { InterviewRecorder } from './InterviewRecorder';
import { download, editableAnswers, type Layout, type Project, type Scenario, type Scene } from './types';
import './workspace.css';
import { WorkspaceShell } from './WorkspaceShell';

type Props = { project: Project; scenarios: Record<Scene, Scenario>; busy: boolean; command: (body: Record<string, unknown>) => Promise<Project | undefined>; onLayout: (positions: Layout['positions']) => void; onHome: () => void; onStartAI: () => void; onHistory: () => void };

export function Workspace({ project, scenarios, busy, command, onLayout, onHome, onStartAI, onHistory }: Props) {
  const [view, setView] = useState(() => new URLSearchParams(window.location.search).get('view') === 'capture' ? 'interviews' : 'flow');
  const [selectedStep, setSelectedStep] = useState(project.stage);
  const [artifactId, setArtifactId] = useState<string | null>(null);
  const [focusParagraph, setFocusParagraph] = useState<number | null>(null);
  const [chatVisible, setChatVisible] = useState(true);
  const [assistantTab, setAssistantTab] = useState<AssistantTab>('chat');
  const [answers, setAnswers] = useState(() => editableAnswers(project));
  const [customAnswers, setCustomAnswers] = useState<Record<string, string>>(project.custom_answers || {});
  const [context, setContext] = useState(project.context);
  const [location, setLocation] = useState(project.location || {});
  const [editorDirty, setEditorDirty] = useState(false);
  const [captureBusy, setCaptureBusy] = useState(false);
  const lastOutline = useRef<string | null>(null);
  const lastCreatedDocument = useRef<string | null>(null);
  const [exportMenu, setExportMenu] = useState(false);
  const current = scenarios[project.scenario];
  const artifact = project.artifacts.find(item => item.id === artifactId);
  const working = busy || !!project.job && ['queued', 'running', 'retry'].includes(project.job.status);
  const draftDirty = project.stage === 0 && (JSON.stringify(answers) !== JSON.stringify(editableAnswers(project)) || context !== project.context || JSON.stringify(location) !== JSON.stringify(project.location || {}));
  const customDirty = project.stage === 0 && JSON.stringify(customAnswers) !== JSON.stringify(project.custom_answers || {});
  const dirty = customDirty || editorDirty || draftDirty || captureBusy;
  const allowLeave = useCallback(() => { if (captureBusy) { alert('请先暂停录音并等待最后分片保存到本机，再离开采集页。'); return false; } return !dirty || confirm('还有未保存的修改，确定离开吗？'); }, [dirty, captureBusy]);
  const act = useCallback((body: Record<string, unknown>) => command(body).catch(() => undefined), [command]);
  useEffect(() => { setSelectedStep(project.stage); }, [project.stage]);
  useEffect(() => { const last = project.messages[project.messages.length - 1]; if (last?.document_created && last.artifact_id && lastCreatedDocument.current !== last.artifact_id) { lastCreatedDocument.current = last.artifact_id; setSelectedStep(last.document_created.step); } }, [project.messages]);
  useEffect(() => {
    const outline = project.artifacts.find(item => item.step === 2 && item.kind !== 'question_selection' && item.kind !== 'document');
    if (project.stage === 2 && outline && lastOutline.current !== outline.id && (!project.job || project.job.status === 'ready')) { lastOutline.current = outline.id; setView('interviews'); setArtifactId(null); }
  }, [project.stage, project.job?.status, project.artifacts]);
  useEffect(() => { const url = new URL(window.location.href); if (view === 'interviews' || view === 'materials') url.searchParams.set('view', 'capture'); else url.searchParams.delete('view'); history.replaceState({}, '', url.pathname + url.search); }, [view]);
  useEffect(() => { setAnswers(editableAnswers(project)); setCustomAnswers(project.custom_answers || {}); setContext(project.context); }, [project.scenario]);
  useEffect(() => { if (project.known_industry) setAnswers(previous => previous.industry?.length === 1 && previous.industry[0] === project.known_industry ? previous : { ...previous, industry: [project.known_industry!] }); }, [project.known_industry]);
  useEffect(() => { setLocation(project.location || {}); }, [JSON.stringify(project.location)]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    addEventListener('beforeunload', guard);
    return () => removeEventListener('beforeunload', guard);
  }, [dirty]);
  useEffect(() => {
    if (!exportMenu) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setExportMenu(false); };
    addEventListener('keydown', close);
    return () => removeEventListener('keydown', close);
  }, [exportMenu]);
  const openArtifact = useCallback((id: string) => {
    if (captureBusy) { alert('请先暂停录音并等待最后分片保存到本机。'); return; }
    if (editorDirty && !confirm('还有未保存的成果修改，确定离开吗？')) return;
    setFocusParagraph(null);
    setArtifactId(id);
  }, [editorDirty, captureBusy]);
  const navigate = (next: string) => { if (captureBusy) { alert('请先暂停录音并等待最后分片保存到本机，再离开采集页。'); return; } if (!editorDirty || confirm('还有未保存的成果修改，确定离开吗？')) { setView(next); setArtifactId(null); } };
  const selectStep = (index: number) => { setSelectedStep(index); };
  const showAssistant = (tab: AssistantTab) => { setChatVisible(true); setAssistantTab(tab); };
  const latestReport = [...project.artifacts].reverse().find(item => item.step === 5 && !item.stale && item.kind !== 'document');
  const exportAll = () => { download(`${project.title}.md`, `# ${project.title}\n\n场景：${current.name}\n\n> ${project.mode === 'ai' ? '含 AI 草稿，请核对来源并人工审阅。' : '历史流程示例，未执行真实研究。'}\n\n${project.artifacts.map(item => `${item.content}\n\n---\n`).join('\n')}`); setExportMenu(false); };
  const pick = (key: string, value: string, multiple: boolean) => setAnswers(previous => ({ ...previous, [key]: multiple ? (previous[key] || []).includes(value) ? previous[key].filter(v => v !== value) : [...(previous[key] || []), value] : [value] }));


  return <WorkspaceShell scene={project.scenario} title={project.title} saved={captureBusy ? '正在录音 / 本机保存中' : project.connection_lost ? '连接中断，正在重连' : working ? '处理中' : dirty ? '修改待保存' : '已保存'} view={view} fileCount={project.artifacts.length} chatVisible={chatVisible} onChatVisible={setChatVisible} onNavigate={navigate} onHome={() => { if (allowLeave()) onHome(); }} onHistory={() => { if (allowLeave()) onHistory(); }} onToggleExport={() => setExportMenu(!exportMenu)} exportMenu={exportMenu && <div className="export-menu">{latestReport && <button onClick={() => { window.location.href = `/api/platform/projects/${encodeURIComponent(project.id)}/artifacts/${encodeURIComponent(latestReport.id)}/export`; setExportMenu(false); }}>导出研究报告 Word</button>}<button disabled={!project.artifacts.length} onClick={exportAll}>导出全部成果 Markdown</button><button onClick={() => { download(`${project.title}.json`, JSON.stringify(project, null, 2), 'application/json'); setExportMenu(false); }}>导出项目快照 JSON</button></div>}>
    <main className={`studio-main ${view === 'flow' && !artifact ? 'map-view' : 'document-view'}`}>
      {artifact ? <ArtifactEditor key={artifact.id} projectId={project.id} onRegenerate={project.mode === 'ai' ? () => { setArtifactId(null); showAssistant('chat'); void act({ action: 'regenerate_report' }); } : undefined} artifact={artifact} busy={working} referencePrefix={artifact.kind === 'material' ? `M${project.artifacts.filter(a => a.kind === 'material').findIndex(a => a.id === artifact.id) + 1}` : undefined} focusParagraph={focusParagraph} onReference={reference => { const match = /M(\d+)-P(\d+)/.exec(reference); if (!match) return; const material = project.artifacts.filter(a => a.kind === 'material')[Number(match[1]) - 1]; if (material && (!editorDirty || confirm('还有未保存的修改，确定查看原文吗？'))) { setArtifactId(material.id); setFocusParagraph(Number(match[2]) - 1); } }} onDirty={setEditorDirty} onBack={() => setArtifactId(null)} onSave={async content => !!await act({ action: 'artifact', artifact_id: artifact.id, content })}/>
      : view === 'flow' ? <><ResearchMap project={project.stage === 0 ? { ...project, answers, custom_answers: customAnswers } : project} current={current} selectedStep={selectedStep} onSelect={selectStep} onOpen={openArtifact} onTasks={() => showAssistant('tasks')} onLayout={onLayout}/><div className="selected-branch-chip"><span className={`stage-status ${selectedStep < project.stage ? 'complete' : selectedStep === project.stage ? 'current' : ''}`}/><span>{current.steps[selectedStep]}</span><button onClick={() => { const found = project.artifacts.find(item => item.step === selectedStep); if (found) openArtifact(found.id); else showAssistant('tasks'); }}>{project.artifacts.some(item => item.step === selectedStep) ? '查看成果' : '查看任务'}<ArrowUpRight size={12}/></button></div></>
      : view === 'files' ? <><div className="studio-page-heading"><span>项目工作空间</span><h1>{project.scenario === 'memoir' ? '讲述资料' : '研究资料'}</h1><p>集中管理研究方案、访谈原文、分析草稿与研究成果。</p></div><div className="studio-file-grid">{project.artifacts.length ? project.artifacts.map(item => <button className="studio-file-card" key={item.id} onClick={() => openArtifact(item.id)}><span><FileText size={26} weight="duotone"/></span><strong>{item.title}</strong><small>{current.steps[item.step]} · v{item.version}{item.stale ? ' · 需复核' : ''}</small><div><span>{item.demo ? '历史示例' : item.origin === 'ai' ? 'AI 草稿' : '原始 / 人工材料'}</span><ArrowUpRight size={16}/></div></button>) : <div className="empty-state"><FolderSimple size={40}/><h3>成果会在这里汇集</h3><p>先确认调研目标，准备计划与提纲会保存到此处。</p><button className="secondary" onClick={() => showAssistant('chat')}>继续定义调研目标</button></div>}</div></>
      : view === 'materials' ? <><div className="studio-page-heading"><button className="text-button" onClick={() => navigate('interviews')}>返回访谈</button><h1>添加访谈原文</h1></div><MaterialCapture project={project} busy={working} onDirty={setEditorDirty} onOpen={openArtifact} onSave={async (title, content) => !!await act({ action: 'material', title, content })}/></>
      : view === 'interviews' ? <><div className="studio-page-heading"><span>访谈执行与证据收集</span><h1>{project.scenario === 'memoir' ? '多次讲述' : '开展访谈'}</h1><p>先确认要问的问题，再进入专门的录音页；已有录音或文字稿也可以从这里归档。</p></div>{project.mode === 'ai' && project.stage >= 2 ? <InterviewRecorder project={project} busy={working} command={act} onBusy={setCaptureBusy} onPaste={() => navigate('materials')} onOpen={openArtifact} onReturn={() => navigate('flow')} outlineArtifact={project.artifacts.find(item => item.step === 2 && item.kind !== 'question_selection' && item.kind !== 'document')}/> : project.mode === 'ai' && project.stage >= 1 ? <MaterialCapture project={project} busy={working} onDirty={setEditorDirty} onOpen={openArtifact} onSave={async (title, content) => !!await act({ action: 'material', title, content })}/> : <div className="empty-state interviews-empty"><Microphone size={45}/><h3>先审阅访谈提纲</h3><p>提纲生成后，这里会出现可勾选的问题清单和现场录音。</p><button className="secondary" onClick={() => navigate('flow')}>查看研究方案<ArrowUpRight size={15}/></button></div>}</>
      : <><div className="studio-page-heading"><span>{current.name}</span><h1>项目概览</h1><p>{project.title}</p></div><div className="overview-panel"><section><h2>研究目标与问题</h2><p>{project.prompt}</p><dl><div><dt>研究方式</dt><dd>{current.name}</dd></div><div><dt>当前阶段</dt><dd>{current.steps[project.stage]}</dd></div><div><dt>已存成果</dt><dd>{project.artifacts.length} 份</dd></div></dl></section><section><h2>研究范围与对象</h2>{current.fields.map(field => <p key={field.key}><span>{field.label}</span><strong>{project.answers[field.key]?.join('、') || '尚待确认'}</strong></p>)}</section><button className="primary" onClick={() => { navigate('flow'); showAssistant('chat'); }}>继续研究<ArrowUpRight size={15}/></button></div></>}
    </main>
    {chatVisible && <AssistantPanel project={project} current={current} selectedStep={selectedStep} busy={busy} tab={assistantTab} onTab={setAssistantTab} onClose={() => setChatVisible(false)} answers={answers} customAnswers={customAnswers} onCustom={(key, value) => setCustomAnswers(previous => ({ ...previous, [key]: value }))} context={context} location={location} onLocation={setLocation} onStartAI={() => { if (allowLeave()) onStartAI(); }} onCapture={() => navigate('interviews')} onPick={pick} onContext={setContext} act={act} onOpen={openArtifact} onSelect={selectStep} onExport={exportAll}/>}
  </WorkspaceShell>;
}
