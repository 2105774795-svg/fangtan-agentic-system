export type Scene = 'market' | 'enterprise' | 'memoir' | 'interview';
export type Field = { key: string; label: string; options: string[]; multiple: boolean; allow_custom?: boolean; resolved_value?: string };
export type Scenario = { name: string; description: string; noun: string; steps: string[]; fields: Field[] };
export type Artifact = { id: string; step: number; title: string; content: string; version: number; demo: boolean; origin?: 'ai' | 'user' | 'system' | 'demo'; kind?: string; source_session?: string; stale?: boolean; versions: { version: number; content: string; at: number }[] };
export type Message = { id: string; role: 'user' | 'assistant'; text: string; at: number; job_id?: string; artifact_id?: string; checklist?: Field[]; step?: number; document_created?: { step: number; title: string } };
export type Layout = { version: number; positions: Record<string, { x: number; y: number }> };
export type InterviewSummary = { id: string; subject: string; status: string; outline: string[]; utterance_count: number; has_report: boolean; number: number; recorded_ms: number; recording_state: string; created_at: number; synced: boolean; has_audio: boolean };
export type Project = { experience?: 'workspace' | 'conversation'; id: string; title: string; prompt: string; scenario: Scene; stage: number; answers: Record<string, string[]>; custom_answers?: Record<string, string>; known_industry?: string; context: string; artifacts: Artifact[]; messages: Message[]; revision: number; updated_at: number; mode?: 'ai' | 'demo'; active_session_id?: string; interviews?: InterviewSummary[]; outline_questions?: string[]; checklist?: Field[]; completed?: boolean; location?: { city?: string; market?: string }; layout?: Layout; research?: { status: string; note: string; sources: { id: string; title: string; url: string; retrieved_at: string }[] }; job?: { id: string; kind: string; status: string; stage: string; output: string; error: string; progress: number } | null; connection_lost?: boolean };
export type ProjectSummary = Pick<Project, 'id' | 'title' | 'scenario' | 'stage' | 'updated_at' | 'mode'>;

export function visibleSteps(project: Project): number[] {
  const steps = new Set(project.artifacts.map(a => a.step));
  if (project.messages.some(m => m.role === 'assistant')) steps.add(0);
  if (project.messages.some(m => m.step === 3 || m.text.startsWith('提纲已确认。'))) steps.add(3);
  return [...steps].sort((a, b) => a - b);
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/platform${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : '暂时无法完成操作，请重试');
  }
  return response.json();
}

/** Existing interview APIs live outside the research-platform namespace. */
export async function legacyApi<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : '访谈服务暂时无法完成操作');
  }
  return response.json();
}

export function download(name: string, content: string, type = 'text/markdown;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name.replace(/[<>:"/\\|?*]/g, '_');
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function editableAnswers(project: Project): Record<string, string[]> {
  const answers = Object.fromEntries(Object.entries(project.answers).map(([key, values]) => [key, values.map(value => project.custom_answers?.[key] === value ? '其他' : value)]));
  if (project.stage === 0 && project.known_industry) answers.industry = [project.known_industry];
  return answers;
}
