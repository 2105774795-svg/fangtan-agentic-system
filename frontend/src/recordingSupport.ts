export function recordingUnavailableReason(): string {
  if (!window.isSecureContext) return '当前页面使用 HTTP，浏览器禁止访问麦克风。请通过 HTTPS 地址打开本项目后录音；已有资料仍可查看或导入。';
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return '当前浏览器缺少录音能力，请使用最新版 Edge、Chrome 或 Safari，也可以导入已有录音。';
  return '';
}

export function recordingFailureMessage(cause: unknown): string {
  const error = cause as { name?: string; message?: string } | null;
  switch (error?.name) {
    case 'NotAllowedError': case 'PermissionDeniedError':
      return '麦克风权限未开启。请点击地址栏的网站权限，允许使用麦克风后重试；也请检查系统的麦克风隐私设置。';
    case 'NotFoundError': case 'DevicesNotFoundError':
      return '未找到麦克风，请连接或启用麦克风后重试。';
    case 'NotReadableError': case 'TrackStartError':
      return '麦克风暂时无法使用，可能被其他应用占用。请关闭占用麦克风的应用后重试。';
    case 'SecurityError':
      return '浏览器或网站权限策略禁止使用麦克风，请使用 HTTPS 地址并检查网站的麦克风权限。';
    default: return error?.message || '录音启动失败，请检查麦克风后重试；也可以导入已有录音。';
  }
}
