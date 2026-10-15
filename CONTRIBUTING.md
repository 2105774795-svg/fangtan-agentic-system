# 协作指南

欢迎改进汇言笔与时叙录。代码按 [MIT 许可证](LICENSE) 开放；运行资料、账号权限和第三方服务的使用权不因此开放。

## 开始之前

- 先阅读 [README](README.md)、[架构说明](specs/ARCHITECTURE.md) 和相关接口文档，大改动先开 Issue 说明目标。
- 保留现有 UI 和交互习惯；未经明确约定，不合并汇言笔与时叙录的数据、权限或导出范围。
- FastAPI 当前必须采用单 Uvicorn Worker；耗时任务由独立的 `backend/worker.py` 处理。不能简单增加 API Worker 或声称支持多实例。
- 不要自行启停生产服务、执行部署脚本或连接生产数据库。服务启动与上线需要维护者明确批准。

## 先用隔离数据做离线测试

不要使用现有业务数据库。首次测试应在新的终端设置独立临时目录，使用项目虚拟环境和合成材料，并清空云服务密钥；不要复制真实 `.env` 或开启真实云调用。

下面的 PowerShell 命令只运行后端测试，不启动 API 或后台服务。前提是已经按 README 安装好测试所需依赖。

```powershell
$testDataDir = Join-Path ([IO.Path]::GetTempPath()) ('fangtan-tests-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDataDir | Out-Null
$env:FANGTAN_DATA_DIR = $testDataDir
$env:FANGTAN_AUTH_ENABLED = 'false'
$env:KIMI_API_KEY = ''
$env:DASHSCOPE_API_KEY = ''
$env:TEAMFLOW_USAGE_URL = ''
$env:TEAMFLOW_PRODUCT_ID = ''
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -q
```

`FANGTAN_AUTH_ENABLED=false` 仅用于这里的隔离测试，不是公网部署建议。环境变量只作用于该终端，测试后关闭即可。需要浏览器回归时，使用 `backend/tests/test_*frontend.cjs` 等现有脚本及其 mock 数据；准备好 Node.js、Playwright 和本地浏览器后运行，禁止用真实访谈替代测试夹具。实际云服务联调另行申请授权，并说明费用及数据去向。

## 提交与 Pull Request

1. 一个 PR 聚焦一项修改，补充必要测试、文档和兼容性说明。
2. 只暂存本次明确选择的文件，提交前检查 `git diff --cached`；不要将其他人的未暂存工作一并提交。
3. 说明执行了哪些测试和结果；未执行、仅 mock 或尚无环境验证的项目如实注明。
4. UI 改动附桌面和手机截图，只用合成姓名、录音与文字稿。
5. 涉及认证、分享、导出、后台任务或删除时，覆盖越权、资料隔离、取消、迟到结果及删除后的派生数据清理。
6. 涉及录音保存、云分析、发言人分离时，检查单独同意、撤回和删除机制，不默认扩大数据处理范围。
7. 鸿蒙工程未完成 DevEco SDK 编译、签名和对应真机验证前，不宣称 HAP 已可运行或已通过后台录音实测。

## 绝不提交的内容

禁止提交 `.env`、API 密钥、Cookie、令牌、私钥、数据库、真实录音/逐字稿/纪要、日志、生产备份、员工信息，以及私密时叙录材料。截图、测试报告和 Git 历史同样需要脱敏。示例配置只能使用空值或明显占位符，不得复制现网域名、账号或主机配置作为可直接使用的凭据。

发现误提交密钥或隐私资料时，立即按 [安全说明](SECURITY.md) 私下联系维护者；只删除当前文件不代表 Git 历史和下载副本已清除。
