# 上传到用户问答的 Ragas 评测

本工具评测实际平台链路：HTTP 上传 → Celery 解析/切片/真实 BGE 嵌入 → PostgreSQL/pgvector 发布 → 普通用户提问 → DeepSeek 流式生成 → 引用与历史核对 → Ragas 评分。它不另写一个简化 RAG，不用预检索结果、标准答案或夹具替代模型实际使用的证据。

## 环境与入口

平台 API 和 worker 使用项目运行环境；评测单独使用 Python 3.11 的 `.venv-ragas`，不升级平台依赖。创建环境后根据已冻结的哈希锁安装：

```powershell
uv venv --offline --no-python-downloads --python <已安装的Python3.11路径> .venv-ragas
uv pip sync --require-hashes --python .venv-ragas/Scripts/python.exe evaluation/requirements.lock
.venv-ragas/Scripts/python.exe -B -m evaluation --help
```

安装与数据准备命令会下载外部资源，应遵守工作区下载授权约定。`evaluation/requirements.in`声明直接依赖，`requirements.lock`固定全部传递版本和摘要；Ragas 版本为 `0.4.4`。不下载新的 tokenizer、嵌入模型或生成权重。

推荐建立独立评测数据库、独立 `DATA_ROOT`，并给 API 与 worker 配置相同的独立 Redis DB。生产数据不要作为评测写入目标。本轮使用 API `8017`、前端 `5177`、Redis DB `1` 和数据库 `knowledge_base_ragas_20261116`，原 `8016/5176` 服务保留。前端可用进程变量 `KNOWLEDGE_API_TARGET=http://127.0.0.1:8017` 指向隔离 API。

生成配置使用 `MODEL_MODE=external_api`、`MODEL_BASE_URL=https://api.deepseek.com`、`MODEL_NAME=deepseek-flash`。本轮显式设置 `MODEL_DISABLE_THINKING=true`，向支持该参数的供应商发送 `thinking.type=disabled`；默认 false 保留既有行为。不要对不支持该参数的模型启用它。API Key、JWT 密钥及用户密码通过进程环境注入，不写入代码、配置、数据集或报告。

## 数据选择和可复现准备

选用 [CMRC 2018](https://github.com/ymcui/cmrc2018) 中文开发集，原始段落和人工问题/答案适合测试中文检索及有依据问答。来源固定为 revision `c0eb1b6ba219847457e6af3180da722bbeb656af` 的 `squad-style-data/cmrc2018_dev.json`，许可 [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)，作者为 Yiming Cui 等。它是通用中文阅读理解资料，不等同于企业技术支持领域数据。

```powershell
.venv-ragas/Scripts/python.exe -B -m evaluation prepare `
  --download-dir data/ragas/cmrc-source `
  --output data/ragas/cmrc12 --count 12 --seed 42
```

已有原始数据时改用 `--source data/ragas/cmrc-source/cmrc2018_dev.json`；旁边须有原 `LICENCE`。网络需要代理时通过运行环境配置 `HTTPS_PROXY`，本地 API 应设置 `NO_PROXY=127.0.0.1,localhost`，工具不硬编码代理地址。

默认从内容排序、去重的不同段落固定抽取 12 题，每段一个原问题；`--count` 可扩大规模。所有选中段落同时上传，互为检索干扰项。上传文档只包含原段落、标题、署名、出处与许可，问题和参考答案只保存在独立 `cases.jsonl`，不会添加到知识正文或用户问题。数据准备拒绝覆盖已有输出目录。

`manifest.json`记录来源 revision、原文件/许可/文档/样本 SHA-256、抽样种子、变换说明及标注处理。官方开发集 3,219 题的答案中，193 条使用 `answer_start=-1` 表示非逐字人工答案；它们仍作为人工参考保留。另有 7 条非负字符偏移与原文不符，逐条记录在 `annotations.invalid_offsets` 后从候选参考中排除；没有剩余可用答案的问题单独记账，不悄悄修复原标注。加载准备集时重新验证摘要、路径和 ID，防止准备后替换数据。

## 实际上传和普通用户提问

在隔离平台创建两类账号：上传者拥有知识管理/读取动作；普通读者仅拥有 `knowledge:read`、`chat:ask`。采集器拒绝把知识管理员当作普通读者，也不允许两者为同一账号。上传 ACL 只授权这两个明确的用户，不默认公开资料。

在 PowerShell 进程内输入账号凭证，不把密码放进命令行参数：

```powershell
$env:RAG_EVAL_BASE_URL = 'http://127.0.0.1:8017'
$env:RAG_EVAL_UPLOADER_USERNAME = 'ragas-uploader'
$env:RAG_EVAL_READER_USERNAME = 'ragas-reader'
$env:RAG_EVAL_UPLOADER_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host '上传者密码' -AsSecureString)).Password
$env:RAG_EVAL_READER_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host '普通读者密码' -AsSecureString)).Password
.venv-ragas/Scripts/python.exe -B -m evaluation collect `
  --dataset data/ragas/cmrc12 --output outputs/ragas/cmrc12-run1 `
  --concurrency 2
```

也可提供 `RAG_EVAL_UPLOADER_TOKEN`、`RAG_EVAL_READER_TOKEN`，相应角色将不执行密码登录。每次运行使用新的输出目录，避免覆盖记录或把重复导入混进同一次报告。

上传完成不等于可提问：采集器等待真实任务 `succeeded`，核对全部文档版本和普通读者可读切片，部分失败即阻止后续评测。每题创建独立会话，以原始问题调用平台，只有完整成功 SSE、持久化正文一致、来源/模型一致及引用版本一致才记录成功。`retrieved_contexts`逐项来自该答案的引用接口，代表本轮实际提示中的正文；不另行调用检索来冒充生成上下文。

输出：

- `pipeline.json`：上传任务、知识/版本映射、权限身份、阶段耗时、成功/失败分母和数据来源。
- `samples.jsonl`：原问题、人工参考、真实回答、实际引用正文、模型标识、请求/会话/消息 ID、Token 和耗时。逐例写入，失败仍保留。

## 执行 Ragas 评分

```powershell
$env:RAGAS_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host '评审API Key' -AsSecureString)).Password
.venv-ragas/Scripts/python.exe -B -m evaluation score `
  --samples outputs/ragas/cmrc12-run1/samples.jsonl `
  --output outputs/ragas/cmrc12-run1/ragas `
  --judge-base-url https://api.deepseek.com/v1 `
  --judge-model deepseek-flash --disable-thinking --concurrency 2
```

API Key 也可由 `MODEL_API_KEY` 环境变量提供。工具关闭 Ragas 遥测，使用官方现代 `collections` 指标与结构化输出：

| 指标 | 评估内容 |
|---|---|
| `faithfulness` | 回答中的事实是否得到实际检索证据支持 |
| `context_precision` | 实际证据相对人工参考的相关性及排序精度 |
| `context_recall` | 实际证据是否覆盖人工参考所需信息 |
| `answer_accuracy` | 结合原问题判断回答与人工短答案是否一致，作为CMRC问答准确性的主指标 |
| `factual_correctness` | 不接收原问题，只对完整回答与参考文本作双向事实蕴含比较；作为补充诊断 |

这组指标只使用显式评审 LLM，不触发额外 embedding 模型或 API 下载。`scores.jsonl`保留逐例分数/错误及指标状态；`summary.json`包含实际可用分数均值、每指标计数、总例数、失败数、依赖版本与评审配置。失败、缺失指标和 NaN 不会被悄悄丢弃。`complete`只表示技术执行完整，不表示质量达到某个未设定的门槛；CLI 对样本失败返回非零退出码。

CMRC参考通常是「十八张」「126间」等短片段。完整回答中的主语或额外但有依据的事实，不能由短参考单独蕴含，因此`factual_correctness`不宜直接当作问答错误率。默认运行上述五指标，原始低分仍保留；问答准确性另看接收原问题的官方`AnswerAccuracy`，不改写参考答案来提升分数。

若原四指标报告已经完成，可在新的报告目录使用`--metrics answer_accuracy`只补算问答准确性，避免重复请求和覆盖原报告。新版汇总的`requested_metrics`明确本次评分范围；历史原始报告保持不变。合并时必须核对样本ID集合、样本内容摘要及评审配置一致。

生成和评审沿用同一供应商模型，存在自评偏差。12 题用于可复现链路测试，不能替代全量基准、领域验证集、独立人工审核或正式检索校准。低分应保留，不通过修改参考答案、减少失败样本或替换实际上下文改善报告。

## 本轮真实结果

使用固定seed42的12题和12份原段落，上传任务`14f4d9d3-7186-4d6f-8da9-a5d0072caea3`全部成功，普通用户12次提问全部完成，每题实际提示证据均包含对应原文档。上传到索引47.265秒，平台问答平均67.680秒、中位59.535秒、P95为90.033秒；生成总28,514 Token，其中推理Token为0。未授权用户正文、切片与下载均403，提问拒答且不调用模型。浏览器已验证普通读者回答、引用原文、版本及源许可可追溯。

所有指标均对同一12题实际回答/上下文评分：`answer_accuracy=0.9375`、`faithfulness=1.0`、`context_precision≈1.0`、`context_recall=1.0`。补充诊断`factual_correctness=0.0416667`原样保留，不据此改写标签或删除样本。较低问答准确性样本包括「肇事客机为什么改飞济州？」（0.75）和「额肌的主要功能是什么？」（0.5），可从逐例文件检查完整回答与人工参考。

本轮本地输出位于`outputs/ragas/cmrc12-run1/`：`pipeline.json`和`samples.jsonl`为平台取证，`performance.json`为耗时/Token统计，`ragas/`保留原四指标报告，`ragas-answer-accuracy/`保留新增问答准确性报告，`evaluation-summary.json`与`evaluation-scores.jsonl`核对两次评分的样本ID/评审配置后合并，记录原报告摘要；`metric-diagnostics.json`保留短参考适用性诊断。外层`outputs/ragas/acl-checks.json`与`runtime.json`保存实际拒权及隔离参数，无密钥。

评分技术失败0例，质量未设置通过门槛；12题、自评模型、通用百科领域和平均67.7秒响应仍限制结论，不代表生产质量或性能通过。


## 必要回归

```powershell
.venv-ragas/Scripts/python.exe -B -m pytest evaluation/tests -q
```

这些确定性测试覆盖数据防泄漏、官方标注兼容、摘要篡改、流中断/矛盾终态、实际引用采集、部分导入失败及评分失败分母。测试内的 HTTP 夹具只用于协议验证，不能作为真实模型与入库联调证据。实际服务结果位于对应运行的 `pipeline.json`、`samples.jsonl` 和 `ragas/summary.json`。
