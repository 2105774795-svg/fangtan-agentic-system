export type Scene = 'market' | 'enterprise' | 'memoir';
export type Field = { key: string; label: string; options: string[]; multiple: boolean };
export type Scenario = { name: string; description: string; noun: string; steps: string[]; fields: Field[] };
export type Artifact = { id: string; step: number; title: string; content: string; version: number; demo: boolean; stale?: boolean; versions: { version: number; content: string; at: number }[] };
export type Message = { id: string; role: 'user' | 'assistant'; text: string; at: number };
export type Project = { id: string; title: string; prompt: string; scenario: Scene; stage: number; answers: Record<string, string[]>; context: string; artifacts: Artifact[]; messages: Message[]; revision: number; updated_at: number };
export type ProjectSummary = Pick<Project, 'id' | 'title' | 'scenario' | 'stage' | 'updated_at'>;

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/platform${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : '暂时无法完成操作，请重试');
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
