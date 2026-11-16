"""准备固定版本 CMRC 开发集，保留许可、标注及可验证的数据摘要。"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from urllib.request import urlopen

REVISION = 'c0eb1b6ba219847457e6af3180da722bbeb656af'
ROOT_URL = f'https://raw.githubusercontent.com/ymcui/cmrc2018/{REVISION}'
SOURCE_URL = f'{ROOT_URL}/squad-style-data/cmrc2018_dev.json'
LICENSE_URL = f'{ROOT_URL}/LICENCE'
ATTRIBUTION = 'CMRC 2018 — Yiming Cui et al.; https://github.com/ymcui/cmrc2018'
LICENSE = 'CC-BY-SA-4.0'


def _sha(data: bytes) -> str:
    """计算原始字节摘要。"""
    return hashlib.sha256(data).hexdigest()


def _json(path: Path):
    """读取 UTF-8 JSON。"""
    return json.loads(path.read_text(encoding='utf-8'))


def _save_json(path: Path, value) -> None:
    """以确定格式保存 JSON。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def download_cmrc(directory: Path) -> Path:
    """显式下载固定开发集及许可；仅复用摘要验证成功的缓存，不覆盖文件。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / 'cmrc2018_dev.json'
    license_path = directory / 'LICENCE'
    receipt = directory / 'download.json'
    if any(path.exists() for path in (source, license_path, receipt)):
        if not all(path.is_file() for path in (source, license_path, receipt)):
            raise ValueError('缓存不完整；请使用新的下载目录')
        metadata = _json(receipt)
        if (metadata.get('revision') != REVISION
                or metadata.get('url') != SOURCE_URL
                or metadata.get('license_url') != LICENSE_URL
                or metadata.get('sha256') != _sha(source.read_bytes())
                or metadata.get('license_sha256') != _sha(license_path.read_bytes())):
            raise ValueError('缓存来源或摘要不匹配')
        return source
    # 先完成两个远程读取，避免网络中断留下半套缓存。
    with urlopen(SOURCE_URL, timeout=60) as response:
        source_bytes = response.read()
    with urlopen(LICENSE_URL, timeout=60) as response:
        license_bytes = response.read()
    json.loads(source_bytes)
    if not license_bytes.strip():
        raise ValueError('源许可为空')
    with source.open('xb') as stream:
        stream.write(source_bytes)
    with license_path.open('xb') as stream:
        stream.write(license_bytes)
    metadata = dict(revision=REVISION, url=SOURCE_URL, sha256=_sha(source_bytes),
                    license=LICENSE, license_url=LICENSE_URL,
                    license_sha256=_sha(license_bytes), attribution=ATTRIBUTION)
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2)
    return source


def _paragraphs(payload, annotations) -> list[dict]:
    """保留官方-1改述答案，记录错误字符偏移并按段落内容合并。"""
    grouped = {}
    ids = set()
    for article in payload['data']:
        title = article.get('title', '')
        if not isinstance(title, str):
            raise ValueError('标题必须是字符串')
        for paragraph in article['paragraphs']:
            context = paragraph['context']
            if not isinstance(context, str) or not context.strip():
                raise ValueError('段落不能为空')
            key = _sha(context.encode('utf-8'))
            entry = grouped.setdefault(key, dict(context=context, titles=set(), qas=[]))
            entry['titles'].add(title)
            for qa in paragraph['qas']:
                identifier, question = qa['id'], qa['question']
                if not isinstance(identifier, str) or not identifier or identifier in ids:
                    raise ValueError('问题 ID 为空或重复')
                ids.add(identifier)
                if not isinstance(question, str) or not question.strip():
                    raise ValueError('问题不能为空')
                answers = qa['answers']
                if not isinstance(answers, list) or not answers:
                    raise ValueError('问题缺少人工答案')
                texts = []
                for answer_index, answer in enumerate(answers):
                    text, start = answer['text'], answer['answer_start']
                    if not isinstance(text, str) or not text or type(start) is not int or start < -1:
                        raise ValueError('答案标注格式无效')
                    if start == -1:
                        annotations['nonextractive_answers'] += 1
                    elif context[start:start + len(text)] != text:
                        annotations['invalid_offsets'].append(dict(question_id=identifier, answer_index=answer_index, answer_start=start))
                        continue
                    if text not in texts:
                        texts.append(text)
                if texts:
                    entry['qas'].append(dict(id=identifier, question=question, answers=texts))
                else:
                    annotations['unusable_questions'].append(identifier)
    entries = []
    for key, entry in sorted(grouped.items()):
        if entry['qas']:
            entry['document_id'] = f'cmrc-{key}'
            entry['title'] = min(entry.pop('titles'))
            entry['qas'].sort(key=lambda item: item['id'])
            entries.append(entry)
    return entries


def prepare_dataset(source_path: Path, output_dir: Path, count=12, seed=42) -> dict:
    """从不同原段落稳定抽样，每段保留一个原问题，生成隔离的文档与评审标签。"""
    source_path, output_dir = Path(source_path), Path(output_dir)
    if output_dir.exists():
        raise FileExistsError('准备目标已存在，请使用新的输出目录')
    if type(count) is not int or count <= 0 or type(seed) is not int:
        raise ValueError('count 必须为正整数，seed 必须为整数')
    annotations = dict(nonextractive_answers=0, invalid_offsets=[], unusable_questions=[])
    paragraphs = _paragraphs(_json(source_path), annotations)
    if count > len(paragraphs):
        raise ValueError('可用的不同段落数量不足')
    license_path = source_path.parent / 'LICENCE'
    if not license_path.is_file():
        raise ValueError('源数据缺少 LICENCE')
    source = dict(revision=REVISION, url=SOURCE_URL, sha256=_sha(source_path.read_bytes()),
                  license=LICENSE, license_url=LICENSE_URL, attribution=ATTRIBUTION,
                  license_sha256=_sha(license_path.read_bytes()), verified_download=False)
    receipt = source_path.parent / 'download.json'
    if receipt.is_file():
        metadata = _json(receipt)
        expected = {key: source[key] for key in ('revision', 'url', 'sha256', 'license_url', 'license_sha256')}
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise ValueError('下载凭据与源数据不一致')
        source['verified_download'] = True
    rng = random.Random(seed)
    selected = rng.sample(paragraphs, count)
    documents, cases = [], []
    for paragraph in selected:
        qa = rng.choice(paragraph['qas'])
        document_id = paragraph['document_id']
        relative = f'documents/{document_id}.md'
        body = (f"# {paragraph['title']}\n\n{paragraph['context']}\n\n"
                f'出处：{SOURCE_URL}\n署名：{ATTRIBUTION}\n许可：{LICENSE} ({LICENSE_URL})\n')
        documents.append(dict(document_id=document_id, path=relative,
                              sha256=_sha(body.encode('utf-8')), title=paragraph['title']))
        cases.append(dict(id=qa['id'], document_id=document_id, question=qa['question'],
                          reference=qa['answers'][0], reference_answers=qa['answers'],
                          reference_context=paragraph['context']))
        (output_dir / 'documents').mkdir(parents=True, exist_ok=True)
        (output_dir / relative).write_bytes(body.encode('utf-8'))
    cases_bytes = ''.join(json.dumps(case, ensure_ascii=False, sort_keys=True) + '\n' for case in cases).encode('utf-8')
    (output_dir / 'cases.jsonl').write_bytes(cases_bytes)
    (output_dir / 'LICENCE').write_bytes(license_path.read_bytes())
    manifest = dict(documents=documents, cases_file='cases.jsonl', cases_sha256=_sha(cases_bytes),
                    source=source, count=count, seed=seed,
                    transformations='固定种子从内容排序的不同段落抽样，每段选择一个原标注问题；问题及段落不改写；文档仅添加标题、来源、署名和许可；参考标注单独保存。',
                    license_file='LICENCE')
    manifest['annotations'] = annotations
    manifest['transformations'] += '保留官方-1偏移的非逐字人工答案；字符偏移错误的答案单独记账并排除，无可用人工答案的问题不参加抽样。'
    _save_json(output_dir / 'manifest.json', manifest)
    return manifest


def _bounded(root: Path, relative: str) -> Path:
    """限制清单路径只能指向数据目录内的普通文件。"""
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or ':' in relative or Path(relative).is_absolute() or '..' in Path(relative).parts):
        raise ValueError('清单文件路径非法')
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('清单文件越界或不存在')
    return path


def load_cases(dataset_dir: Path) -> list[dict]:
    """验证清单、文档、许可和样本摘要，拒绝重复 ID 及越界路径。"""
    root = Path(dataset_dir).resolve()
    manifest = _json(root / 'manifest.json')
    source = manifest['source']
    if (source.get('revision') != REVISION or source.get('url') != SOURCE_URL
            or source.get('license') != LICENSE or source.get('license_url') != LICENSE_URL
            or source.get('attribution') != ATTRIBUTION or not manifest.get('transformations')):
        raise ValueError('源许可或来源说明不完整')
    license_path = _bounded(root, manifest['license_file'])
    if _sha(license_path.read_bytes()) != source['license_sha256']:
        raise ValueError('许可摘要不匹配')
    documents = {}
    paths = set()
    for entry in manifest['documents']:
        identifier = entry['document_id']
        path = _bounded(root, entry['path'])
        if not isinstance(identifier, str) or not identifier or identifier in documents or path in paths:
            raise ValueError('文档 ID 或路径重复')
        data = path.read_bytes()
        if _sha(data) != entry['sha256']:
            raise ValueError('文档摘要不匹配')
        documents[identifier] = data.decode('utf-8')
        paths.add(path)
    raw = _bounded(root, manifest['cases_file']).read_bytes()
    if _sha(raw) != manifest['cases_sha256']:
        raise ValueError('样本摘要不匹配')
    cases = [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    seen, used = set(), set()
    for case in cases:
        if set(case) != {'id', 'document_id', 'question', 'reference', 'reference_answers', 'reference_context'}:
            raise ValueError('样本字段不符合契约')
        if any(not isinstance(case[key], str) or not case[key].strip()
               for key in ('id', 'document_id', 'question', 'reference', 'reference_context')):
            raise ValueError('样本字符串字段无效')
        identifier, document_id = case['id'], case['document_id']
        if identifier in seen or document_id in used or document_id not in documents:
            raise ValueError('样本 ID 或段落重复，或缺少文档')
        answers = case['reference_answers']
        if (not isinstance(answers, list) or not answers or case['reference'] != answers[0]
                or any(not isinstance(text, str) or not text for text in answers)
                or case['reference_context'] not in documents[document_id]):
            raise ValueError('参考答案或上下文无效')
        seen.add(identifier)
        used.add(document_id)
    if len(cases) != manifest['count'] or len(documents) != len(cases) or used != set(documents):
        raise ValueError('样本与文档数量不匹配')
    return cases
