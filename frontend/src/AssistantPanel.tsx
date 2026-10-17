import { useEffect, useRef, useState } from 'react';
import { ArrowClockwise, ArrowUpRight, CaretDown, CaretRight, Check, DownloadSimple, FileText, FolderSimple, ListChecks, MagnifyingGlass, Plus, SidebarSimple, Sparkle, X } from '@phosphor-icons/react';
import { sceneRegistry } from './sceneRegistry';
import { MarkdownContent } from './MarkdownContent';
import { visibleSteps, type Project, type Scenario } from './types';

export type AssistantTab = 'chat' | 'sources' | 'tasks' | 'insights';
type Props = {
  project: Project; current: Scenario; selectedStep: number; busy: boolean;
  tab: AssistantTab; onTab: (tab: AssistantTab) => void; onClose: () => void;
  answers: Record<string, string[]>; customAnswers: Record<string, string>; onCustom: (key: string, value: string) => void; context: string; location: Project['location'];
  onLocation: (location: NonNullable<Project['location']>) => void; onCapture: () => void; onStartAI: () => void;
  onPick: (key: string, value: string, multiple: boolean) => void;
  onContext: (context: string) => void;
  act: (body: Record<string, unknown>) => Promise<Project | undefined>;
  onOpen: (id: string) => void; onSelect: (index: number) => void; onExport: () => void;
  onUploadResume?: (file: File) => Promise<void>;
};

export function AssistantPanel({ project, current, selectedStep, busy, tab, onTab, onClose, answers, customAnswers, onCustom, context, onPick, onContext, act, onOpen, onSelect, onExport, location, onLocation, onCapture, onStartAI, onUploadResume }: Props) {
  const [chat, setChat] = useState('');
  const ai = project.mode === 'ai';
  const interview = project.scenario === 'interview';
  const research = project.scenario === 'market' || project.scenario === 'enterprise';
  const language = sceneRegistry[project.scenario];
  const running = !!project.job && ['queued', 'running', 'retry'].includes(project.job.status);
  const [attachment, setAttachment] = useState<{ name: string; status: 'uploading' | 'ready' | 'failed'; message: string } | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const retryFile = useRef<File | null>(null);
  const uploading = useRef(false);
  const dragDepth = useRef(0);
  const blocked = busy || running || attachment?.status === 'uploading';
  const uploadResume = async (files: File[]) => {
    if (!onUploadResume || uploading.current) return;
    if (blocked) { setAttachment({ name: '', status: 'failed', message: '请等待当前操作完成，再上传简历。' }); return; }
    retryFile.current = null;
    const file = files[0];
    const message = files.length !== 1 ? '请一次上传一份简历。'
      : !/\.(pdf|docx|txt|md|pptx)$/i.test(file.name) ? '支持 PDF、DOCX、TXT、MD、PPTX 文件；旧版 DOC 请另存为 DOCX。'
      : !file.size ? '文件为空，请重新选择。'
      : file.size > 20 * 1024 * 1024 ? '简历文件最多 20MB，请压缩或拆分后上传。' : '';
    if (message) { setAttachment({ name: file?.name || '', status: 'failed', message }); return; }
    retryFile.current = file; uploading.current = true; setReferences(false);
    setAttachment({ name: file.name, status: 'uploading', message: '正在上传并解析简历…' });
    try {
      await onUploadResume(file);
      setAttachment({ name: file.name, status: 'ready', message: '已加入项目资料，后续讨论会参考这份简历。' });
      retryFile.current = null;
    } catch (cause) {
      setAttachment({ name: file.name, status: 'failed', message: cause instanceof Error ? cause.message : '上传未完成，请重试。' });
    } finally { uploading.current = false; }
  };
  const reached = visibleSteps(project);
  const fields = project.checklist || current.fields;
  const unansweredFields = fields.filter(field => !field.resolved_value);
  const effectiveCustom = Object.fromEntries(Object.entries(customAnswers).filter(([key]) => answers[key]?.includes('其他')));
  const settled = !blocked && (!project.job || project.job.status === 'ready');
  const checklistReady = !interview && settled && project.messages.some(m => m.role === 'assistant');
  const needsAnalysis = project.artifacts.some(a => a.step >= 4 && a.stale) && project.artifacts.some(a => a.kind === 'material');
  const advanceText = needsAnalysis ? '根据最新证据更新分析' : ['', ai ? '审阅方案，生成访谈提纲' : '审阅方案，生成提纲示例', ai ? '勾选提纲，准备访谈' : '确认提纲，进入采集准备', '根据原始证据提炼洞察', '生成研究报告草稿', '确认研究成果'][project.stage];
  const [references, setReferences] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const brief = useRef<HTMLDetailsElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const followOutput = useRef(true);
  const [showLatest, setShowLatest] = useState(false);
  const generation = useRef<HTMLElement>(null);
  useEffect(() => { followOutput.current = true; setShowLatest(false); }, [project.job?.id]);
  useEffect(() => {
    if (tab !== 'chat' || !followOutput.current) return;
    const frame = requestAnimationFrame(() => {
      const element = scroll.current;
      if (!element) return;
      if (project.stage === 0 && checklistReady && brief.current) element.scrollTop = Math.max(0, brief.current.offsetTop - element.offsetTop - 18);
      else element.scrollTop = element.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [project.job?.output, project.job?.status, project.messages.length, tab, checklistReady]);
  const complete = fields.every(field => (!!field.resolved_value || (answers[field.key] || []).length > 0) && (!(answers[field.key] || []).includes('其他') || !!customAnswers[field.key]?.trim()));
  const focusBrief = () => { onTab('chat'); requestAnimationFrame(() => { if (brief.current) { brief.current.open = true; brief.current.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' }); } }); };
  const count = fields.reduce((n, field) => n + (answers[field.key]?.length || 0), 0);
  return <aside className="studio-assistant">
    <div className="assistant-tabs" role="tablist" aria-label="调研助手面板">
      {([{ key: 'chat', label: sceneRegistry[project.scenario].assistantLabel }, { key: 'sources', label: research ? '研究资料' : '资料' }, { key: 'tasks', label: '任务' }, { key: 'insights', label: language.insightsLabel }] as const).map(item => <button key={item.key} role="tab" id={`assistant-tab-${item.key}`} aria-controls="assistant-content" aria-selected={tab === item.key} tabIndex={tab === item.key ? 0 : -1} onClick={() => onTab(item.key)} onKeyDown={event => { if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); const tabs: AssistantTab[] = ['chat', 'sources', 'tasks', 'insights']; const next = tabs[(tabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : 3)) % 4]; onTab(next); document.getElementById(`assistant-tab-${next}`)?.focus(); } }}>{item.label}{item.key === 'tasks' && <small>{project.completed ? 0 : 1}</small>}</button>)}
      <button className="icon-button collapse-assistant" aria-label="收起助手" onClick={onClose}><SidebarSimple size={19}/></button>
    </div>
    {!ai && <section className="demo-upgrade" aria-label="历史示例提示"><strong>这是旧版流程示例，未调用 AI 或联网检索</strong><p>带入主题与已保存的选项，重新确认后开始真实调研。旧项目保留，示例成果不带入。</p><button disabled={blocked} onClick={onStartAI}>转为真实 AI 调研<ArrowUpRight size={15}/></button></section>}
    <div id="assistant-content" role="tabpanel" aria-labelledby={`assistant-tab-${tab}`} className="assistant-content" ref={scroll} onScroll={event => { const element = event.currentTarget; followOutput.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100; setShowLatest(!followOutput.current); }}>
      {tab === 'chat' ? <>
        <div className="conversation-date">{interview ? '面试准备 · 场外讨论' : ai ? 'AI 辅助研究' : '历史示例调研'}</div>
        {interview && !project.messages.length && !project.job && <div className="studio-message assistant"><span className="studio-agent-icon"><Sparkle size={19}/></span><div className="studio-message-body"><strong>一起准备这次面试。</strong><p>你的目标和已有资料已保留。可以在这里讨论准备思路，也可以打开画布阶段查看材料、问题和练习。</p></div></div>}
        {project.messages.map((message, index) => <div key={message.id} className={`studio-message ${message.role}`}>
          {message.role === 'assistant' && <span className="studio-agent-icon"><Sparkle size={19} weight="fill"/></span>}
          <div className="studio-message-body">{message.role === 'assistant' && <strong>{index < 2 ? interview ? '一起准备这次面试。' : research ? '一起明确研究问题。' : '一起梳理讲述目标。' : '体察'}</strong>}<MarkdownContent text={(message.artifact_id && project.artifacts.find(a => a.id === message.artifact_id)?.content) || message.text}/>{message.document_created && <div className="document-created"><Check size={14}/>已新增至「{current.steps[message.document_created.step]}」：{message.document_created.title}</div>}{message.artifact_id && <button className="message-document" onClick={() => onOpen(message.artifact_id!)}><FileText size={15}/>打开已保存成果<ArrowUpRight size={13}/></button>}</div>
        </div>)}
        {project.job && project.job.status !== 'ready' && !project.messages.some(message => message.job_id === project.job?.id) && <section ref={generation} className={`live-generation status-${project.job.status}`} aria-label="AI 实时回复" aria-live="polite"><header><span>{running ? <ArrowClockwise className="spin" size={16}/> : <X size={16}/>}</span><strong>{running ? project.job.status === 'queued' ? '正在准备回复…' : project.job.stage : project.job.status === 'failed' ? '本次生成未完成' : '生成已取消'}</strong>{running && <button disabled={busy} onClick={() => void act({ action: 'cancel' })}>停止生成</button>}{['failed', 'cancelled'].includes(project.job.status) && <button disabled={busy} onClick={() => void act({ action: 'retry', ...(interview ? { chat_task_id: project.job?.id } : {}) })}>重试</button>}</header>{['failed', 'cancelled'].includes(project.job.status) && <p>{project.job.error || '已保留原有材料与生成片段。'}</p>}{project.job.output && <div className="streamed-response"><MarkdownContent text={project.job.output} streaming={running}/></div>}</section>}
        {project.stage === 0 && checklistReady ? <details className="briefing-card" open ref={brief}>
          <summary><span><ListChecks size={18}/>请确认本次研究范围</span><span>{count ? `已选择 ${count} 项` : '待补充'}<CaretDown size={14}/></span></summary>
          <div className="brief-form">
            {project.known_industry && <div className="resolved-context"><Check size={15}/><span>调研行业 / 产品：<strong>{project.known_industry}</strong></span><small>已从你的需求带入</small></div>}
            {unansweredFields.map((field, index) => <fieldset key={`${project.scenario}-${field.key}`}><legend><span>{index + 1}</span>{field.label}<small>{field.multiple ? '多选' : '单选'}</small></legend><div className="choice-options">{[...field.options, ...((field.allow_custom || ['goal', 'audience'].includes(field.key)) ? ['其他'] : [])].map(option => { const checked = (answers[field.key] || []).includes(option); return <button key={option} role={field.multiple ? 'checkbox' : 'radio'} aria-checked={checked} className={checked ? 'checked' : ''} disabled={blocked} onClick={() => onPick(field.key, option, field.multiple)}>{option}{checked && <Check size={12}/>}</button>; })}</div>{(answers[field.key] || []).includes('其他') && <label className="custom-choice-label">{field.key === 'goal' ? '补充研究问题' : '补充目标人群'}<input aria-label={field.key === 'goal' ? '其他调研目的' : '其他受访对象'} maxLength={160} disabled={blocked} value={customAnswers[field.key] || ''} onChange={event => onCustom(field.key, event.target.value)} placeholder={field.key === 'goal' ? '例如：评估上海新能源汽车售后服务的机会' : '例如：充电站运营方、新能源车主'}/><small>最多 160 字，将与已选项一起用于后续调研</small></label>}</fieldset>)}
            {ai && project.scenario !== 'memoir' && <div className="location-fields"><label>城市<input aria-label="调研城市" value={location?.city || ''} disabled={blocked} maxLength={64} onChange={event => onLocation({ ...location, city: event.target.value })} placeholder="例如：锦州"/></label><label>市场 / 园区<input aria-label="具体市场或园区" value={location?.market || ''} disabled={blocked} maxLength={120} onChange={event => onLocation({ ...location, market: event.target.value })} placeholder="选填"/></label></div>}          </div>
        </details> : null}
        {!interview && settled && project.stage === 3 && <div className="capture-prompt"><p>开展访谈，或导入已有录音与逐字稿，收集洞察分析所需的原始证据。</p><button className="secondary" onClick={onCapture}><Plus size={15}/>开展访谈</button></div>}
      </> : tab === 'sources' ? <div className="assistant-materials"><div className="assistant-section-title"><h2>{language.materialsLabel}</h2><span>{project.artifacts.length} 份</span></div><p className="panel-description">画布上的每份成果，都汇集在这里。</p>{project.artifacts.length ? project.artifacts.map(item => <button className="side-file" key={item.id} onClick={() => onOpen(item.id)}><span><FileText size={19}/></span><div><strong>{item.title}</strong><small>{current.steps[item.step]} · v{item.version}{item.stale ? ' · 需复核' : ''}</small></div><CaretRight size={14}/></button>) : <div className="assistant-empty"><FolderSimple size={35}/><h3>还没有资料</h3><p>{interview ? '上传简历、岗位要求或其他准备材料。' : '确认目标后，研究方案和访谈提纲会保存在这里。'}</p><button className="secondary" onClick={interview ? () => onSelect(1) : focusBrief}>{interview ? '添加准备材料' : '定义调研目标'}</button></div>}{project.research?.sources.map(source => <a key={source.id} className="public-source" href={source.url} target="_blank" rel="noopener noreferrer"><MagnifyingGlass size={16}/><span><strong>{source.title}</strong><small>{source.id} · {source.retrieved_at}</small></span><ArrowUpRight size={14}/></a>)}<div className="source-note"><MagnifyingGlass size={17}/><span>{ai ? project.research?.note || '本项目尚无外部检索来源。' : '历史示例未执行外部检索。'}</span></div></div>
      : tab === 'tasks' ? <div className="assistant-tasks"><div className="assistant-section-title"><h2>{interview ? '面试任务' : '研究任务'}</h2><span>{project.completed ? reached.length : reached.filter(index => index < project.stage).length} / {reached.length} 已确认</span></div><p className="panel-description">{interview ? '查看准备、练习和复盘的处理状态。' : '从调研目标到研究成果，查看每一步的状态。'}</p>{interview && project.tasks?.map(task => <section className="workspace-job" key={task.id}><strong>{task.stage || task.kind}</strong><small>{({ queued: '排队中', running: '进行中', ready: '已完成', failed: '未完成', cancelled: '已取消', retry: '正在恢复' } as Record<string,string>)[task.status] || task.status}</small>{task.error && <p>{task.error}</p>}</section>)}<div className="task-timeline">{reached.map(index => <section key={index} className={`task-item ${(index < project.stage || project.completed) ? 'complete' : index === project.stage ? 'current' : ''}`}><span className="task-marker">{(index < project.stage || project.completed) ? <Check size={12}/> : index + 1}</span><button onClick={() => onSelect(index)}><strong>{current.steps[index]}</strong><small>{(index < project.stage || project.completed) ? '已确认' : index === project.stage ? '等待操作' : '尚未开始'}</small></button>{project.artifacts.filter(a => a.step === index).map(item => <button key={item.id} className="task-file" onClick={() => onOpen(item.id)}><FileText size={14}/>{item.title}<ArrowUpRight size={12}/></button>)}</section>)}</div><aside className="panel-note">{ai ? '后续步骤将随着问答与操作逐步展开。生成结果是 AI 草稿，需核对原文与来源。' : '历史示例项目；新建研究项目将使用真实 AI。'}</aside></div>
      : interview ? <div className="assistant-insights"><div className="assistant-section-title"><h2>面试复盘</h2><Sparkle size={20}/></div><p className="panel-description">原话核对后的复盘建议，可回到对应练习查看与重练。</p>{project.scene_state?.insights.length ? project.scene_state.insights.map((item, i) => <button className="side-file" key={i} onClick={() => onOpen(item.artifact_id)}><Sparkle size={18}/><span>{item.text}</span><ArrowUpRight size={14}/></button>) : <p>完成练习并生成复盘后，复盘建议会出现在这里。模拟模式不提前公开点评。</p>}</div> : <div className="assistant-insights"><div className="assistant-section-title"><h2>{language.insightsLabel}</h2><Sparkle size={18}/></div><p className="panel-description">用原始证据支撑研究发现，说明洞察依据与待验证假设。</p><section className="insight-section"><h3>已确认的研究问题</h3>{project.answers.goal?.length ? <div className="insight-chips">{project.answers.goal.map(goal => <span key={goal}>{goal}</span>)}</div> : <p>尚未确认。可以先在对话中选择本次关注的主题。</p>}</section><section className="insight-section"><h3>研究证据</h3><p>已保存 {project.artifacts.length} 份成果，其中 {project.artifacts.filter(a => a.kind === 'material').length} 份原始文字材料，{project.research?.sources.length || 0} 个公开检索来源。</p></section>{project.artifacts.some(a => a.step >= 4 && !a.demo) ? project.artifacts.filter(a => a.step >= 4 && !a.demo).map(a => <button key={a.id} className="side-file" onClick={() => onOpen(a.id)}><FileText size={20}/><div><strong>{a.title}</strong><small>{a.stale ? '来源已更新，需重新分析' : '查看带原文引用的草稿'}</small></div><ArrowUpRight size={14}/></button>) : <div className="assistant-empty"><Sparkle size={33}/><h3>从原始证据提炼洞察</h3><p>添加访谈原文后，分析将保留段落引用。研究发现描述现象，洞察解释原因，需求建议与待验证假设仍需核实。</p></div>}</div>}
    </div>
    {tab === 'chat' && showLatest && <button className="secondary" onClick={() => { followOutput.current = true; setShowLatest(false); if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }}>回到最新<CaretDown size={14}/></button>}
    <form className={`studio-composer unified-composer ${dragging ? 'resume-dragging' : ''}`} aria-busy={attachment?.status === 'uploading'}
      onDragEnter={event => { if (!onUploadResume || !event.dataTransfer.types.includes('Files')) return; event.preventDefault(); dragDepth.current++; setDragging(true); }}
      onDragOver={event => { if (!onUploadResume || !event.dataTransfer.types.includes('Files')) return; event.preventDefault(); event.dataTransfer.dropEffect = blocked ? 'none' : 'copy'; }}
      onDragLeave={event => { if (!onUploadResume) return; event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); }}
      onDrop={event => { if (!onUploadResume || !event.dataTransfer.types.includes('Files')) return; event.preventDefault(); event.stopPropagation(); dragDepth.current = 0; setDragging(false); void uploadResume(Array.from(event.dataTransfer.files)); }}
      onSubmit={event => { event.preventDefault(); if (blocked) return; onTab('chat'); if (interview) { if (chat.trim()) void act({ action: 'message', content: chat }).then(result => { if (result) setChat(''); }); } else if (project.stage === 0) { if (checklistReady && complete) void act({ action: 'brief', answers, custom_answers: effectiveCustom, context, location }); } else if (chat.trim()) { void act({ action: 'message', content: chat, selected_step: selectedStep }).then(result => { if (result) { setChat(''); onTab('chat'); } }); } else if (settled) { if (ai && project.stage === 2) { onCapture(); return; } if (project.completed || !ai && project.stage >= 3) onExport(); else void act({ action: needsAnalysis ? 'regenerate' : 'advance' }); } }}>
      {onUploadResume && <input ref={fileInput} hidden type="file" aria-label="选择简历附件" accept=".pdf,.docx,.txt,.md,.pptx" onChange={event => { const files = Array.from(event.currentTarget.files || []); event.currentTarget.value = ''; if (files.length) void uploadResume(files); }}/>}
      {dragging && <div className="resume-drop-overlay"><Plus size={24}/><strong>{blocked ? '请等待当前操作完成' : '松开以上传简历'}</strong><small>文件将加入当前项目资料</small></div>}
      {attachment && <div className={`resume-attachment status-${attachment.status}`} role={attachment.status === 'failed' ? 'alert' : 'status'}>
        {attachment.status === 'uploading' ? <ArrowClockwise className="spin" size={18}/> : attachment.status === 'ready' ? <Check size={18}/> : <FileText size={18}/>}
        <div><strong title={attachment.name}>{attachment.name || '简历上传'}</strong><span>{attachment.message}</span></div>
        {attachment.status === 'failed' && retryFile.current && <button type="button" disabled={busy || running} onClick={() => { if (retryFile.current) void uploadResume([retryFile.current]); }}>重试上传</button>}
        {attachment.status === 'ready' && <button type="button" onClick={() => onTab('sources')}>查看资料</button>}
        {attachment.status !== 'uploading' && <button type="button" className="icon-button" aria-label="关闭简历上传提示" onClick={() => { setAttachment(null); retryFile.current = null; }}><X size={14}/></button>}
      </div>}
      <textarea ref={composer} aria-label="给体察助手补充信息" rows={2} maxLength={interview ? 12000 : project.stage === 0 ? 1000 : 2000} disabled={blocked} value={!interview && project.stage === 0 ? context : chat} placeholder={interview ? '讨论面试目标、岗位或准备思路…' : project.stage === 0 ? '补充研究背景、产品决策或待验证假设（选填）…' : '有修改意见？写在这里；满意则直接确认继续…'} onChange={event => !interview && project.stage === 0 ? onContext(event.target.value) : setChat(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); const button = event.currentTarget.form?.querySelector<HTMLButtonElement>('button[type="submit"]'); if (button && !button.disabled) event.currentTarget.form?.requestSubmit(); } }}/>
      <div className="composer-tools"><div><button type="button" className="icon-button" disabled={blocked || !interview && project.stage === 0} aria-label={interview ? "添加简历附件或引用成果" : "引用项目成果"} aria-expanded={references} onClick={() => setReferences(!references)}><Plus size={20}/></button><span className="composer-mode">{running ? 'AI 正在回复' : interview ? '场外讨论 · 不提交本题答案' : project.stage === 0 ? '确认研究范围' : chat.trim() ? '发送意见，继续讨论' : '审阅后继续研究'}</span></div><button type="submit" className="composer-submit" disabled={blocked || (interview ? !chat.trim() : project.stage === 0 && (!checklistReady || !complete) || project.stage > 0 && !chat.trim() && (!settled || ai && project.stage === 3 && !project.artifacts.some(a => a.kind === 'material')))}>{running ? '正在生成…' : interview ? '发送消息' : project.stage === 0 ? '确认目标，生成研究方案' : chat.trim() ? '发送补充意见' : project.completed ? '导出归档成果' : !ai && project.stage >= 3 ? '导出当前成果' : advanceText}<ArrowUpRight size={16}/></button></div>
      {references && <div className="reference-menu"><header><strong>{interview ? "添加附件与引用" : "引用项目成果"}</strong><button type="button" className="icon-button" aria-label="关闭引用" onClick={() => setReferences(false)}><X size={15}/></button></header>{onUploadResume && <button type="button" disabled={blocked} onClick={() => { setReferences(false); fileInput.current?.click(); }}><Plus size={16}/>上传简历附件</button>}{project.artifacts.length ? project.artifacts.map(item => <button type="button" key={item.id} onClick={() => { setChat(value => `${value}${value ? '\n' : ''}参考《${item.title}》：`); setReferences(false); composer.current?.focus(); }}><FileText size={15}/>{item.title}</button>) : <p>{interview ? "可上传本地简历，或将文件拖入下方输入区。" : "确认研究目标后，成果会保存在这里。"}</p>}</div>}
      {onUploadResume && <small className="resume-upload-hint">支持拖入 PDF、DOCX、TXT、MD、PPTX，单份最多 20MB；扫描 PDF 请先 OCR。</small>}
    </form>
    <div className="assistant-footnote">{ai ? project.connection_lost ? '连接已中断，自动重连后恢复进度' : 'AI 可能出错，请核对来源与原文' : '历史示例 · 新建项目可使用真实 AI'}</div>
  </aside>;
}
