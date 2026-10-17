export type ConversationJob = { id: string; status: string; stage: string; error: string };
export type ConversationTurn = { id: string; role: 'human' | 'assistant'; text: string; output: string; client_id: string; control: string; audio_ids: string[]; job: ConversationJob | null };
export type Evidence = { turn_id: string; quote: string };
export type ConversationArchive = { status: 'draft' | 'reviewed'; revision: number; paragraphs: { edited?: boolean; text: string; evidence: Evidence[] }[]; events: { title: string; description: string; date_text: string; status: string; evidence: Evidence[] }[] };
export type Conversation = { id: string; kind: string; status: 'live' | 'done'; created_at: number; turns: ConversationTurn[]; archive: ConversationArchive | null; archive_job: ConversationJob | null; audio: { id: string; turn_key: string; part_index: number; status: string; transcript: string }[]; no_speech?: boolean };
export async function conversationRequest<T>(url: string, body?: unknown): Promise<T> {
 const r = await fetch(url, body === undefined ? undefined : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
 if (!r.ok) { const value = await r.json().catch(() => ({})); throw new Error(typeof value.detail === 'string' ? value.detail : '暂时无法完成，请稍后重试'); }
 return r.json();
}
