# 独立对谈模块 · 2026-10-17

> 新版口述史已增加主 Agent、可追溯长期记忆及跨次章节，当前说明见 [口述史 Agent 实现](MEMOIR_AGENT.md)。下文保留通用对谈协议。

连续语音的设备复用、自动轮转、停顿计时和故障恢复见 [连续语音对谈](AUTO_CONVERSATION.md)。一次开启后自动进行多轮，暂停与结束时关闭麦克风。

## 业务与基础设施

统一项目入口 → 口述史策略 → ConversationRoom（复用） → Conversation API（复用） → 逐轮回复任务 → 本次结束 → 有据整理任务 → MemoirExperience 档案。

面试可注册新策略并使用同一个 ConversationRoom、语音输入、轮次协议和任务机制。现有 interview 策略在接口层可用；当前产品仅添加口述史入口。

## 数据与协议

- conversation_sessions：项目、策略、活动/结束、整理结果与任务。
- conversation_turns：原始本人文字与助手回应分角色保存，client_id 幂等，流式 output 不冒充已保存全文。
- conversation_audio：session/turn_key/part_index 关联，顺序与文件摘要校验，原始容器和转写用 WAV 分开留存。
- conversation_people：仅记录本人明确说明的称呼及来源轮次。
- conversation_archive_versions：整理稿修改与审阅的旧版本，原音原话不变。

端点以 /api/platform/projects/{pid} 为范围：POST conversations（创建/续聊）；GET conversations/{sid}；GET events；POST audio、turns、finish、retry、archive-review；GET audio/{id}（original=true 下载原始文件）；GET conversation-archive。

SQLite 事务保证一次本人轮次只关联一个助手轮次。转写失败保留服务端音频和客户端 IndexedDB 片段。轮次没有提交成功不清理本机索引；刷新能恢复，已结束场次可继续阅读，再聊创建新场次。项目删除取消两种对谈任务，所有迟到保存重新检查项目与任务租约。

## 对话与档案约束

一次只问一个具体问题，从上句承接；允许拒答、重复、停顿和换题。只有短且明确的暂停/结束指令改变状态，故事中引用“今天先聊到这里”不会自动挂断。

归档复用旧口述史纯领域的分块和证据校验。仅使用当前场次本人原话，助手文字不作为经历来源。时间短语必须来自证据或标为未提及；候选线索和未确认细节不变成已核实事实。自动保存为草稿，可人工修改和审阅。

## 运行边界

API + 通用 Worker + conversation_worker.py 即时回应进程。语音采用分段 ASR、流式文字回应、设备本地中文朗读与自动轮转。没有伪装为全双工实时音频模型；没有承诺全方言、锁屏后台录音或设备均有中文声音。新版免登录按项目隔离资料，不修改旧时叙录认证表。
