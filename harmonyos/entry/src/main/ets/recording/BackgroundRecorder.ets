import { audio } from '@kit.AudioKit';
import { abilityAccessCtrl, common, wantAgent } from '@kit.AbilityKit';
import { backgroundTaskManager } from '@kit.BackgroundTasksKit';
import { fileIo as fs } from '@kit.CoreFileKit';
import { http } from '@kit.NetworkKit';
import { util } from '@kit.ArkTS';
import { webview } from '@kit.ArkWeb';
import { deviceInfo } from '@kit.BasicServicesKit';
import { waveHeader, joinBytes } from './Wave';

// 开源示例域名：构建前同时填写自己的页面入口和受信任 HTTPS 来源。
export const SERVICE_URL = 'https://example.com/fangtan/';
export const SERVICE_ORIGIN = 'https://example.com/';
const SAMPLE_BYTES = 32000;
const CHUNK_BYTES = SAMPLE_BYTES * 10;

interface Chunk {
  id: string; sessionId: string; path: string; dataBytes: number; capturing: boolean;
}
interface Journal { sessionId: string; elapsedMs: number; queue: Chunk[]; }
export interface RecordingStatus {
  sessionId: string; recording: boolean; uploading: boolean; pendingUploads: number; elapsedMs: number; error: string;
}

class BackgroundRecorder {
  private context?: common.UIAbilityContext;
  private capturer?: audio.AudioCapturer;
  private currentFile?: fs.File;
  private current?: Chunk;
  private directory: string = '';
  private queue: Chunk[] = [];
  private active: boolean = false;
  private starting: boolean = false;
  private uploading: boolean = false;
  private sessionId: string = '';
  private elapsedMs: number = 0;
  private error: string = '';
  private retryTimer: number = -1;
  public onStatus?: (status: RecordingStatus) => void;

  bind(context: common.UIAbilityContext): void {
    this.context = context;
    if (this.directory) return;
    this.directory = context.filesDir + '/recording-outbox';
    if (!fs.accessSync(this.directory)) fs.mkdirSync(this.directory, true);
    const journal = this.directory + '/queue.json';
    if (fs.accessSync(journal)) {
      const saved = JSON.parse(fs.readTextSync(journal)) as Journal;
      this.sessionId = saved.sessionId; this.elapsedMs = saved.elapsedMs; this.queue = saved.queue;
      // Recover even the partially recorded file after a process exit. Cookie
      // credentials are never stored in this journal or sent to JavaScript.
      this.queue.forEach(chunk => {
        if (chunk.capturing) {
          chunk.dataBytes = Math.max(0, fs.statSync(chunk.path).size - 44);
          const file = fs.openSync(chunk.path, fs.OpenMode.READ_WRITE);
          fs.writeSync(file.fd, waveHeader(chunk.dataBytes)); fs.closeSync(file);
          chunk.capturing = false; this.elapsedMs += chunk.dataBytes * 1000 / SAMPLE_BYTES;
        }
      });
      this.queue = this.queue.filter(chunk => {
        if (!chunk.dataBytes) { fs.unlinkSync(chunk.path); return false; }
        return true;
      });
      this.persist();
    }
    if (deviceInfo.sdkApiVersion >= 15) {
      backgroundTaskManager.on('continuousTaskCancel', () => {
        if (this.active) this.interrupt('系统取消了后台录音任务，已有录音已保存在手机，请回到 App 继续');
      });
    }
  }

  status(): RecordingStatus {
    return {sessionId: this.sessionId, recording: this.active, uploading: this.uploading, pendingUploads: this.queue.length,
      elapsedMs: this.elapsedMs + (this.current ? this.current.dataBytes * 1000 / SAMPLE_BYTES : 0), error: this.error};
  }

  async start(sessionId: string, offsetMs: number = 0): Promise<RecordingStatus> {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(sessionId)) throw new Error('访谈编号无效');
    if (this.active || this.starting) {
      if (this.sessionId !== sessionId) throw new Error('另一场访谈仍在录音，请先停止');
      return this.status();
    }
    if (!this.context) throw new Error('录音环境尚未准备好');
    if (this.queue.length && this.queue.some(chunk => chunk.sessionId !== sessionId)) throw new Error('另一场访谈仍有未上传录音，请先返回该访谈重试上传');
    this.starting = true;
    try {
      const permission = await abilityAccessCtrl.createAtManager().requestPermissionsFromUser(this.context, ['ohos.permission.MICROPHONE']);
      if (permission.authResults[0] !== 0) throw new Error('需要麦克风授权才能录音');
      const baseMs = Math.max(0, Math.min(7 * 86400000, Math.floor(offsetMs)));
      this.elapsedMs = this.sessionId === sessionId ? Math.max(this.elapsedMs, baseMs) : baseMs;
      this.sessionId = sessionId; this.error = '';
      this.capturer = await audio.createAudioCapturer({
        streamInfo: {samplingRate: audio.AudioSamplingRate.SAMPLE_RATE_16000, channels: audio.AudioChannel.CHANNEL_1,
          sampleFormat: audio.AudioSampleFormat.SAMPLE_FORMAT_S16LE, encodingType: audio.AudioEncodingType.ENCODING_TYPE_RAW},
        capturerInfo: {source: audio.SourceType.SOURCE_TYPE_MIC, capturerFlags: 0}
      });
      this.openChunk();
      this.capturer.on('readData', (buffer: ArrayBuffer) => this.capture(buffer));
      this.capturer.on('stateChange', (state: audio.AudioState) => {
        if (this.active && (state === audio.AudioState.STATE_STOPPED || state === audio.AudioState.STATE_RELEASED)) this.interrupt('系统停止了录音，已记录内容保存在手机，请回来继续');
      });
      this.capturer.on('audioInterrupt', (event: audio.InterruptEvent) => {
        if (event.hintType === audio.InterruptHint.INTERRUPT_HINT_STOP || event.hintType === audio.InterruptHint.INTERRUPT_HINT_PAUSE) this.interrupt('来电或其他应用中断了录音，已有内容已保存，请回来继续');
      });
      await this.capturer.start();
      const notification = await wantAgent.getWantAgent({
        wants: [{bundleName: 'org.example.fangtan', abilityName: 'EntryAbility'}],
        actionType: wantAgent.OperationType.START_ABILITY, requestCode: 0,
        actionFlags: [wantAgent.WantAgentFlags.UPDATE_PRESENT_FLAG]
      });
      await backgroundTaskManager.startBackgroundRunning(this.context, backgroundTaskManager.BackgroundMode.AUDIO_RECORDING, notification);
      this.active = true; this.persist(); this.emit(); this.retry();
      return this.status();
    } catch (error) {
      await this.stop();
      throw error;
    } finally { this.starting = false; }
  }

  private openChunk(): void {
    const id = util.generateRandomUUID(true);
    const path = this.directory + '/' + id + '.wav';
    this.currentFile = fs.openSync(path, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE);
    fs.writeSync(this.currentFile.fd, waveHeader(0));
    this.current = {id, sessionId: this.sessionId, path, dataBytes: 0, capturing: true};
    this.queue.push(this.current); this.persist();
  }

  private capture(buffer: ArrayBuffer): void {
    if (!this.current || !this.currentFile) return;
    const bytes = new Uint8Array(buffer);
    let offset = 0;
    try {
      while (offset < bytes.length && this.current && this.currentFile) {
        const length = Math.min(CHUNK_BYTES - this.current.dataBytes, bytes.length - offset);
        const piece = bytes.slice(offset, offset + length);
        let written = 0;
        while (written < length) {
          const count = fs.writeSync(this.currentFile.fd, piece.slice(written).buffer,
            {offset: 44 + this.current.dataBytes, length: length - written});
          if (count <= 0) throw new Error('录音文件写入不完整');
          this.current.dataBytes += count; written += count; offset += count;
        }
        if (this.current.dataBytes === CHUNK_BYTES) { this.sealChunk(); this.openChunk(); }
      }
    } catch (error) { this.interrupt('手机存储写入失败，录音已停止，请检查剩余空间'); }
  }

  private sealChunk(): void {
    if (!this.current || !this.currentFile) return;
    const chunk = this.current;
    fs.writeSync(this.currentFile.fd, waveHeader(chunk.dataBytes), {offset: 0});
    fs.fsyncSync(this.currentFile.fd); fs.closeSync(this.currentFile);
    chunk.capturing = false; this.elapsedMs += chunk.dataBytes * 1000 / SAMPLE_BYTES;
    this.current = undefined; this.currentFile = undefined;
    if (!chunk.dataBytes) { this.queue = this.queue.filter(item => item.id !== chunk.id); fs.unlinkSync(chunk.path); }
    this.persist(); this.emit(); this.retry();
  }

  async stop(): Promise<RecordingStatus> {
    this.active = false;
    if (this.capturer) {
      try { await this.capturer.stop(); } catch (error) {}
      try { await this.capturer.release(); } catch (error) {}
      this.capturer = undefined;
    }
    this.sealChunk();
    if (this.context) { try { await backgroundTaskManager.stopBackgroundRunning(this.context); } catch (error) {} }
    this.persist(); this.emit(); this.retry();
    return this.status();
  }

  private interrupt(message: string): void {
    this.error = message; this.stop().catch(() => { this.active = false; this.emit(); });
  }

  retry(): void {
    if (this.retryTimer !== -1) { clearTimeout(this.retryTimer); this.retryTimer = -1; }
    this.upload().catch(() => {});
  }

  private async upload(): Promise<void> {
    if (this.uploading || !this.directory) return;
    this.uploading = true;
    try {
      let chunk = this.queue.find(item => !item.capturing);
      while (chunk) {
        const cookie = await webview.WebCookieManager.fetchCookie(SERVICE_URL);
        if (!cookie) throw new Error('请在 App 中重新登录，录音仍保存在手机');
        const file = fs.openSync(chunk.path, fs.OpenMode.READ_ONLY);
        const content = new ArrayBuffer(fs.statSync(chunk.path).size);
        try { fs.readSync(file.fd, content); } finally { fs.closeSync(file); }
        const boundary = 'fangtan-' + chunk.id;
        const encoder = new util.TextEncoder();
        const prefix = encoder.encodeInto('--' + boundary + '\r\nContent-Disposition: form-data; name="chunk_id"\r\n\r\n' + chunk.id + '\r\n--' + boundary + '\r\nContent-Disposition: form-data; name="audio"; filename="chunk.wav"\r\nContent-Type: audio/wav\r\n\r\n');
        const suffix = encoder.encodeInto('\r\n--' + boundary + '--\r\n');
        const client = http.createHttp();
        try {
          const response = await client.request(SERVICE_URL + 'api/sessions/' + chunk.sessionId + '/audio', {
            method: http.RequestMethod.POST, header: {'Content-Type': 'multipart/form-data; boundary=' + boundary, 'Cookie': cookie},
            extraData: joinBytes([prefix, new Uint8Array(content), suffix]), connectTimeout: 15000, readTimeout: 180000
          });
          if (response.responseCode === 401) throw new Error('登录已过期，请回到 App 登录后重试，录音已保存在手机');
          if (response.responseCode !== 200) throw new Error('录音上传失败（' + response.responseCode + '），分片已保留，可重试');
        } finally { client.destroy(); }
        const acknowledged = chunk;
        this.queue = this.queue.filter(item => item.id !== acknowledged.id); this.persist();
        fs.unlinkSync(acknowledged.path); this.error = ''; this.emit();
        chunk = this.queue.find(item => !item.capturing);
      }
    } catch (error) {
      this.error = (error as Error).message; this.emit();
      this.retryTimer = setTimeout(() => { this.retryTimer = -1; this.retry(); }, 5000);
    } finally { this.uploading = false; this.emit(); }
  }

  discardPending(): void {
    if (this.active || this.capturer || this.starting || this.uploading) throw new Error('请先停止录音并等待当前上传结束');
    this.queue.forEach(chunk => {
      if (chunk.path !== this.directory + '/' + chunk.id + '.wav' || !/^[A-Za-z0-9_-]+$/.test(chunk.id)) throw new Error('本机录音路径核对失败');
    });
    if (this.retryTimer !== -1) { clearTimeout(this.retryTimer); this.retryTimer = -1; }
    this.queue.forEach(chunk => { if (fs.accessSync(chunk.path)) fs.unlinkSync(chunk.path); });
    this.queue = []; this.sessionId = ''; this.elapsedMs = 0; this.error = ''; this.persist(); this.emit();
  }

  private persist(): void {
    if (!this.directory) return;
    const temp = this.directory + '/queue.tmp';
    const file = fs.openSync(temp, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
    try {
      fs.writeSync(file.fd, JSON.stringify({sessionId: this.sessionId, elapsedMs: this.elapsedMs, queue: this.queue} as Journal));
      fs.fsyncSync(file.fd);
    } finally { fs.closeSync(file); }
    fs.renameSync(temp, this.directory + '/queue.json');
  }

  private emit(): void { if (this.onStatus) this.onStatus(this.status()); }
}

export const recordingEngine = new BackgroundRecorder();
