# -*- coding: utf-8 -*-
"""
md2docx.py -- 把 Markdown 文档转换成 Word (.docx) 文档。

为什么需要它：
    项目要求每个阶段结束时额外产出一份 Word 文档。
    手写 Word 太麻烦，所以写一个 "Markdown -> Word" 的小工具，
    阶段一、二、三直接复用同一条命令：
        python scripts/md2docx.py docs/某文档.md docs/某文档.docx "文档标题"

支持的语法：# ~ #### 标题、段落、**加粗**、行内代码、- 无序列表、
            1. 有序列表、> 引用、| 表格 |、--- 分隔线、代码围栏块

依赖：python-docx   （安装：pip install python-docx）
"""

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

# 用 __file__ 推算项目根目录，绝不写死绝对路径（见 BUG-020）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

BT = chr(0x60)          # 反引号字符
FENCE = BT * 3          # 代码围栏 "三个反引号"

FONT_CN = "微软雅黑"      # 中文字体
FONT_MONO = "Consolas"    # 代码字体
FONT_BODY = "Calibri"     # 西文正文


def set_run_font(run, name_cn=FONT_CN, name_en=FONT_BODY, size=None, bold=None, color=None):
    """给一个 run（文字片段）设置中英文字体、字号、加粗、颜色。"""
    run.font.name = name_en
    rfonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), name_cn)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = RGBColor(*color)


def shade_paragraph(paragraph, color_hex):
    """给段落加背景底色。"""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex)
    paragraph._p.get_or_add_pPr().append(shd)


def shade_cell(cell, color_hex):
    """给表格单元格加背景底色。"""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color_hex)
    cell._tc.get_or_add_tcPr().append(shd)


# 匹配 **加粗** 或 反引号包裹的行内代码
INLINE_RE = re.compile(r"(\*\*.+?\*\*|" + BT + r"[^" + BT + r"]+" + BT + r")")


def add_inline(paragraph, text, size=10.5):
    """把一行文字里的 **加粗** 和行内代码解析成 Word 的 run。"""
    for piece in INLINE_RE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            run = paragraph.add_run(piece[2:-2])
            set_run_font(run, size=size, bold=True)
        elif piece.startswith(BT) and piece.endswith(BT) and len(piece) > 2:
            run = paragraph.add_run(piece[1:-1])
            set_run_font(run, name_cn=FONT_MONO, name_en=FONT_MONO, size=size - 0.5)
            run.font.color.rgb = RGBColor(0xC7, 0x25, 0x4E)
        else:
            run = paragraph.add_run(piece)
            set_run_font(run, size=size)


def add_code_block(doc, lines):
    """代码块：单列表格 + 灰底 + 等宽字体，视觉上像代码框。"""
    table = doc.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    cell = table.cell(0, 0)
    shade_cell(cell, "F2F2F2")
    cell.text = ""
    for i, line in enumerate(lines):
        p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        run = p.add_run(line.replace("\t", "    "))
        set_run_font(run, name_cn=FONT_MONO, name_en=FONT_MONO, size=8.5)
    doc.add_paragraph()


def add_table(doc, rows):
    """把 Markdown 表格转成 Word 表格。"""
    n_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=0, cols=n_cols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for row_idx, row in enumerate(rows):
        cells = table.add_row().cells
        for col_idx in range(n_cols):
            text = row[col_idx] if col_idx < len(row) else ""
            cell = cells[col_idx]
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            add_inline(p, text, size=9.5)
            if row_idx == 0:
                shade_cell(cell, "D9E2F3")
                for run in p.runs:
                    run.font.bold = True
    doc.add_paragraph()


def split_row(line):
    """把 | a | b | 拆成 ['a', 'b']。"""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def convert(md_path, docx_path, title):
    lines = md_path.read_text(encoding="utf-8").splitlines()

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = FONT_BODY
    normal.font.size = Pt(10.5)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT_CN)

    # 封面标题
    cover = doc.add_paragraph()
    cover.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = cover.add_run(title)
    set_run_font(run, size=22, bold=True, color=(0x1F, 0x3B, 0x73))
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    set_run_font(sub.add_run("离线 To-Do List 项目 ・ 项目文档"), size=11, color=(0x66, 0x66, 0x66))
    doc.add_paragraph()

    i = 0
    total = len(lines)
    while i < total:
        line = lines[i]

        # ---- 代码围栏块 ----
        if line.strip().startswith(FENCE):
            block = []
            i += 1
            while i < total and not lines[i].strip().startswith(FENCE):
                block.append(lines[i])
                i += 1
            i += 1
            add_code_block(doc, block)
            continue

        # ---- 表格 ----
        if line.strip().startswith("|") and i + 1 < total and set(lines[i + 1].strip()) <= set("|-: "):
            rows = [split_row(line)]
            i += 2
            while i < total and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            add_table(doc, rows)
            continue

        stripped = line.strip()

        # ---- 分隔线 ----
        if stripped in ("---", "***", "___"):
            p = doc.add_paragraph()
            pbdr = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "6")
            bottom.set(qn("w:color"), "BBBBBB")
            pbdr.append(bottom)
            p._p.get_or_add_pPr().append(pbdr)
            i += 1
            continue

        # ---- 标题 ----
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(14 if level <= 2 else 10)
            p.paragraph_format.space_after = Pt(6)
            sizes = {1: 17, 2: 14, 3: 12, 4: 11}
            colors = {1: (0x1F, 0x3B, 0x73), 2: (0x2E, 0x5C, 0x9A),
                      3: (0x33, 0x33, 0x33), 4: (0x44, 0x44, 0x44)}
            set_run_font(p.add_run(m.group(2).replace("**", "")),
                         size=sizes[level], bold=True, color=colors[level])
            i += 1
            continue

        # ---- 引用 ----
        if stripped.startswith(">"):
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            shade_paragraph(p, "FFF7E6")
            add_inline(p, stripped.lstrip("> ").strip(), size=10)
            i += 1
            continue

        # ---- 无序列表 ----
        m = re.match(r"^[-*]\s+(.*)$", stripped)
        if m:
            add_inline(doc.add_paragraph(style="List Bullet"), m.group(1))
            i += 1
            continue

        # ---- 有序列表 ----
        m = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m:
            add_inline(doc.add_paragraph(style="List Number"), m.group(1))
            i += 1
            continue

        # ---- 空行 ----
        if not stripped:
            i += 1
            continue

        # ---- 普通段落 ----
        add_inline(doc.add_paragraph(), stripped)
        i += 1

    docx_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(docx_path))
    print("[OK] 已生成 Word 文档: " + str(docx_path))


def convert_all(docs_dir=None, skip_names=()) -> int:
    """
    把 docs/ 下所有 .md 批量转换成同名 .docx。返回成功转换的数量。

    【为什么需要批量模式？】
        因为 .docx 是【派生产物】，已经被 .gitignore 排除，不会上传到 GitHub。
        别人（或者你换台电脑）clone 下来后，仓库里只有 .md，没有 Word 版。
        这时候一条命令就能把全部 Word 生成出来，比手敲十几遍强得多。

    【为什么跳过某些文件？】
        参数 skip_names 用于排除 README 这类"只给网页看、不需要 Word 版"的文档。
    """
    folder = Path(docs_dir) if docs_dir else (PROJECT_ROOT / "docs")
    md_files = sorted(folder.glob("*.md"))

    if not md_files:
        print("[ERROR] " + str(folder) + " 下没有找到任何 .md 文件")
        return 0

    print("=" * 64)
    print("批量生成 Word 文档 —— 共 %d 个 .md" % len(md_files))
    print("目录: " + str(folder))
    print("=" * 64)

    ok = 0
    for md in md_files:
        if md.stem in skip_names:
            print("[跳过] " + md.name)
            continue
        # .docx 和有 .docx 后缀的临时文件都不算（~$ 开头的直接忽略）
        out = md.with_suffix(".docx")
        try:
            convert(md, out, md.stem)
            ok += 1
        except Exception as exc:
            # 一个文件失败不应该让整批停下来 —— 报清楚是哪个，继续跑下一个
            print("[失败] " + md.name + " -> " + str(exc))

    print()
    print("=" * 64)
    print("完成：%d 个成功，%d 个失败" % (ok, len(md_files) - ok))
    print("=" * 64)
    return ok


def main():
    args = sys.argv[1:]

    # 批量模式：python scripts/md2docx.py --all
    if "--all" in args:
        convert_all()
        return

    if len(args) < 2:
        print("用法:")
        print("    单个转换: python scripts/md2docx.py <输入.md> <输出.docx> [文档标题]")
        print("    批量转换: python scripts/md2docx.py --all")
        sys.exit(1)

    md, out = Path(args[0]), Path(args[1])
    title = args[2] if len(args) > 2 else md.stem
    if not md.exists():
        print("[ERROR] 找不到输入文件: " + str(md))
        sys.exit(1)
    convert(md, out, title)


if __name__ == "__main__":
    main()
