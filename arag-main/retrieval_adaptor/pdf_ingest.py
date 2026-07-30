"""英文论文 PDF -> 带元数据的分块语料；中文公众号文章 -> 待核查断言列表。

原创代码（非 A-RAG 开源部分）。

产出的 chunk 结构是原生 A-RAG chunk 格式的**超集**：保留 ``id`` / ``text`` 两个必需字段，
额外挂上论文元数据与段落定位信息，所以原生 ``KeywordSearchTool`` 不用改也能直接读。

解析质量上有三处针对学术版式的专门处理，缺了任何一处都会污染句子级证据：

1. **双栏阅读顺序**：按栏切分行再逐栏纵向排序。直接用 PyMuPDF 的原始块顺序会让左右栏
   交错，把两个不相干的半句拼成一句；
2. **字号/字重识别标题**：Nature 一类期刊用描述性无编号小标题，正则认不出来，靠字号与
   加粗判断；同时避免把 "0 DPA" 这种图注标签误判成编号章节；
3. **参考文献跳过而非截断**：不同期刊把 References 排在 Methods 前或后，遇到即停会丢掉
   整个 Methods，所以改为跳过该节、遇到下一个正文标题时恢复。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 这些章节的内容不作为事实证据
STOP_SECTIONS = (
    "reference",
    "bibliography",
    "acknowledg",
    "author contribution",
    "competing interest",
    "supplementary information",
    "extended data fig",
)

# 常见章节标题，用于给 chunk 打 section 标签
SECTION_KEYWORDS = (
    "abstract",
    "introduction",
    "background",
    "main",
    "related work",
    "preliminaries",
    "method",
    "methods",
    "methodology",
    "approach",
    "model",
    "experiment",
    "experiments",
    "experimental setup",
    "evaluation",
    "results",
    "analysis",
    "ablation",
    "discussion",
    "limitations",
    "conclusion",
    "conclusions",
    "future work",
    "data availability",
    "code availability",
)

YEAR_PATTERN = re.compile(r"\b(19[89]\d|20[0-4]\d)\b")
# 编号式章节标题，如 "3 Method" / "3.1 Encoder"。要求标题部分含小写字母，
# 以排除 "0 DPA" "2 KB" 这类图注/单位标签。
NUMBERED_SECTION = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)\.?\s+([A-Z][A-Za-z0-9 ,\-:]*[a-z][A-Za-z0-9 ,\-:]{1,58})$")
# 图表标题，不作为章节标题也不入正文
CAPTION_PREFIX = re.compile(r"^(fig(ure)?\.?\s*\d|table\s*\d|extended\s+data|supplementary)", re.IGNORECASE)

CJK_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])")

# 公众号排版里的非断言行：图表标题、编号小标题、栏目名
NON_CLAIM_PATTERNS = (
    re.compile(r"^\s*[（(]?[图表]\s*\d"),
    re.compile(r"^\s*\d+\s*[.、]"),
    re.compile(r"^\s*公众号\s*[:：]"),
    re.compile(r"^\s*(参考文献|来源|编辑|排版|责编|图片来源)\s*[:：]?\s*$"),
)


@dataclass
class Line:
    """PDF 中的一行，带版式属性。"""

    text: str
    page: int = 1
    block: int = 0
    is_heading: bool = False


@dataclass
class ExtractResult:
    """PDF 抽取结果。"""

    pages: list[tuple[int, str]] = field(default_factory=list)
    title_guess: str = ""
    lines: list[Line] = field(default_factory=list)
    # 前两页「未清理页眉」的原始文本。页眉里带着刊名、卷期、DOI，是元数据的主要来源，
    # 但对正文分块是噪声——所以正文用清理后的，元数据识别用这份原始文本。
    head_text: str = ""


@dataclass
class PaperDoc:
    """从单个 PDF 解析出的论文。

    只保留定位来源所必需的字段：标题、前两位作者、发表年份、来源文件，其余（期刊、DOI、
    链接）不再抽取——它们对幻觉判定没有增量信息，且识别噪声大。
    """

    paper_id: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    source_file: str = ""
    pages: list[tuple[int, str]] = field(default_factory=list)
    # 带版式属性的行序列；PyMuPDF 路径可用，pypdf 退化路径为空
    lines: list[Line] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": list(self.authors),
            "year": self.year,
            "source_file": self.source_file,
        }


# ---------------------------------------------------------------------- 版式处理


def _order_lines_by_column(
    raw_lines: list[dict[str, Any]], page_width: float
) -> list[dict[str, Any]]:
    """把一页的行按「双栏阅读顺序」重排。

    横跨中线的宽行（标题、跨栏图表）作为水平分隔带，带内先出左栏再出右栏。
    单栏页面直接按纵坐标排序。
    """
    if not raw_lines:
        return []

    mid = page_width / 2
    margin = page_width * 0.04
    full: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    right: list[dict[str, Any]] = []

    for line in raw_lines:
        x0, _, x1, _ = line["bbox"]
        spans_mid = x0 < mid - margin and x1 > mid + margin
        if spans_mid and (x1 - x0) > page_width * 0.55:
            full.append(line)
        elif (x0 + x1) / 2 < mid:
            left.append(line)
        else:
            right.append(line)

    def by_y(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(items, key=lambda item: (round(item["bbox"][1], 1), item["bbox"][0]))

    # 只有一侧有内容，说明本页其实是单栏
    if not left or not right:
        return by_y(raw_lines)

    ordered: list[dict[str, Any]] = []
    previous_y = float("-inf")
    for separator in [*by_y(full), None]:
        boundary = separator["bbox"][1] if separator is not None else float("inf")
        ordered.extend(by_y([item for item in left if previous_y <= item["bbox"][1] < boundary]))
        ordered.extend(by_y([item for item in right if previous_y <= item["bbox"][1] < boundary]))
        if separator is not None:
            ordered.append(separator)
            previous_y = boundary
    return ordered


def _drop_running_headers(
    per_page: list[list[dict[str, Any]]], *, min_ratio: float = 0.3
) -> list[list[dict[str, Any]]]:
    """删掉页眉页脚。

    期刊会在每页重复刊名、栏目名（如 Nature 的 "Article"）和卷期信息。这些短行若不清理，
    会被当成小标题或混进正文，污染每一个 chunk。判据是「短行 + 在足够多的页面上重复出现」。
    """
    page_count = len(per_page)
    if page_count < 4:
        return per_page

    counts: Counter[str] = Counter()
    for lines in per_page:
        seen = {item["text"].strip() for item in lines if len(item["text"].strip()) <= 80}
        counts.update(seen)

    threshold = max(3, int(page_count * min_ratio))
    repeated = {text for text, count in counts.items() if count >= threshold}
    if not repeated:
        return per_page

    return [
        [item for item in lines if item["text"].strip() not in repeated] for lines in per_page
    ]


def _style_runs(styles: list[tuple[float, bool]]) -> list[int]:
    """给每行标注它所处「连续同字号同字重行」的长度。

    这是区分标题与"大字号正文"的关键信号：标题是 1-2 行的孤立样式，而 Nature 一类期刊
    把摘要整段排成比正文更大的字号，会形成很长的同样式连续行。
    """
    runs = [1] * len(styles)
    start = 0
    while start < len(styles):
        end = start
        while end < len(styles) and styles[end] == styles[start]:
            end += 1
        for index in range(start, end):
            runs[index] = end - start
        start = end
    return runs


def _looks_like_heading(
    text: str, size: float, bold: bool, body_size: float, style_run: int
) -> bool:
    """靠字号、字重与样式连续长度判断是否为小标题。"""
    stripped = text.strip()
    if not stripped or len(stripped) > 90:
        return False
    if style_run > 2:
        return False
    if CAPTION_PREFIX.match(stripped):
        return False
    if stripped.endswith((".", ",", ";", ":")):
        return False
    # 含句子边界的说明是正文而不是标题
    if re.search(r"\.\s+[A-Z]", stripped):
        return False
    # 需要含字母且不是纯数字/纯大写缩写
    if not re.search(r"[A-Za-z]{3}", stripped):
        return False

    larger = size >= body_size + 0.8
    emphasised = bold and size >= body_size - 0.3
    return bool(larger or emphasised)


def _extract_with_pymupdf(path: Path) -> ExtractResult:
    """用 PyMuPDF 抽取正文，处理双栏顺序并识别小标题。"""
    import fitz

    per_page: list[list[dict[str, Any]]] = []
    head_lines: list[str] = []
    title_guess = ""

    with fitz.open(str(path)) as doc:
        for page in doc:
            layout = page.get_text("dict")
            raw_lines: list[dict[str, Any]] = []
            for block_index, block in enumerate(layout.get("blocks", [])):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans") or []
                    text = "".join(span.get("text") or "" for span in spans).strip()
                    if not text:
                        continue
                    raw_lines.append(
                        {
                            "text": text,
                            "bbox": line.get("bbox", (0, 0, 0, 0)),
                            "size": max((float(s.get("size", 0)) for s in spans), default=0.0),
                            "bold": any(
                                "bold" in (s.get("font") or "").lower() for s in spans
                            ),
                            "block": block_index,
                            "chars": len(text),
                        }
                    )
            # 元数据（刊名/年份/DOI/作者）按自上而下的顺序读最准，且必须在清理页眉之前留存；
            # 正文则用分栏顺序 + 清理页眉后的版本。
            if len(per_page) < 2:
                head_lines.extend(
                    item["text"]
                    for item in sorted(
                        raw_lines, key=lambda line: (round(line["bbox"][1], 1), line["bbox"][0])
                    )
                )
            per_page.append(_order_lines_by_column(raw_lines, float(page.rect.width)))

        per_page = _drop_running_headers(per_page)

        if doc.page_count and per_page and per_page[0]:
            # 首页最大字号即标题
            max_size = max(item["size"] for item in per_page[0])
            title_guess = " ".join(
                item["text"] for item in per_page[0] if item["size"] >= max_size - 0.5
            ).strip()

    # 正文字号取按字符数加权的众数
    weights: Counter[float] = Counter()
    for lines in per_page:
        for item in lines:
            weights[round(item["size"], 1)] += item["chars"]
    body_size = weights.most_common(1)[0][0] if weights else 10.0

    pages: list[tuple[int, str]] = []
    all_lines: list[Line] = []
    for page_no, lines in enumerate(per_page, start=1):
        pages.append((page_no, "\n".join(item["text"] for item in lines)))
        runs = _style_runs([(round(item["size"], 1), item["bold"]) for item in lines])
        for item, style_run in zip(lines, runs):
            all_lines.append(
                Line(
                    text=item["text"],
                    page=page_no,
                    block=item["block"],
                    is_heading=_looks_like_heading(
                        item["text"], item["size"], item["bold"], body_size, style_run
                    ),
                )
            )
    return ExtractResult(
        pages=pages,
        title_guess=title_guess,
        lines=_merge_wrapped_headings(all_lines),
        head_text="\n".join(head_lines),
    )


def _merge_wrapped_headings(lines: list[Line]) -> list[Line]:
    """把同一块内换行的长标题合并成一行。

    否则 "The evolutionary development of flower and ovary in cucumber" 折行后，
    第二行 "cucumber" 会变成一个独立的假标题。
    """
    merged: list[Line] = []
    for line in lines:
        if (
            merged
            and line.is_heading
            and merged[-1].is_heading
            and merged[-1].block == line.block
            and merged[-1].page == line.page
        ):
            merged[-1] = Line(
                text="%s %s" % (merged[-1].text.strip(), line.text.strip()),
                page=line.page,
                block=line.block,
                is_heading=True,
            )
            continue
        merged.append(line)
    return merged


def _extract_with_pypdf(path: Path) -> ExtractResult:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [
        (index, page.extract_text() or "") for index, page in enumerate(reader.pages, start=1)
    ]
    return ExtractResult(
        pages=pages, head_text="\n".join(text for _, text in pages[:2])
    )


def extract_pdf(path: Path) -> ExtractResult:
    """抽取 PDF，优先 PyMuPDF（可还原双栏顺序与标题层级），失败退回 pypdf。"""
    try:
        return _extract_with_pymupdf(path)
    except ImportError:
        return _extract_with_pypdf(path)


# ---------------------------------------------------------------------- 元数据识别


def _clean_title(raw: str) -> str:
    title = re.sub(r"\s+", " ", raw).strip(" .,-")
    if len(title) > 300:
        title = title[:300].rsplit(" ", 1)[0]
    return title


MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
# 首页侧栏的投稿/版权信息，容易被误当成作者名
PUBLISHING_NOISE = (
    "received", "accepted", "published", "check for", "update", "correspondence",
    "e-mail", "email", "licence", "license", "open access", "reprints",
    "peer review", "springer", "nature", "supplementary", "author",
)

# 人名的形状：2-4 个首字母大写的词，允许缩写点、连字符与撇号
NAME_WORD = re.compile(r"^[A-Z][A-Za-z'\u2019\-.]*$")


def _is_person_name(name: str) -> bool:
    """判断一个片段是否像人名。"""
    if ":" in name or any(char.isdigit() for char in name):
        return False
    words = name.split()
    if not 2 <= len(words) <= 4:
        return False
    lowered = name.lower()
    if any(noise in lowered for noise in PUBLISHING_NOISE):
        return False
    if any(word.lower() in MONTHS for word in words):
        return False
    return all(NAME_WORD.match(word) for word in words)


def _guess_authors(first_page: str, title: str, limit: int = 2) -> list[str]:
    """取前若干位作者用于引用标注。

    作者不是关键信息（下游靠 title / doi / section 定位来源），所以只取前两位，
    识别不到就留空，不为此做更复杂的版式推断。
    """
    lines = [line.strip() for line in first_page.splitlines() if line.strip()]
    start = 0
    if title:
        head = title.split()[0].lower() if title.split() else ""
        for index, line in enumerate(lines[:15]):
            if head and head in line.lower():
                start = index + 1
                break

    candidates: list[str] = []
    for line in lines[start : start + 12]:
        lowered = line.lower()
        if lowered.startswith("abstract") or "@" in line:
            break
        if any(word in lowered for word in ("university", "institute", "laborator", "department")):
            continue
        for part in re.split(r",| and |&|\u00b7", line):
            # 去掉作者名上的角标数字与符号
            name = re.sub(r"[\d\*\u2020\u2021\^]", "", part).strip()
            if _is_person_name(name) and name not in candidates:
                candidates.append(name)
                if len(candidates) >= limit:
                    return candidates
    return candidates


def parse_metadata(path: Path, extracted: ExtractResult) -> PaperDoc:
    """综合 PDF 首页文本与同名 sidecar 文件识别元数据。

    自动识别难免有偏差，允许在 PDF 同目录放 ``<同名>.meta.json`` 做人工覆盖，
    其中的字段优先级最高。
    """
    pages = extracted.pages
    # 刊名、卷期、DOI 都在页眉里，必须用未清理页眉的原始文本
    head_text = extracted.head_text or "\n".join(text for _, text in pages[:2])
    first_page = head_text

    title = _clean_title(extracted.title_guess) if extracted.title_guess else ""
    if not title:
        for line in first_page.splitlines():
            stripped = line.strip()
            if len(stripped) > 15 and re.search(r"[A-Za-z]", stripped):
                title = _clean_title(stripped)
                break

    # 首页同时印着投稿年与出版年（Received 2024 / April 2025），取最大值即发表年
    years = YEAR_PATTERN.findall(head_text)

    doc = PaperDoc(
        paper_id=path.stem,
        title=title,
        authors=_guess_authors(first_page, title),
        year=max(years) if years else "",
        source_file=path.name,
        pages=pages,
        lines=list(extracted.lines),
    )

    sidecar = path.with_suffix(".meta.json")
    if sidecar.exists():
        override = json.loads(sidecar.read_text(encoding="utf-8"))
        for key, value in override.items():
            if value and hasattr(doc, key):
                setattr(doc, key, value)
    return doc


# ---------------------------------------------------------------------- 分块


def _normalize_heading(section: str) -> str:
    """剥掉章节编号与标点，得到纯标题词，如 "6 References." -> "references"。"""
    return section.lower().strip().strip(" .:0123456789")


def _is_stop_section(section: str) -> bool:
    """是否是参考文献/致谢一类不应作为证据的章节（前缀匹配以兼容单复数变体）。"""
    normalized = _normalize_heading(section)
    return any(normalized.startswith(prefix) for prefix in STOP_SECTIONS)


# 首页的投稿流程信息与版权声明，混进正文会污染句子级证据
FRONT_MATTER_PREFIXES = (
    "received", "accepted", "published online", "published:", "check for update",
    "correspondence", "e-mail", "peer review", "reprints", "open access",
    "springer nature", "\u00a9",
)
AFFILIATION_WORDS = ("university", "institute", "laborator", "department", "academy", "college")


def _is_author_byline(text: str) -> bool:
    """判断一行是否是带机构角标的作者署名行。

    形如 "Zhaonian Dong1,2,11, Xiaolin Liu1,11, Xing Guo 3,11, ..."：角标数字让逐个人名的
    判断失效，所以先去掉数字与上标符号，再看拆出来的片段是否大多是人名。
    """
    stripped = text.strip()
    if len(stripped) > 500 or stripped.endswith("."):
        return False
    cleaned = re.sub(r"[\d\u2020\u2021*\u2709\u00a0]+", "", stripped)
    parts = [part.strip(" ,&") for part in re.split(r"[,&]", cleaned)]
    parts = [part for part in parts if part]
    if len(parts) < 3:
        return False
    namish = sum(1 for part in parts if _is_person_name(part))
    return namish >= max(2, int(len(parts) * 0.6))


def _is_front_matter_noise(text: str) -> bool:
    """判断一行是否是首页的作者名、机构、投稿日期一类元信息。

    这些行与正文混在同一个文本块里，若不剔除会被当成句子入索引，检索时就会出现
    "…orchestrated by KNOX1 Received: 1 October 2024 Wenwen Shao…" 这种拼接证据。
    元数据已经单独抽取，正文里不需要它们。
    """
    stripped = text.strip()
    lowered = stripped.lower()
    if any(lowered.startswith(prefix) for prefix in FRONT_MATTER_PREFIXES):
        return True
    # 独占一行的人名基本只出现在作者块
    if _is_person_name(stripped) or _is_author_byline(stripped):
        return True
    # 机构行以角标数字开头，如 "1Institute of Vegetables and Flowers, ..."
    if stripped[:1].isdigit() and any(word in lowered for word in AFFILIATION_WORDS):
        return True
    return False


def _is_section_like(text: str) -> bool:
    """字号判出的标题还要看写法像不像章节名。

    学术小标题用句式大小写（"Cell types in cucumber female floral development"），
    而首页那些加粗小字的作者行是词首大写（"Liyuan Zhong Tao Yang"）。因此除单词标题外，
    要求至少含一个全小写的词。刊头（"nature plants"）字号同样很大，靠首字母大写排除。
    """
    stripped = text.strip()
    if not stripped[:1].isupper():
        return False
    words = stripped.split()
    if len(words) <= 1:
        return True
    return any(word.islower() for word in words)


def _detect_section(line: str) -> str:
    """基于文本规则判断章节标题，是则返回规范化标题，否则返回空串。"""
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return ""
    if CAPTION_PREFIX.match(stripped) and not _is_stop_section(stripped):
        return ""

    numbered = NUMBERED_SECTION.match(stripped)
    if numbered:
        return "%s %s" % (numbered.group(1), numbered.group(2).strip())

    lowered = _normalize_heading(stripped)
    if lowered in SECTION_KEYWORDS or _is_stop_section(stripped):
        return stripped.strip(" .:")
    return ""


def _iter_lines(doc: PaperDoc) -> Iterable[Line]:
    """统一迭代接口：优先用带版式属性的行，否则退化到纯文本按行切。"""
    if doc.lines:
        yield from doc.lines
        return
    for page_no, page_text in doc.pages:
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", page_text)
        for index, raw_line in enumerate(text.splitlines()):
            yield Line(text=raw_line.rstrip(), page=page_no, block=index)


def split_paper_into_chunks(
    doc: PaperDoc,
    *,
    target_chars: int = 1200,
    min_chars: int = 200,
) -> list[dict[str, Any]]:
    """按段落聚合成目标长度的 chunk，保留章节与页码。

    参考文献/致谢等章节被跳过而不是就地截断——不同期刊的排版顺序不同，遇到即停会
    连带丢掉排在其后的 Methods。
    """
    chunks: list[dict[str, Any]] = []
    metadata = doc.metadata()
    current_section = "Front Matter"
    buffer: list[str] = []
    buffer_page = doc.pages[0][0] if doc.pages else 1
    previous_block = -1
    skipping = False

    # 切块在攒够 target_chars 时触发，若保留下限高于它，每一块都会在切出后被丢弃，
    # 正文会被静默吞掉。因此把下限压到目标长度之下。
    keep_floor = min(min_chars, max(60, target_chars // 2))

    def flush() -> None:
        nonlocal buffer
        text = re.sub(r"[ \t]+", " ", " ".join(buffer)).strip()
        if len(text) >= keep_floor:
            chunks.append(
                {"text": text, "section": current_section, "page": str(buffer_page), **metadata}
            )
        buffer = []

    has_layout = bool(doc.lines)

    for line in _iter_lines(doc):
        raw_text = line.text.strip()

        # 有版式信息时以字号/字重为准，否则退回文本规则
        named_section = _detect_section(raw_text)
        is_heading_line = line.is_heading if has_layout else bool(named_section)
        if is_heading_line and not named_section and not _is_section_like(raw_text):
            is_heading_line = False
        heading = (named_section or raw_text) if is_heading_line else ""

        if heading:
            if _is_stop_section(heading):
                flush()
                skipping = True
                previous_block = line.block
                continue
            # 参考文献里的期刊名往往是加粗的，不能让它把跳过状态解除；
            # 只有被文本规则确认的正式章节名（Methods / 3 Results / ...）才恢复正文。
            if skipping and not named_section:
                continue
            flush()
            skipping = False
            current_section = heading
            buffer_page = line.page
            previous_block = line.block
            continue

        if skipping or not raw_text:
            if not raw_text and buffer and sum(len(item) for item in buffer) >= target_chars:
                flush()
                buffer_page = line.page
            continue

        # 图表标题与首页元信息都不入正文
        if CAPTION_PREFIX.match(raw_text) or _is_front_matter_noise(raw_text):
            continue

        # 换块视为段落边界，够长就切
        if previous_block != line.block and buffer:
            if sum(len(item) for item in buffer) >= target_chars:
                flush()
                buffer_page = line.page
        previous_block = line.block

        if not buffer:
            buffer_page = line.page
        buffer.append(raw_text)

        if sum(len(item) for item in buffer) >= target_chars * 1.6:
            flush()
            buffer_page = line.page

    flush()
    return _repair_hyphenation(chunks)


def _repair_hyphenation(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """合并断行连字符：informa- tion -> information。"""
    for chunk in chunks:
        chunk["text"] = re.sub(r"(\w)-\s+(\w)", r"\1\2", chunk["text"])
    return chunks


# ---------------------------------------------------------------------- 对外接口


def ingest_papers(
    papers_dir: Path,
    *,
    target_chars: int = 1200,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """扫描目录下所有 PDF，产出统一编号的 chunk 列表。"""
    pdf_files = sorted(Path(papers_dir).glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError("%s 下没有找到任何 PDF" % papers_dir)

    all_chunks: list[dict[str, Any]] = []
    for pdf_path in pdf_files:
        extracted = extract_pdf(pdf_path)
        doc = parse_metadata(pdf_path, extracted)
        chunks = split_paper_into_chunks(doc, target_chars=target_chars)
        if verbose:
            print(
                "  %-40s -> %3d 页 / %3d 块 | %s"
                % (
                    pdf_path.name,
                    len(extracted.pages),
                    len(chunks),
                    doc.title[:50] or "(标题未识别)",
                )
            )
        all_chunks.extend(chunks)

    # 全局连续编号，chunk id 必须是字符串（原生 A-RAG 按字符串比对）
    for index, chunk in enumerate(all_chunks):
        chunk["id"] = str(index)
    return all_chunks


def save_chunks(chunks: list[dict[str, Any]], output_file: Path) -> None:
    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")


def is_claim_like(text: str, *, min_chars: int = 8) -> bool:
    """判断一行是否是值得核查的事实断言。

    图注（"图1 黄瓜单性花与子房下位的进化和发育"）和编号小标题（"1.黄瓜花、子房的进化
    和发育"）在长度上与断言无异，但没有可核查的内容，逐条送进检索纯属浪费 API 调用。
    """
    stripped = text.strip()
    if len(stripped) < min_chars:
        return False
    if any(pattern.match(stripped) for pattern in NON_CLAIM_PATTERNS):
        return False
    # 至少要有中文，且不是纯数字/符号
    return bool(re.search(r"[\u4e00-\u9fff]", stripped))


def split_chinese_sentences(text: str, *, min_chars: int = 8) -> list[str]:
    """把中文长文切成句子级断言，并滤掉图注与小标题一类的非断言行。"""
    normalized = re.sub(r"[ \t]+", " ", text.replace("\r", "\n"))
    sentences: list[str] = []
    for block in normalized.split("\n"):
        for piece in CJK_SENTENCE_END.split(block):
            candidate = piece.strip()
            if is_claim_like(candidate, min_chars=min_chars):
                sentences.append(candidate)
    return sentences


def load_wechat_claims(
    wechat_dir: Path,
    *,
    min_chars: int = 8,
    patterns: Iterable[str] = ("*.txt", "*.md"),
) -> list[dict[str, Any]]:
    """[DEPRECATED] 规则切句读取公众号文章。

    生产路径请改用 ``retrieval_adaptor.claim_extractor.extract_claims_from_article``
    （LLM 筛选事实性科学断言）。本函数仅供 ``--legacy-split`` 调试。
    """
    files: list[Path] = []
    for pattern in patterns:
        files.extend(sorted(Path(wechat_dir).glob(pattern)))
    if not files:
        raise FileNotFoundError("%s 下没有找到 .txt / .md 文章" % wechat_dir)

    claims: list[dict[str, Any]] = []
    for file_path in files:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        for index, sentence in enumerate(split_chinese_sentences(content, min_chars=min_chars)):
            claims.append(
                {
                    "claim_id": "%s#%d" % (file_path.stem, index),
                    "claim_zh": sentence,
                    "source_file": file_path.name,
                }
            )
    return claims
