export type AudioChunk = { id: string; sid: string; blob: Blob; createdAt: number; order: number };
type Outbox = { put: (chunk: AudioChunk) => Promise<unknown>; remove: (id: string) => Promise<unknown>; list: (sid: string) => Promise<AudioChunk[]> };
declare global { interface Window { RecordingStore?: Outbox } }
let loading: Promise<Outbox> | undefined;

// Reuse the original recorder's durable IndexedDB outbox and chunk IDs.
export function recordingOutbox(): Promise<Outbox> {
  if (window.RecordingStore) return Promise.resolve(window.RecordingStore);
  return loading ||= new Promise<Outbox>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/assets/recording-support.js';
    script.onload = () => window.RecordingStore ? resolve(window.RecordingStore) : reject(new Error('录音存储初始化失败'));
    script.onerror = () => { loading = undefined; reject(new Error('无法加载录音存储，请刷新后重试')); };
    document.head.appendChild(script);
  });
}
