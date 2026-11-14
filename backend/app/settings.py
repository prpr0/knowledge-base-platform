"""从运行环境读取数据库与认证配置，拒绝隐式降级。"""
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    """保存单个应用实例使用的配置。"""
    database_url: str
    jwt_secret: str
    token_minutes: int = 60

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
        return cls(url, os.environ.get("JWT_SECRET", ""), int(os.environ.get("TOKEN_MINUTES", "60")))
