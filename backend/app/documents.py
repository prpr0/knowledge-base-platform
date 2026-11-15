"""真实解析导入文档，保留来源并按生成模型 token 预算切片。"""
import codecs
import re
import shutil
import subprocess
import tempfile
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path

class DocumentError(ValueError):
    """表示文件不支持、损坏、无正文或路径不合法。"""

@dataclass(frozen=True)
class Block:
    """保留一段正文的章节、页码和原始定位。"""
    text: str
    section: str = ''
    page: int | None = None
    location: str = ''
    kind: str = 'text'
    header: str = ''

@dataclass(frozen=True)
class ParsedDocument:
    """记录解析后标题、正文块与源格式。"""
    title: str
    blocks: list[Block]
    format: str

@dataclass(frozen=True)
class ChunkDraft:
    """描述待写入新版本的token受限切片。"""
    text: str
    section: str
    page: int | None
    location: str
    token_count: int

def normalize_relative_path(name: str) -> str:
    """规范化客户端相对路径并拒绝 Windows 和 POSIX 的危险路径。"""
    if not isinstance(name, str) or not name:
        raise DocumentError('文件路径不能为空')
    normalized = name.replace('\\', '/')
    if normalized.startswith('/'):
        raise DocumentError('文件路径必须为相对路径，不能使用绝对路径或 UNC 路径')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    parts = normalized.split('/')
    for part in parts:
        if not part or part in {'.', '..'}:
            raise DocumentError('文件路径包含空目录段或目录穿越')
        if part != part.strip() or part.endswith('.'):
            raise DocumentError('文件名不能以空白或句点结尾')
        if any(ord(char) < 32 or ord(char) == 127 or char in '<>:"|?*' for char in part):
            raise DocumentError('文件路径包含盘符、控制字符或不安全文件名')
        stem = part.split('.', 1)[0].upper().rstrip(' ')
        if stem in reserved or re.fullmatch(r'(?:COM|LPT)[1-9¹²³]', stem):
            raise DocumentError('文件路径包含系统保留文件名')
    return normalized


def _decode_text(data: bytes) -> str:
    """严格解码带 BOM、UTF-8 或 GB18030 文本，不替换损坏字节。"""
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encodings = ('utf-32',)
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encodings = ('utf-16',)
    elif data.startswith(codecs.BOM_UTF8):
        encodings = ('utf-8-sig',)
    else:
        encodings = ('utf-8', 'gb18030')
    for encoding in encodings:
        try:
            text = data.decode(encoding, errors='strict')
        except UnicodeDecodeError:
            continue
        if any(ord(char) < 32 and char not in '\t\n\r\f' for char in text):
            raise DocumentError('文本包含二进制控制字符，无法作为正文解析')
        return text.replace('\r\n', '\n').replace('\r', '\n')
    raise DocumentError('文本解码失败：需要有效的 UTF-8、带 BOM Unicode 或 GB18030 编码')


def _parse_text(path: Path, relative_path: str) -> ParsedDocument:
    """按空行划分纯文本段落并保留源行号。"""
    text = _decode_text(path.read_bytes())
    blocks = []
    for match in re.finditer(r'[^\n]+(?:\n(?![ \t]*\n)[^\n]+)*', text):
        body = match.group().strip('\n')
        if body.strip():
            line = text.count('\n', 0, match.start()) + 1
            blocks.append(Block(body, location=f'{relative_path}:line:{line}'))
    return ParsedDocument(Path(relative_path).stem, blocks, 'txt')


def _section_name(headings: dict[int, str], level: int, title: str) -> str:
    """更新当前标题层级并返回完整章节路径。"""
    for existing in tuple(headings):
        if existing >= level:
            del headings[existing]
    headings[level] = title
    return ' / '.join(headings[key] for key in sorted(headings))


def _parse_markdown(path: Path, relative_path: str) -> ParsedDocument:
    """使用 Markdown 语法树保留标题、代码、表格与原始行号。"""
    from markdown_it import MarkdownIt

    text = _decode_text(path.read_bytes())
    lines = text.splitlines(keepends=True)
    tokens = MarkdownIt('commonmark').enable('table').parse(text)
    headings = {}
    section = ''
    title = Path(relative_path).stem
    title_found = False
    blocks = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.type in {'fence', 'code_block'} and not token.content.strip():
            index += 1
            continue
        if token.type == 'html_block' and not re.sub(r'<!--.*?-->', '', token.content, flags=re.DOTALL).strip():
            index += 1
            continue
        if token.type == 'heading_open':
            level = int(token.tag[1:])
            heading = tokens[index + 1].content.strip()
            if not heading:
                index += 1
                continue
            section = _section_name(headings, level, heading)
            if level == 1 and not title_found:
                title, title_found = heading, True
        if token.type in {'heading_open', 'paragraph_open', 'fence', 'code_block', 'table_open', 'html_block'} and token.map:
            start, end = token.map
            body = ''.join(lines[start:end]).rstrip('\n')
            kind = 'text'
            header = ''
            if token.type == 'table_open':
                kind = 'table'
                header = ''.join(lines[start:min(start + 2, end)]).rstrip('\n')
                while index + 1 < len(tokens) and tokens[index + 1].type != 'table_close':
                    index += 1
            elif token.type in {'fence', 'code_block'}:
                kind = 'code'
            if body.strip():
                blocks.append(Block(body, section, location=f'{relative_path}:lines:{start + 1}-{end}', kind=kind, header=header))
        index += 1
    return ParsedDocument(title, blocks, 'md')


def _table_cell(value: str | None) -> str:
    """转义 Markdown 表格单元格并保留单元格内换行。"""
    return (value or '').replace('\\', '\\\\').replace('|', '\\|').replace('\r\n', '\n').replace('\n', '<br>')


def _render_table(rows: list[list[str | None]]) -> tuple[str, str]:
    """将表格转为带首行表头的可重复 Markdown 行。"""
    if not rows or not any(value and value.strip() for row in rows for value in row):
        return '', ''
    width = max(len(row) for row in rows)
    rendered = ['| ' + ' | '.join(_table_cell(value) for value in row + [''] * (width - len(row))) + ' |' for row in rows]
    header = rendered[0] + '\n| ' + ' | '.join(['---'] * width) + ' |'
    return '\n'.join([header, *rendered[1:]]), header


def _docx_heading_level(paragraph) -> int | None:
    """读取 Word 内置标题样式或显式大纲级别。"""
    style = paragraph.style
    while style is not None:
        match = re.fullmatch(r'(?:Heading|标题)\s*([1-9])', style.name, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
        style = style.base_style
    properties = paragraph._p.pPr
    if properties is not None:
        from docx.oxml.ns import qn

        outline = properties.find(qn('w:outlineLvl'))
        if outline is not None:
            level = int(outline.get(qn('w:val')))
            if 0 <= level <= 8:
                return level + 1
    return None


def _docx_cell_text(cell) -> str:
    """按单元格内部顺序读取段落及嵌套表格，避免遗漏证据。"""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    parts = []
    for child in cell._tc.iterchildren():
        if child.tag == qn('w:p'):
            parts.append(Paragraph(child, cell).text)
        elif child.tag == qn('w:tbl'):
            table = Table(child, cell)
            rendered, _ = _render_table([[_docx_cell_text(nested) for nested in row.cells] for row in table.rows])
            parts.append(rendered)
    return '\n'.join(parts).strip('\n')


def _parse_docx(path: Path, relative_path: str) -> ParsedDocument:
    """按照 Word 正文 XML 顺序解析段落、标题与表格。"""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(path)
    blocks = []
    headings = {}
    section = ''
    title = Path(relative_path).stem
    title_found = False
    for position, child in enumerate(document.element.body.iterchildren(), 1):
        location = f'{relative_path}:body:{position}'
        if child.tag == qn('w:p'):
            paragraph = Paragraph(child, document)
            body = paragraph.text
            if not body.strip():
                continue
            level = _docx_heading_level(paragraph)
            if level is not None:
                section = _section_name(headings, level, body.strip())
                if level == 1 and not title_found:
                    title, title_found = body.strip(), True
            blocks.append(Block(body, section, location=location))
        elif child.tag == qn('w:tbl'):
            table = Table(child, document)
            body, header = _render_table([[_docx_cell_text(cell) for cell in row.cells] for row in table.rows])
            if body:
                blocks.append(Block(body, section, location=location, kind='table', header=header))
    return ParsedDocument(title, blocks, 'docx')


def _parse_pdf(path: Path, relative_path: str) -> ParsedDocument:
    """提取 PDF 每页正文与表格，拒绝加密文件和需要 OCR 的页面。"""
    import pdfplumber

    blocks = []
    with pdfplumber.open(path) as document:
        if getattr(document.doc, 'encryption', None) is not None or not document.doc.is_extractable:
            raise DocumentError('不支持加密或禁止提取正文的 PDF')
        for number, page in enumerate(document.pages, 1):
            if not any(char.get('text', '').strip() for char in page.chars):
                if page.images:
                    raise DocumentError(f'PDF 第 {number} 页为扫描件或图片，无法提取正文')
                continue
            tables = []
            for table in page.find_tables():
                body, header = _render_table(table.extract())
                if body:
                    tables.append((table, body, header))
            regions = [table.bbox for table, _, _ in tables]

            def outside_tables(obj) -> bool:
                """将表格内部字符从普通正文提取中排除，避免重复。"""
                if obj.get('object_type') != 'char':
                    return True
                x = (obj['x0'] + obj['x1']) / 2
                y = (obj['top'] + obj['bottom']) / 2
                return not any(left <= x <= right and top <= y <= bottom for left, top, right, bottom in regions)

            ordered = []
            for line in page.filter(outside_tables).extract_text_lines(layout=False, strip=False, return_chars=False):
                body = line['text']
                if body.strip():
                    location = f'{relative_path}:page:{number}:y:{line["top"]:.2f}'
                    ordered.append((line['top'], line['x0'], Block(body, page=number, location=location)))
            for index, (table, body, header) in enumerate(tables, 1):
                if body:
                    location = f'{relative_path}:page:{number}:table:{index}'
                    ordered.append((table.bbox[1], table.bbox[0], Block(body, page=number, location=location, kind='table', header=header)))
            blocks.extend(entry[2] for entry in sorted(ordered, key=lambda entry: (entry[0], entry[1])))
    return ParsedDocument(Path(relative_path).stem, blocks, 'pdf')


def _parse_legacy_doc(path: Path, relative_path: str) -> ParsedDocument:
    """使用独立配置和临时目录无头转换旧 DOC，禁止加载宏。"""
    executable = shutil.which('soffice') or shutil.which('libreoffice')
    if executable is None:
        raise DocumentError('旧 DOC 需要已安装的 LibreOffice soffice，当前转换工具不可用')
    with tempfile.TemporaryDirectory(prefix='kb-doc-') as temporary:
        root = Path(temporary)
        source = root / 'source.doc'
        shutil.copyfile(path, source)
        profile = root / 'profile'
        user = profile / 'user'
        user.mkdir(parents=True)
        (user / 'registrymodifications.xcu').write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
            '</item></oor:items>',
            encoding='utf-8',
        )
        output = root / 'converted'
        output.mkdir()
        try:
            result = subprocess.run(
                [executable, f'-env:UserInstallation={profile.resolve().as_uri()}', '--headless', '--nologo',
                 '--nodefault', '--nofirststartwizard', '--norestore', '--convert-to', 'docx:Office Open XML Text',
                 '--outdir', str(output), str(source)],
                cwd=root, capture_output=True, timeout=60, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentError('旧 DOC 转换超时，未产生可用正文') from exc
        converted = output / 'source.docx'
        if result.returncode != 0 or not converted.is_file():
            raise DocumentError('LibreOffice 无法将旧 DOC 转换为有效 DOCX')
        parsed = _parse_docx(converted, relative_path)
        return ParsedDocument(parsed.title, parsed.blocks, 'doc')


def parse_document(path: Path, relative_path: str) -> ParsedDocument:
    """校验相对路径并分派真实格式解析，任何失败均明确报告。"""
    relative_path = normalize_relative_path(relative_path)
    parsers = {'.txt': _parse_text, '.md': _parse_markdown, '.markdown': _parse_markdown,
               '.docx': _parse_docx, '.pdf': _parse_pdf, '.doc': _parse_legacy_doc}
    suffix = Path(relative_path).suffix.lower()
    parser = parsers.get(suffix)
    if parser is None:
        raise DocumentError(f'不支持的文件格式：{suffix or "无扩展名"}')
    try:
        parsed = parser(Path(path), relative_path)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f'{suffix} 文件解析失败：{exc}') from exc
    if not parsed.blocks or not any(block.text.strip() for block in parsed.blocks):
        raise DocumentError('文件为空或没有可提取的正文')
    return parsed


def _encode(tokenizer, text: str) -> list[int]:
    """使用调用方真实分词器获取不含特殊符号的 token。"""
    return tokenizer.encode(text, add_special_tokens=False)


def _fitting_prefix(text: str, tokenizer, target: int, prefix: str = '', start: int = 0) -> str:
    """以真实分词结果扩展窗口并校正 Unicode 边界，不反复编码整篇余文。"""
    remaining = len(text) - start
    probe = 1
    while True:
        window = text[start:start + probe]
        if len(_encode(tokenizer, prefix + window)) > target:
            break
        if probe >= remaining:
            return window
        probe = min(probe * 2, remaining)
    available = max(1, target - len(_encode(tokenizer, prefix)))
    candidate = tokenizer.decode(_encode(tokenizer, window)[:available])
    if candidate and window.startswith(candidate) and len(_encode(tokenizer, prefix + candidate)) <= target:
        return candidate
    # BPE 窗口可能落在汉字或 emoji 的字节内部；边界搜索始终重新分词，不估算 token。
    low, high = 1, len(window)
    length = 0
    while low <= high:
        middle = (low + high) // 2
        if len(_encode(tokenizer, prefix + window[:middle])) <= target:
            length = middle
            low = middle + 1
        else:
            high = middle - 1
    if not length:
        raise DocumentError('切片预算不足以容纳表头及一个完整字符，请增大 target')
    return window[:length]


def _overlap_suffix(text: str, tokenizer, overlap: int) -> str:
    """提取不超过重叠预算的原文后缀，保留完整 Unicode 字符。"""
    if overlap == 0:
        return ''
    tokens = _encode(tokenizer, text)
    if len(tokens) <= overlap:
        return ''
    candidate = tokenizer.decode(tokens[-overlap:])
    if candidate and text.endswith(candidate) and len(_encode(tokenizer, candidate)) <= overlap:
        return candidate
    low, high = 1, len(text) - 1
    length = 0
    while low <= high:
        middle = (low + high) // 2
        if len(_encode(tokenizer, text[-middle:])) <= overlap:
            length = middle
            low = middle + 1
        else:
            high = middle - 1
    return text[-length:] if length else ''


def _split_blocks(blocks: list[Block], tokenizer, target: int, overlap: int, header: str = '') -> list[ChunkDraft]:
    """按段落或表格行优先切分同章节正文，并为各片保留来源集合。"""
    separator = '\n' if header else '\n\n'
    text = separator.join(block.text for block in blocks)
    prefix = header + '\n' if header else ''
    spans = []
    position = 0
    for block in blocks:
        spans.append((position, position + len(block.text), block.location))
        position += len(block.text) + len(separator)
    boundaries = [end for _, end, _ in spans]
    beginnings = [left for left, _, _ in spans]
    chunks = []
    start = covered = 0
    while start < len(text):
        fitted = _fitting_prefix(text, tokenizer, target, prefix, start)
        end = start + len(fitted)
        if end <= covered:
            # 表头占用大部分预算时减少重叠，以保证每片都包含新证据。
            start = covered
            continue
        boundary_index = bisect_right(boundaries, end) - 1
        if boundary_index >= 0 and boundaries[boundary_index] > covered and end < len(text):
            boundary = boundaries[boundary_index]
            candidate = text[start:boundary]
            if (header or len(_encode(tokenizer, candidate)) > overlap) and len(_encode(tokenizer, prefix + candidate)) <= target:
                end = boundary
        body = text[start:end]
        if body.strip():
            first = bisect_right(boundaries, start)
            last = bisect_left(beginnings, end)
            locations = dict.fromkeys(location for _, _, location in spans[first:last] if location)
            content = prefix + body
            count = len(_encode(tokenizer, content))
            if count > target:
                raise DocumentError('分词器在切片期间产生不一致的 token 计数')
            chunks.append(ChunkDraft(content, blocks[0].section, blocks[0].page, '; '.join(locations), count))
        if end == len(text):
            break
        covered = end
        if header and end in boundaries:
            # 常规表格只重叠完整行，避免将单元格尾部误放到下一片的第一列。
            suffix = ''
            first = bisect_right(boundaries, start)
            last = bisect_right(boundaries, end)
            for left, _, _ in reversed(spans[first:last]):
                candidate = text[left:end]
                if left < start or left == start or len(_encode(tokenizer, candidate)) > overlap:
                    break
                suffix = candidate
            start = end - len(suffix)
            if not suffix and text[start:start + 1] == '\n':
                start += 1
        else:
            suffix = _overlap_suffix(body, tokenizer, overlap)
            start = end - len(suffix)
    return chunks


def _split_table(block: Block, tokenizer, target: int, overlap: int) -> list[ChunkDraft]:
    """优先按整行拆分表格，超长行按 token 窗口拆分并逐片重复表头。"""
    header = block.header.strip('\n')
    if not header.strip():
        raise DocumentError('表格缺少可重复的表头')
    if len(_encode(tokenizer, header)) > target:
        raise DocumentError('表格表头本身超过切片 token 预算')
    body = block.text.strip('\n')
    if body.startswith(header):
        body = body[len(header):].lstrip('\n')
    if not body.strip():
        return [ChunkDraft(header, block.section, block.page, block.location, len(_encode(tokenizer, header)))]
    rows = [Block(row, block.section, block.page, block.location) for row in body.splitlines() if row.strip()]
    return _split_blocks(rows, tokenizer, target, overlap, header)


def split_document(document: ParsedDocument, tokenizer, target: int = 512, overlap: int = 64) -> list[ChunkDraft]:
    """按真实 tokenizer 切分正文，保留章节、页码、重叠内容与重复表头。"""
    if type(target) is not int or type(overlap) is not int or target <= 0 or not 0 <= overlap < target:
        raise DocumentError('切片参数必须为整数，且满足 target > 0、0 <= overlap < target')
    if not callable(getattr(tokenizer, 'encode', None)) or not callable(getattr(tokenizer, 'decode', None)):
        raise DocumentError('切片需要提供真实 tokenizer 的 encode 和 decode 接口')
    chunks = []
    pending = []
    try:
        for block in document.blocks:
            if not block.text.strip():
                continue
            if pending and (block.kind == 'table' or (block.section, block.page) != (pending[0].section, pending[0].page)):
                chunks.extend(_split_blocks(pending, tokenizer, target, overlap))
                pending = []
            if block.kind == 'table':
                chunks.extend(_split_table(block, tokenizer, target, overlap))
            else:
                pending.append(block)
        if pending:
            chunks.extend(_split_blocks(pending, tokenizer, target, overlap))
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(f'生成模型 tokenizer 切片失败：{exc}') from exc
    if not chunks:
        raise DocumentError('文档没有可切片的非空正文')
    return chunks
