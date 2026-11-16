"""提供数据准备、真实全链路采集和Ragas评分的显式命令。"""
import argparse
import asyncio
from contextlib import ExitStack
import json
import logging.config
import os
from pathlib import Path

from .client import PlatformClient
from .dataset import download_cmrc, prepare_dataset
from .runner import run_pipeline
from .scoring import METRICS, score_samples


def configure_logging():
    """由独立配置统一控制评测日志，认证信息不进入日志。"""
    logging.config.fileConfig(Path(__file__).with_name('logging.ini'), disable_existing_loggers=False)


def parser():
    """声明可复现的准备、采集及评分参数，密钥只从环境读取。"""
    root = argparse.ArgumentParser(description='真实知识库上传问答全链路与Ragas评测；不下载模型、不生成参考答案')
    commands = root.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='显式下载固定CMRC开发集并确定性抽样')
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument('--download-dir', type=Path, help='允许下载时指定缓存目录')
    source.add_argument('--source', type=Path, help='已有本地CMRC开发集，旁边须有LICENCE')
    prepare.add_argument('--output', type=Path, required=True)
    prepare.add_argument('--count', type=int, default=12)
    prepare.add_argument('--seed', type=int, default=42)
    collect = commands.add_parser('collect', help='真实上传、等待索引、普通用户问答并保存实际证据')
    collect.add_argument('--dataset', type=Path, required=True)
    collect.add_argument('--output', type=Path, required=True)
    collect.add_argument('--base-url', default=os.environ.get('RAG_EVAL_BASE_URL', 'http://127.0.0.1:8017'))
    collect.add_argument('--concurrency', type=int, default=2)
    collect.add_argument('--import-timeout', type=int, default=900)
    score = commands.add_parser('score', help='用Ragas对真实采集结果评分；失败样本保留分母')
    score.add_argument('--samples', type=Path, required=True)
    score.add_argument('--output', type=Path, required=True)
    score.add_argument('--judge-base-url', default=os.environ.get('RAGAS_BASE_URL', 'https://api.deepseek.com/v1'))
    score.add_argument('--judge-model', default=os.environ.get('RAGAS_MODEL_NAME', 'deepseek-flash'))
    score.add_argument('--concurrency', type=int, default=2)
    score.add_argument('--disable-thinking', action='store_true', help='显式传递DeepSeek非思考参数；仅供支持该参数的服务')
    score.add_argument('--metrics', nargs='+', choices=METRICS, default=METRICS, help='默认全部指标；补算时可显式只请求新增指标')
    return root


def authenticated_client(stack, base_url, role):
    """读取指定角色的进程环境，令牌和密码不进入命令行参数。"""
    prefix = 'RAG_EVAL_' + role.upper()
    token = os.environ.get(prefix + '_TOKEN', '')
    client = stack.enter_context(PlatformClient(base_url, token))
    if not token:
        username, password = os.environ.get(prefix + '_USERNAME'), os.environ.get(prefix + '_PASSWORD')
        if not username or not password:
            raise ValueError(prefix + '_credentials_missing')
        client.login(username, password)
    return client


def main(argv=None):
    """执行显式步骤并通过退出码区分技术成功、样本失败及配置错误。"""
    args = parser().parse_args(argv)
    configure_logging()
    try:
        if args.command == 'prepare':
            source = download_cmrc(args.download_dir) if args.download_dir else args.source
            manifest = prepare_dataset(source, args.output, count=args.count, seed=args.seed)
            result = dict(status='complete', dataset='CMRC2018', count=manifest['count'], seed=manifest['seed'], source=manifest['source'], output=str(args.output))
        elif args.command == 'collect':
            with ExitStack() as stack:
                uploader = authenticated_client(stack, args.base_url, 'uploader')
                reader = authenticated_client(stack, args.base_url, 'reader')
                result = run_pipeline(args.dataset, args.output, uploader=uploader, reader=reader,
                    concurrency=args.concurrency, import_timeout=args.import_timeout)
        else:
            samples = [json.loads(line) for line in args.samples.read_text(encoding='utf-8').splitlines() if line.strip()]
            key = os.environ.get('RAGAS_API_KEY') or os.environ.get('MODEL_API_KEY', '')
            result = asyncio.run(score_samples(samples, args.output, base_url=args.judge_base_url,
                model=args.judge_model, api_key=key, concurrency=args.concurrency,
                disable_thinking=args.disable_thinking, metric_names=args.metrics))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result['status'] == 'complete' else 1
    except Exception as error:
        logging.getLogger(__name__).error('流程=RAG评测 状态=失败 error_type=%s', type(error).__name__)
        print(json.dumps(dict(status='error', error_type=type(error).__name__), ensure_ascii=False))
        return 2
