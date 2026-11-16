"""从运行环境读取数据库与认证配置，拒绝隐式降级。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    """保存单个应用实例使用的配置。"""
    database_url: str = field(repr=False)
    jwt_secret: str = field(repr=False)
    token_minutes: int = 60
    data_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / 'data')
    tokenizer_path: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / 'models' / 'qwen-tokenizer')
    embedding_model_path: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / 'models' / 'bge-m3')
    reranker_model_path: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / 'models' / 'bge-reranker-v2-m3')
    redis_url: str = field(default='redis://127.0.0.1:16379/0', repr=False)
    max_upload_bytes: int = 50 * 1024 * 1024
    model_base_url: str = ''
    model_mode: str = 'finetuned'
    model_name: str = 'kb-grounded-qwen3-4b-v1'
    model_disable_thinking: bool = False
    model_api_key: str = field(default='', repr=False)
    model_release_manifest_path: Path | None = None
    model_calibration_manifest_path: Path | None = None
    config_encryption_key: str = field(default='', repr=False)
    faq_cluster_similarity: float = .90

    def __post_init__(self):
        """验证密钥长度和令牌寿命。"""
        if self.model_mode not in ('finetuned', 'external_api'):
            raise ValueError('MODEL_MODE必须为finetuned或external_api')
        if len(self.jwt_secret.encode()) < 32:
            raise ValueError("JWT_SECRET至少需要32字节")
        if not 1 <= self.token_minutes <= 1440:
            raise ValueError("TOKEN_MINUTES必须在1至1440之间")
        if not 0 < self.faq_cluster_similarity <= 1:
            raise ValueError('FAQ_CLUSTER_SIMILARITY必须在0到1之间')

    @classmethod
    def from_env(cls):
        """正式运行必须显式连接PostgreSQL，不回退到本机数据库。"""
        url = os.environ.get("DATABASE_URL", "")
        if not url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL必须显式配置虚拟机PostgreSQL连接")
        root = Path(__file__).resolve().parents[2]
        return cls(database_url=url, jwt_secret=os.environ.get('JWT_SECRET', ''),
                   token_minutes=int(os.environ.get('TOKEN_MINUTES', '60')),
                   data_root=Path(os.environ.get('DATA_ROOT', str(root / 'data'))),
                   tokenizer_path=Path(os.environ.get('TOKENIZER_PATH', str(root / 'models' / 'qwen-tokenizer'))),
                   embedding_model_path=Path(os.environ.get('EMBEDDING_MODEL_PATH', str(root / 'models' / 'bge-m3'))),
                   reranker_model_path=Path(os.environ.get('RERANKER_MODEL_PATH', str(root / 'models' / 'bge-reranker-v2-m3'))),
                   redis_url=os.environ.get('REDIS_URL', 'redis://127.0.0.1:16379/0'),
                   max_upload_bytes=int(os.environ.get('MAX_UPLOAD_BYTES', str(50 * 1024 * 1024))),
                   model_base_url=os.environ.get('MODEL_BASE_URL', ''),
                   model_mode=os.environ.get('MODEL_MODE', 'finetuned'),
                   model_name=os.environ.get('MODEL_NAME', 'kb-grounded-qwen3-4b-v1'),
                   model_disable_thinking=os.environ.get('MODEL_DISABLE_THINKING', 'false').lower() in ('true', '1', 'yes'),
                   model_api_key=os.environ.get('MODEL_API_KEY', ''),
                   model_release_manifest_path=Path(os.environ['MODEL_RELEASE_MANIFEST_PATH']) if os.environ.get('MODEL_RELEASE_MANIFEST_PATH') else None,
                   model_calibration_manifest_path=Path(os.environ['MODEL_CALIBRATION_MANIFEST_PATH']) if os.environ.get('MODEL_CALIBRATION_MANIFEST_PATH') else None,
                   config_encryption_key=os.environ.get('CONFIG_ENCRYPTION_KEY', ''),
                   faq_cluster_similarity=float(os.environ.get('FAQ_CLUSTER_SIMILARITY', '.90')))
