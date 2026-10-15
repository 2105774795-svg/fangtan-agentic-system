"""配置加载：优先使用进程环境变量，本地开发可读取 backend/.env。"""
import os
from pathlib import Path


def _load_dotenv():
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

PORT = int(os.getenv("FANGTAN_PORT", "18795"))
# 默认保留员工认证；只有显式关闭时才启用本地单人工作区。
AUTH_ENABLED = os.getenv("FANGTAN_AUTH_ENABLED", "true").strip().lower() not in {"false", "0", "no", "off"}
_data_setting = Path(os.getenv("FANGTAN_DATA_DIR", "../data")).expanduser()
# .env 位于 backend/，相对目录也以 backend/ 为基准，避免从项目根目录运行时写到旁边的目录。
DATA_DIR = (_data_setting if _data_setting.is_absolute() else Path(__file__).resolve().parent / _data_setting).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
REPORT_DIR = DATA_DIR / "reports"
DB_PATH = DATA_DIR / "fangtan.db"

PUBLIC_BASE = os.getenv("FANGTAN_PUBLIC_BASE", f"http://127.0.0.1:{PORT}")

KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
KIMI_MODEL = os.getenv("KIMI_MODEL", "moonshot-v1-32k")
KIMI_OUTLINE_MODEL = os.getenv("KIMI_OUTLINE_MODEL", KIMI_MODEL)

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_FILETRANS_MODEL = os.getenv("DASHSCOPE_FILETRANS_MODEL", "qwen-audio-3.0-asr-flash-filetrans")
DASHSCOPE_FLASH_MODEL = os.getenv("DASHSCOPE_FLASH_MODEL", "qwen-audio-3.0-asr-flash")
DASHSCOPE_FLASH_URL = os.getenv(
    "DASHSCOPE_FLASH_URL",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
)

CHUNK_MS = int(os.getenv("FANGTAN_CHUNK_MS", "10000"))   # 前端每段录音时长
SUGGEST_COOLDOWN_SEC = int(os.getenv("FANGTAN_COOLDOWN", "45"))  # 追问建议冷却
MAX_AUDIO_BYTES = int(os.getenv("FANGTAN_MAX_AUDIO_BYTES", str(20 * 1024 * 1024)))
MAX_IMPORT_AUDIO_BYTES = int(os.getenv("FANGTAN_MAX_IMPORT_AUDIO_BYTES", str(200 * 1024 * 1024)))
MAX_IMPORT_TEXT_BYTES = int(os.getenv("FANGTAN_MAX_IMPORT_TEXT_BYTES", str(20 * 1024 * 1024)))

# 可选的发布探针与使用量统计；默认不向任何统计服务发送数据。
DEV_PROGRESS_TOKEN = os.getenv("DEV_PROGRESS_TOKEN", "")
TEAMFLOW_USAGE_URL = os.getenv("TEAMFLOW_USAGE_URL", "")
TEAMFLOW_PRODUCT_ID = os.getenv("TEAMFLOW_PRODUCT_ID", "")
TEAMFLOW_PRODUCT_NAME = os.getenv("TEAMFLOW_PRODUCT_NAME", "访谈-体察Agentic系统")
