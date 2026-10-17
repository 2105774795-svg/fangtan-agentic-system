import { useEffect, useReducer, useRef } from 'react';
import type { Conversation } from './types';
import type { useConversationAudio } from './useConversationAudio';
import type { useConversationSpeech } from './useConversationSpeech';

type Mode = 'off' | 'auto' | 'paused';
type State = { mode: Mode; preparing: boolean; error: string; revision: number };
type Event = { type: 'mode'; mode: Mode } | { type: 'preparing'; value: boolean } | { type: 'error'; message: string } | { type: 'settled' };
function reducer(state: State, event: Event): State {
  switch (event.type) {
    case 'mode': return { ...state, mode: event.mode, error: '' };
    case 'preparing': return { ...state, preparing: event.value };
    case 'error': return { ...state, error: event.message };
    case 'settled': return { ...state, revision: state.revision + 1 };
  }
}
type Options = { session: Conversation | null; voice: ReturnType<typeof useConversationAudio>; speech: ReturnType<typeof useConversationSpeech> };

/** 唯一的自动轮转入口：所有异步接续都要通过代次检查，暂停优先。 */
export function useAutoConversation(options: Options) {
  const ref = useRef(options); ref.current = options;
  const [state, dispatch] = useReducer(reducer, { mode: 'off', preparing: false, error: '', revision: 0 });
  const mode = useRef<Mode>('off');
  const epoch = useRef(0);
  const busy = useRef(false);
  const active = useRef(true);
  const played = useRef(new Set<string>());
  const controls = useRef(new Set<string>());
  const latest = options.session?.turns.at(-1);
  const generating = !!latest?.job && ['queued', 'running', 'retry'].includes(latest.job.status);
  const failed = latest?.job?.status === 'failed';

  useEffect(() => { active.current = true; return () => { active.current = false; epoch.current++; mode.current = 'off'; }; }, []);
  const valid = (token: number) => active.current && mode.current === 'auto' && epoch.current === token;

  async function pause() {
    epoch.current++; mode.current = 'paused'; dispatch({ type: 'mode', mode: 'paused' });
    ref.current.speech.cancel();
    await ref.current.voice.pause();
  }

  async function start() {
    const token = ++epoch.current;
    mode.current = 'auto'; busy.current = true;
    dispatch({ type: 'mode', mode: 'auto' }); dispatch({ type: 'preparing', value: true });
    ref.current.voice.setError(''); ref.current.speech.prime();
    try {
      if (!await ref.current.voice.prepare() || !valid(token)) return;
      if (ref.current.voice.pending) {
        const turn = ref.current.session?.turns.at(-1);
        if (turn) played.current.add(turn.id);
        if (!await ref.current.voice.start() && valid(token)) dispatch({ type: 'error', message: '收音未能启动，请检查麦克风后继续。' });
      }
    } catch (cause) { if (valid(token)) dispatch({ type: 'error', message: (cause as Error).message }); }
    finally { busy.current = false; if (active.current) { dispatch({ type: 'preparing', value: false }); dispatch({ type: 'settled' }); } }
  }

  useEffect(() => {
    if (options.session?.status === 'done' || (latest?.control && !controls.current.has(latest.id))) {
      if (latest?.control) { controls.current.add(latest.id); played.current.add(latest.id); }
      if (mode.current === 'auto') void pause().catch(cause => dispatch({ type: 'error', message: cause.message }));
      return;
    }
    if (mode.current !== 'auto' || busy.current || state.preparing || !options.session || options.voice.processing || options.voice.listening || options.voice.error || state.error || generating || failed || options.speech.speaking) return;
    if (!latest || latest.role !== 'assistant' || !latest.text) return;
    const token = epoch.current;
    busy.current = true;
    void (async () => {
      try {
        if (!played.current.has(latest.id)) {
          await ref.current.speech.speak(latest.text);
          if (!valid(token)) return;
          played.current.add(latest.id);
        }
        if (!valid(token)) return;
        if (!await ref.current.voice.start() && valid(token)) dispatch({ type: 'error', message: '收音未能启动，录音已保留，请检查设备后继续。' });
      } catch (cause) { if (valid(token)) dispatch({ type: 'error', message: (cause as Error).message }); }
      finally { busy.current = false; if (active.current) dispatch({ type: 'settled' }); }
    })();
  }, [latest?.id, latest?.text, latest?.control, latest?.job?.status, options.session?.status, options.voice.processing, options.voice.listening, options.voice.error, options.speech.speaking, state]);

  // 浏览器 Performance 面板可查看阶段耗时；不记录录音、原话或密钥。
  const previousPhase = useRef('off');

  async function interrupt() {
    const token = ++epoch.current;
    mode.current = 'auto'; dispatch({ type: 'mode', mode: 'auto' });
    ref.current.speech.cancel();
    const turn = ref.current.session?.turns.at(-1); if (turn) played.current.add(turn.id);
    // 在旧播报流程退出前占用控制权，避免重复启动。
    if (!await ref.current.voice.start() && valid(token)) dispatch({ type: 'error', message: '收音未能启动，请检查设备后继续。' });
  }

  const enabled = state.mode === 'auto';
  const error = state.error || options.voice.error;
  const phase = !enabled ? state.mode === 'paused' ? 'paused' : 'off' : error ? 'error' : state.preparing ? 'preparing' :
    options.speech.speaking ? 'speaking' : options.voice.retryAttempt ? 'recovering' : options.voice.processing ? 'transcribing' : generating ? 'thinking' : failed ? 'error' :
      options.voice.listening ? options.voice.countdown !== null ? 'silence' : options.voice.userSpeaking ? 'user-speaking' : 'listening' : 'connecting';
  useEffect(() => {
    const mark = `conversation:${phase}`;
    const before = `conversation:${previousPhase.current}`;
    if (performance.getEntriesByName(before, 'mark').length) performance.measure(`conversation:${previousPhase.current}->${phase}`, before);
    performance.clearMarks(before); performance.mark(mark); previousPhase.current = phase;
    // 长访谈只保留最近的阶段测量，避免诊断缓冲区无限增长。
    if (performance.getEntriesByType('measure').filter(e => e.name.startsWith('conversation:')).length > 100) {
      for (const entry of performance.getEntriesByType('measure')) if (entry.name.startsWith('conversation:')) performance.clearMeasures(entry.name);
    }
  }, [phase]);
  return { enabled, phase, error, start, pause, interrupt };
}
