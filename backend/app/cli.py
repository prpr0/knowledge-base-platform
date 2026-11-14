"""以交互或标准输入初始化管理员和重设密码，不保存明文凭证。"""
import argparse
import getpass
import sys
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .database import make_engine, session_factory
from .models import Role, User
from .schemas import PERMISSION_CODES, UserCreate
from .security import hash_password
from .settings import Settings
from .logging_setup import configure_logging


def main() -> int:
    """显式维护管理员凭证，不在启动时生成默认账号。"""
    parser = argparse.ArgumentParser(description='知识库管理员维护')
    parser.add_argument('action', choices=['create-admin', 'set-password'])
    parser.add_argument('username')
    parser.add_argument('--display-name', default='系统管理员')
    parser.add_argument('--password-stdin', action='store_true', help='从标准输入读取一行密码，避免出现在命令参数中')
    args = parser.parse_args()
    configure_logging()
    try:
        settings = Settings.from_env()
        password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('密码：')
        if not args.password_stdin and password != getpass.getpass('再次输入密码：'):
            raise ValueError('两次密码不一致')
        validated = UserCreate(username=args.username, display_name=args.display_name, password=password)
        engine = make_engine(settings.database_url)
        try:
            with session_factory(engine)() as session:
                user = session.scalar(select(User).where(User.username == args.username))
                if args.action == 'create-admin':
                    if user:
                        raise ValueError('账号已存在；如需重设密码使用set-password')
                    role = Role(name='管理员:' + args.username, permissions=sorted(PERMISSION_CODES))
                    user = User(username=validated.username, display_name=validated.display_name,
                                password_hash=hash_password(password), roles=[role])
                    session.add(user)
                else:
                    if not user:
                        raise ValueError('账号不存在')
                    user.password_hash = hash_password(password)
                    user.token_version += 1
                    user.permission_version += 1
                session.commit()
            print('管理员创建成功' if args.action == 'create-admin' else '密码已更新，旧令牌已撤销')
            return 0
        finally:
            engine.dispose()
    except (ValueError, IntegrityError):
        print('操作失败：请检查账号、密码长度、配置或重复名称；未写入凭证。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
