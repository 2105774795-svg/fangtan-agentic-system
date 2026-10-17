import { ConversationHttpError } from './retry';

export type ConversationJob = { id: string; status: string; stage: string; error: string; error_code?: string; notice?: string; attempts?: number; max_attempts?: number };
export type ConversationTurn = { id: string; role: 'human' | 'assistant'; text: string; output: string; client_id: string; control: string; audio_ids: string[]; job: ConversationJob | null };
export type Evidence = { turn_id: string; quote: string };
export type LifeMemory = { id: string; title: string; description: string; date_text: string; category: string; status: string; note: string; revision: number; evidence: Evidence[]; related_ids: string[] };
export type BookParagraph = { text: string; evidence: Evidence[]; edited?: boolean };
export type BiographyChapter = { id: string; title: string; paragraphs: BookParagraph[]; locked?: boolean; needs_review?: boolean; pending_revision?: BiographyChapter };
export type BookGap = { id: string; title: string; question: string; note?: string; chapter_id?: string; status: string; focused: boolean };
export type BookSettings = { revision: number; title: string; voice_note: string; style_approved: boolean; style: { name: string; principles: string[]; boundaries: string }; feedback: { id: string; chapter_id: string; kind: string; note: string }[] };
export type LifeBook = { revision: number; status: string; stale: boolean; title?: string; format?: string; stage?: string; outline?: { id: string; title: string }[]; editorial_notes?: { chapter_id: string; message: string }[]; chapters: BiographyChapter[]; coverage: { source_turns: number; cited_turns: number; uncited_turn_ids: string[]; notice: string }; pending_draft?: LifeBook };
export type LifeNotebook = { memories: LifeMemory[]; book: LifeBook | null; book_job: ConversationJob | null; book_settings?: BookSettings; book_gaps?: BookGap[] };
export type ConversationArchive = { status: 'draft' | 'reviewed'; revision: number; paragraphs: { edited?: boolean; text: string; evidence: Evidence[] }[]; events: { title: string; description: string; date_text: string; status: string; evidence: Evidence[] }[] };
export type Conversation = { id: string; kind: string; status: 'live' | 'done'; created_at: number; turns: ConversationTurn[]; archive: ConversationArchive | null; archive_job: ConversationJob | null; audio: { id: string; turn_key: string; part_index: number; status: string; transcript: string }[]; no_speech?: boolean };
export async function conversationRequest<T>(url: string, body?: unknown): Promise<T> {
 const r = await fetch(url, { signal: AbortSignal.timeout(30000), ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
 if (!r.ok) { const value = await r.json().catch(() => ({})); throw new ConversationHttpError(typeof value.detail === 'string' ? value.detail : '暂时无法完成，请稍后重试', r.status); }
 return r.json();
}
