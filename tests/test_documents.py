"""用真实临时文件验证路径安全、格式正文与来源定位。"""
from pathlib import Path
import pytest
from docx import Document
from backend.app.documents import DocumentError, normalize_relative_path, parse_document

@pytest.mark.parametrize('path', ['../outside.txt', '/etc/passwd', r'C:\secret.txt', r'folder\..\outside.txt', 'a/../../b.md', 'a//b.txt', 'a/./b.txt'])
def test_reject_unsafe_relative_paths(path):
    """拒绝绝对路径、盘符、空目录段和目录穿越。"""
    with pytest.raises(DocumentError):
        normalize_relative_path(path)


def test_folder_path_normalized():
    """合法文件夹路径保留相对层级，不变成服务器存储路径。"""
    assert normalize_relative_path(r'技术\Python\说明.txt') == '技术/Python/说明.txt'


def test_txt_and_markdown_preserve_code_and_location(tmp_path):
    """正文、代码、章节和源定位都必须存在。"""
    text = tmp_path / '操作.txt'
    text.write_text('第一段操作说明。\n\n第二段必要条件。', encoding='utf-8')
    document = parse_document(text, '资料/操作.txt')
    assert '第一段操作说明' in '\n'.join(block.text for block in document.blocks)
    assert all(block.location for block in document.blocks)
    markdown = tmp_path / 'python.md'
    markdown.write_text('# Python操作\n\n## 环境\n\n执行以下命令。\n\n```bash\npython --version\n```\n\n|项目|值|\n|---|---|\n|版本|3.11|', encoding='utf-8')
    parsed = parse_document(markdown, 'python.md')
    assert parsed.title == 'Python操作'
    assert any('python --version' in block.text for block in parsed.blocks)
    assert any('环境' in block.section for block in parsed.blocks)
    assert any(block.kind == 'table' and '项目' in block.header for block in parsed.blocks)


def test_docx_table_header_and_section(tmp_path):
    """DOCX表格保留表头与所在章节，而不是拼成无来源文本。"""
    path = tmp_path / '运维.docx'
    doc = Document()
    doc.add_heading('集群运维', level=1)
    doc.add_paragraph('需要先核对当前集群。')
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = '命令'
    table.cell(0, 1).text = '含义'
    table.cell(1, 0).text = 'kubectl version'
    table.cell(1, 1).text = '查看版本'
    doc.save(path)
    parsed = parse_document(path, path.name)
    assert any(block.kind == 'table' and '命令' in block.header and 'kubectl version' in block.text for block in parsed.blocks)
    assert any('集群运维' in block.section for block in parsed.blocks)


@pytest.mark.parametrize('name,data', [('空.txt', b''), ('坏.pdf', b'not-a-pdf'), ('坏.docx', b'not-a-docx')])
def test_empty_and_corrupt_files_fail_explicitly(tmp_path, name, data):
    """空文件和损坏文档必须失败，不能将空正文发布为成功。"""
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(DocumentError):
        parse_document(path, name)
