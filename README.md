# 知识库管理平台

中文技术支持与操作知识库平台。当前实施“组织与权限”阶段：本地FastAPI后端与Vue控制台，虚拟机PostgreSQL16持久化。

已实现登录、部门树、用户/角色管理、菜单按钮RBAC、文本知识单位及四维ACL。文件导入、索引、检索、问答、FAQ、缺口、运营看板和模型联调仍按方案后续实施；文本单位用于真实ACL保护读取，不冒充完整知识导入流程。

本项目对应 `real/2.9.md` 的知识维护、权限问答、FAQ、缺口与运营需求。最终生成模型必须来自独立项目 `knowledge-model-finetuning`，发布别名为 `kb-grounded-qwen3-4b-v1`。 两项目通过 OpenAI 兼容服务契约协作，不复制代码或导入兄弟目录。微调模型发布前，基座对照实验不计为最终联调成功。

详细技术栈、架构、需求覆盖、实施顺序、统一契约与未来验收见 [实现方案](docs/implementation-plan.md)。

## 本地依赖与运行前提

Python 3.11.x、uv、Node.js24。本次Python依赖安装在项目`.venv`，前端依赖在`frontend/node_modules`；`uv.lock`和`frontend/package-lock.json`保留用于复现。安装命令会下载缺失依赖，应先取得下载授权；本阶段已经获准安装。

```powershell
uv sync --locked
Push-Location frontend
npm ci
Pop-Location
```

数据库运行于虚拟机，本地不启动Docker或PostgreSQL。当前使用已配置SSH别名`financial-risk-audit-vm`（192.168.10.150），只复用虚拟机连接与已有PostgreSQL16镜像缓存；本项目容器`knowledge-base-platform-postgres`、数据卷`knowledge-base-platform-postgres-data`独立。容器端口仅绑定虚拟机`127.0.0.1:15432`，不对局域网暴露，不操作其他项目数据库。

保持一个独立终端运行隧道：

```powershell
ssh -o ExitOnForwardFailure=yes -N -L 127.0.0.1:15432:127.0.0.1:15432 financial-risk-audit-vm
```

新终端在项目根配置进程环境。以下将初始化密码直接存入进程变量，不回显或写入配置文件；适用于当前已有的本项目容器。如果已修改数据库角色密码，应使用修改后的密码。

```powershell
$databasePassword = (ssh financial-risk-audit-vm 'docker exec knowledge-base-platform-postgres printenv POSTGRES_PASSWORD').Trim()
$escapedDatabasePassword = [uri]::EscapeDataString($databasePassword)
$env:DATABASE_URL = "postgresql+psycopg://knowledge_platform:${escapedDatabasePassword}@127.0.0.1:15432/knowledge_base_platform"
$env:JWT_SECRET = (& .venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(48))").Trim()
Remove-Variable databasePassword,escapedDatabasePassword
$env:TOKEN_MINUTES = '60'
.venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
```

`DATABASE_URL`与至少32字节`JWT_SECRET`必填，缺失明确失败，不自动降级到SQLite；`.env.example`仅说明实际变量，程序不自动加载`.env`。JWT密钥在同一服务生命周期保持稳定，换密钥会撤销已有登录。

首次新数据库用`create-admin`交互创建管理员；本阶段验证已创建`admin`，交给用户使用前应交互设定自己的密码：

```powershell
# 仅首次空数据库需要：
.venv/Scripts/python.exe -m backend.app.cli create-admin admin --display-name 管理员
# 已有账号时使用，不设置默认或公开密码：
.venv/Scripts/python.exe -m backend.app.cli set-password admin
.venv/Scripts/python.exe -m uvicorn backend.app.main:create_app --factory --host 127.0.0.1 --port 8016 --no-access-log
```

密码交互输入两次，或自动化显式使用`--password-stdin`；密码不出现在命令行参数。重设密码会撤销旧令牌。后端健康检查`http://127.0.0.1:8016/api/health`实际访问数据库，API文档`http://127.0.0.1:8016/docs`。

另一个终端启动前端：

```powershell
Set-Location frontend
npm run dev -- --host 127.0.0.1
```

浏览器访问`http://127.0.0.1:5176`，开发代理将`/api`转给本地8016。登录令牌仅在内存，刷新页面需要重新登录；服务端每次读取最新账号与角色，菜单隐藏不能替代API鉴权。

## 验证与权限语义

```powershell
.venv/Scripts/python.exe -B -m pytest tests/test_access.py tests/test_api.py tests/test_login_contract.py -q
Push-Location frontend
npm test
npm run build
Pop-Location
```

相关测试使用每例独立SQLite内存数据库；正式运行与迁移必须连接虚拟机PostgreSQL。已执行的具体结果记录在工作区任务记录中，不把单元测试当成模型或全平台验收。

四维ACL为`global`、`department`、`role`、`user`，默认拒绝，任一实体命中即允许；department仅直属部门，不继承父部门。系统管理员没有正文读取特权。`knowledge:manage`允许维护台账/授权和替换正文，但读取原正文仍必须有`knowledge:read`且命中ACL。管理台账信息由独立管理权限授权，普通知识列表仅列出当前可读内容。修改角色/部门立即影响已有令牌；停用、密码变更撤销旧令牌。删除部门/角色/用户清除其知识授权，防止编号重用恢复旧权限。禁止形成部门环和移除最后有效管理员。

`deploy/logging.yaml`统一日志设置，请求贯穿`request_id`，控制台记录流程开始、结果与安全异常堆栈；不记录密码、Token、请求正文或数据库URL。

## 当前结构

- `backend/app/`：配置、实体、密码/JWT、组织与ACL服务、API、管理员CLI。
- `backend/migrations/`与`backend/alembic.ini`：显式数据库迁移；启动不自动建表。
- `frontend/`：真实API驱动的组织与知识权限控制台及锁文件。
- `tests/`：组织、身份、撤权与ACL行为回归。
- `deploy/logging.yaml`、`.env.example`：当前日志与运行变量说明，不含真实凭证。
- `pyproject.toml`、`.python-version`、`uv.lock`：Python环境和依赖。
- `src/knowledge_base_platform/__init__.py`：保留初始化包说明；运行入口在`backend.app`，不宣称已打包安装。
- `docs/implementation-plan.md`：完整后续实施设计及状态边界。

依赖、数据、模型、日志和凭证不纳入Git；本阶段不下载模型、不训练、不接入微调生成服务。原始需求是工作区只读资料`real/2.9.md`与`real/4.1.md`。
