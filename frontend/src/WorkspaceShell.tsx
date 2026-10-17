import { useRef, type CSSProperties, type ReactNode } from 'react';
import { CaretDown, CaretRight, Check, CirclesThree, Clock, DownloadSimple, FlowArrow, FolderSimple, Microphone, Plus, SidebarSimple } from '@phosphor-icons/react';
import './workspace.css';
import { sceneRegistry } from './sceneRegistry';
import type { Scene } from './types';

type Props = {
  title: string; saved: string; view: string; interview?: boolean; scene?: Scene; fileCount: number;
  chatVisible: boolean; onChatVisible: (visible: boolean) => void;
  onNavigate: (view: string) => void; onHome: () => void; onHistory: () => void;
  exportMenu?: ReactNode; onToggleExport: () => void; children: ReactNode;
};

/** 所有工作台场景共用的导航、项目栏、导出与可调整宽度的助手区域。 */
export function WorkspaceShell(props: Props) {
  const language = sceneRegistry[props.interview ? 'interview' : props.scene || 'market'];
  const root = useRef<HTMLDivElement>(null);
  const width = useRef(Number(localStorage.getItem('fieldwork-assistant-width')) || 420);
  const resize = (value: number) => {
    width.current = Math.max(340, Math.min(560, value));
    root.current?.style.setProperty('--assistant-width', `${width.current}px`);
  };
  const saveWidth = () => localStorage.setItem('fieldwork-assistant-width', String(width.current));
  const navigation = [
    { key: 'flow', label: language.mapLabel, Icon: FlowArrow },
    { key: 'interviews', label: language.captureLabel, Icon: Microphone },
    { key: 'files', label: language.materialsLabel, Icon: FolderSimple },
    { key: 'overview', label: '项目概览', Icon: CirclesThree },
  ];
  return <div ref={root} className={`workspace studio ${props.chatVisible ? '' : 'chat-hidden'}`} style={{ '--assistant-width': `${width.current}px` } as CSSProperties}>
    <header className="studio-topbar">
      <button className="studio-brand" aria-label="体察首页" onClick={props.onHome}><CirclesThree size={27} weight="duotone"/><strong>访谈·体察</strong><span>Research Studio</span></button><span className="topbar-divider"/>
      <button className="project-picker" onClick={props.onHistory}><FolderSimple size={18}/><strong title={props.title}>{props.title}</strong><CaretDown size={14}/></button>
      <div className="studio-top-actions"><span className="studio-saved"><Check size={14}/>{props.saved}</span><div className="export-wrapper"><button className="studio-export" onClick={props.onToggleExport}><DownloadSimple size={18}/><span>导出</span></button>{props.exportMenu}</div><button className="icon-button" aria-label={props.chatVisible ? '收起对话' : '展开对话'} onClick={() => props.onChatVisible(!props.chatVisible)}><SidebarSimple size={20}/></button></div>
    </header>
    <aside className="studio-sidebar"><button className="studio-new" onClick={props.onHome}><span><Plus size={19}/></span>{language.newProjectLabel}</button>
      <nav aria-label="工作空间">{navigation.map(item => <button key={item.key} className={props.view === item.key ? 'active' : ''} onClick={() => props.onNavigate(item.key)}><item.Icon size={20}/><span>{item.label}</span>{item.key === 'files' && props.fileCount > 0 && <small>{props.fileCount}</small>}</button>)}</nav>
      <div className="studio-sidebar-bottom"><button onClick={props.onHistory}><Clock size={19}/><span>历史项目</span></button><div className="studio-profile"><span>体</span><div>项目工作空间<small>资料按项目管理</small></div><CaretRight size={15}/></div></div>
    </aside>
    {props.chatVisible && <div className="assistant-resize-handle" role="separator" aria-label="调整助手宽度" aria-orientation="vertical" tabIndex={0}
      onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); resize(width.current + (event.key === 'ArrowLeft' ? 20 : -20)); saveWidth(); } }}
      onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); event.currentTarget.dataset.dragging = 'true'; }}
      onPointerMove={event => { if (event.currentTarget.dataset.dragging === 'true') resize(Math.round(innerWidth - event.clientX)); }}
      onPointerUp={event => { delete event.currentTarget.dataset.dragging; saveWidth(); }}
      onLostPointerCapture={event => { delete event.currentTarget.dataset.dragging; }}/>}
    {props.children}
  </div>;
}
