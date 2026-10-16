# 体察 · 第一轮统一工作台

新首页和工作台基于 React、TypeScript、Vite 与 React Flow。服务端是现有 FastAPI 中新增的 `/api/platform`，三类场景均不调用员工中心或口述史认证。

## 安装与启动

在仓库根目录执行，依赖留在 D 盘项目目录，缓存与临时文件也可放到 D 盘：

```powershell
$env:npm_config_cache='D:\codex-deps\npm-cache'
$env:TEMP='D:\codex-deps\tmp'
$env:TMP=$env:TEMP
npm --prefix frontend ci
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 18995 --workers 1
```

访问 `http://127.0.0.1:18995/`。构建产物写到 `backend/static/platform`，由 FastAPI 提供服务；图片位于 `backend/static/platform-media`。此阶段的流程示例不依赖 Worker 或云服务密钥。

开发模式另开终端运行 `npm --prefix frontend run dev`，访问 Vite 输出的地址。Vite 代理 `/api` 到 18995，`/platform-media` 也代理到 API。

## 当前交付

- 首页使用用户提供的浮雕图片；Canvas 遮罩随光标轨迹显露图片，停止后恢复。触屏可划动，也可点击查看底图；减少动态效果时直接显示静态背景。
- 市场调研、企业访谈、口述史共用三栏工作台，场景推荐可更改，需求通过选择卡片确认。
- 项目、需求草稿、流程进度、消息和成果均持久化至 SQLite。需求选择先点击“保存草稿”或“确认”，状态栏会提示未保存修改。
- 可编辑成果并保存新版本，查看历史版本、导出 Markdown 或项目 JSON。
- 研究计划和提纲是确定性模板生成的流程示例，始终明确标注；未联网、未调用自由对话模型，不生成虚构录音或分析结果。
- 真实 AI 流式输出、录音、材料导入、手机配对、综合报告与账号权限在后续接入。当前对话反馈是实际完成模板整理后的消息，不冒充模型流式推理。

新工作台复用既有 `projects` 表，新增 `research_workspace_runs` 保存带版本号的快照。过期写入返回 409，首次创建按请求 ID 幂等。已有访谈项目没有自动改造成新流程。旧页面保留在 `/legacy` 作为能力迁移参考，其历史业务与认证尚未改造；新工作台不跳转该页面或调用其认证。

## 验证

Python 测试创建独立临时数据库：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p test_research_platform.py -v
```

浏览器测试必须另启独立数据目录的 API，默认端口 18996，不使用日常数据：

```powershell
$env:FANGTAN_DATA_DIR='D:\codex-deps\tmp\fieldwork-browser-test'
$env:FANGTAN_AUTH_ENABLED='false'
$env:KIMI_API_KEY=''
$env:DASHSCOPE_API_KEY=''
$env:TEAMFLOW_USAGE_URL=''
.\.venv\Scripts\python.exe -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 18996 --workers 1
```

在另一终端运行 `npm ci --cache D:\codex-deps\npm-cache` 和 `node scripts/test-platform.cjs`。测试使用已安装的 Edge，截图写入 `data/qa/platform`。覆盖 Canvas 实际擦除、三场景、保存恢复、版本与下载、移动布局、深色模式、减少动态效果及无认证请求。

## 视觉参考

参考 https://github.com/kklt-ai/MiMoCodeWeb 的 Canvas 遮罩交互原理与用户截图，动效独立实现；没有复制其源码、字体或小米品牌素材。底图来自用户提供的“象牙浮雕中的雅集对谈.png”。拉丁字体 Manrope 来自带 OFL 许可的 Fontsource 包，图标为 MIT 许可的 Phosphor，画布为 MIT 许可的 React Flow；保留 React Flow 原生署名。
