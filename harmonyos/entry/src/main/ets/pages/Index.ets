import { webview } from '@kit.ArkWeb';
import { recordingEngine, SERVICE_URL, SERVICE_ORIGIN, RecordingStatus } from '../recording/BackgroundRecorder';

interface BridgeRequest { requestId: string; action: string; sessionId?: string; offsetMs?: number; }
interface BridgeResult extends RecordingStatus { requestId: string; failed: boolean; }

class RecordingBridge {
  public allowed: boolean = false;
  private controller: webview.WebviewController;
  constructor(controller: webview.WebviewController, onStatus: (status: RecordingStatus) => void) {
    this.controller = controller;
    recordingEngine.onStatus = (status: RecordingStatus) => { onStatus(status); this.send(status, ''); };
  }
  call(message: string): string {
    if (!this.allowed) return '只有访谈页面可以操作录音';
    if (message.length > 2048) return '录音请求过长';
    const request = JSON.parse(message) as BridgeRequest;
    if (!request.requestId || !['start', 'stop', 'status', 'retry'].includes(request.action)) return '录音请求无效';
    this.dispatch(request).catch((error: Error) => {
      const status = recordingEngine.status(); status.error = error.message;
      this.send(status, request.requestId, true);
    });
    return 'accepted';
  }
  private async dispatch(request: BridgeRequest): Promise<void> {
    let status = recordingEngine.status();
    if (request.action === 'start') status = await recordingEngine.start(request.sessionId || '', request.offsetMs || 0);
    else if (request.action === 'stop') status = await recordingEngine.stop();
    else if (request.action === 'retry') recordingEngine.retry();
    this.send(status, request.requestId);
  }
  private send(status: RecordingStatus, requestId: string, failed: boolean = false): void {
    if (!this.allowed) return;
    const result: BridgeResult = {sessionId: status.sessionId, recording: status.recording,
      uploading: status.uploading, pendingUploads: status.pendingUploads, elapsedMs: status.elapsedMs, error: status.error, requestId, failed};
    this.controller.runJavaScript('if(window.fangtanNativeResult){window.fangtanNativeResult(' + JSON.stringify(result) + ')}').catch(() => {});
  }
}

@Entry
@Component
struct Index {
  @State nativeRecording: boolean = false;
  @State nativeUploading: boolean = false;
  @State nativePending: number = 0;
  @State nativeMessage: string = '';
  private controller: webview.WebviewController = new webview.WebviewController();
  private bridge: RecordingBridge = new RecordingBridge(this.controller, (status: RecordingStatus) => {
    this.nativeRecording = status.recording; this.nativeUploading = status.uploading;
    this.nativePending = status.pendingUploads; this.nativeMessage = status.error;
  });
  build() {
    Column() {
      if (this.nativeRecording || this.nativePending > 0) {
        Row({space: 8}) {
          Text(this.nativeMessage || (this.nativeRecording ? '原生录音中 · 可切后台' : '本机待上传：' + this.nativePending + ' 片'))
            .fontSize(12).fontColor('#5c554a').layoutWeight(1)
          if (this.nativeRecording) {
            Button('停止录音').fontSize(12).backgroundColor('#b0452e')
              .onClick(() => { recordingEngine.stop().catch(() => {}); })
          } else {
            Button('重试').fontSize(12).backgroundColor('#211d17').onClick(() => recordingEngine.retry())
            Button('清理').fontSize(12).enabled(!this.nativeUploading).backgroundColor('#b0452e')
              .onClick(() => AlertDialog.show({title: '清除本机待上传录音？',
                message: '将永久删除本手机尚未上传的录音分片。服务器已保存的访谈不受影响。请确认这些本机录音不再需要。',
                cancel: {value: '保留', action: () => {}},
                confirm: {value: '确认清除', action: () => { try { recordingEngine.discardPending(); } catch (error) { this.nativeMessage = (error as Error).message; } }}
              }))
          }
        }.padding(10).width('100%').backgroundColor('#f6f3ec')
      }
      Web({src: SERVICE_URL, controller: this.controller})
        .javaScriptAccess(true)
        .javaScriptProxy({object: this.bridge, name: 'FangtanNativeBridge', methodList: ['call'], controller: this.controller})
        .onPageBegin(event => { this.bridge.allowed = (event?.url || '').startsWith(SERVICE_URL); })
        .onLoadIntercept(event => {
          const url = event.data.getRequestUrl();
          return !url.startsWith(SERVICE_ORIGIN);
        })
        .width('100%').layoutWeight(1)
    }.width('100%').height('100%')
  }
}
