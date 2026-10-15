# 访谈·体察鸿蒙原生录音工程

此工程复用已上线的网页界面，录音由原生 `AudioCapturer` 采集 PCM，并申请 `audioRecording` 后台长时任务。它不是只包装网页的 WebView：切后台后的采音、分片、持久化和上传由原生侧承担，网页负责提纲、摸排提示和项目管理。

## 当前交付边界

已编写原生录音、通知长任务、独立 WAV 分片、断网持久队列、异常退出后的部分文件修复、登录 Cookie 原生传递及网页异步桥接代码。后端和网页对接已有自动化验证。当前环境没有已配置的 DevEco/鸿蒙 SDK 和已授权真机，**尚未编译 HAP、签名、真机验证或提交应用市场**，不能把源码验证等同于设备后台录音验收。

## 构建与运行

1. 在安装了 HarmonyOS 6 / API 20 SDK 的 DevEco Studio 中打开本目录；工程以 ArkTS Stage 模型开发，最低兼容 HarmonyOS 5 / API 12。API 15 才提供的后台任务取消通知已作版本判断。工具若提示升级项目模型或 SDK，以当前安装的 DevEco 配套版本同步 `build-profile.json5` 与 `hvigor` 配置；本仓库不保存本机 SDK 路径。实际提交应用市场前，以华为当前要求的目标 SDK 为准。
2. 使用自己的开发者身份配置自动签名，并在产品配置中选择生成的签名配置；未配置签名时不能安装至真实设备。DevEco 提供 Hvigor 启动器和插件，仓库未伪造本机专用启动器。
3. 确认 `bundleName` 为自己可用的标识；默认示例为 `org.example.fangtan`。修改时同步 `AppScope/app.json5` 与原生通知 `wantAgent` 中的包名。
4. 在 `BackgroundRecorder.ets` 中将 `SERVICE_URL` 和 `SERVICE_ORIGIN` 改为自己部署的 HTTPS 页面入口及来源（末尾保留 `/`）。默认 `https://example.com/fangtan/` 只是占位，不连接原项目网站。两项必须同源；不要把来源检查改成允许所有网站。构建 `entry` HAP，在授权测试手机上安装，完成自己部署的员工登录。
5. 网页点击录音时将通过 `FangtanNativeBridge` 请求原生录音。首次使用须授权麦克风，系统通知可返回录音页面。App 不记录 Cookie 到文件、不向网页返回 Cookie、不放宽 HTTPS 校验、不新增免登录公网上传接口。

## 数据与恢复

录音位于应用沙箱 `filesDir/recording-outbox/`，每 10 秒按实际 PCM 字节数封装完整 WAV 文件。队列和文件先写入本机，服务器确认后才移除已上传分片。每片固定 UUID，重试使用相同 ID；后端对相同内容去重。结束访谈前必须等待队列上传完毕，原音和逐字稿随后由既有后端保存与归档。

应用正常切后台时不停止采音。来电、麦克风被占用、后台任务被系统取消、存储不足会停止并提示。用户强制结束进程、系统终止进程或设备断电时不能保证持续录音；已落盘分片和未完成 WAV 会在再次启动时修复并重试。异常磁盘损坏、写入失败仍需要真机故障测试，不作零丢失承诺。

## 上架前必须执行的验收

- 前台录音 5 分钟，逐字稿、摸排提纲、计时与回听对应。
- 真机熄屏 30 分钟、切其他应用 30 分钟：后台录音通知存在，波形/时间连续，不能仅检查 UI 显示“录音中”。
- 断网 10 分钟后恢复：原生队列继续上传，无重复音频；登录过期时保留录音并提示重新登录。
- 来电或其他录音应用抢占：正确停止、保存并提示，用户可以继续同一访谈。
- 强制退出后重启：恢复未上传分片及部分 WAV；不会自动启动新的麦克风采集。
- 拒绝麦克风、撤销权限、磁盘空间不足、服务异常：不假装录音成功，不吞掉未上传文件。
- 验证主站 Cookie 在 ArkWeb 与原生 HTTP 之间的实际兼容性；测试 App 内 Word 下载、文件上传及系统返回键。
- 完成真实开发者签名、图标和版本信息、隐私说明、后台录音用途声明及应用市场审核要求。

## 官方依据

- [华为录音开发指导](https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/using-avrecorder-for-recording)
- [OpenHarmony 后台长时任务](https://github.com/openharmony/docs/blob/master/en/application-dev/task-management/continuous-task.md)
- [AudioCapturer 采音](https://github.com/openharmony/docs/blob/master/en/application-dev/media/audio/using-audiocapturer-for-recording.md)
- [ArkWeb Cookie API](https://github.com/openharmony/docs/blob/master/en/application-dev/reference/apis-arkweb/arkts-apis-webview-WebCookieManager.md)
