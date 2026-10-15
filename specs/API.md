# 访谈·体察 Agentic 系统——会话管理 API

| 项 | 内容 |
|---|---|
| 文档版本 | v0.12 |
| 文档状态 | 当前实现基线 |
| 日期 | 2026-10-15 |
| 对应 PRD | [`PRD.md`](./PRD.md) |
| 对应 SPEC | [`SPEC.md`](./SPEC.md) |
| 维护入口 | [`specs/README.md`](./README.md) |
| 基础路径 | `/api` |
| 适用范围 | 访前提纲与实战访谈会话管理 |

## 1. 通用约定

### 时叙录独立 API（第一版）

产品更名为“时叙录”及首页新增“汇言笔”入口只调整用户可见名称和导航，不重命名以下 API、Cookie、表或资料路径。两域权限保持不变；本轮上线状态以发布审计为准。

新增 `/api/memoir/` 路由组，不复用共享工作区身份。`auth/login` 使用员工中心单独验证并签发专用 HttpOnly Cookie；`auth/me/logout` 独立于旧登录；所有其他路由强制正整数用户身份和人物档案归属，越权返回 404。所有响应不缓存，写请求进行同源校验，不向通用任务端点暴露私密任务。

录音上传使用 multipart 和稳定 `client_turn_id`，`text` 与 `audio` 二选一；相同标识不同原文/原音返回 409，不重复 ASR。每段音频限 10MB/120秒，文本限2万字。响应中的 `human_turn` 是本人原稿，`question` 包含 `text/turn/degraded/paused`；重复请求保持暂停与降级状态。引用使用稳定片段 ID，而非可变的段落序号。旧访谈 API 合同不变。

以下路由均以 `/api/memoir` 为前缀：

| 方法与路径 | 合同 |
|---|---|
| `POST /auth/login`、`GET /auth/me`、`POST /auth/logout` | 姓名/密码验证独立 Cookie；不改变旧工作区登录及资料归属 |
| `GET/POST /profiles` | `{profiles}` / `{profile}`；创建须 `name`、`cloud_consent=true`、`recording_consent=true`，可选 `description` |
| `GET/DELETE /profiles/{pid}` | 详情含 `profile/sessions/memories/chapters/tasks`；删除返回文件清理数及 `audio_cleanup_pending` |
| `POST /profiles/{pid}/sessions` | 可选 `title`，201 返回 `{session,question}` |
| `GET/DELETE /profiles/{pid}/sessions/{sid}` | `{session,turns}`，或删除该场及受影响的派生内容 |
| `POST /profiles/{pid}/sessions/{sid}/turn` | multipart `client_turn_id` 加 `text` 或 `audio`；先保存本人原话再返回追问 |
| `POST /profiles/{pid}/sessions/{sid}/finish` | 本场须有本人原稿；202 返回 `{session,task_id}` |
| `POST /profiles/{pid}/events/{eid}/confirm` | 确认回忆线索，不代表外部事实核验 |
| `POST /profiles/{pid}/chapters` | `title` 仅命名本次全量原稿整理，202 返回 `{task_id}` |
| `GET /profiles/{pid}/chapters/{cid}`、`POST .../confirm` | `{chapter}` 含逐段原话依据、`source_stale` 和确认状态 |
| `GET /profiles/{pid}/chapters/{cid}/export?format=md或docx` | 仅导出已确认且未过期版本 |
| `GET /profiles/{pid}/audio/{turn_id}` | 回听私有原音真实格式及原始上传字节 |
| `GET /tasks/{task_id}` | 仅所有者可查状态，不返回原话/payload/物理路径；旧通用任务路由一律404 |

### 1.1 协议与格式

- 普通请求及响应使用 `application/json`。
- 音频上传使用 `multipart/form-data`。
- SSE 使用 `text/event-stream`。
- 既有业务时间字段为 Unix 秒；地点来源的 `retrieved_at` 为 UTC ISO 8601 字符串，`published_at` 为检索服务返回的可用日期，缺失时省略。
- sid 当前为 12 位小写十六进制字符串；删除接口接受 `^[A-Za-z0-9_-]{1,64}$`。
- FastAPI 配置 `redirect_slashes=False`，集合路由必须使用本文规定的尾斜杠。
- 分页参数：`limit` 默认 100，服务端限制为 1～500；`offset` 默认 0，最小为 0。
- 认证模式（`FANGTAN_AUTH_ENABLED=true`，默认）下，除登录、注册、找回密码和带独立令牌的开发探针外，`/api` 请求须携带本站员工会话 Cookie。会话详情与下载还须具备创建人或明确分享权限；写入、删除与共享管理只限创建人。

### 1.1A 本机免登录模式

- 显式配置 `FANGTAN_AUTH_ENABLED=false` 时，服务作为单人本地工作区，任何能访问服务的人都可查看、编辑当前 `FANGTAN_DATA_DIR` 的现有记录。业务接口无需员工 Cookie，新增记录的 `owner_id=-1`；历史员工归属与共享记录不迁移、不覆盖，不发送员工使用统计。部署应绑定 `127.0.0.1`。
- `GET /api/auth/me` 返回 `{"user":{"id":-1,"name":"本地使用者","type":"本地用户"},"auth_enabled":false}`；认证模式返回原员工信息，并增加 `auth_enabled:true`。
- `POST /api/auth/logout` 在免登录模式下无操作，返回 `{"ok":true}`。
- 免登录模式下，登录、注册、重置密码、员工列表（`/api/auth/login`、`/api/auth/register`、`/api/auth/reset-password`、`/api/auth/people`），团队共享（`/api/sessions/{sid}/shares`）、分享链接管理（`/api/sessions/{sid}/share-links` 及其撤销接口）和公开链接访问（`/api/share/{token}`）均暂停，返回 `409`，包括旧链接。
- 切回 `true` 恢复原认证逻辑，员工中心不可用仍拒绝登录；本地新增记录不会自动归属员工或公开，应先导出或明确归属。AI 密钥、云端发言人分离的公网 HTTPS 回调及所有发言人的单独同意仍为必要条件。

### 1.2 枚举

访谈对象 `interviewee_type`：

```text
商户、企业家、企业高管、一线员工、行业专家、投资人、政府/协会人士、消费者
```

行业 `industry`：

```text
综合/不限、金融业、汽车业、制造业、食品饮料、消费零售、互联网科技、
房地产建筑、能源化工、医药健康、农业、交通物流、文化传媒、教育服务
```

会话状态 `status`：`live`、`done`、`deleting`，导入中的固定会话占位为 `importing`，不可按进行中访谈续录。

报告状态 `report_status`：`none`、`generating`、`ready`、`failed`。

标题来源 `subject_source`：`default`、`auto`、`user`。

建议状态 `status`：`new`、`asked`、`dismissed`。

### 1.3 错误格式

```json
{
  "detail": "session not found"
}
```

| 状态码 | 当前含义 |
|---:|---|
| `200` | 成功；当前创建和动作接口也使用 200 |
| `202` | 导入文件已接收，后台处理已启动 |
| `400` | 字段类型、长度、枚举或动作不合法 |
| `401` | 未登录或员工会话失效 |
| `403` | 已登录但没有写入权限，或模拟转写接口未启用 |
| `404` | 会话、建议、报告或一次性音频不存在 |
| `409` | 会话不是 `live`，不能继续写入；或免登录模式暂停账户、员工共享及分享链接功能 |
| `413` | 音频分片超过配置上限 |
| `415` | 导入文件格式不在白名单中 |
| `422` | FFmpeg 无法处理音频 |
| `502` | Kimi 等上游生成失败 |
| `503` | FFmpeg 不可用或无法启动 |
| `504` | FFmpeg 转码超时 |

## 2. 接口总览

### 地点背景、地区提纲后台任务与录音衔接（v0.10）

- `POST /api/outlines/jobs` 返回 `202`，接收 `subject/interviewee_type/industry/focus/location` 及可选 `regenerate`。新请求的 `location` 仅包含 `city/market`；生成任务要求城市和市场/园区。结果除原字段外新增 `sources/claims/research_status/retrieved_at`。`background` 使用真实搜索页面摘录及 `[1]` 引用，`background_note` 说明日期、匹配和核验边界；不是独立核验结论。
- `GET /api/outlines/jobs/{job_id}` 查询持久任务，仅创建人可见（线上共享工作区沿用现有主站门禁）。兼容状态为 `generating/ready/failed`，内部由独立 worker 的 `queued/running/retry` 映射生成中；断开客户端不取消任务，新队列任务按过期租约恢复。无队列记录的旧生成中任务提示重试。相同设定/模型/规则去重，成功缓存 24 小时。保留旧同步 `/api/outlines/generate`。
- `POST /api/sessions/` 新增 `location`，`outline` 传入已筛选的字符串数组。主题与手工改名上限统一为 80 字符。`GET .../state` 中有提纲的 `coverage.mode='outline'`，`dims` 对应问题并带 `item`；无提纲保留七维返回。
- `POST /api/sessions/{sid}/outline_asked` 只接受本次提纲内问题，保存后推送最新覆盖度。
- `POST /api/sessions/{sid}/audio` 可带 multipart `chunk_id`（8–64 位字母/数字/下划线/连字符）。同编号同原始内容重传返回原 `seq`；内容不同返回 `409`。转写失败返回 `502`，原音和待处理编号保留，下次同编号重试不重复保存。已完成分片的确认可重复读取，不会再次追加录音。
- 导入材料增加可选表单 `location`（JSON），地区随会话保存；不新增免登录公网上传通道。鸿蒙客户端原生 HTTP 沿用主站 Cookie 与现有音频接口，Cookie 不传给网页、不存入录音队列。

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/outlines/generate` | 生成 AI 采访提纲 |
| `POST` | `/api/sessions/` | 创建会话 |
| `GET` | `/api/sessions/?limit=&offset=` | 查询历史会话 |
| `GET` | `/api/sessions/{sid}` | 查询会话详情 |
| `GET` | `/api/sessions/{sid}/state` | 获取录音恢复快照 |
| `PATCH` | `/api/sessions/{sid}` | 手动重命名 |
| `DELETE` | `/api/sessions/{sid}` | 删除会话及清理文件 |
| `POST` | `/api/sessions/{sid}/audio` | 上传音频分片并转写 |
| `POST` | `/api/imports/analyze` | 上传已有音频或文字稿并启动分析 |
| `GET` | `/api/imports/{job_id}` | 查询导入处理进度 |
| `POST` | `/api/imports/{job_id}/cancel` | 创建人请求取消导入，在安全检查点清理临时文件 |
| `GET` | `/api/sessions/{sid}/events` | 建立 SSE 事件流 |
| `POST` | `/api/sessions/{sid}/finish` | 结束会话并生成或复用报告 |
| `GET` | `/api/sessions/{sid}/report-job` | 查询后台纪要状态、阶段与进度 |
| `GET` | `/api/tasks/{task_id}` | 查询权限内通用任务元信息 |
| `GET` | `/api/sessions/{sid}/report.md` | 下载 Markdown 报告 |
| `POST` | `/api/suggestions/{sug_id}/{action}` | 更新建议状态 |
| `POST` | `/api/sessions/{sid}/outline_asked` | 标记提纲项已问 |
| `POST` | `/api/sessions/{sid}/dim_asked` | 标记维度已覆盖 |
| `GET` | `/api/sessions/{sid}/coverage` | 只读 AI 问透状态和原文证据，不自动启动历史材料分析 |
| `POST` | `/api/sessions/{sid}/coverage/analyze` | 明确发起完整逐字稿问透评估，持久后台执行 |
| `POST` | `/api/sessions/{sid}/simulate` | 开发环境注入模拟转写 |
| `GET` | `/asr-audio/{token}` | DashScope 一次性拉取音频 |
| `POST/GET/POST` | `/api/auth/login`、`/api/auth/me`、`/api/auth/logout` | 共用员工中心登录、本地会话与退出 |
| `POST` | `/api/auth/register`、`/api/auth/reset-password` | 员工注册与找回密码 |
| `GET` | `/api/auth/people?search=` | 查找可分享的在职员工 |
| `GET` | `/api/sessions/{sid}/recording` | 权限内回听录音；`download=1` 下载 |
| `GET` | `/api/sessions/{sid}/transcript.txt` | 下载逐字稿 |
| `GET` | `/api/sessions/{sid}/minutes.docx` | 下载含纪要和逐字稿的 Word |
| `GET/POST/DELETE` | `/api/sessions/{sid}/share-links` | 创建人生成、列出和撤销限时单项分享链接 |
| `GET` | `/api/share/{token}` | 无需登录，仅访问链接授权的单项内容；7 天后失效 |
| `GET` | `/api/sessions/{sid}/speakers`、`/api/sessions/{sid}/speakers.txt` | 查看或下载本次录音的发言人分离稿 |
| `POST` | `/api/sessions/{sid}/speakers/analyze` | 创建人确认单独同意后启动发言人分离 |
| `DELETE` | `/api/sessions/{sid}/speakers` | 清除分离结果及同意记录 |
| `PATCH` | `/api/sessions/{sid}/project` | 设定项目分组 |
| `GET/POST/DELETE` | `/api/sessions/{sid}/shares` | 员工共享名单管理 |
| `GET/POST/PATCH/DELETE` | `/api/sessions/{sid}/todos` | 待办查询、添加、勾选与删除 |
| `POST` | `/api/sessions/{sid}/ask` | 基于逐字稿的有据问答 |
| `PATCH` | `/api/sessions/{sid}/utterances/{seq}/speaker` | 人工标记发言人 |
| `PUT` | `/api/sessions/{sid}/report` | 人工修订纪要 Markdown |
| `GET` | `/api/projects`、`/api/projects/overview`、`/api/management/overview` | 项目列表与总览、统计和全部待办 |
| `POST` | `/api/projects` | 新建命名项目（同一创建人的同名项目返回原项目） |
| `GET/PATCH` | `/api/projects/{project_id}` | 项目访谈分页/总记录列表与项目改名 |
| `GET` | `/api/projects/{project_id}/bundle.zip` | 下载当前权限内的项目录音、完整逐字稿与已有纪要 |
| `POST` | `/api/projects/{project_id}/records` | 选择多场访谈，异步生成总访谈记录 |
| `GET` | `/api/project-records/{record_id}` | 生成状态、来源与正文 |
| `GET` | `/api/project-records/{record_id}/export?format=docx或md` | 导出综合 Word 或 Markdown |

### 项目文件夹与多场访谈综合

`GET /api/projects/{project_id}/bundle.zip` 返回 `application/zip` 附件，解压为“项目名_ID/访谈标题_ID/”。每场含已留存录音、`完整逐字稿.txt`、`完整逐字稿.jsonl`、已有 `会议纪要.md/.docx`；根目录含 `manifest.json` 与说明。JSONL 保留 `seq/start_ms/end_ms/speaker/text`，Word 只含纪要，逐字稿完整单独提供。没有音频或纪要时清单明确缺项，不伪造、不自动生成。

仅打包当前可见的资料；可见范围存在未结束或导入中访谈时返回 `409`，空项目 `400`，无权或不存在 `404`。默认最多 200 场、2 GiB 实际资料写入总量、1000 万文字字符、20 万逐字稿段，超限 `413`，绝不静默截断。打包及开始下载前复核权限、项目归属、文件和资料版本；变更时拒绝快照。临时文件在完成或断开后清理，ZIP 未加密，已传出的字节不能被事后撤权收回。底层仍按会话 ID 保存，下载包不替代整个数据目录备份。

`POST /api/projects` 接收 `{"name":"重庆制造业调研"}`，返回项目 `id/name/owner_id/access`。空项目保留。`GET /api/projects` 为兼容历史筛选保留 `projects` 名称数组，并新增 `items`，包含各项目的稳定 ID、权限与统计。同名项目按创建人隔离。

`PATCH /api/sessions/{sid}/project` 可接收 `{"project_id":"项目ID"}` 归档，空字符串解除归档；兼容旧 `{"project_name":"名称"}` 写法（自动创建当前会话创建人的项目）。只能将有修改权限的访谈归档到有修改权限的项目。

`GET /api/projects/{project_id}?limit=100&offset=0` 返回 `project/items/total/records`。`items` 含 `merge_eligible`（已结束且有逐字稿）；同事仅可查看获分享的访谈，不返回项目总记录。历史会话列表也支持 `project_id` 精确筛选。

`POST /api/projects/{project_id}/records` 接收 `{"title":"总体调研记录","session_ids":["会话1","会话2"]}`，返回 `202 {"record_id":"...","status":"generating"}`。只有项目创建人可操作，要求同一项目下 2–50 场不同的已结束访谈，每场必须有逐字稿；没有单场纪要时仍以原文综合。来源按访谈时间排序。重复提交同标题、同材料的进行中任务会返回原任务。

状态查询返回 `status/stage/error/markdown/sources`，状态为 `generating/ready/failed/stale`。独立 worker 完整读取全部逐字稿与纪要；长稿分段提取证据后分层综合，材料上限 200 万字，超限不截断。新持久任务中断后按过期租约恢复，失败重试耗尽后保留可读错误；用户可重新提交。正文含访谈编号/段落/时间引用及来源清单。来源指纹仅内部核验，不返回前端。

导出默认 `format=docx`，也可 `format=md`。来源材料修改、删除、移出项目或失去权限后，正文不再返回，导出返回 `409`，需重新生成。删除访谈或逐字稿片段同时清除相关总记录的派生正文。所有总记录读取与导出均验证项目创建人及各来源的访问权限；免登录时沿用单人工作区规则。

发言人分离状态接口 `/api/sessions/{sid}/speakers` 额外返回 `available`（必要配置是否齐全）及 `unavailable_reason`（不可用原因，配置齐全时为 `null`）。该检查不代表云端回调已实际连通。`POST /api/sessions/{sid}/speakers/analyze` 在缺少语音密钥或可供云端读取的 HTTPS 基础地址时返回 `503`，不会创建任务或写入同意记录；其余权限、单独同意和访谈结束状态检查保持不变。已生成的分离结果仍可读取、下载和删除。

认证模式下员工中心连接失败时，登录接口返回 `503`，提示检查员工中心服务和 `AUTH_API_URL`，不创建登录 Cookie 或会话；免登录模式登录接口按第 1.1A 节返回 `409`。

## 3. AI 采访提纲

### 3.1 生成提纲

`POST /api/outlines/generate`

请求：

```json
{
  "subject": "汽车零部件价格战",
  "interviewee_type": "企业高管",
  "industry": "汽车业",
  "focus": "销量、渠道",
  "location": {"city": "重庆市", "market": "受访企业所在汽车产业园"}
}
```

字段约束：

| 字段 | 必填 | 约束 |
|---|---:|---|
| `subject` | 是 | 字符串，去首尾空格后非空，最长 80 字 |
| `interviewee_type` | 是 | 必须属于访谈对象枚举，最长 20 字 |
| `industry` | 否 | 默认“综合/不限”，必须属于行业枚举，最长 30 字 |
| `focus` | 否 | 默认空字符串，最长 500 字 |
| `location` | 是 | 对象；生成提纲要求非空 `city`、`market`，不再收集 `district/town/address`；历史字段保持只读兼容，不改写历史记录 |

成功响应 `200`：

```json
{
  "background": "该地点的具体业态和经营特点尚待实地核实。",
  "background_note": "已执行联网检索，暂未取得可引用的地点公开资料；具体情况请实地核实。",
  "research_status": "no_results",
  "sources": [],
  "claims": [],
  "retrieved_at": "2026-10-15T08:00:00+00:00",
  "questions": [
    "您目前主要负责哪一块业务？",
    "近三年销量发生了什么变化？",
    "价格战最激烈是什么时候？"
  ],
  "count": 10
}
```

服务端会去除题号、清理引号、去重并补充问号；最多保留 12 个问题。少于 6 个有效问题视为生成失败并返回 `502`。网页先展示地点背景和核实提示，再展示问题；背景不写入录音问题列表或当作逐字稿证据。

上例演示无可引用结果，不是成功检索的来源示例。`research_status` 可为 `ready/ambiguous/no_results/unavailable/not_requested`；ready 时 `sources` 含 `{id,title,url,snippet,retrieved_at,published_at?}`，`claims` 含 `{source_id,excerpt}`，excerpt 为对应 snippet 的连续原样子串。只有实际搜索结果中的公共 HTTP(S) URL 才能作为来源；模型自写 URL 无效。搜索只发城市/市场，最多 2 次请求；同名异地、网络失败与无源均显式返回，仍可生成提纲。来源可能较旧，并不保证当前事实正确。

### 3.2 AI 问透状态

`GET /api/sessions/{sid}/coverage` 为权限内只读状态；`POST /api/sessions/{sid}/coverage/analyze` 显式入队，空稿不生成有效评估。详情和 `/state` 同样携带 coverage。新增逐字稿可触发增量任务，普通查看旧资料不会批量自动上云。

coverage 保留 `mode/dims/covered/missing/ratio`，新增 `engine='ai'`、`analysis_status/source_stale/task_id/stage/updated_at/analyzed_utterances/transcript_utterances/note/error`。每个 dim 带 `status/status_label/reason/evidence/follow_up/manual_asked`；evidence 包含真实 `seq/quote` 及定位时间。四状态为 `not_discussed`（尚未讨论）、`mentioned`（仅被提到）、`partial`（仍需追问）、`sufficient`（已有充分回答）。非空判断须引用可验证原话；`ratio` 只计 sufficient 项，`partial_ratio` 另表部分回答程度，人工已问不增加两者。AI 判断需人工核实，`source_stale` 时不得把旧结果说成最新全文分析。

## 4. 会话接口

### 4.1 创建会话

`POST /api/sessions/`

请求：

```json
{
  "subject": "汽车零部件价格战",
  "scene": "商户门店",
  "interviewee_type": "企业高管",
  "industry": "汽车业",
  "focus": "销量、渠道",
  "outline": [
    "您目前主要负责哪一块业务？",
    "近三年销量发生了什么变化？"
  ]
}
```

字段约束：

| 字段 | 必填 | 默认值与约束 |
|---|---:|---|
| `subject` | 否 | 默认“未命名访谈”，最长 40 字 |
| `scene` | 否 | 默认“商户门店”，最长 40 字 |
| `interviewee_type` | 否 | 默认“商户”，必须属于访谈对象枚举 |
| `industry` | 否 | 默认“综合/不限”，必须属于行业枚举 |
| `focus` | 否 | 默认空字符串，最长 500 字 |
| `outline` | 否 | 字符串数组或换行分隔字符串；最多 100 项，每项最多 200 字 |

非默认标题会令 `subject_source=user`；默认标题令其为 `default`。创建后服务端还会保存一条 `playbook` 开场建议。

成功响应 `200`：

```json
{
  "session_id": "abc123def456"
}
```

### 4.2 会话列表

`GET /api/sessions/?limit=100&offset=0&search=&project=&scope=all`

`scope` 可取 `all`、`mine`、`shared`。认证模式结果仅含当前员工创建或明确分享的会话；免登录模式以当前数据目录为单人工作区，不按员工归属隔离。`search` 同时查询标题、项目与逐字稿。

成功响应 `200`：

```json
{
  "items": [
    {
      "id": "abc123def456",
      "subject": "零部件渠道价格变化",
      "subject_source": "auto",
      "display_subject": "零部件渠道价格变化",
      "interviewee_type": "企业高管",
      "industry": "汽车业",
      "status": "done",
      "utterance_count": 18,
      "has_report": true,
      "report_status": "ready",
      "created_at": 1790557200.0
    }
  ],
  "total": 1
}
```

当前响应不回显 `limit` 和 `offset`。排序规则为 `live` 优先，其余按 `created_at DESC`；过滤 `deleting` 会话。

### 4.3 会话详情

`GET /api/sessions/{sid}`

成功响应 `200`：

```json
{
  "id": "abc123def456",
  "subject": "零部件渠道价格变化",
  "scene": "商户门店",
  "interviewee_type": "企业高管",
  "industry": "汽车业",
  "focus": "销量、渠道",
  "outline": ["您目前主要负责哪一块业务？"],
  "status": "done",
  "created_at": 1790557200.0,
  "finished_at": 1790559000.0,
  "subject_source": "auto",
  "utterance_count": 18,
  "has_report": true,
  "report_status": "ready",
  "display_subject": "零部件渠道价格变化",
  "utterances": [],
  "suggestions": [],
  "coverage": {
    "ratio": 0.71,
    "missing": ["risk"],
    "dims": []
  }
}
```

- `utterances` 最多返回最近 500 条，按 seq 升序排列。
- `suggestions` 不包含内部 `auto_naming` 记录。
- 建议行中的 `payload` 当前仍是 JSON 字符串，由前端解析。
- `report_markdown` 不在详情响应中返回。

### 4.4 恢复快照

`GET /api/sessions/{sid}/state`

返回会话表的全部字段，并附加：

- 解析后的 `outline`；
- `report_status`、`has_report`、`display_subject`；
- 最近 500 条 `utterances`；
- 非 `auto_naming` 的 `suggestions`；
- `coverage`。

与详情接口相比，当前 state 响应不包含 `utterance_count`。不存在返回 `404`。

### 4.5 手动重命名

`PATCH /api/sessions/{sid}`

请求：

```json
{
  "subject": "高管谈渠道价格变化"
}
```

标题去除首尾空格后必须为 1～80 字。成功后写入 `subject_source=user` 并发布 `renamed` SSE 事件。

成功响应 `200`：

```json
{
  "id": "abc123def456",
  "subject": "高管谈渠道价格变化",
  "subject_source": "user"
}
```

### 4.6 删除会话

`DELETE /api/sessions/{sid}`

创建人首次删除返回 `200`；删除后的再次请求返回 `404`，以避免泄露其他员工记录是否存在：

```json
{
  "ok": true,
  "already_deleted": false,
  "files_deleted": true,
  "cleanup_errors": []
}
```

数据库记录先删除，再清理 `data/uploads/{sid}`、`data/reports/{sid}.md` 与 `data/recordings/{sid}`。文件清理失败时：

- `ok` 仍表示数据库已达到删除目标；
- `files_deleted=false`；
- `cleanup_errors` 返回失败路径字符串；
- 服务端记录异常日志。

## 5. 音频、恢复与实时事件

### 5.1 上传音频分片

`POST /api/sessions/{sid}/audio`

请求为 `multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `audio` | File | 是 | 浏览器录音分片；默认最大 20 MiB |

成功响应 `200`：

```json
{
  "seq": 18
}
```

处理行为：

1. 读取数据库 `MAX(seq)+1`；
2. 将原始分片转为 16kHz 单声道 WAV；
3. 调用 DashScope ASR；
4. 非空文本入库并触发维度、信号、建议和命名流程；
5. 删除临时原始文件与 WAV 文件。

ASR 调用异常时，当前 HTTP 请求仍返回已分配的 `seq`，同时发布 `error` SSE 事件，并且不写入空逐字稿。

### 5.2 SSE 事件流

`GET /api/sessions/{sid}/events`

响应头：

```text
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

连接顺序：

1. 回放最近 500 条逐字稿；
2. 回放状态为 `new` 的非内部建议；
3. 发送 `ready`；
4. 持续发送实时事件。

| `type` | 主要字段 | 稳定 ID/说明 |
|---|---|---|
| `transcript` | `seq`、`start_ms`、`end_ms`、`text`、`dims` | `transcript:{seq}` |
| `signal` | `seq`、`signals` | UUID |
| `suggestion` | `id`、`kind` 及展开后的 payload | `suggestion:{id}` |
| `coverage` | `coverage` | UUID |
| `naming_started` | `phase` | UUID |
| `renamed` | `subject`、`subject_source` | UUID |
| `naming_failed` | 无额外业务字段 | UUID |
| `report_ready` | 无额外业务字段 | UUID |
| `error` | `message` | UUID |
| `ready` | 无 | 当前历史回放事件不带 SSE id |

当前服务端不发送主动心跳。浏览器依靠 `EventSource` 自动重连，并以 seq、建议 ID 或 `event_id` 去重。

### 5.3 标记提纲项已问

`POST /api/sessions/{sid}/outline_asked`

请求：

```json
{
  "item": "近三年销量发生了什么变化？"
}
```

成功响应：`{"ok":true}`。当前实现会为重复请求新增 `outline` 建议记录，但剩余提纲按集合语义计算。

### 5.4 标记维度已覆盖

`POST /api/sessions/{sid}/dim_asked`

请求：

```json
{
  "dim": "risk"
}
```

维度必须存在于服务端 `DIM_MAP`。成功响应 `{"ok":true}`，并发布最新 `coverage` 事件。

### 5.5 开发模拟转写

`POST /api/sessions/{sid}/simulate`

仅当 `FANGTAN_ALLOW_SIM=1` 时开放，否则返回 `403`。

请求：

```json
{
  "text": "今年原材料成本涨了不少。"
}
```

文本最长 5000 字。成功后走真实 seq、入库、维度、信号、建议和自动命名管线，并返回 `{"seq":18}`。

## 6. 建议状态

### 6.1 标记已问或忽略

`POST /api/suggestions/{sug_id}/{action}`

路径参数 `action` 仅允许：

- `asked`：已问；
- `dismissed`：忽略。

成功响应：

```json
{
  "ok": true
}
```

建议不存在返回 `404`，非法动作返回 `400`。

## 7. 报告接口

### 7.0 导入已有材料

`POST /api/imports/analyze` 使用 `multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `material` | File | 是 | 支持的音频或文字稿 |
| `subject` | string | 否 | 最长 40 字；空值使用文件名 |
| `user_name` | string | 否 | 兼容旧客户端；认证模式使用已登录员工姓名，免登录模式使用“本地使用者”，不接受冒名填写 |
| `interviewee_type` | string | 否 | 默认“商户”，必须属于枚举 |
| `industry` | string | 否 | 默认“综合/不限”，必须属于枚举 |
| `focus` | string | 否 | 最长 500 字 |

音频支持 `.mp3 .m4a .wav .aac .ogg .flac .wma .webm .mp4`，默认最大 200MB；文字稿支持 `.txt .md .docx .srt .vtt .csv .json .html .htm`，默认最大 20MB，解析后正文最多 50 万字符和 1 万段。

成功接收返回 `202`，字段包括 `job_id`、`status`、`stage`、`progress`、`filename`、`kind`、`session_id`、`error`、`created_at`、`updated_at`。临时磁盘路径和内部分析上下文不返回。

`GET /api/imports/{job_id}` 返回相同公开结构，`progress` 为 0～100 百分比。状态包括 `queued/processing/ready/failed/cancelling/cancelled`；内部重试映射为排队。接收时预建固定会话占位，只有归档逐字稿后才通过公开 `session_id` 提供结果入口。任务不存在返回 `404`，格式不支持返回 `415`，超限返回 `413`。任务持久化，独立 worker 中断后可重新领取；失败保留恢复所需材料，确认成功或取消才清理限定目录。旧进程内任务必须在升级前处理完毕，不凭空恢复。

### 7.1 结束会话并生成报告

`POST /api/sessions/{sid}/finish`

兼容旧客户端的同步模式（不带后台标记）：

1. 将会话标记为 `done`，`finished_at` 只在首次写入；
2. 默认标题仍未命名时提交一次收尾自动命名；
3. 已有数据库报告时直接复用；
4. 否则在线程池调用共享的报告业务处理器，与 worker 采用相同的来源版本校验和原子保存；
5. 成功后数据库为最终报告依据，并发布当前 API 进程的 `report_ready`。

成功响应 `200`：

```json
{
  "report": "# 访谈纪要：……",
  "report_status": "ready"
}
```

上游失败返回 `502`。此时会话已是 `done`，`report_status=failed`，用户可以从详情页重试。

### 7.1A 新网页后台报告模式（v0.11）

相同 `POST /api/sessions/{sid}/finish` 带请求头 `X-Background-Task: 1`，或查询参数 `background=true`。已有报告仍返回上述 `200`；否则提交或复用正在执行的持久报告任务，返回：

```json
{
  "session_id": "会话ID",
  "task_id": "任务ID",
  "report_status": "generating"
}
```

响应状态为 `202`。客户端无需保持请求连接，改为查询 `GET /api/sessions/{sid}/report-job`，返回 `report_status/task_id/stage/progress/error`；`progress` 是 0～100 百分比，状态为 `none/generating/ready/failed`，数据库报告存在时优先为 `ready`。报告正文仍通过会话详情或 `/report.md` 读取，任务结果不重复保存纪要正文。后台处理器不依赖跨进程 SSE，网页以轮询校准。

若该会话仍有执行中的导入任务，纪要查询优先展示导入阶段；`finish` 复用该任务返回 `202`，不得对尚未归档完的空稿另排报告任务。提纲、导入与 `report-job` 的公开进度统一为 0～100，下面通用任务接口保留 0～1。

`GET /api/tasks/{task_id}` 返回权限内的 `id/kind/status/stage/progress/attempts/max_attempts/created_at/updated_at`。该通用接口的 `progress` 为 0～1，内部状态为 `queued/running/retry/ready/failed/cancelled`；不返回 payload、文件路径、租约令牌或模型材料。内部任务取消是合作式，不能撤回已发送给云端的调用；仅导入提供既有用户取消入口，其他取消由资源删除或同意撤回等业务动作触发。

### 7.2 下载报告

`GET /api/sessions/{sid}/report.md`

- 报告存在：返回 `200` 和 `text/markdown; charset=utf-8`；
- 报告不存在：返回 `404`；
- 正文直接从 SQLite 读取，不依赖 Markdown 副本。

## 8. 内部接口

### 8.1 DashScope 拉取一次性音频

`GET /asr-audio/{token}`

- 仅供 DashScope filetrans 使用；
- token 只可消费一次；
- 成功返回 `audio/wav`；
- token 不存在、已消费或对应文件不存在时返回 `404`。

## 9. 维护记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.7 | 2026-10-09 | 增加免登录工作区开关、`auth_enabled` 响应字段、暂停接口的 `409` 与切回认证的归属规则 |
| v0.8 | 2026-10-12 | 新增稳定 ID 项目文件夹、多访谈综合记录异步任务、来源有效性核验与 Word/Markdown 导出 |
| v0.9 | 2026-10-13 | 地区提纲持久任务、筛选问题覆盖度、location 上下文及 chunk_id 录音重试去重 |
| v0.10 | 2026-10-15 | 提纲结果新增地点背景与核实提示；新地区输入简化为 city/district/market |
| v0.10 补充 | 2026-10-15 | 移除区县输入及生成必填约束，新地区仅 city/market；历史地区字段保留 |
| v0.11 | 2026-10-15 | 五类任务持久化、后台 finish 202 与 report-job 查询、通用任务元信息、旧同步兼容及中断恢复边界 |
