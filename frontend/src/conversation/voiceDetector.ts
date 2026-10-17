import type { MicVAD } from '@ricky0123/vad-web';

export type VoiceDetector = Pick<MicVAD, 'start' | 'pause' | 'destroy'>;

/** 模型、worklet 和 WASM 均由本机站点提供，不向外部 CDN 发送音频。 */
export async function createVoiceDetector(context: AudioContext, stream: MediaStream, frame: (probability: number) => void): Promise<VoiceDetector> {
  const { MicVAD } = await import('@ricky0123/vad-web');
  const detector = await MicVAD.new({
    model: 'v5',
    startOnLoad: false,
    audioContext: context,
    baseAssetPath: '/platform-assets/vad/',
    onnxWASMBasePath: '/platform-assets/vad/',
    ortConfig: ort => { ort.env.wasm.numThreads = 1; },
    getStream: async () => stream,
    // 换轮只断开检测节点，麦克风生命周期由会话管理。
    pauseStream: async () => {},
    resumeStream: async () => stream,
    onFrameProcessed: probabilities => frame(probabilities.isSpeech),
  });
  await detector.start();
  await detector.pause();
  return detector;
}
