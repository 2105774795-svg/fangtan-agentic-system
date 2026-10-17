import { useRef, type Dispatch, type SetStateAction } from 'react';
import { api, type Project } from './types';
import { requestId } from './requestId';

export function useProjectCommands(options: { project: Project | null; setProject: Dispatch<SetStateAction<Project | null>>; setBusy: (b: boolean) => void; onError: (message: string) => void; onChanged: () => void }) {
  const inFlight = useRef(false);
  const pending = useRef<{ key: string; id: string } | null>(null);
  return async (body: Record<string, unknown>): Promise<Project | undefined> => {
    if (!options.project || inFlight.current) return;
    const project = options.project;
    const key = JSON.stringify([project.id, body]);
    if (pending.current?.key !== key) pending.current = { key, id: requestId() };
    const id = pending.current.id;
    inFlight.current = true; options.setBusy(true); options.onError('');
    try {
      const result = await api<Project>(`/projects/${project.id}/commands`, { method: 'POST', body: JSON.stringify({ revision: project.revision, request_id: id, ...body }) });
      options.setProject(previous => previous?.id !== project.id || previous.revision > result.revision || previous.revision === result.revision && (previous.event_cursor ?? 0) > (result.event_cursor ?? 0) ? previous : result);
      pending.current = null; options.onChanged(); return result;
    } catch (cause) { options.onError((cause as Error).message); throw cause; }
    finally { inFlight.current = false; options.setBusy(false); }
  };
}
