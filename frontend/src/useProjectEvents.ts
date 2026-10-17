import { useEffect, type Dispatch, type SetStateAction } from 'react';
import { api, type Project } from './types';

/** 一项目一条助手连接；快照恢复，事件按游标、尝试次数和序号去重。 */
export function useProjectEvents(project: Project | null, setProject: Dispatch<SetStateAction<Project | null>>, onDeleted: (id: string) => void) {
  useEffect(() => {
    if (!project?.id || project.scenario === 'memoir' && project.mode === 'ai') return;
    const id = project.id;
    let disposed = false;
    let recovering = false;
    const recover = () => {
      if (recovering) return;
      recovering = true;
      void api<Project>(`/projects/${id}`).then(data => {
        if (!disposed) setProject(previous => previous?.id === id && data.revision >= previous.revision && (data.event_cursor ?? 0) >= (previous.event_cursor ?? 0) ? { ...data, connection_lost: false } : previous);
      }).catch(() => {}).finally(() => { recovering = false; });
    };
    const stream = new EventSource(`/api/platform/projects/${id}/events`);
    stream.addEventListener('snapshot', event => {
      try {
        const data = JSON.parse((event as MessageEvent).data) as Project;
        setProject(previous => previous?.id === id && data.revision >= previous.revision && (data.event_cursor ?? 0) >= (previous.event_cursor ?? 0) ? { ...data, connection_lost: false } : previous);
      } catch { /* 重连用完整快照恢复。 */ }
    });
    for (const kind of ['message.started', 'message.delta', 'message.completed', 'message.failed']) {
      stream.addEventListener(kind, event => {
        try {
          const e = event as MessageEvent;
          const data = JSON.parse(e.data);
          const cursor = Number(e.lastEventId);
          setProject(previous => {
            if (previous?.id !== id || !Number.isSafeInteger(cursor) || cursor <= (previous.event_cursor ?? 0)) return previous;
            const next = { ...previous, event_cursor: cursor, connection_lost: false };
            const job = previous.job;
            if (!job || job.id !== data.task_id) return next;
            if (kind === 'message.started' && (data.attempt ?? 0) >= (job.attempt ?? 0)) next.job = { ...job, output: '', output_version: 0, attempt: data.attempt };
            if (kind === 'message.delta' && data.attempt === job.attempt && data.seq > (job.output_version ?? 0)) {
              if (data.seq !== (job.output_version ?? 0) + 1) { queueMicrotask(recover); return previous; }
              next.job = { ...job, output: job.output + data.delta, output_version: data.seq };
            }
            if (kind === 'message.completed') {
              next.job = { ...job, output: '', status: 'ready' };
              if (!previous.messages.some(m => m.id === data.message_id)) next.messages = [...previous.messages, { id: data.message_id, role: 'assistant', text: data.text, at: Date.now() / 1000, job_id: job.id }];
            }
            if (kind === 'message.failed') next.job = { ...job, error: '本次回复暂未完成，消息已保存。' };
            return next;
          });
        } catch { /* 下一份快照会校正。 */ }
      });
    }
    stream.addEventListener('deleted', () => { stream.close(); onDeleted(id); });
    stream.onerror = () => setProject(previous => previous?.id === id ? { ...previous, connection_lost: true } : previous);
    return () => { disposed = true; stream.close(); };
  }, [project?.id, project?.scenario, project?.mode]);
}
