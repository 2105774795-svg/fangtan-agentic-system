import { type CSSProperties, memo, useEffect, useMemo, useRef, useState } from 'react';
import { Background, Handle, MarkerType, MiniMap, Panel, Position, ReactFlow, ReactFlowProvider, useNodesState, useNodesInitialized, useReactFlow, useOnViewportChange, type Edge, type Node, type NodeProps } from '@xyflow/react';
import { ArrowsOut, ArrowUpRight, BookOpen, ChartBar, CheckCircle, CirclesThree, Crosshair, FileText, FolderSimple, FlowArrow, ListBullets, MagnifyingGlass, MapTrifold, Microphone, Minus, Plus } from '@phosphor-icons/react';
import { visibleSteps, type Artifact, type Layout, type Project, type Scenario } from './types';

const tones = ['ochre', 'blue', 'green', 'violet', 'coral', 'rose'];
const colors = ['#c39159', '#6c91c1', '#65a88b', '#9a8dc8', '#c68867', '#cf8586'];
const icons = [FileText, MagnifyingGlass, ListBullets, Microphone, ChartBar, FolderSimple];
type BranchData = {
  title: string; index: number; state: 'done' | 'active' | 'pending';
  artifacts: Artifact[]; questions: { label: string; value: string }[]; selected: boolean;
  onOpen: (id: string) => void;
};
type RootData = { title: string; subtitle: string; count: number; scene: string };
type Branch = Node<BranchData, 'branch'>;
type Root = Node<RootData, 'project'>;

const BranchCard = memo(function BranchCard({ data }: NodeProps<Branch>) {
  const Icon = icons[data.index];
  return <section className={`map-branch tone-${tones[data.index]} state-${data.state} ${data.selected ? 'is-selected' : ''}`} aria-label={`${data.title}阶段`}>
    <Handle type="target" position={Position.Top}/><Handle type="source" position={Position.Bottom}/>
    <header className="branch-drag-handle"><span className="branch-icon"><Icon size={18} weight="duotone" /></span><strong>{data.index + 1}. {data.title}</strong><span className={`branch-state ${data.state}`} title={data.state === 'done' ? '已确认' : data.state === 'active' ? '当前阶段' : '待开始'}>{data.state === 'done' ? <CheckCircle size={14} weight="fill" /> : data.state === 'active' ? <span className="active-indicator"/> : null}</span></header>
    <div className="branch-content">
      {data.artifacts.map(a => <button key={a.id} aria-label={a.title} className="branch-file nodrag nopan" onClick={event => { event.stopPropagation(); data.onOpen(a.id); }}><span className="branch-file-icon"><FileText size={15}/></span><span title={a.title}>{a.title.replace(' · 示例草稿', '')}</span><small className={a.stale ? 'needs-review' : ''}>{a.stale ? '复核' : `v${a.version}`}</small></button>)}
      {data.questions.map(question => <div key={question.label} className="branch-question"><span>{question.label}</span><small>{question.value || '待选择'}</small></div>)}
      {!data.artifacts.length && !data.questions.length && <p className="branch-empty">等待现场录音或导入材料</p>}
    </div>
    <footer><span>{data.artifacts.length ? `${data.artifacts.length} 份成果` : '尚无成果'}</span><span>{data.state === 'active' ? '进行中' : data.state === 'done' ? '已确认' : '待开始'}<ArrowUpRight size={11}/></span></footer>
  </section>;
});

const ProjectCard = memo(function ProjectCard({ data }: NodeProps<Root>) {
  return <section className="map-project" aria-label="调研项目中心"><Handle id="bottom" type="source" position={Position.Bottom}/><span className="project-emblem">{data.scene === '口述史' ? <BookOpen size={28} weight="duotone"/> : <ChartBar size={28} weight="duotone"/>}</span><div><h1 title={data.title}>{data.title}</h1><p>{data.subtitle || data.scene}</p><small>{data.count} 份成果 · {data.scene}</small></div></section>;
});

const nodeTypes = { branch: BranchCard, project: ProjectCard };

function MapControls({ activeId, onTasks, onArrange }: { activeId: string; onTasks: () => void; onArrange: () => void }) {
  const { fitView, zoomIn, zoomOut, getZoom } = useReactFlow();
  const [zoom, setZoom] = useState(100);
  const duration = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 240;
  const [minimap, setMinimap] = useState(false);
  useOnViewportChange({ onChange: viewport => setZoom(Math.round(viewport.zoom * 100)) });
  useEffect(() => { setZoom(Math.round(getZoom() * 100)); }, [getZoom]);
  return <>
    {minimap && <MiniMap pannable zoomable nodeStrokeWidth={2} nodeColor={node => node.type === 'project' ? '#c27659' : '#e6e1d8'} position="bottom-right"/>}
    <Panel position="bottom-left" className="map-control-panel"><div className="map-controls"><button aria-label="整理为顺序流程" onClick={onArrange}><FlowArrow size={20}/></button><button aria-label="切换缩略图" aria-pressed={minimap} onClick={() => setMinimap(!minimap)}><MapTrifold size={20}/></button><button aria-label="适应全部节点" onClick={() => void fitView({ padding: .12, duration })}><ArrowsOut size={19}/></button><button aria-label="定位当前阶段" onClick={() => void fitView({ nodes: [{ id: activeId }], padding: 1.1, maxZoom: 1, duration })}><Crosshair size={20}/></button><span className="control-divider"/><button aria-label="缩小画布" onClick={() => void zoomOut({ duration })}><Minus size={17}/></button><span className="zoom-value" aria-live="off">{zoom}%</span><button aria-label="放大画布" onClick={() => void zoomIn({ duration })}><Plus size={17}/></button></div><button className="map-help" aria-label="查看阶段任务" onClick={onTasks}><ListBullets size={20}/></button></Panel>
  </>;
}

function MapSurface({ project, current, selectedStep, onSelect, onOpen, onTasks, onLayout }: { project: Project; current: Scenario; selectedStep: number; onSelect: (i: number) => void; onOpen: (id: string) => void; onTasks: () => void; onLayout: (positions: Layout['positions']) => void }) {
  const instance = useReactFlow();
  const initialLayout = useRef(false);
  const measured = useNodesInitialized();
  const dragging = useRef<string | null>(null);
  const moved = useRef(new Set<string>());
  const lastDocument = useRef<string | null>(null);
  const steps = visibleSteps(project);
  const stepKey = steps.join(',');
  const previousSteps = useRef(stepKey);
  const generated = useMemo<(Branch | Root)[]>(() => {
    let y = 210;
    const branches = steps.map((index): Branch => {
      const artifacts = project.artifacts.filter(a => a.step === index);
      const questions = index === 0 && !artifacts.length ? (project.checklist || current.fields).map(field => ({ label: field.label, value: (project.answers[field.key] || []).map(value => value === '其他' ? project.custom_answers?.[field.key]?.trim() || '其他（待补充）' : value).join('、') || field.resolved_value || '' })) : [];
      const node: Branch = { id: String(index), type: 'branch', position: { x: 20, y }, initialWidth: 260, initialHeight: 106 + Math.max(1, artifacts.length + questions.length) * 47, draggable: true, dragHandle: '.branch-drag-handle', data: { title: current.steps[index], index, state: index < project.stage || project.completed ? 'done' : 'active', artifacts, questions, selected: selectedStep === index, onOpen } };
      y += (node.initialHeight || 160) + 82;
      return node;
    });
    return [{ id: 'project', type: 'project', position: { x: 0, y: 0 }, initialWidth: 300, initialHeight: 132, draggable: true, data: { title: project.title, subtitle: (project.answers.goal || []).join(' · '), count: project.artifacts.length, scene: current.name } }, ...branches];
  }, [project.title, project.answers, project.custom_answers, project.completed, project.artifacts, project.checklist, project.stage, current, selectedStep, onOpen, stepKey]);
  const [nodes, setNodes, onNodesChange] = useNodesState<Branch | Root>(generated);
  useEffect(() => {
    if (!measured || initialLayout.current) return;
    initialLayout.current = true;
    void instance.fitView({ nodes: steps.length > 1 ? steps.slice(-2).map(index => ({ id: String(index) })) : undefined, padding: .18, maxZoom: 1, duration: 0 });
  }, [measured, instance, stepKey]);
  useEffect(() => { setNodes(previous => generated.map(node => { const old = previous.find(n => n.id === node.id); return { ...node, position: dragging.current === node.id ? old?.position || node.position : project.layout?.positions[node.id] || (moved.current.has(node.id) ? old?.position : undefined) || node.position }; })); }, [generated, project.layout?.version, setNodes]);
  useEffect(() => { if (previousSteps.current === stepKey) return; previousSteps.current = stepKey; const frame = requestAnimationFrame(() => void instance.fitView({ nodes: steps.slice(-2).map(index => ({ id: String(index) })), padding: .24, maxZoom: 1, duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 240 })); return () => cancelAnimationFrame(frame); }, [stepKey, instance]);
  useEffect(() => {
    const message = project.messages[project.messages.length - 1];
    if (!message?.document_created || !message.artifact_id || lastDocument.current === message.artifact_id) return;
    lastDocument.current = message.artifact_id;
    const frame = requestAnimationFrame(() => void instance.fitView({ nodes: [{ id: String(message.document_created!.step) }], padding: .5, maxZoom: 1, duration: 240 }));
    return () => cancelAnimationFrame(frame);
  }, [project.messages, instance]);
  const edges = useMemo<Edge[]>(() => steps.map((index, order) => ({ id: `branch-${index}`, source: order ? String(steps[order - 1]) : 'project', sourceHandle: order ? undefined : 'bottom', target: String(index), type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed, color: colors[index], width: 16, height: 16 }, style: { '--branch-color': colors[index], stroke: colors[index], strokeWidth: 1.5, opacity: .85 } as CSSProperties })), [stepKey]);
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  return <div className="research-map" data-testid="research-map"><ReactFlow nodes={nodes} onNodesChange={onNodesChange} onNodeDragStart={(_, node) => { dragging.current = node.id; moved.current.add(node.id); }} onNodeDragStop={(_, node) => { dragging.current = null; onLayout({ [node.id]: node.position }); }} edges={edges} nodeTypes={nodeTypes} onNodeClick={(_, node) => { if (node.type === 'branch') onSelect(Number(node.id)); }} minZoom={.2} maxZoom={1.6} nodesConnectable={false} panOnScroll zoomOnDoubleClick={false} aria-label="调研流程画布">
    <Background color="var(--studio-dot)" gap={22} size={1}/>
    <Panel position="top-left" className="map-top-status"><span className="map-mode"><CirclesThree size={15}/>调研地图</span><span className="map-mode-divider"/><span>{current.steps[project.stage]}</span><span className="mode-state">{project.mode === 'ai' ? 'AI 工作流' : '历史示例'}</span></Panel>
    <MapControls activeId={steps.length ? String(steps[steps.length - 1]) : 'project'} onTasks={onTasks} onArrange={() => { const positions = Object.fromEntries(generated.map(node => [node.id, node.position])); setNodes(previous => previous.map(node => ({ ...node, position: positions[node.id] }))); onLayout(positions); requestAnimationFrame(() => void instance.fitView({ nodes: steps.slice(-2).map(index => ({ id: String(index) })), padding: .24, maxZoom: 1, duration: 240 })); }}/>
  </ReactFlow><span className="map-bottom-hint">{reduced ? '点击文件查看成果' : '拖动画布探索 · 点击文件查看成果'}</span></div>;
}

export function ResearchMap(props: Parameters<typeof MapSurface>[0]) {
  return <ReactFlowProvider><MapSurface {...props}/></ReactFlowProvider>;
}
