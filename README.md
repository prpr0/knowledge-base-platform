# 知识库管理平台

中文技术支持与操作知识库平台。本地FastAPI、Vue控制台与Celery worker，虚拟机PostgreSQL16/pgvector及独立Redis持久化服务。

已实现组织身份/RBAC、四维ACL、多格式导入、真实token切片与BGE混合检索、版本发布与鉴权下载、私人会话/SSE、安全历史及引用、FAQ聚类审核缓存、缺口补充任务、运营看板和独立审计。生成支持正式微调与外部API联调两种显式模式；当前可先用DeepSeek等OpenAI兼容服务验证平台链路。正式微调模式仍要求真实发布产物及独立检索校准；FAQ与无证据拒答不调用模型。

本项目对应 `real/2.9.md` 的知识维护、权限问答、FAQ、缺口与运营需求。最终生成模型必须来自独立项目 `knowledge-model-finetuning`，发布别名为 `kb-grounded-qwen3-4b-v1`。 两项目通过 OpenAI 兼容服务契约协作，不复制代码或导入兄弟目录。微调模型发布前，基座对照实验不计为最终联调成功。

详细技术栈、架构、需求覆盖、实施顺序、统一契约与未来验收见 [实现方案](docs/implementation-plan.md)。

上传到普通用户提问的自动化全链路测试及Ragas评分入口见[评测操作说明](docs/ragas-evaluation.md)。使用固定版本CMRC 2018人工标注开发集，保留真实上传任务、索引版本、模型回答、实际引用上下文及逐例质量指标；数据、评测依赖和报告分别保存在忽略目录。

## 本地依赖与运行前提

Python 3.11.x、uv、Node.js24。本次Python依赖安装在项目`.venv`，前端依赖在`frontend/node_modules`；`uv.lock`和`frontend/package-lock.json`保留用于复现。安装命令会下载缺失依赖，应先取得下载授权；本阶段已经获准安装。

```powershell
uv sync --locked
Push-Location frontend
npm ci
Pop-Location
```

数据库运行于虚拟机，本地不启动Docker或PostgreSQL。SSH别名`financial-risk-audit-vm`指向192.168.10.150。本项目活动容器为`knowledge-base-platform-postgres-vector`，数据卷`knowledge-base-platform-pgvector-data`，回环端口15433；独立Redis为`knowledge-base-platform-redis`，回环16379。旧`knowledge-base-platform-postgres`容器及数据卷保留作为迁移来源。各实例仅绑定虚拟机回环地址，通过SSH访问。

保持一个独立终端运行隧道：

```powershell
ssh -o ExitOnForwardFailure=yes -N -L 127.0.0.1:15432:127.0.0.1:15433 -L 127.0.0.1:16379:127.0.0.1:16379 financial-risk-audit-vm
```

新终端在项目根配置进程环境。以下将初始化密码直接存入进程变量，不回显或写入配置文件；适用于当前已有的本项目容器。如果已修改数据库角色密码，应使用修改后的密码。

```powershell
$databasePassword = (ssh financial-risk-audit-vm 'docker exec knowledge-base-platform-postgres-vector printenv POSTGRES_PASSWORD').Trim()
$escapedDatabasePassword = [uri]::EscapeDataString($databasePassword)
$env:DATABASE_URL = "postgresql+psycopg://knowledge_platform:${escapedDatabasePassword}@127.0.0.1:15432/knowledge_base_platform"
$env:JWT_SECRET = (& .venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(48))").Trim()
Remove-Variable databasePassword,escapedDatabasePassword
$env:TOKEN_MINUTES = '60'
$env:REDIS_URL = 'redis://127.0.0.1:16379/0'
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

## 导入worker与本地运行模型

本阶段已获授权将模型资源下载在本机`models/`（Git忽略）。运行时`local_files_only=True`且禁用远程代码，不隐式联网下载；缺失资源会使导入/检索明确失败。实际来源与不可变revision如下，文件hash见`models/resource-manifest.json`及`models/bge-m3/weight-manifest.json`：

| 本地目录 | 官方资源 | revision |
|---|---|---|
| `models/qwen-tokenizer` | Qwen/Qwen3-4B-Instruct-2507，仅tokenizer/config/许可材料，无生成权重 | cdbee75f17c01a7cc42f958dc650907174af0554 |
| `models/bge-m3` | BAAI/bge-m3，MIT，真实1024维嵌入，权重为pytorch_model.bin | 5617a9f61b028005a4858fdac845db406aefb181 |
| `models/bge-reranker-v2-m3` | BAAI/bge-reranker-v2-m3，Apache 2.0，真实重排 | 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e |

当前CPU加载真实嵌入/重排，不承诺本机延时或吞吐。路径可通过`TOKENIZER_PATH`、`EMBEDDING_MODEL_PATH`、`RERANKER_MODEL_PATH`覆盖。原文件保存于`DATA_ROOT`（默认项目`data/`），服务端生成存储名，客户端相对路径只作为元数据；单文件默认上限50MiB，可用`MAX_UPLOAD_BYTES`调整。无公共静态文件目录。

保持与API相同的数据库/Redis/路径进程环境，在另一终端从项目根启动真实worker：

```powershell
.venv/Scripts/python.exe -m celery -A backend.app.worker:celery_app worker --pool=solo --concurrency=1 --queues=knowledge_imports --loglevel=INFO
```

Windows使用solo池。任务进度、错误及发布状态以PostgreSQL为准，Redis只承载消息。队列不可用会持久化失败并返回503；重试入口只重排失败项，不重复成功项。

已有文本单位迁入新版本体系时，显式提交真实重建任务，不伪造向量或预置成功状态：

```powershell
.venv/Scripts/python.exe -m backend.app.ingestion_cli --actor admin
```

控制台“知识导入中心”支持单文件、批量、文件夹选择/拖放，保留相对层级；PDF/DOCX/Markdown/TXT实际解析，旧DOC仅在已安装LibreOffice时隔离转换。加密、损坏、无可提取文字扫描件和转换器缺失均明确失败，不生成空正文成功项。不会自动下载LibreOffice或执行OCR。

切片以真实Qwen tokenizer按章节/段落处理，目标512 token、重叠最多64 token；为保持Unicode与表格行完整，边界重叠可适当缩减。每片重新计数并保留章节/页码/表头/定位。索引未全部构建完成前不切换活动版本；失败继续保留旧活动内容。新文本创建或正文/标题编辑返回`rebuild_task_id`，提交成功不等于索引已发布。任务号为UUID字符串，页面据此跟踪真实进度。

知识管理支持版本、获准切片、原文件下载和启停。切片编辑生成独立文本快照，保留旧切片出处与重叠上下文，重新构建整个版本，不覆盖旧原文件。停用/删除即时使正文、切片、下载及检索失效；所有正文入口重新鉴权。

“知识检索”使用向量/关键词各50条、RRF60、当前ACL过滤后BGE重排，最多8片并受token预算限制。受限候选正文/标题/编号/数量/分数不进入重排或客户端，只给固定提示。页面分数为模型排序输出，不代表已校准置信度；低证据阈值与生成问答质量仍需独立验证集和发布微调模型验收。


## 问答、FAQ与运营

新增动作分别为`chat:ask`、`faq:manage`、`gap:manage`、`metrics:read`、`audit:read`、`model:manage`。迁移不会给旧账号隐式提权；已有管理员通过“角色权限”显式勾选。菜单与服务端检查使用同一权限目录，管理员仍不能绕过正文ACL。

“AI智能问答”支持本人会话创建/改名/删除、安全Markdown与代码高亮复制、fetch SSE和中止。每轮检查当前证据，历史读取与引用预览重新鉴权；一个来源失效时整条答案不可用。事件为`meta/delta/citations/usage/done/error`，失败无成功done，未知Token不估算。受限来源只给固定提示，候选ID仅`audit:read`可查看。

“FAQ挖掘审核”提交真实Celery任务。7天内同部门和同来源版本的问题用真实BGE聚类，至少5次产生候选；相似度`FAQ_CLUSTER_SIMILARITY=.90`是设计参数，不是质量概率。草案明确标为资料摘录，人工润色并保留引用后才能发布。发布记录完整来源，Redis键隔离身份/权限版本/全部来源版本，命中仍逐项复核；不同来源必须全部获准。知识更新、ACL变化、停用和删除使依赖FAQ失效。无部门候选仅创建者可见，无部门缺口按本人聚合。

无召回或已验证低证据请求进入缺口池；纯权限阻断及模型服务失败不误记缺口。补充任务导入入口携带`supplement_task_id`，关联导入全部成功发布后，补充任务和缺口同事务关闭。指标来自真实有效问答审计，包含失败、拒答和权限拦截，排除认证/格式错误；Token未知次数单列。`POST /metrics/aggregate`提交每日快照任务，`GET /metrics`使用相同计数规则。图表无演示数据，问题及来源排行另受本人/直属部门和当前ACL限制。

## 模型配置与联调

“模型设置”只能由`model:manage`维护。`MODEL_MODE=finetuned`为默认正式模式，模型名固定`kb-grounded-qwen3-4b-v1`。先联调外部服务时显式选择`external_api`，并配置`MODEL_BASE_URL`、`MODEL_NAME`和`MODEL_API_KEY`，例如`https://api.deepseek.com`与`deepseek-flash`。数据库保存配置后不再混用环境值。API密钥以Fernet加密，独立`CONFIG_ENCRYPTION_KEY`由运行环境提供，缺失时拒绝写入密钥。GET不回显凭证或密文；空密钥提交表示保留原值。URL只允许不含内嵌凭证的HTTP/HTTPS。

发布清单路径为`MODEL_RELEASE_MANIFEST_PATH`，校准路径为`MODEL_CALIBRATION_MANIFEST_PATH`。清单包含`model_name/model_version/base_model/base_revision/artifacts:[{path,sha256}]/evaluation_report`；`base_model`固定`Qwen/Qwen3-4B-Instruct-2507`，所有文件限于清单目录，评估报告本身必须列入摘要。报告`metrics.base`和`metrics.finetuned`包含`citation_validity/evidence_support/keypoint_coverage/terminology_format/refusal_recall/answerable_false_refusal`比例；执行方案固定门槛及基座比较，未通过不发布。配置与使用前重新验证，不以布尔标记代替文件证据。

校准报告包含`validated=true`、`configuration_id`、有限`threshold`，以及`unanswerable_count/wrong_answer_count/answerable_count/supported_retrieval_count`真实人工审核计数；错误作答率须≤5%。配置标识可从模型设置页读取，绑定当前检索模型路径与50/50、RRF60、8片、4096/1024预算。检索模型或参数变化后需重新独立校准。正式模式缺少真实发布与校准时明确失败。外部联调模式无需微调发布清单，校准报告可选；如未提供则不使用低证据分数阈值，页面明确标识未校准，继续执行无证据拒答、ACL过滤、引用验证和审计。外部API联调不代表正式微调质量验收通过。

上游使用OpenAI兼容`POST /v1/chat/completions`、`stream=true`与真实usage，请求中的`model`使用选定配置。输入使用固定证据system约束、问题及本轮编号证据；保留本地Qwen聊天模板3072输入token上限及1024输出限制。外部模型的分词与本地Qwen不同，该上限仅控制输入规模，实际Token用量以供应商返回值为准。不存在的引用编号使本轮失败，不生成虚构卡片。外部生成的消息、流meta和审计均记录`source_mode=external_api`及实际模型名，历史消息保留联调标识。

前轮使用`deepseek-flash`完成真实BGE检索→供应商流式生成→引用预览与历史标识验证，凭证通过进程环境注入，不写项目文件。该轮真实问答返回1,366 Token（471输入、895输出，输出含607推理Token），平台记录耗时约295秒，不代表性能达标。本轮CMRC评测显式使用非思考模式，参数与结果另行记录，不能直接作未经控制的性能对照。进程重启后须重新安全注入运行变量，实际命令和范围见工作区`external-api-integration/PROGRESS.md`。

## 验证与权限语义

```powershell
.venv/Scripts/python.exe -B -m pytest tests/test_external_api.py tests/test_model_gateway.py tests/test_qa_operations.py -q
Push-Location frontend
npm test
npm run build
Pop-Location
```

持久化/ACL/任务契约测试使用独立SQLite与明确的发布状态夹具，不将夹具向量当作模型推理证明。另已执行真实Qwen token切片、真实BGE嵌入/重排、虚拟机PG迁移与Celery导入、混合检索及浏览器操作。真实批量验证为4种有效格式成功、损坏PDF明确失败；重试只改变失败项。目录相对路径与同名隔离通过真实API验证；浏览器自动化未能将目录路径传入原生webkitdirectory选择器，因此不声称原生文件夹选择已通过自动化。

四维ACL为`global`、`department`、`role`、`user`，默认拒绝，任一实体命中即允许；department仅直属部门，不继承父部门。系统管理员没有正文读取特权。`knowledge:manage`允许维护台账/授权和替换正文，但读取原正文仍必须有`knowledge:read`且命中ACL。管理台账信息由独立管理权限授权，普通知识列表仅列出当前可读内容。修改角色/部门立即影响已有令牌；停用、密码变更撤销旧令牌。删除部门/角色/用户清除其知识授权，防止编号重用恢复旧权限。禁止形成部门环和移除最后有效管理员。

`deploy/logging.yaml`统一日志设置，请求贯穿`request_id`，控制台记录流程开始、结果与安全异常堆栈；不记录密码、Token、请求正文或数据库URL。

## 当前结构

- `backend/app/`：组织/认证、解析索引、版本发布、Celery导入/聚类/指标、检索、模型门禁、会话SSE、完整证据授权、FAQ/缺口及独立审计。
- `backend/migrations/`与`backend/alembic.ini`：显式迁移及真实GIN/HNSW索引；启动不自动建表，当前包含0004外部模型联调模式迁移。
- `frontend/`：组织/知识/导入/检索、会话、FAQ审核、缺口补充、运营、审计与模型设置控制台。
- `tests/`：组织/ACL、解析发布、上传重试、历史撤权/FAQ来源AND、正式模型门禁、外部API配置/传输/引用审计、缺口/指标与前端UUID回归。
- `deploy/logging.yaml`、`.env.example`：当前日志与运行变量说明，不含真实凭证。
- `pyproject.toml`、`.python-version`、`uv.lock`：Python环境和依赖。
- `src/knowledge_base_platform/__init__.py`：保留初始化包说明；运行入口在`backend.app`，不宣称已打包安装。
- `docs/implementation-plan.md`：完整后续实施设计及状态边界。

依赖、数据、模型、日志和凭证不纳入Git。已下载获准tokenizer与检索模型，不下载生成模型权重或训练数据，不执行训练；最终生成服务仍必须来自独立微调项目。原始需求是工作区只读资料`real/2.9.md`与`real/4.1.md`。
