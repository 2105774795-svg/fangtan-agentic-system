# 体察 · Research Studio

React / TypeScript / Vite 前端，FastAPI 与独立 Python Worker。市场调研、企业访谈、口述史统一免登录。

## 当前能力

- 首页保留浮雕底图的光标擦除效果，发送需求后进入工作台。
- 默认纵向顺序流程，阶段节点仅在对话或真实成果出现后展示，不预填规划文件。工具栏可将旧布局重新整理为顺序流程。项目与阶段卡片可拖拽，位置独立保存并在刷新后恢复。
- 新项目默认真实 AI：需求识别→选择确认→背景与计划→提纲→原始文字材料→分析→报告或章节草稿→人工确认归档。
- 首轮需求说明与后续成果均使用真实模型流式输出，回复结束后显示同一份工作流选择清单，已识别的场景不再重复选择。底部统一确认或发送补充意见。
- Worker 持久化模型增量内容，SSE 推送完整快照；支持刷新恢复、取消和失败重试。
- 公开检索保存实际来源与检索状态。失败明确说明，不把模型记忆冒充联网结果；口述史不检索私人经历。
- 提纲保留 Markdown，同时提取实际问题供勾选；确认后创建绑定本项目的录音会话。录音复用原模块的分片接口与 IndexedDB 缓存，独立分段上传，支持失败重试、刷新恢复、原音回听和已问标记。
- 已有录音和 TXT / MD / DOCX / 字幕等文件复用原导入任务，刷新可恢复进度；原始逐字稿与 AI 纪要分别保存，分析只以原文为依据。
- 原始文字支持粘贴及 TXT / MD 导入。分析与报告校验段落标记，点击引用跳回原文。
- 对话支持新增文档工具：明确要求在已进入的模块新增或另存文档时，Kimi 调用 create_document，生成或复用已有回复，落库后在该模块新增独立文件；不覆盖旧成果，也不推进阶段。
- 成果编辑、历史版本、Markdown / JSON 导出。修改原文会将下游成果标记过期。
- 旧项目继续标为历史示例，不自动变成真实研究。可带入原需求与选择，新建真实 AI 项目。

已接入提纲勾选、专用录音页、实时逐字稿、已有录音或文字稿导入，并把原音、逐字稿和纪要回流到工作台。手机扫码接续和统一账号权限尚未接入。旧页面保留在 /legacy，旧资料没有迁移或删除。

## 安装与启动

在仓库根目录执行。依赖安装到项目目录，缓存与临时文件建议放在 D 盘：

```powershell
$env:npm_config_cache='D:\codex-deps\npm-cache'
$env:TEMP='D:\codex-deps\tmp'
$env:TMP=$env:TEMP
npm --prefix frontend ci
npm run build:platform
```

在本地 backend/.env 配置 KIMI_API_KEY、KIMI_BASE_URL、KIMI_MODEL。当前已验证 kimi-k3。密钥只供后端读取，不进入浏览器和 Git。

在两个终端分别启动，API 与 Worker 必须使用同一环境和数据目录：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 18995 --workers 1
```

```powershell
.\.venv\Scripts\python.exe backend\worker.py
```

访问 http://127.0.0.1:18995/ 。配置变更后重启两个进程。没有 Worker 时，界面明确显示排队等待。开发模式另开终端执行 npm run dev:platform。

## 边界

单份原始材料最多 20000 字，项目合计最多 50000 字，超限拒绝而不静默截断。输出是 AI 草稿，段落标记校验不等于事实与语义已核验，需人工审阅。

取消是合作式操作，已发出的云端请求可能仍产生费用。重试会再次调用模型，任务租约与输入指纹阻止迟到结果覆盖新状态。画布使用独立版本，不使业务任务失效。

## 验证

后端测试需设置独立数据目录并清空云服务密钥，再执行 unittest；参照 CONTRIBUTING.md。新增测试为 test_research_platform.py 与 test_platform_ai.py。

普通前端回归：在独立数据目录启动 18996 API，执行 npm run test:platform。脚本强制创建 demo 项目，不调用付费模型，覆盖节点渐进出现、位置恢复及原有流程。

AI 界面回归：运行 `.\.venv\Scripts\python.exe scripts\serve-platform-test.py`，另一终端执行 `node scripts\test-platform-ai.cjs`。18998 服务使用合成模型 fixture，验证真实队列、SSE、引用跳转和归档，不调用外部服务。

真实联调：显式运行 `.\.venv\Scripts\python.exe scripts\smoke-platform-ai.py`。这会使用本地密钥发起少量真实模型及公开检索请求，使用合成材料，数据库位于 D:\codex-deps\tmp。结果写到 data/qa/platform-ai-live.json。不要把该脚本放入自动回归。

## 视觉参考

首页参考 https://github.com/kklt-ai/MiMoCodeWeb 的 Canvas 遮罩原理与用户截图，独立实现，没有复制小米代码、字体或品牌素材。底图由用户提供。工作台参考用户给出的 Research Studio 效果图。

字体 Manrope（OFL）、Phosphor 图标（MIT）、React Flow（MIT），保留原生署名。

采集回归：先运行隔离服务 `scripts/serve-platform-test.py`，再执行 `node scripts/test-platform-capture.cjs`。该测试使用真实浏览器 MediaRecorder、FFmpeg 和存储链路，转写与模型使用明确夹具，不产生付费调用。

Markdown 和选项回归：`node scripts/test-platform-markdown.cjs` 验证流式 Markdown、已知行业跳过追问、其他目的/对象保存，以及新增模块文档。`scripts/smoke-platform-document.py` 是显式真实云联调，使用合成项目和 D 盘隔离数据库，不纳入自动测试。

### 首页字标、字幕与项目删除

- 首页中文字标使用本地 Noto Serif SC 小字集，英文 FIELDWORK 降为辅助标识。字体文件约 7 KB，授权见 `src/assets/NotoSerifSC-OFL.txt`。
- 副标题参考 [MiMoCodeWeb 打字机说明](https://github.com/kklt-ai/MiMoCodeWeb/blob/main/docs/subtitle-typewriter-effect.md)：350ms 起步、55ms/字，完成后隐藏光标。文字预留完整尺寸；手机端和减少动态效果设置直接显示全文，窗口变化不重播。
- 历史项目每行右侧可删除，先确认再提交；删除当前项目会回首页。后端逻辑删除并取消该项目的 AI 任务，旧链接和写入返回 404，原始访谈及录音保留。
- 隔离浏览器验证：`node scripts/test-platform-home-delete.cjs`（配合 `scripts/serve-platform-test.py`，不删除日常项目）。
