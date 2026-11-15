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

    def __post_init__(self):
        """验证密钥长度和令牌寿命。"""
        if len(self.jwt_secret.encode()) < 32:
            raise ValueError("JWT_SECRET至少需要32字节")
        if not 1 <= self.token_minutes <= 1440:
            raise ValueError("TOKEN_MINUTES必须在1至1440之间")

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
                   max_upload_bytes=int(os.environ.get('MAX_UPLOAD_BYTES', str(50 * 1024 * 1024))))
