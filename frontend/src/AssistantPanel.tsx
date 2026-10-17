import { useEffect, useRef, useState } from 'react';
import { ArrowClockwise, ArrowUpRight, CaretDown, CaretRight, Check, DownloadSimple, FileText, FolderSimple, ListChecks, MagnifyingGlass, Plus, SidebarSimple, Sparkle, X } from '@phosphor-icons/react';
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
};

export function AssistantPanel({ project, current, selectedStep, busy, tab, onTab, onClose, answers, customAnswers, onCustom, context, onPick, onContext, act, onOpen, onSelect, onExport, location, onLocation, onCapture, onStartAI }: Props) {
  const [chat, setChat] = useState('');
  const ai = project.mode === 'ai';
  const running = !!project.job && ['queued', 'running', 'retry'].includes(project.job.status);
  const blocked = busy || running;
  const reached = visibleSteps(project);
  const fields = project.checklist || current.fields;
  const unansweredFields = fields.filter(field => !field.resolved_value);
  const effectiveCustom = Object.fromEntries(Object.entries(customAnswers).filter(([key]) => answers[key]?.includes('其他')));
  const settled = !blocked && (!project.job || project.job.status === 'ready');
  const checklistReady = settled && project.messages.some(m => m.role === 'assistant');
  const needsAnalysis = project.artifacts.some(a => a.step >= 4 && a.stale) && project.artifacts.some(a => a.kind === 'material');
  const advanceText = needsAnalysis ? '根据最新材料重新分析' : ['', ai ? '确认计划，生成提纲' : '确认计划，生成提纲示例', ai ? '勾选提纲，准备访谈' : '确认提纲，进入采集准备', '根据原始材料生成分析', '生成报告草稿', '确认归档'][project.stage];
  const [references, setReferences] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const brief = useRef<HTMLDetailsElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const followOutput = useRef(true);
  const generation = useRef<HTMLElement>(null);
  useEffect(() => { followOutput.current = true; }, [project.job?.id]);
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
      {([{ key: 'chat', label: '调研对话' }, { key: 'sources', label: '资料' }, { key: 'tasks', label: '任务' }, { key: 'insights', label: '见解' }] as const).map(item => <button key={item.key} role="tab" id={`assistant-tab-${item.key}`} aria-controls="assistant-content" aria-selected={tab === item.key} tabIndex={tab === item.key ? 0 : -1} onClick={() => onTab(item.key)} onKeyDown={event => { if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') { event.preventDefault(); const tabs: AssistantTab[] = ['chat', 'sources', 'tasks', 'insights']; const next = tabs[(tabs.indexOf(tab) + (event.key === 'ArrowRight' ? 1 : 3)) % 4]; onTab(next); document.getElementById(`assistant-tab-${next}`)?.focus(); } }}>{item.label}{item.key === 'tasks' && <small>{project.completed ? 0 : 1}</small>}</button>)}
      <button className="icon-button collapse-assistant" aria-label="收起助手" onClick={onClose}><SidebarSimple size={19}/></button>
    </div>
    {!ai && <section className="demo-upgrade" aria-label="历史示例提示"><strong>这是旧版流程示例，未调用 AI 或联网检索</strong><p>带入主题与已保存的选项，重新确认后开始真实调研。旧项目保留，示例成果不带入。</p><button disabled={blocked} onClick={onStartAI}>转为真实 AI 调研<ArrowUpRight size={15}/></button></section>}
    <div id="assistant-content" role="tabpanel" aria-labelledby={`assistant-tab-${tab}`} className="assistant-content" ref={scroll} onScroll={event => { const element = event.currentTarget; followOutput.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100; }}>
      {tab === 'chat' ? <>
        <div className="conversation-date">{ai ? '真实 AI 调研' : '历史示例调研'}</div>
        {project.messages.map((message, index) => <div key={message.id} className={`studio-message ${message.role}`}>
          {message.role === 'assistant' && <span className="studio-agent-icon"><Sparkle size={19} weight="fill"/></span>}
          <div className="studio-message-body">{message.role === 'assistant' && <strong>{index < 2 ? '一起规划这次调研。' : '体察'}</strong>}<MarkdownContent text={(message.artifact_id && project.artifacts.find(a => a.id === message.artifact_id)?.content) || message.text}/>{message.document_created && <div className="document-created"><Check size={14}/>已新增至「{current.steps[message.document_created.step]}」：{message.document_created.title}</div>}{message.artifact_id && <button className="message-document" onClick={() => onOpen(message.artifact_id!)}><FileText size={15}/>打开已保存成果<ArrowUpRight size={13}/></button>}</div>
        </div>)}
        {project.job && project.job.status !== 'ready' && !project.messages.some(message => message.job_id === project.job?.id) && <section ref={generation} className={`live-generation status-${project.job.status}`} aria-label="AI 实时回复" aria-live="polite"><header><span>{running ? <ArrowClockwise className="spin" size={16}/> : <X size={16}/>}</span><strong>{running ? project.job.status === 'queued' ? '正在准备回复…' : project.job.stage : project.job.status === 'failed' ? '本次生成未完成' : '生成已取消'}</strong>{running && <button disabled={busy} onClick={() => void act({ action: 'cancel' })}>停止生成</button>}{['failed', 'cancelled'].includes(project.job.status) && <button disabled={busy} onClick={() => void act({ action: 'retry' })}>重试</button>}</header>{['failed', 'cancelled'].includes(project.job.status) && <p>{project.job.error || '已保留原有材料与生成片段。'}</p>}{project.job.output && <div className="streamed-response"><MarkdownContent text={project.job.output} streaming={running}/></div>}</section>}
        {project.stage === 0 && checklistReady ? <details className="briefing-card" open ref={brief}>
          <summary><span><ListChecks size={18}/>请选择本次调研范围</span><span>{count ? `已选择 ${count} 项` : '待补充'}<CaretDown size={14}/></span></summary>
          <div className="brief-form">
            {project.known_industry && <div className="resolved-context"><Check size={15}/><span>调研行业 / 产品：<strong>{project.known_industry}</strong></span><small>已从你的需求带入</small></div>}
            {unansweredFields.map((field, index) => <fieldset key={`${project.scenario}-${field.key}`}><legend><span>{index + 1}</span>{field.label}<small>{field.multiple ? '多选' : '单选'}</small></legend><div className="choice-options">{[...field.options, ...((field.allow_custom || ['goal', 'audience'].includes(field.key)) ? ['其他'] : [])].map(option => { const checked = (answers[field.key] || []).includes(option); return <button key={option} role={field.multiple ? 'checkbox' : 'radio'} aria-checked={checked} className={checked ? 'checked' : ''} disabled={blocked} onClick={() => onPick(field.key, option, field.multiple)}>{option}{checked && <Check size={12}/>}</button>; })}</div>{(answers[field.key] || []).includes('其他') && <label className="custom-choice-label">{field.key === 'goal' ? '补充你的调研目的' : '填写你想访谈的对象'}<input aria-label={field.key === 'goal' ? '其他调研目的' : '其他受访对象'} maxLength={160} disabled={blocked} value={customAnswers[field.key] || ''} onChange={event => onCustom(field.key, event.target.value)} placeholder={field.key === 'goal' ? '例如：评估上海新能源汽车售后服务的机会' : '例如：充电站运营方、新能源车主'}/><small>最多 160 字，将与已选项一起用于后续调研</small></label>}</fieldset>)}
            {ai && project.scenario !== 'memoir' && <div className="location-fields"><label>城市<input aria-label="调研城市" value={location?.city || ''} disabled={blocked} maxLength={64} onChange={event => onLocation({ ...location, city: event.target.value })} placeholder="例如：锦州"/></label><label>市场 / 园区<input aria-label="具体市场或园区" value={location?.market || ''} disabled={blocked} maxLength={120} onChange={event => onLocation({ ...location, market: event.target.value })} placeholder="选填"/></label></div>}          </div>
        </details> : null}
        {settled && project.stage === 3 && <div className="capture-prompt"><p>下一步：现场录音，或导入已有录音与文字稿；完成后回到这里分析。</p><button className="secondary" onClick={onCapture}><Plus size={15}/>进入现场采集</button></div>}
      </> : tab === 'sources' ? <div className="assistant-materials"><div className="assistant-section-title"><h2>项目资料</h2><span>{project.artifacts.length} 份</span></div><p className="panel-description">画布上的每份成果，都汇集在这里。</p>{project.artifacts.length ? project.artifacts.map(item => <button className="side-file" key={item.id} onClick={() => onOpen(item.id)}><span><FileText size={19}/></span><div><strong>{item.title}</strong><small>{current.steps[item.step]} · v{item.version}{item.stale ? ' · 需复核' : ''}</small></div><CaretRight size={14}/></button>) : <div className="assistant-empty"><FolderSimple size={35}/><h3>还没有资料</h3><p>确认需求后，准备计划和提纲会自动归入此处。</p><button className="secondary" onClick={focusBrief}>去确认需求</button></div>}{project.research?.sources.map(source => <a key={source.id} className="public-source" href={source.url} target="_blank" rel="noopener noreferrer"><MagnifyingGlass size={16}/><span><strong>{source.title}</strong><small>{source.id} · {source.retrieved_at}</small></span><ArrowUpRight size={14}/></a>)}<div className="source-note"><MagnifyingGlass size={17}/><span>{ai ? project.research?.note || '本项目尚无外部检索来源。' : '历史示例未执行外部检索。'}</span></div></div>
      : tab === 'tasks' ? <div className="assistant-tasks"><div className="assistant-section-title"><h2>调研执行</h2><span>{project.completed ? reached.length : reached.filter(index => index < project.stage).length} / {reached.length} 已确认</span></div><p className="panel-description">从需求到成果，查看每一步的状态。</p><div className="task-timeline">{reached.map(index => <section key={index} className={`task-item ${(index < project.stage || project.completed) ? 'complete' : index === project.stage ? 'current' : ''}`}><span className="task-marker">{(index < project.stage || project.completed) ? <Check size={12}/> : index + 1}</span><button onClick={() => onSelect(index)}><strong>{current.steps[index]}</strong><small>{(index < project.stage || project.completed) ? '已确认' : index === project.stage ? '等待操作' : '尚未开始'}</small></button>{project.artifacts.filter(a => a.step === index).map(item => <button key={item.id} className="task-file" onClick={() => onOpen(item.id)}><FileText size={14}/>{item.title}<ArrowUpRight size={12}/></button>)}</section>)}</div><aside className="panel-note">{ai ? '后续步骤将随着问答与操作逐步展开。生成结果是 AI 草稿，需核对原文与来源。' : '历史示例项目；新建调研将使用真实 AI。'}</aside></div>
      : <div className="assistant-insights"><div className="assistant-section-title"><h2>调研见解</h2><Sparkle size={18}/></div><p className="panel-description">先明确关注方向，再让证据支撑结论。</p><section className="insight-section"><h3>已确认的关注方向</h3>{project.answers.goal?.length ? <div className="insight-chips">{project.answers.goal.map(goal => <span key={goal}>{goal}</span>)}</div> : <p>尚未确认。可以先在对话中选择本次关注的主题。</p>}</section><section className="insight-section"><h3>材料现状</h3><p>已保存 {project.artifacts.length} 份成果，其中 {project.artifacts.filter(a => a.kind === 'material').length} 份原始文字材料，{project.research?.sources.length || 0} 个公开检索来源。</p></section>{project.artifacts.some(a => a.step >= 4 && !a.demo) ? project.artifacts.filter(a => a.step >= 4 && !a.demo).map(a => <button key={a.id} className="side-file" onClick={() => onOpen(a.id)}><FileText size={20}/><div><strong>{a.title}</strong><small>{a.stale ? '来源已更新，需重新分析' : '查看带原文引用的草稿'}</small></div><ArrowUpRight size={14}/></button>) : <div className="assistant-empty"><Sparkle size={33}/><h3>让见解来自真实材料</h3><p>添加访谈文字材料后，分析将保留材料段落引用。</p></div>}</div>}
    </div>
    <form className="studio-composer unified-composer" onSubmit={event => { event.preventDefault(); if (blocked) return; onTab('chat'); if (project.stage === 0) { if (checklistReady && complete) void act({ action: 'brief', answers, custom_answers: effectiveCustom, context, location }); } else if (chat.trim()) { void act({ action: 'message', content: chat, selected_step: selectedStep }).then(result => { if (result) { setChat(''); onTab('chat'); } }); } else if (settled) { if (ai && project.stage === 2) { onCapture(); return; } if (project.completed || !ai && project.stage >= 3) onExport(); else void act({ action: needsAnalysis ? 'regenerate' : 'advance' }); } }}>
      <textarea ref={composer} aria-label="给体察助手补充信息" rows={2} maxLength={project.stage === 0 ? 1000 : 2000} disabled={blocked} value={project.stage === 0 ? context : chat} placeholder={project.stage === 0 ? '补充地点、对象或其他要求（选填）…' : '有修改意见？写在这里；满意则直接确认继续…'} onChange={event => project.stage === 0 ? onContext(event.target.value) : setChat(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); const button = event.currentTarget.form?.querySelector<HTMLButtonElement>('button[type="submit"]'); if (button && !button.disabled) event.currentTarget.form?.requestSubmit(); } }}/>
      <div className="composer-tools"><div><button type="button" className="icon-button" disabled={blocked || project.stage === 0} aria-label="引用项目成果" aria-expanded={references} onClick={() => setReferences(!references)}><Plus size={20}/></button><span className="composer-mode">{running ? 'AI 正在回复' : project.stage === 0 ? '选择后统一确认' : chat.trim() ? '发送意见，继续讨论' : '审阅后继续下一步'}</span></div><button type="submit" className="composer-submit" disabled={blocked || project.stage === 0 && (!checklistReady || !complete) || project.stage > 0 && !chat.trim() && (!settled || ai && project.stage === 3 && !project.artifacts.some(a => a.kind === 'material'))}>{running ? '正在生成…' : project.stage === 0 ? '确认需求，生成计划' : chat.trim() ? '发送补充意见' : project.completed ? '导出归档成果' : !ai && project.stage >= 3 ? '导出当前成果' : advanceText}<ArrowUpRight size={16}/></button></div>
      {references && <div className="reference-menu"><header><strong>引用项目成果</strong><button type="button" className="icon-button" aria-label="关闭引用" onClick={() => setReferences(false)}><X size={15}/></button></header>{project.artifacts.length ? project.artifacts.map(item => <button type="button" key={item.id} onClick={() => { setChat(value => `${value}${value ? '\n' : ''}参考《${item.title}》：`); setReferences(false); composer.current?.focus(); }}><FileText size={15}/>{item.title}</button>) : <p>确认需求后，成果会保存在这里。</p>}</div>}
    </form>
    <div className="assistant-footnote">{ai ? project.connection_lost ? '连接已中断，自动重连后恢复进度' : 'AI 可能出错，请核对来源与原文' : '历史示例 · 新建项目可使用真实 AI'}</div>
  </aside>;
}
