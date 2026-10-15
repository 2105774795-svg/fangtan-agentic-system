import { UIAbility } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { recordingEngine } from '../recording/BackgroundRecorder';

export default class EntryAbility extends UIAbility {
  onCreate(): void { recordingEngine.bind(this.context); }
  onWindowStageCreate(stage: window.WindowStage): void { stage.loadContent('pages/Index'); }
  // onBackground deliberately does not stop capture: it is owned by the native
  // audioRecording continuous task, not the Web page or a foreground UI timer.
  onForeground(): void { recordingEngine.retry(); }
  onDestroy(): void { recordingEngine.stop().catch(() => {}); }
}
