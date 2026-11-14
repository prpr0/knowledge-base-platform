"""为组织权限接口提供独立数据库和真实签名身份。"""
import secrets
import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.models import Base, Department, Role, User
from backend.app.schemas import PERMISSION_CODES
from backend.app.security import hash_password, issue_token
from backend.app.settings import Settings

@pytest.fixture(scope="session")
def password_hash():
    """测试密码只用于每例独立的内存数据库。"""
    return hash_password("test-password-only")

@pytest.fixture
def api(password_hash):
    """创建可撤权的管理员与读者，令牌从真实安全函数产生。"""
    settings = Settings("sqlite+pysqlite:///:memory:", secrets.token_urlsafe(48))
    app = create_app(settings)
    Base.metadata.create_all(app.state.engine)
    with app.state.sessions() as session:
        parent = Department(name="技术部")
        session.add(parent)
        session.flush()
        department = Department(name="运维部", parent_id=parent.id)
        admin_role = Role(name="系统管理", permissions=sorted(PERMISSION_CODES))
        reader_role = Role(name="知识读者", permissions=["knowledge:read"])
        session.add_all([department, admin_role, reader_role])
        session.flush()
        admin = User(username="admin", display_name="管理", password_hash=password_hash, roles=[admin_role])
        reader = User(username="reader", display_name="读者", password_hash=password_hash, department_id=department.id, roles=[reader_role])
        session.add_all([admin, reader])
        session.commit()
        ids = dict(admin=admin.id, reader=reader.id, parent=parent.id, department=department.id,
                   admin_role=admin_role.id, reader_role=reader_role.id)
        headers = {"Authorization": "Bearer " + issue_token(admin.id, admin.token_version, settings)}
        reader_headers = {"Authorization": "Bearer " + issue_token(reader.id, reader.token_version, settings)}
    with TestClient(app) as client:
        yield dict(client=client, app=app, ids=ids, admin=headers, reader=reader_headers)
