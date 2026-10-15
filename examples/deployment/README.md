# 通用 Linux 部署示例

这些文件是自管部署参考，不代表已经安装、启用或通过你的环境验收。由管理员先审阅再执行；不要直接复制到不明服务器，也不要把开发机密钥、录音或数据提交至仓库。

## 1. 运行约束

- Python 3.10+，一个 FastAPI 进程及一个独立 Worker，二者使用相同虚拟环境、配置和数据目录。
- API **仅一个 Uvicorn worker**。实时录音锁与 SSE 是进程内状态，不能直接横向扩容。
- 示例安装目录 `/opt/fangtan-agent`，虚拟环境 `/opt/fangtan-agent/.venv`，运行账户/用户组均为 `fangtan`，监听 `127.0.0.1:18995`。
- 不要以 root 运行应用。管理员需预先创建专用账户、安装目录和数据目录，并仅授予必要访问权；`UMask=0077` 不能修复已有文件的过宽权限。
- 示例单机使用 SQLite。数据库文件及其目录必须允许同一运行账户读写；不要放在未经验证的网络共享盘上。

## 2. 安装与配置

在安装目录取得源码，创建虚拟环境并安装依赖。国内环境可使用活跃的镜像：

```bash
python3 -m venv /opt/fangtan-agent/.venv
/opt/fangtan-agent/.venv/bin/python -m pip install -i https://mirrors.aliyun.com/pypi/simple/ -r /opt/fangtan-agent/backend/requirements.txt
```

由管理员复制 `backend/.env.example` 为 `backend/.env` 并填入自己的配置。密钥文件仅限运行账户读取，建议权限 `0600`。API 与 Worker 都读取同一份文件；示例只用 dotenv 支持的简单 `KEY=VALUE` 格式，不要在值后面追加行尾注释。

重点检查：

1. `FANGTAN_DATA_DIR` 指向受限数据目录，例如 `/opt/fangtan-agent/data`，不在网页静态目录内；相对路径以 `backend/` 为基准。
2. `FANGTAN_AUTH_ENABLED=true`，并适配 `AUTH_API_URL` 指向的员工中心。员工中心实现不在本仓库；身份接口契约见 `backend/employee_auth.py`。不要误把示例本机端口当作已提供的服务。
3. 配置自己的 Moonshot、DashScope 凭据和可用模型，确认上传资料的授权及供应商处理规则。
4. `FANGTAN_PUBLIC_BASE` 改为自己的公网 HTTPS 根地址，或含子路径的公开地址。必须与反向代理和浏览器实际访问路径一致，禁止沿用他人站点。
5. 用量统计默认关闭；只有在已告知使用者、确需统计并控制接收端时才主动配置 `TEAMFLOW_USAGE_URL`。

`FANGTAN_AUTH_ENABLED=false` 仅适用于受控本机单人使用，不是可公开的生产配置。时叙录始终要求独立员工验证，不提供匿名降级。员工中心无法使用时应先完成适配，不应绕过鉴权。

## 3. 双进程 systemd 模板

本目录提供：

- [`fangtan-api.service`](fangtan-api.service)：网页与 API；
- [`fangtan-worker.service`](fangtan-worker.service)：持久任务处理。

模板统一使用 `User=fangtan`、`Group=fangtan`、`UMask=0077`。若调整路径或用户，两份文件和数据权限必须同步调整。代码可由管理员维护，运行账户只需读取代码/环境和写入数据、临时目录；注意 FFmpeg 所需的临时文件空间。

确认账户、目录权限、环境文件和依赖后，管理员可显式安装与启用：

```bash
sudo install -m 0644 /opt/fangtan-agent/examples/deployment/fangtan-api.service /etc/systemd/system/fangtan-api.service
sudo install -m 0644 /opt/fangtan-agent/examples/deployment/fangtan-worker.service /etc/systemd/system/fangtan-worker.service
sudo systemctl daemon-reload
sudo systemctl enable --now fangtan-api.service fangtan-worker.service
```

这一步会启动服务，只应在准备就绪后手动执行。检查两个服务，而非只看网页是否能打开：

```bash
sudo systemctl status fangtan-api.service fangtan-worker.service
sudo journalctl -u fangtan-api.service -u fangtan-worker.service --since "10 minutes ago"
```

缺少 Worker 时网页可能正常，但提纲、纪要等任务会留在队列。不要因为任务排队而重复启动多个 API 实例。日志可能含业务标识或错误详情，故障反馈前需脱敏。

## 4. HTTPS 与反向代理

应用端口保持仅回环监听，由你管理的 Nginx/Caddy 等提供 HTTPS：

- 转发普通 API、静态资源及 SSE；SSE 禁止代理缓冲，设置合理长连接超时。
- 反向代理上传上限需容纳默认 200MB 音频及 multipart 额外开销，但不能无限放宽；同时配置应用层限额和临时目录容量。
- 正确设置 Cookie、HTTPS 和可信代理头。模板只信任本机代理；若代理位于其他主机，应精确调整可信地址，而不是信任所有来源。
- 路径前缀部署时，代理转发路径、公开 URL、下载和 Cookie 路径必须一起验收。
- 供应商异步转写需访问应用生成的受控音频回调；不得直接暴露 `data/`、SQLite、`.env` 或私密录音目录。
- 检查主站已有门禁与应用权限如何叠加；不能因加了反向代理就假定所有接口受保护。

本仓库不附带域名证书、主站登录网关或员工中心。具体代理和身份集成应由部署者按自己的基础设施配置。

## 5. 升级、备份与验收

升级前先确认没有正在录音、导入或执行的生成任务，安排维护窗口，并备份当前源码版本、环境配置和整个数据目录。SQLite 一致备份应使用 SQLite 在线备份接口，或在确认两个进程都已安全停止后备份；不要只复制活动数据库主文件而漏掉 WAL 内容。

由管理员显式停止并更新两个进程，在隔离数据目录跑测试，再启动 API 和 Worker。不应自动清空队列、删库重建或把旧数据库覆盖到有新数据的实例。回滚前先核对迁移兼容性，不能仅回滚代码就假定数据兼容。

部署后至少人工验证：

- 员工登录、退出，以及匿名用户对列表、任务、录音和导出接口的拒绝访问；
- 一个明确标识的合成示例能完成提纲、文本导入、纪要及项目导出，Worker 确实消费任务；
- 时叙录再次验证、两个测试账号的资料隔离；旧访谈搜索及项目导出无法看到私密资料；
- 删除示例后清理其任务、原音和派生内容，不误删真实资料；
- 手机操作、SSE 更新、上传和下载路径。浏览器熄屏持续录音不作保证，鸿蒙工程另需编译签名和真机验收。

备份也包含个人资料，需受控保存并定期验证恢复。ZIP 导出没有加密，不能代替数据库和全部原音备份。源码发布不包含、也不应包含你的 `.env`、运行数据或审计记录。
