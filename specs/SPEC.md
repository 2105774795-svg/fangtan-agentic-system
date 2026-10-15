# 访谈·体察 Agentic 系统——会话管理模块技术规范（SPEC）

| 项 | 内容 |
|---|---|
| 文档版本 | v0.12 |
| 文档状态 | 已实现基线 |
| 对应 PRD | [`PRD.md`](./PRD.md) |
| 维护入口 | [`specs/README.md`](./README.md) |
| 日期 | 2026-10-15 |
| 适用范围 | 实战访谈会话管理 |
| 部署基线 | 单机、单 Uvicorn Worker＋独立任务进程；本轮未启用生产部署 |

## 1. 目的与范围

### 时叙录第一版增量

首页采用上方“汇言笔”、下方“时叙录”的双入口，汇言笔内保留实战访谈、现场录音及现有操作。时叙录是原私密口述史功能的产品更名，不迁移或改写资料，不更改内部 `memoir` 标识；本轮改动经回归及真实验收后发布，实际状态以审计为准。

新增 `backend/memoir/`，分离认证/API、人物档案存储、实时提问与后台生成。`memoir_*` 表保存独立认证、人物档案、会话、不可覆盖的轮次、带真实片段依据的记忆与章节版本；音频仅保存在 UUID 命名的私有目录。任何人名不得直接用于磁盘路径。

本人轮次使用 `client_turn_id` 幂等，同键内容不同须冲突；先保存原稿再生成追问，失败不能重复插入或把助手问题当本人回答。资料指纹覆盖全部本人原稿；只读查看不能触发批量上云。记忆保留原始日期措辞与待确认状态。章节逐段核对 `turn_id/quote` 为本人原文连续子串，无依据或源过期拒绝确认/导出；明确上限并拒绝超限，不静默截断。部分素材缺音频时仍可回查原始文字，不伪造时间定位。

`memoir_memory` 和 `memoir_chapter` 由已有 Worker 执行，任务参数只存归属 ID，不复制正文到通用队列。原工作区的本地身份不得授权私密 API。独立 Cookie 的退出/过期、请求同源校验及逐对象权限是服务器规则，不能只靠页面隐藏实现。所有新测试使用临时数据库与模拟资料，不读取用户家庭录音。

本文档定义会话管理模块的技术实现规范，覆盖：

- 历史会话列表与详情；
- 访谈对象、行业、采访重点与 AI 提纲生成；
- 进行中会话恢复；
- 音频分片上传、转写落库与序号分配；
- 自动命名与手动重命名；
- 报告生成、查询与下载；
- SSE 实时事件与断线恢复；
- 会话删除及文件清理；
- 数据模型、API 契约和推荐工程目录。

本文档覆盖会话与录音的员工鉴权、指定同事只读共享、搜索、项目文件夹、音频留存、多场访谈综合记录、AI 原文问透评估、联网地点背景和项目 ZIP；不覆盖跨访谈声纹身份识别、标签以及 ASR 底层优化。本轮新增能力须实测后发布，本文不表示已经部署。

## 2. 技术选型

### 2.1 推荐选型总览

| 层级 | 推荐技术 | 建议版本/模式 | 选择理由 |
|---|---|---|---|
| 运行时 | Python | 3.11+ | 与现有代码一致，异步生态成熟，类型提示和性能满足本期需求 |
| Web 框架 | FastAPI | FastAPI 0.110+ | 与当前实现一致；路由使用显式字典校验，后续复杂化时再引入 Pydantic 请求模型 |
| ASGI Server | Uvicorn | 单 Worker | 实时录音锁、命名和 SSE 总线仍在进程内；不可只增加 API Worker |
| 数据库 | SQLite 3 | WAL 模式 | 当前为单机 MVP，零运维、迁移成本低；数据规模和并发量可控 |
| 数据访问 | Python `sqlite3` + Repository 层 | 参数化 SQL | 现有项目无需引入 ORM；显式 SQL 更容易控制兼容迁移和事务边界 |
| 实时推送 | Server-Sent Events（SSE） | 浏览器原生 `EventSource` | 数据主要由服务端单向推送，SSE 比 WebSocket 更简单且自带重连能力 |
| 后台任务 | SQLite `tasks`＋独立 `worker.py` | 租约、心跳、有限重试 | 提纲、纪要、综合、导入和发言人分离持久化；实时录音、追问和自动命名仍留在 API |
| 外部 HTTP | HTTPX | 0.27+ | 已在项目中使用，支持超时、连接池及同步/异步调用 |
| 语音识别 | 阿里云百炼 DashScope | 现有模型配置 | 已完成接入；支持本地 Base64 短音频和公网 filetrans 两种模式 |
| 大模型 | Kimi OpenAI 兼容接口 | 环境变量配置模型 | 已完成提纲、追问、报告和自动命名；JSON 模式适合结构化输出 |
| 音频转码 | FFmpeg + `imageio-ffmpeg` | 系统 FFmpeg 优先 | 浏览器 WebM/Opus 统一转为 16kHz 单声道 WAV，且可在无系统 FFmpeg 时兜底 |
| 前端 | 单页 HTML + 原生 JavaScript + 内嵌 CSS | 无构建工具 | 样式不变，交互拆至 `assets/main.js`、`recording.js`，无需 Node 构建 |
| 测试 | unittest + FastAPI TestClient/HTTPX、Node/Playwright | 单元 + 集成 + 并发测试 | 覆盖状态、幂等、竞争条件和 API 契约 |
| 日志 | Python `logging` | 结构化上下文字段 | 无需新增日志平台即可追踪 sid、任务类型、结果和异常 |

Python 依赖安装优先使用国内镜像：

```powershell
pip install -i https://mirrors.aliyun.com/pypi/simple/ -r backend\requirements.txt
```

### 2.2 为什么本期不引入 PostgreSQL、Redis 和 Celery

- 当前保持单机和原有工作区可见性，SQLite 继续保存业务和任务；没有把线上共享工作区误称为个人隔离。
- 第一阶段用 SQLite 持久队列解决任务进程独立、重试和恢复，不引入 Redis/Celery 或迁移数据库。
- 实时录音进程内锁、命名和 SSE 总线要求 Uvicorn 仍只能运行一个 Worker。
- 未来需要多个 API 实例时，再按并发证据迁移数据库、分布式锁与事件总线；不能只增加 Uvicorn Worker 数量。

### 2.3 SQLite 运行参数

每个数据库连接应启用：

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 30000;
PRAGMA foreign_keys = ON;
```

说明：现有业务表没有物理外键，`foreign_keys=ON` 主要为后续新表做准备。本期删除关系仍由同一数据库事务显式维护，避免为增加外键而重建历史表。

## 3. 总体架构

```text
浏览器单页应用
  ├─ REST：AI 提纲、列表、详情、恢复、重命名、删除、音频、报告
  └─ SSE：转写、覆盖度、建议、命名、报告状态
          │
          ▼
FastAPI（单 Worker）
  ├─ API / Schema：请求校验与响应序列化
  ├─ Service：会话、音频、命名、报告、删除流程
  ├─ Coordinator：按 sid 加锁、命名和 SSE 事件总线
  ├─ Repository：SQLite 参数化查询与事务
  └─ Integration：DashScope、Kimi、FFmpeg
          │
          ├─ SQLite：业务事实来源＋tasks 租约队列
          ├─ data/uploads/{sid}/：音频处理临时目录
          └─ data/recordings/{sid}/：原始录音

独立 worker.py（显式运行，不开放端口）
  ├─ job_queue.py：领取、租约、心跳、重试与合作取消
  └─ task_handlers.py / services/materials.py：五类业务处理与原文版本校验
```

数据事实来源约定：

1. 会话、转写、建议和报告正文以 SQLite 为最终事实来源。
2. `CHUNK_SEQ` 仅为热缓存，任何首次处理或缓存校准都必须查询数据库。
3. `localStorage` 记录最近 sid、准备草稿及待查询任务 ID，不作为服务器业务状态依据。
4. 新后台报告以数据库为唯一正文依据，不要求落盘 Markdown 副本；旧副本继续在删除流程中清理。
5. 五类任务执行状态来自持久 `tasks`；API 重启不主动把新队列任务标为失败。无对应队列记录的旧 `generating` 只提示重新提交。

## 4. 数据模型

### 4.1 实体关系

```text
sessions 1 ────── N utterances
    │
    ├──────────── N suggestions
    │
    └────────── 0..1 reports
```

本期不重建现有表，因此关系由应用事务维护。删除会话时必须在同一事务内依次删除 `suggestions`、`utterances`、`reports` 和 `sessions`。

### 4.2 `sessions`：会话

下表“可空”描述业务层约束。当前 SQLite 历史 DDL 除主键外未声明 `NOT NULL`，因此写入校验由 API 和存储函数承担。

| 字段 | SQLite 类型 | 可空 | 默认值 | 说明 |
|---|---|---:|---|---|
| `id` | TEXT | 否 | 无 | 主键；12 位小写 UUID 截断值，对外称 sid |
| `subject` | TEXT | 否 | `未命名访谈` | 会话标题，用户标题最多 80 字，AI 标题最多 15 个汉字 |
| `subject_source` | TEXT | 否 | `default` | `default`、`auto`、`user` |
| `scene` | TEXT | 否 | `商户门店` | 访谈场景，最长 40 字 |
| `interviewee_type` | TEXT | 否 | `商户` | 访谈对象类型，最长 20 字 |
| `industry` | TEXT | 否 | 空字符串 | 所属行业；接口默认“综合/不限”，最长 30 字 |
| `focus` | TEXT | 否 | 空字符串 | 采访重点或提示词，最长 500 字 |
| `outline` | TEXT | 否 | `[]` | JSON 字符串数组；最多 100 项，每项最多 200 字 |
| `status` | TEXT | 否 | `live` | `live`、`done`、`deleting`；导入过程使用 `importing` 占位 |
| `created_at` | REAL | 否 | 当前时间 | Unix 时间戳，单位秒 |
| `finished_at` | REAL | 是 | `NULL` | 首次完成时间；重复完成不得覆盖 |

约束：

- `subject_source=user` 后，自动命名不得再写入。
- `status=done` 或 `deleting` 时禁止继续上传音频。
- 列表默认过滤 `deleting`。
- 默认标题的数据库值保持为“未命名访谈”，展示层计算为“未命名 · MM-DD HH:mm”。
- `industry`、`focus` 和 `outline` 参与追问及报告上下文；AI 提纲生成结果在用户编辑并创建会话后才持久化。

### 4.3 `utterances`：逐字稿

| 字段 | SQLite 类型 | 可空 | 默认值 | 说明 |
|---|---|---:|---|---|
| `id` | INTEGER | 否 | 自增 | 主键 |
| `session_id` | TEXT | 否 | 无 | 逻辑外键，关联 `sessions.id` |
| `seq` | INTEGER | 否 | 无 | 会话内从 0 开始递增的唯一序号 |
| `start_ms` | INTEGER | 否 | 无 | 相对会话开始时间，毫秒 |
| `end_ms` | INTEGER | 否 | 无 | 相对会话结束时间，毫秒，必须大于等于 `start_ms` |
| `text` | TEXT | 否 | 无 | 转写文本，禁止空字符串入库 |
| `dims` | TEXT | 否 | 空字符串 | 命中的维度 ID，以英文逗号分隔；保持现有存储格式 |
| `created_at` | REAL | 否 | 当前时间 | Unix 时间戳，单位秒 |

当前索引：

```sql
CREATE INDEX IF NOT EXISTS idx_utterances_session_seq
ON utterances(session_id, seq);
```

当前版本依靠单 Worker 和 sid 级进程锁防止重复 seq，数据库索引尚未设置为唯一。若未来增加唯一索引，必须先检测历史重复数据：

```sql
SELECT session_id, seq, COUNT(*) AS count
FROM utterances
GROUP BY session_id, seq
HAVING COUNT(*) > 1;
```

检测到重复时应中止迁移并记录人工修复信息，不得静默删除逐字稿。

### 4.4 `suggestions`：建议及轻量状态记录

| 字段 | SQLite 类型 | 可空 | 默认值 | 说明 |
|---|---|---:|---|---|
| `id` | INTEGER | 否 | 自增 | 主键 |
| `session_id` | TEXT | 否 | 无 | 逻辑外键，关联 `sessions.id` |
| `kind` | TEXT | 否 | 无 | 见下方枚举 |
| `payload` | TEXT | 否 | `{}` | JSON 对象 |
| `status` | TEXT | 否 | `new` | `new`、`asked`、`dismissed` |
| `created_at` | REAL | 否 | 当前时间 | Unix 时间戳，单位秒 |

`kind` 取值：

| 值 | 用途 | `payload` 关键字段 |
|---|---|---|
| `playbook` | 开场话术卡 | `scripts`、`consent`、`why` |
| `follow_up` | AI 追问建议 | `follow_up`、`reason`、`alternative`、`signals` |
| `coverage` | 覆盖度提醒 | `dim`、`dim_name`、`question`、`why` |
| `outline` | 已确认提纲项 | `item`、`asked` |
| `dim` | 手动确认已覆盖维度 | `dim` |
| `auto_naming` | 自动命名尝试记录 | `phase=early|finish` |

`auto_naming` 属于内部记录，不得出现在用户建议列表或 SSE 建议回放中。其行数用于保证每个会话最多两次尝试，并保证早期阶段最多一次。

推荐索引：

```sql
CREATE INDEX IF NOT EXISTS idx_suggestions_session_kind_created
ON suggestions(session_id, kind, created_at);
```

### 4.5 `reports`：纪要报告

| 字段 | SQLite 类型 | 可空 | 默认值 | 说明 |
|---|---|---:|---|---|
| `session_id` | TEXT | 否 | 无 | 主键及逻辑外键，关联 `sessions.id`，保证每场最多一份报告 |
| `markdown` | TEXT | 否 | 无 | 完整 Markdown 正文 |
| `created_at` | REAL | 否 | 当前时间 | 报告生成成功时间 |

本期遵守 PRD 的最小 Schema 原则，不新增 `reports.status` 字段。报告状态计算规则：

| 条件 | `report_status` |
|---|---|
| `reports` 中存在记录 | `ready` |
| 最新持久任务为 queued/running/retry | `generating` |
| 最近一次生成调用失败且尚未重试 | `failed` |
| 以上均不满足 | `none` |

`ready` 的优先级最高。任务状态与尝试次数持久保存，执行进程意外退出后由过期租约恢复，而不是回退成 `none`。旧同步兼容路径仍可能使用瞬时状态，不作为新网页任务的事实来源。

### 4.6 文件存储模型

| 路径 | 内容 | 生命周期 |
|---|---|---|
| `data/fangtan.db` | SQLite 主数据库 | 持久保存 |
| `data/uploads/{sid}/` | 音频处理临时文件 | 单片处理完成即删除；删除会话时清理目录 |
| `data/uploads/_imports/{job_id}/` | 导入原文件及转换中间件 | 重试时保留，确认成功或取消后清理 |
| `data/reports/{sid}.md` | 历史报告副本（兼容） | 新后台报告不要求写入；删除会话时清理旧副本 |
| `data/recordings/{sid}/` | 权限内回听与下载的音频原件 | 删除会话时清理 |

文件路径中的 sid 必须通过正则 `^[A-Za-z0-9_-]{1,64}$` 校验，且最终路径必须位于配置目录内，防止路径穿越。

### 4.7 兼容迁移

启动时执行幂等迁移：

1. `CREATE TABLE IF NOT EXISTS` 创建缺失表；
2. 通过 `PRAGMA table_info(sessions)` 检查 `subject_source`、`industry` 和 `focus`；
3. 仅在相应字段不存在时分别执行：

   ```sql
   ALTER TABLE sessions
   ADD COLUMN subject_source TEXT DEFAULT 'default';

   ALTER TABLE sessions
   ADD COLUMN industry TEXT DEFAULT '';

   ALTER TABLE sessions
   ADD COLUMN focus TEXT DEFAULT '';
   ```

4. 创建普通索引 `idx_utterances_session_seq` 和 `idx_sessions_status_created`；
5. 不重建表，不删除历史字段，不修改历史业务内容。

## 5. 核心流程规范

### 5.1 恢复与 `seq` 分配

单个 sid 的音频处理必须持有会话级互斥锁，完整覆盖“状态检查—序号计算—ASR—入库”过程：

```text
取得 sid 锁
  → 查询 sessions.status，非 live 返回 409
  → 查询 COALESCE(MAX(seq), -1) + 1
  → 处理音频及 ASR
  → INSERT utterance
  → 更新内存缓存
  → 释放锁
```

数据库 `MAX(seq)` 是最终事实来源。服务重启、前端刷新、缓存缺失或缓存落后都不能改变该规则。

### 5.2 自动命名

触发条件：

- `subject_source=default` 且 `subject=未命名访谈`；
- 累计转写达到 5 句时触发一次 `early`；
- `finish` 时仍未成功命名，允许再触发一次 `finish`；
- 总尝试次数不超过 2。

后台任务步骤：

1. 在短事务中再次检查条件并写入 `auto_naming` 尝试记录；
2. 发布 `naming_started`；
3. 在独立守护线程中调用 Kimi；
4. 验证标题长度、内容和敏感信息；
5. 使用条件更新写入：

   ```sql
   UPDATE sessions
   SET subject = ?, subject_source = 'auto'
   WHERE id = ?
     AND subject_source = 'default'
     AND subject = '未命名访谈';
   ```

6. 更新成功后发布 `renamed`；条件不成立则丢弃结果；异常时发布 `naming_failed` 并记录日志。

模型调用不得占用音频主链路，也不得持有数据库连接或会话锁。

### 5.3 报告生成与完成

- 收到完成请求先关闭录音并标记 `done`，默认标题仍未命名时提交收尾命名任务。
- 已有报告时直接返回，不再次调用 Kimi。
- 新网页带 `X-Background-Task: 1`，没有报告时提交/复用 `report` 任务并返回 `202`；通过 `GET /api/sessions/{sid}/report-job` 读取阶段与状态，最终报告仍从既有接口读取。
- 不带后台标记的旧客户端保留同步等待兼容模式；其请求超时语义不代表新队列任务被取消。
- 旧同步路径与 worker 复用报告业务处理器的来源检查和原子保存；存在活动导入时，报告状态与 `finish` 优先复用该导入任务并返回 `202`，不得对空稿另建报告任务。
- 后台处理器读取一致原文快照，生成后在同一写事务核验执行租约、取消、会话存在和来源指纹，再原子保存报告、待办与结束状态。重复领取命中缓存，不重复插入待办；`finished_at` 仅首次写入。
- 模型错误向上交任务中心有限重试，不写空报告；重试耗尽为 `failed`，允许人工再次请求。数据库是报告正文依据，不依赖文件副本或跨进程 SSE。

### 5.3A 已有材料导入

- 上传接口只接受白名单扩展名；音频和文字稿分别执行 200MB、20MB 上限，超限过程中立即停止写入并清理临时目录。
- 文件名只用于显示；磁盘名由安全字符组成并强制保留已校验扩展名，禁止路径穿越。
- 音频经 FFmpeg 转为 16kHz 单声道 WAV，以 240 秒为一段串行转写。
- 转写段的起止时间按标准化 WAV 的实际采样数累计，最终取整到毫秒；尾段不补齐到 240 秒，未识别出文本的段仍计入累计时间。
- 文字稿由 `importer.py` 解析，DOCX 只读取 `word/document.xml`，并限制解压正文大小；有效正文上限 50 万字符和 1 万段。
- 导入执行状态存入 `tasks`，公开兼容状态包括 `queued/processing/ready/failed/cancelling/cancelled`；不返回临时路径或内部上下文。
- 接收上传时预分配固定会话占位，场景按音频/文字稿/字幕稿区分；处理器只向已存在的会话归档，不能重建已被删除的会话。原文归档与内部 `import_archive` 标记一次事务写入，内部标记不得作为用户建议展示。
- 失败保留恢复所需原材料；若原文已归档，重试只补纪要、不重复解析和插入。确认成功或取消后清理限定任务目录；硬中断后不借旧租约删除新执行者文件。
- 长稿超过 28,000 字符时按来源完整行切分，先抽取事实、待办、风险和原话，再用中间结果生成最终纪要。

### 5.4 删除

1. 校验 sid；
2. 取得会话快照并等待该 sid 的音频处理锁；
3. 将状态更新为 `deleting`；
4. 在单个数据库事务中删除关联业务记录和会话；
5. 数据库提交后清理上传目录与报告文件；
6. 文件失败时记录 `sid`、路径和异常，并返回未清理路径；
7. 重复删除返回 `404`，避免向非创建人泄露删除前会话是否存在。

数据库回滚时不得开始文件删除。数据库已提交但文件删除失败时，不得声称文件已全部清理。

### 5.5 SSE 重连

- 浏览器使用 `EventSource` 自动重连，并在 `onerror` 显示重连状态。
- 每次连接建立后，服务端可回放最多 500 条历史转写和当前未处理建议。
- 转写事件 ID 固定为 `transcript:{seq}`，建议事件 ID 固定为 `suggestion:{id}`。
- 瞬时事件使用 UUID `event_id`。
- 前端必须维护已渲染的 `seq` 和建议 ID 集合，重复事件不得重复渲染。
- 收到 `ready` 或重新建立连接后，客户端调用 state 接口校准完整状态。

## 6. API 规范

完整且独立的接口契约见 [`specs/API.md`](./API.md)。本节保留技术实现所需的接口摘要、示例和流程约束；若两处表述不一致，以 `API.md` 的对外协议为准。

### 6.1 通用约定

- 基础路径：`/api`。
- Content-Type：普通请求和响应使用 `application/json; charset=utf-8`。
- 时间字段：Unix 时间戳秒，JSON number。
- sid：`^[A-Za-z0-9_-]{1,64}$`；当前生成 12 位小写十六进制字符串。
- 分页：`limit` 默认 100，范围 1～500；`offset` 默认 0。
- 错误响应沿用 FastAPI：`{"detail":"错误说明"}`。
- 当前路由成功时默认返回 `200`；常用错误码为 `400`、`403`、`404`、`409`、`413`、`422`、`502`、`503`、`504`。
- 为兼容现有前端，可保留 `/api/sessions/` 尾斜杠路由；新代码统一生成一个规范 URL，避免依赖重定向。

### 6.2 接口清单

| 方法 | 路径 | 用途 | 幂等性 |
|---|---|---|---|
| `POST` | `/api/outlines/generate` | 生成 AI 采访提纲 | 否 |
| `POST` | `/api/sessions/` | 创建实战访谈会话 | 否 |
| `GET` | `/api/sessions/?limit=&offset=` | 获取历史会话列表 | 是 |
| `GET` | `/api/sessions/{sid}` | 获取会话详情 | 是 |
| `GET` | `/api/sessions/{sid}/state` | 获取恢复快照 | 是 |
| `PATCH` | `/api/sessions/{sid}` | 手动重命名 | 对相同请求结果幂等 |
| `DELETE` | `/api/sessions/{sid}` | 删除会话及触发文件清理 | 是 |
| `POST` | `/api/sessions/{sid}/audio` | 上传音频分片并转写 | 否 |
| `GET` | `/api/sessions/{sid}/events` | 建立 SSE 事件流 | 是 |
| `POST` | `/api/sessions/{sid}/finish` | 结束会话并生成/复用报告 | 业务幂等 |
| `GET` | `/api/sessions/{sid}/report.md` | 下载 Markdown 报告 | 是 |
| `POST` | `/api/suggestions/{suggestion_id}/{action}` | 更新建议状态；action 为 asked 或 dismissed | 对相同请求结果幂等 |
| `POST` | `/api/sessions/{sid}/outline_asked` | 确认一个提纲项已问 | 当前实现允许重复记录 |
| `POST` | `/api/sessions/{sid}/dim_asked` | 确认一个维度已覆盖 | 当前实现允许重复记录 |
| `POST` | `/api/sessions/{sid}/simulate` | 开发环境注入模拟转写 | 否；默认关闭 |
| `GET` | `/asr-audio/{token}` | 供 DashScope 一次性拉取音频 | token 仅消费一次 |

`/finish` 是为兼容 PRD 和现有客户端保留的命令式端点；它内部必须按“单一报告资源”实现，不因重复 POST 创建多份报告。

### 6.3 生成 AI 采访提纲

`POST /api/outlines/generate`

请求包含 `subject`、`interviewee_type`、`industry`、`location` 和可选 `focus`。新地区输入只包含 `city/market`，生成要求两项非空，不再收集 `district/town/address`。历史地区 JSON 保持不变，并继续供旧记录上下文读取。服务端校验对象与行业枚举，调用 Kimi 后返回 `background`、`background_note`、`questions` 数组和 `count`；旧同步端点上游失败返回 `502`。网页使用 `POST /api/outlines/jobs` 与 `GET /api/outlines/jobs/{job_id}`，将生成结果保存到持久任务，断开客户端不取消生成。完整字段见 `API.md`。

界面先呈现联网地点背景、来源和检索时间，再展示提纲。`place_research.py` 复用现有 Moonshot 同域 `tools/search_pro`，仅查询公开城市/市场，最多 2 次、5 结果/次、512 KiB 响应，背景采用真实来源连续摘录，并持久保存 `sources/claims/research_status/retrieved_at`。发布日期可选，旧资料可能过时；同名异地、无源与失败显式说明，不阻断问题生成，不用模型记忆冒充联网。背景可追溯但不是独立核验结论，不进入所选问题列表，也不是访谈逐字稿或总记录的事实证据。

现场录音首页只有一个主要录音操作，历史、全部待办、继续最近访谈、导入与项目放在同页折叠区。点击主录音键后进入地区/提纲/问题勾选准备页；点击“开始录音 · 已选 X 道问题”或“直接开始录音”才创建新会话并请求麦克风，取消准备不产生空会话。实战访谈原“带筛选提纲进入录音”路径仅进入工作台，再点“录”采音；恢复会话同样由工作台控制继续采音。重复的“继续最近一场未结束的访谈”入口移除。

### 6.4 创建会话

`POST /api/sessions/`

请求：

```json
{
  "subject": "未命名访谈",
  "scene": "商户门店",
  "interviewee_type": "商户",
  "industry": "消费零售",
  "focus": "客流、成本和淡旺季变化",
  "outline": ["今年客流变化", "主要成本压力"]
}
```

`outline` 同时兼容换行分隔字符串。用户填写非默认标题时，服务端直接设置 `subject_source=user`。

成功响应 `200`：

```json
{
  "session_id": "abc123def456"
}
```

### 6.5 会话列表

`GET /api/sessions/?limit=100&offset=0`

成功响应 `200`：

```json
{
  "items": [
    {
      "id": "abc123def456",
      "subject": "服装档口客流变化",
      "display_subject": "服装档口客流变化",
      "subject_source": "auto",
      "interviewee_type": "商户",
      "industry": "消费零售",
      "status": "done",
      "report_status": "ready",
      "has_report": true,
      "utterance_count": 18,
      "created_at": 1790557200.0
    }
  ],
  "total": 1
}
```

排序固定为：`live` 优先，其余按 `created_at DESC`。`deleting` 不进入列表。

### 6.6 会话详情

`GET /api/sessions/{sid}`

成功响应 `200`：

```json
{
  "id": "abc123def456",
  "subject": "服装档口客流变化",
  "display_subject": "服装档口客流变化",
  "subject_source": "auto",
  "scene": "商户门店",
  "interviewee_type": "商户",
  "industry": "消费零售",
  "focus": "客流、成本和淡旺季变化",
  "outline": ["今年客流变化", "主要成本压力"],
  "status": "done",
  "created_at": 1790557200.0,
  "finished_at": 1790559000.0,
  "utterance_count": 18,
  "report_status": "ready",
  "has_report": true,
  "utterances": [],
  "suggestions": [],
  "coverage": {
    "ratio": 0.71,
    "missing": ["risk"],
    "dims": []
  }
}
```

详情默认最多返回最近 500 条逐字稿。若后续单场规模超过该限制，应增加逐字稿游标分页，不应无限扩大详情响应。

### 6.7 恢复快照

`GET /api/sessions/{sid}/state`

返回会话元信息、逐字稿、提纲、建议、覆盖度和报告状态，结构与详情接口一致。该端点用于录音页恢复和 SSE 重连校准，不读取 `localStorage` 作为事实。

### 6.8 手动重命名

`PATCH /api/sessions/{sid}`

请求：

```json
{
  "subject": "档口老板谈淡季客流"
}
```

处理规则：去除首尾空格，长度 1～80 字；成功后无条件写入 `subject_source=user`，并发布 `renamed` 事件。并发自动命名任务完成后会因条件更新失败而被丢弃。

成功响应 `200`：

```json
{
  "id": "abc123def456",
  "subject": "档口老板谈淡季客流",
  "subject_source": "user"
}
```

### 6.9 删除会话

`DELETE /api/sessions/{sid}`

创建人首次删除返回 `200`；删除后的再次请求返回 `404`：

```json
{
  "ok": true,
  "already_deleted": false,
  "files_deleted": false,
  "cleanup_errors": ["data/uploads/abc123def456"]
}
```

`ok=true` 表示数据库目标状态已达到；`files_deleted` 单独表达文件清理结果。进行中会话允许删除，但前端必须先明确提示删除后无法续录。

### 6.10 上传音频分片

`POST /api/sessions/{sid}/audio`

请求：`multipart/form-data`，字段名 `audio`。单片大小不得超过 `FANGTAN_MAX_AUDIO_BYTES`，默认 20 MiB。

成功响应 `200`：

```json
{
  "seq": 18
}
```

失败规则：会话不存在返回 `404`；会话非 `live` 返回 `409`；空文件返回 `400`；过大返回 `413`；FFmpeg 格式错误返回 `422`；转码超时返回 `504`。ASR 调用失败时当前接口仍返回已分配的 `seq`，同时发布 SSE `error` 且不插入空逐字稿。

### 6.11 SSE 事件流

`GET /api/sessions/{sid}/events`

响应头：

```text
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

统一数据外壳：

```json
{
  "type": "transcript",
  "event_id": "transcript:18"
}
```

事件类型：

| `type` | 主要字段 | 说明 |
|---|---|---|
| `transcript` | `seq`、`start_ms`、`end_ms`、`text`、`dims` | 新转写或历史回放 |
| `signal` | `seq`、`signals` | 某条转写命中的信号 |
| `suggestion` | `id`、`kind` 及业务 payload | 追问、覆盖度或开场建议 |
| `coverage` | `coverage` | 七维覆盖度更新 |
| `naming_started` | `phase` | 自动命名任务开始 |
| `renamed` | `subject`、`subject_source` | 标题已变更 |
| `naming_failed` | 无必填业务字段 | 自动命名失败；客户端保留兜底标题 |
| `report_ready` | 无额外业务字段 | 报告生成完成 |
| `error` | `message`、可选 `code` | 可展示或记录的异步错误 |
| `ready` | 无 | 历史回放完成，客户端可进行 state 校准 |

当前服务端不发送主动心跳，依赖浏览器 `EventSource` 自动重连；连接后通过历史回放和 `ready` 事件校准。事件 payload 不得包含密钥或模型原始敏感响应。

### 6.12 结束会话并生成报告

`POST /api/sessions/{sid}/finish`

已有报告直接返回 `200`。新网页带 `X-Background-Task: 1`（亦支持 `?background=true`），无缓存时返回 `202 {session_id,task_id,report_status:'generating'}`；用 `GET /api/sessions/{sid}/report-job` 轮询并从原报告端点取得正文。旧客户端不带标记时保持同步 `200/502` 兼容。实时链路仍单 API Worker；后台任务以数据库幂等提交防止重复结果，而非依赖跨进程线程锁。

成功响应：

```json
{
  "report": "# 访谈纪要：……",
  "report_status": "ready"
}
```

### 6.13 下载报告

`GET /api/sessions/{sid}/report.md`

- 报告存在：`200`，`Content-Type: text/markdown; charset=utf-8`；
- 报告不存在：`404`；

报告正文优先从数据库读取，不能依赖 Markdown 文件一定存在。

### 6.14 更新建议状态

`POST /api/suggestions/{suggestion_id}/{action}`

`action` 仅允许 `asked` 或 `dismissed`。成功返回 `{"ok":true}`；建议不存在返回 `404`。

### 6.15 确认提纲项

`POST /api/sessions/{sid}/outline_asked`

请求：

```json
{
  "item": "今年客流变化"
}
```

成功返回 `{"ok":true}`。当前实现会为重复请求新增记录，但恢复与剩余提纲计算按集合语义处理。

### 6.16 确认覆盖维度

`POST /api/sessions/{sid}/dim_asked`

请求：

```json
{
  "dim": "risk"
}
```

维度 ID 必须存在于服务端维度表。成功返回 `{"ok":true}` 并发布新的 `coverage` 事件；重复记录不会重复影响按集合计算的覆盖率。

### 6.17 开发模拟转写

`POST /api/sessions/{sid}/simulate`

仅当环境变量 `FANGTAN_ALLOW_SIM=1` 时可用。请求为 `{"text":"模拟受访者发言"}`，服务端按真实 seq 与转写后处理管线写入；默认返回 `403`。

### 6.18 ASR 一次性音频端点

`GET /asr-audio/{token}`

仅供 DashScope filetrans 拉取。token 必须随机、短期有效且只能消费一次；不存在、过期或已消费返回 `404`。生产环境必须使用 HTTPS，响应中不得暴露本地路径。

## 7. 推荐项目目录结构

当前工作区采用扁平后端结构：

```text
访谈-体察Agentic系统/
├─ specs/                             # 规范中心
├─ backend/
│  ├─ app.py                          # FastAPI 路由与业务编排
│  ├─ store.py                        # SQLite 表、迁移和查询
│  ├─ kimi.py                         # 提纲、追问、命名和报告
│  ├─ asr_dashscope.py                # 语音识别
│  ├─ dimensions.py                   # 七维分类与覆盖度
│  ├─ signals.py                      # 信号检测
│  ├─ playbook.py                     # 开场话术
│  ├─ config.py                       # 环境配置
│  ├─ static/index.html               # 单文件前端
│  └─ tests/                          # unittest 自动化测试
└─ data/                              # SQLite、临时音频和报告副本
```

当模块继续增长时，在保留 `backend/app.py` 启动兼容入口的前提下，建议逐步拆分为：

```text
访谈-体察Agentic系统/
├─ README.md
├─ specs/
│  ├─ README.md                     # 规范索引、维护规则和偏差登记
│  ├─ PRD.md                        # 产品范围、需求与验收基线
│  ├─ SPEC.md                       # 技术实现规范
│  ├─ ARCHITECTURE.md               # 系统边界与关键架构决策
│  └─ API.md                        # 对外及内部接口契约
├─ backend/
│  ├─ app.py                         # 兼容启动入口，仅创建并导出 FastAPI app
│  ├─ requirements.txt
│  ├─ .env.example
│  ├─ api/
│  │  ├─ router.py                   # 汇总路由
│  │  ├─ sessions.py                 # 会话 CRUD、state、finish、report
│  │  ├─ audio.py                    # 音频分片上传
│  │  ├─ events.py                   # SSE
│  │  ├─ suggestions.py              # 建议状态、提纲和维度确认
│  │  └─ internal.py                 # DashScope 拉取音频等内部接口
│  ├─ schemas/
│  │  ├─ common.py                   # 通用错误、分页模型
│  │  ├─ session.py                  # 会话请求/响应模型
│  │  ├─ suggestion.py               # 建议模型
│  │  ├─ report.py                   # 报告状态模型
│  │  └─ event.py                    # SSE 事件模型
│  ├─ services/
│  │  ├─ session_service.py          # 会话编排和恢复
│  │  ├─ transcription_service.py    # 转码、ASR、转写入库
│  │  ├─ naming_service.py           # 自动命名任务
│  │  ├─ report_service.py           # 报告幂等生成
│  │  ├─ suggestion_service.py       # 信号、追问和覆盖度
│  │  └─ cleanup_service.py          # 删除与文件清理
│  ├─ repositories/
│  │  ├─ database.py                 # SQLite 连接、事务和 PRAGMA
│  │  ├─ migrations.py               # 兼容迁移
│  │  ├─ session_repository.py
│  │  ├─ utterance_repository.py
│  │  ├─ suggestion_repository.py
│  │  └─ report_repository.py
│  ├─ coordinators/
│  │  ├─ locks.py                    # sid 级音频、报告、命名锁
│  │  ├─ task_registry.py            # 进程内任务状态
│  │  └─ event_bus.py                # SSE 订阅与发布
│  ├─ integrations/
│  │  ├─ dashscope_client.py
│  │  ├─ kimi_client.py
│  │  └─ ffmpeg.py
│  ├─ domain/
│  │  ├─ enums.py                    # 状态和 kind 枚举
│  │  ├─ dimensions.py
│  │  ├─ signals.py
│  │  └─ playbook.py
│  ├─ static/
│  │  ├─ index.html
│  │  └─ js/
│  │     ├─ api.js                   # fetch 封装和错误处理
│  │     ├─ session-store.js         # 页面状态和去重集合
│  │     ├─ history.js               # 历史列表与详情
│  │     ├─ live.js                  # 录音、上传队列与恢复
│  │     └─ events.js                # SSE 与重连
│  └─ tests/
│     ├─ unit/
│     │  ├─ test_session_service.py
│     │  ├─ test_naming_service.py
│     │  └─ test_report_service.py
│     ├─ integration/
│     │  ├─ test_session_api.py
│     │  ├─ test_seq_recovery.py
│     │  ├─ test_delete_cleanup.py
│     │  └─ test_sse_reconnect.py
│     └─ fixtures/
├─ data/                              # 运行时生成并由 Git 忽略
│  ├─ fangtan.db
│  ├─ uploads/
│  └─ reports/
└─ scripts/
   ├─ check_db.py                     # 数据一致性检查
   └─ cleanup_orphans.py              # 清理无 DB 会话对应的残留文件
```

拆分原则：

- Router 只负责协议、校验和状态码，不直接拼 SQL 或调用模型。
- Service 负责业务流程和事务边界。
- Repository 只负责数据读写，不发布 SSE。
- Integration 隔离外部服务，测试时可替换为假实现。
- Coordinator 明确承载“仅单进程有效”的锁、任务和事件能力。

## 8. 配置项

| 环境变量 | 默认值 | 用途 |
|---|---|---|
| `FANGTAN_PORT` | 代码回退 `18795`；示例配置 `18995` | 服务端口 |
| `FANGTAN_DATA_DIR` | `../data` | 数据根目录 |
| `FANGTAN_AUTH_ENABLED` | `true` | 员工认证开关；显式 `false` 启用仅适合本机的单人免登录工作区 |
| `FANGTAN_PUBLIC_BASE` | 本地地址 | DashScope 回调/拉取基础地址 |
| `FANGTAN_CHUNK_MS` | `10000` | 前端音频分片时长 |
| `FANGTAN_MAX_AUDIO_BYTES` | `20971520` | 单片最大字节数 |
| `FANGTAN_MAX_IMPORT_AUDIO_BYTES` | `209715200` | 导入音频最大字节数 |
| `FANGTAN_MAX_IMPORT_TEXT_BYTES` | `20971520` | 导入文字稿最大字节数 |
| `FANGTAN_COOLDOWN` | `45` | 追问建议冷却秒数 |
| `FANGTAN_ALLOW_SIM` | 未设置 | 设为 `1` 时开放模拟转写接口 |
| `KIMI_API_KEY` | 无 | Kimi 密钥 |
| `KIMI_BASE_URL` | 官方兼容地址 | Kimi API 地址 |
| `KIMI_MODEL` | 配置模型 | 提纲、追问、报告、命名模型 |
| `DASHSCOPE_API_KEY` | 无 | DashScope 密钥 |
| `DASHSCOPE_FILETRANS_MODEL` | 配置模型 | 公网异步文件转写模型 |
| `DASHSCOPE_FLASH_MODEL` | 配置模型 | 本地短音频同步转写模型 |
| `DASHSCOPE_FLASH_URL` | DashScope 多模态生成地址 | 同步转写接口地址 |

密钥只能来自环境变量或本地 `.env`，不得写入代码、日志、SSE 或 API 响应。生产环境应由部署平台注入。

## 9. 验证要求

至少覆盖以下自动化测试：

1. 新数据库和旧数据库均能幂等启动，历史数据不丢失。
2. AI 提纲会清洗编号、去重、补问号，并拒绝少于 6 个有效问题的结果。
3. 会话创建、详情和恢复正确保存 `industry`、`focus` 与最终提纲。
4. 服务重启后下一条 `seq=MAX(seq)+1`。
5. 同一 sid 两个并发上传不会产生重复 seq。
6. 不同 sid 可并行处理，不使用全局大锁串行化。
7. 自动命名运行期间手动改名，最终标题保持用户值。
8. 第 5 句只触发一次早期命名，完成阶段总尝试不超过两次。
9. 重复 `finish` 只生成一份报告、只调用一次模型。
10. SSE 历史回放和实时事件重复到达时，前端只渲染一次。
11. 删除数据库事务失败时不删除文件。
12. 文件清理失败时响应准确返回失败路径，数据库记录仍已删除。
13. `done`、`deleting` 会话上传音频返回 `409`。
14. 列表、详情、重命名和删除符合 PRD 的性能与状态要求。
15. 常见文字稿格式能够解析为标准逐字稿，并保留可识别的发言人和时间字段。
16. 导入接口拒绝非法格式、空文件和超限文件，公开状态不泄漏本地路径。
17. 长稿执行分段提炼，文字稿报告使用段落来源标记而非伪造音频时间戳。

## 10. 实施状态与后续演进

当前实现包括 AI 提纲、上下文持久化、列表/详情、录音恢复、DB 权威 seq、SSE 去重、命名、报告、导入、项目及删除。架构第一阶段补充五类任务的独立持久执行和页面脚本拆分；本轮未自行启用生产 worker。

本轮补充以下实现，保持原 UI 布局，仅已有卡片补证据/来源和项目下载按钮；自动测试及真实联调后再发布：

- `coverage_ai.py` 及持久 `coverage` 任务：读取完整逐字稿，约 7000 字符分块、逐题分批、分层合并与稳定片段缓存；新增内容增量处理，来源修改使指纹失效。四状态 `not_discussed/mentioned/partial/sufficient`，非空结果须附真实 seq 的连续原话，程序验证包含关系；回避、只问未答和泛泛表态不能充分。`ratio` 仅 sufficient 占比，人工 `manual_asked` 单独保存、不加分；部分证据或旧前缀使用时显式标记滞后与已分析段数。结果不等于绝对正确，记者仍需核实。只读接口不批量触发历史材料云端分析；取消、删除及来源变更须阻止迟到结果发布，删除同时清除缓存与证据。
- 地点来源只能取实际搜索响应的公共 HTTP(S) URL，不接收模型编造链接或危险/字面私网地址；标题、来源摘要、claim 原文与检索日期持久化，网页作为不可信数据而非系统指令。检索失败不冒充成功，地点消歧采用城市与具体名称共同证据；旧 `$web_search` 不作为新依赖。
- `project_bundle.py` 为 `GET /api/projects/{project_id}/bundle.zip` 生成真实目录：留存音频、完整 TXT/JSONL、已有 MD/DOCX、`manifest.json`、缺项说明。仅当前可见资料，存在未结束/导入中时 `409`；无音频或纪要明确缺项，不伪造、不自动补写。默认 200 场、2 GiB 实际写入量、1000 万字符、20 万段，超限 `413`，不截断。净化目录名、防止路径穿越，安全读取录音，打包和下载开始前复核权限、归属及快照；临时包结束/断开后清理。ZIP 未加密，后续撤权不能收回已传字节；不移动底层音频、不替代 `data/` 备份。

后续按风险排序：

1. 补齐会话 API、迁移、并发、删除和 SSE 集成测试；
2. 为请求和响应逐步引入 Pydantic 模型，保持现有协议兼容；
3. 审计历史重复 seq 后再评估唯一索引；
4. 模块继续增长时按第 7 节拆分 Router、Service、Repository 和 Integration；
5. 需要多 Worker 前迁移锁、任务状态和 SSE 总线到共享基础设施。

API 部署仍必须单 Uvicorn Worker，独立任务进程不计作第二个 API Worker。增加 API Worker 或多实例前，须迁移 sid 锁、命名及 SSE 总线等剩余进程内设施；不能把已持久化的任务状态当作其他协调问题也已解决。

## 11. 员工与录音管理扩展（v0.6）

以下员工鉴权、归属迁移和只读分享规则适用于认证模式；免登录模式以第 12 节为准。

- 员工账号由与会议龙虾共用的 `AUTH_API_URL` 校验；本站只保存随机会话令牌的 SHA-256 摘要，不保存员工密码。Cookie 为 HttpOnly、SameSite=Lax，HTTPS 下标记 Secure；会话最长 7 天，至少每 5 分钟重新核对员工状态。
- `sessions.owner_id` 指向已验证员工 ID；`session_shares` 是明确授权的只读名单。旧记录仅在验证员工姓名与原 `user_name` 完全一致时归属；无姓名的旧记录保持不公开。
- `sessions.project_name` 提供项目分组，列表通过标题、项目及逐字稿检索。`session_todos` 保存可勾选行动项；`session_questions` 保存带原文依据的问答；`utterances.speaker` 支持人工标记发言人。
- 原音保存在 `data/recordings/{sid}`。实时分片转为统一 WAV 后在结束时拼接；导入音频保留原格式。音频 API 先验证创建或分享权限，删除会话时清理音频目录。文件清理结果仍独立于数据库删除结果报告。
- 纪要可由创建人修订，修订不会自动覆盖人工已处理的待办；AI 生成待办只抽取纪要“行动项与待办”章节。问答只依据可定位的逐字稿，原文无据时明确说明。

## 12. 本机免登录工作区（v0.7）

- `FANGTAN_AUTH_ENABLED` 默认 `true`，认证模式保持原有拒绝未认证访问、员工状态校验和权限隔离；员工中心失败不会自动降级。操作者显式设为 `false` 并重新启动后，页面直接进入本地工作区，服务应绑定 `127.0.0.1`。
- 本地身份固定为 `id=-1`、`name=本地使用者`、`type=本地用户`；`GET /api/auth/me` 返回该身份及 `auth_enabled:false`。认证模式在原响应中增加 `auth_enabled:true`。免登录 `POST /api/auth/logout` 无操作并返回 `{"ok":true}`。
- 免登录模式下所有能访问此服务的人都可查看、编辑当前 `FANGTAN_DATA_DIR` 中的现有记录，包括录音、报告、逐字稿、导入任务、待办和问答；不依赖员工 Cookie 或员工归属筛选。新增会话和导入记录写入 `owner_id=-1`，姓名为“本地使用者”，不发送员工统计。
- 不执行历史归属迁移、不修改已有 `owner_id` 或员工共享记录。切回认证后恢复原权限校验，本地新增记录不自动归属员工，不自动公开或共享；应先导出或另行明确归属，无需恢复历史所有权。
- 登录、注册、重置密码、员工列表、团队共享、分享链接管理及公开链接访问统一返回 `409`，前端隐藏相应账户与分享入口；暂停期间旧分享链接也不可访问，历史数据仍保留。
- AI 仍依赖现有 Kimi/DashScope 密钥；云端发言人分离仍要求可被云端读取的公网 HTTPS 回调和所有发言人的单独同意，不因免登录跳过。
- 验证须覆盖默认认证仍拒绝未登录请求、免登录身份与现有记录读写、所有暂停接口的 `409`、本地新增归属与不发送统计，以及切回认证后的数据与权限隔离。

## 13. 项目文件夹与综合记录（v0.8）

- `projects(id,name,owner_id,created_at,updated_at)` 为独立归档实体，`UNIQUE(owner_id,name)` 避免同一员工重复命名。`sessions.project_id` 可空，`project_name` 保留兼容筛选。升级按原项目名和创建人迁移，重复初始化幂等；历史会话认领时同步归入该员工的同名项目。
- `project_records(id,project_id,owner_id,title,sources,status,stage,error,markdown,created_at,finished_at)` 保存综合任务及结果。`sources` 为按访谈时间排序的 JSON，含会话 ID、引用编号、标题、原文段数、纪要存在性及 SHA-256 材料指纹。指纹覆盖完整逐字稿、发言人、时间、顺序、纪要及访谈上下文，不返回前端。
- 访谈分页按稳定项目 ID 筛选；项目统计仅计当前可见的会话，空的本人项目保留。同事仅见获分享的源访谈；项目改名、综合记录生成/读取/导出由创建人控制，免登录沿用本地工作区规则。
- 合并校验 2–50 条不同、同项目、已结束且有逐字稿的访谈。完整材料上限 200 万字，超过明确拒绝。读取调用无段数限制的 `list_export_utterances`，单场纪要也改用完整查询。
- `project_records.py` 在材料不超过 24,000 字符时直接将完整原文及纪要交给模型。超过时分别处理逐字稿与纪要，每个片段显式标明来源与材料类型，每次最多约 10,000 字符；巨长单段也分割并重复引用标记。证据超过上下文预算时逐层整合，最多 8 轮；不收敛、输出空白或模型达到输出长度上限则明确失败，不把截断结果标为成功。
- `generating/ready/failed` 与正文保存在领域记录中，执行进度由持久 `project_record` 任务映射；`stale` 读取时核验。独立 worker 完整读取材料，发布前同事务核验租约与来源；重复进行中的同标题同来源任务复用。新持久任务重启后可按过期租约恢复；旧记录无队列任务只提示重试。
- 发布使用 `BEGIN IMMEDIATE` 保护来源复核和写入。导出再次核验源会话仍在项目中、已结束、权限有效且材料指纹一致；不满足时隐藏正文并返回 `409`。源访谈或逐字稿片段删除时，在同事务中清空相关派生正文并标为失效。
- Word 只导出综合记录与来源清单，Markdown 同内容；不把音频物理拼接，也不将逐场纪要机械串联为总记录。备份仍包含整个 `data/`。
- 自动化验证应覆盖旧项目迁移、同名隔离、空文件夹、归档/改名、选择限制、完整原文读取、长稿及纪要末尾处理、异步失败/中断、来源失效与导出，并使用离线浏览器验证项目入口、勾选、归档与移动端布局。

## 14. 地区提纲、录音衔接与原生路线（v0.9）

- `sessions.location` 使用 JSON TEXT，兼容迁移默认 `{}`。创建录音、导入、提纲、实时追问与纪要均保留地点上下文；地区是用户给定背景，不能替代原文事实。
- `outline_jobs` 保存 owner、参数、指纹和结果，`tasks` 管执行租约与进度；指纹含地区、模型与规则。独立 worker 调用生成，前端轮询并保存 ID，成功结果缓存 24 小时。新队列任务不因 API 重启直接失败，旧无队列记录的生成任务提示重新提交。

- K3 短任务显式 `reasoning_effort=low`。提纲输出预算 8192，连接超时 15 秒、请求超时 90 秒；支持清洗 JSON 围栏。报告继续使用既有深度处理策略，不以短任务设置覆盖报告。
- `_coverage` 在有提纲时按所选问题与持久已问记录计算，未问问题用于摸排提醒；无提纲使用原维度引擎。动态问题纯文本渲染，避免生成文本变成 HTML。
- 追问在独立线程执行，按 SID 锁去重及冷却，不占音频上传锁；发布前在音频/删除共用锁内复核会话仍进行中。
- 浏览器 `RecordingStore` 使用 IndexedDB 保存 Blob、SID、UUID、采集顺序，确认上传后只移除该片；恢复时排除正在上传的同片。网络恢复后重试。同毫秒创建的分片依照单调序号排序，不按随机 UUID 排序。
- `audio_receipts(session_id,chunk_id,digest,seq,status)` 跟踪待处理/完成分片，复用失败分片编号，完成片只返回原序号，原始内容哈希冲突拒绝覆盖。真实 WAV 长度决定时间点，浏览器后台定时器延迟不再伪装成固定 10 秒录音。
- 浏览器可用时申请屏幕唤醒锁、检测录音中断、恢复前台同步；不承诺锁屏/冻结状态下持续采音，也不以静音播放规避系统管理。
- `harmonyos/` 为用户选定的 5/6 原生工程：AudioCapturer 回调采集 PCM，独立 WAV 文件，本机持久队列及部分文件恢复，`audioRecording` 长时任务、通知与中断处理。网页通过同步代理+异步结果调用录音，原生 HTTP 仅向固定 HTTPS 主站传递自身 Cookie。
- 当前原生工程未 SDK 编译、签名、真机测试或上架。已验证浏览器缓存、桥接协议和 WAV 工具逻辑；它们不能代替 SDK 与真实设备验证。用户本轮明确选择先完成网页更新并交付原生工程。

## 15. 持久任务执行规范（架构第一阶段）

- `tasks` 字段包括任务 ID、类型、所有者、目标资源、payload、去重键、状态、阶段、进度、结果、错误、尝试次数/上限、下次执行时间、租约令牌/有效期与创建/更新时间。payload、路径、租约及原始异常不通过公开状态接口泄露。报告任务结果只保留业务引用，不重复保存纪要正文。
- 内部类型为 `outline/report/project_record/import/speaker`，状态为 `queued/running/retry/ready/failed/cancelled`。内部及通用 `/api/tasks` 进度为 0..1，提纲/导入/report-job 公开兼容进度统一为 0..100。
- 每次领取生成新令牌，独立心跳续租；错误在尝试上限内延迟重试。旧令牌、到期租约、用户取消均不能提交业务结果。所有长外部调用须配置超时，并在安全点检查取消。
- 领域写入与队列完成确认分两步，须允许领域已提交后的再次领取幂等返回；云模型可能重复调用或计费，不承诺恰好一次。
- `audio_access_tokens` 只保存一次性音频凭据的哈希、受限文件路径及有效期，解决 API/worker 跨进程回调；沿用原一次性令牌入口，不新增录音匿名查询接口。
- 不删除或重建旧业务表。升级前须等待旧内存导入完成，停止接收旧任务并备份。独立 worker 必须由管理员明确启动；本轮不自动部署或启停。
- API 继续单 Worker，SSE、录音互斥、自动命名尚未共享化；线上主站门禁/共享工作区、SQLite 文件及录音目录不变。PostgreSQL/COS/统一 SSO/完整引用数字校验及鸿蒙真机验收未在本轮实施。

## 16. 维护记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.7 | 2026-10-09 | 新增显式本机免登录配置、身份响应、暂停接口与历史归属保留规则 |
| v0.8 | 2026-10-12 | 新增项目与综合记录数据模型、完整原文分层综合、持久任务与来源核验、派生正文删除机制 |
| v0.9 | 2026-10-13 | 地区提纲持久任务、选题覆盖度、录音缓存及重复确认、实际时间点、异步追问与鸿蒙源码方向 |
| v0.10 | 2026-10-15 | 简洁录音入口与折叠管理、后置准备流程、三字段地区及地点背景/核实提示 |
| v0.11 | 2026-10-15 | 第一阶段模块化、五类任务持久队列与独立 worker、事务执行权校验、后台报告兼容及显式运维启动 |
