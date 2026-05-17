from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import build_ai_index


DEFAULT_INDEX = Path(".ta_artifacts/ai_rag/chunks.jsonl")
DEFAULT_CONTEXT = Path(".ta_artifacts/ai_rag/context.md")
MAX_CHUNK_CHARS = 8000
TEXT_WINDOW_LINES = 120
TEXT_OVERLAP_LINES = 15

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?")


@dataclass
class Chunk:
    id: str
    path: str
    category: str
    kind: str
    symbol: str
    start_line: int
    end_line: int
    text: str
    modified_utc: str


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text):
        lowered = raw.lower()
        tokens.append(lowered)
        if "_" in lowered:
            tokens.extend(part for part in lowered.split("_") if part)
        camel_parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw).lower().split()
        if len(camel_parts) > 1:
            tokens.extend(camel_parts)
    return tokens


def read_text(path: Path) -> str | None:
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return None


def modified_utc(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def line_starts(text: str) -> list[int]:
    starts = [0]
    for match in re.finditer("\n", text):
        starts.append(match.end())
    return starts


def line_for_offset(starts: list[int], offset: int) -> int:
    lo, hi = 0, len(starts)
    while lo < hi:
        mid = (lo + hi) // 2
        if starts[mid] <= offset:
            lo = mid + 1
        else:
            hi = mid
    return max(1, lo)


def split_long_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[tuple[int, int, str]]:
    lines = text.splitlines()
    if len(text) <= max_chars:
        return [(1, max(1, len(lines)), text)]

    chunks: list[tuple[int, int, str]] = []
    start = 0
    while start < len(lines):
        end = min(len(lines), start + TEXT_WINDOW_LINES)
        chunk = "\n".join(lines[start:end]).strip()
        if chunk:
            chunks.append((start + 1, end, chunk))
        if end == len(lines):
            break
        start = max(end - TEXT_OVERLAP_LINES, start + 1)
    return chunks


def make_chunk(
    root: Path,
    rel: Path,
    category: str,
    kind: str,
    symbol: str,
    start_line: int,
    end_line: int,
    text: str,
    ordinal: int,
) -> Chunk:
    path_text = rel.as_posix()
    stable = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{path_text}:{start_line}:{symbol or kind}")[:160]
    return Chunk(
        id=f"{stable}-{ordinal}",
        path=path_text,
        category=category,
        kind=kind,
        symbol=symbol,
        start_line=start_line,
        end_line=end_line,
        text=text.strip(),
        modified_utc=modified_utc(root / rel),
    )


def python_chunks(root: Path, rel: Path, category: str) -> list[Chunk]:
    path = root / rel
    text = read_text(path)
    if text is None:
        return []

    try:
        tree = build_ai_index.ast.parse(text)
    except SyntaxError:
        return text_chunks(root, rel, category, kind="python_text")

    lines = text.splitlines()
    starts = line_starts(text)
    chunks: list[Chunk] = []
    ordinal = 0

    module_doc = build_ai_index.ast.get_docstring(tree) or ""
    imports_and_doc: list[str] = []
    for node in tree.body:
        if isinstance(node, (build_ai_index.ast.Import, build_ai_index.ast.ImportFrom)):
            imports_and_doc.append(build_ai_index.ast.get_source_segment(text, node) or "")
    if module_doc or imports_and_doc:
        ordinal += 1
        header = "\n".join(part for part in [module_doc, *imports_and_doc] if part).strip()
        chunks.append(make_chunk(root, rel, category, "python_module", rel.stem, 1, min(len(lines), 80), header, ordinal))

    for node in tree.body:
        if not isinstance(
            node,
            (
                build_ai_index.ast.ClassDef,
                build_ai_index.ast.FunctionDef,
                build_ai_index.ast.AsyncFunctionDef,
            ),
        ):
            continue
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)
        segment = "\n".join(lines[start_line - 1 : end_line])
        symbol = getattr(node, "name", "")
        kind = "class" if isinstance(node, build_ai_index.ast.ClassDef) else "function"
        for sub_start, sub_end, sub_text in split_long_text(segment):
            ordinal += 1
            chunks.append(
                make_chunk(
                    root,
                    rel,
                    category,
                    kind,
                    symbol,
                    start_line + sub_start - 1,
                    start_line + sub_end - 1,
                    sub_text,
                    ordinal,
                )
            )

    if not chunks:
        for start_line, end_line, chunk_text in split_long_text(text):
            ordinal += 1
            chunks.append(make_chunk(root, rel, category, "python_text", rel.stem, start_line, end_line, chunk_text, ordinal))

    # Keep line_for_offset exercised for future AST byte-offset chunking without leaving dead logic.
    _ = line_for_offset(starts, 0)
    return chunks


def heading_level(line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+(.+)", line)
    if not match:
        return None
    return len(match.group(1))


def doc_chunks(root: Path, rel: Path, category: str) -> list[Chunk]:
    text = read_text(root / rel)
    if text is None:
        return []

    lines = text.splitlines()
    starts = [idx for idx, line in enumerate(lines) if heading_level(line) is not None]
    if not starts:
        return text_chunks(root, rel, category, kind="doc")

    chunks: list[Chunk] = []
    ordinal = 0
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        section = "\n".join(lines[start:end]).strip()
        heading = lines[start].lstrip("#").strip()
        for sub_start, sub_end, sub_text in split_long_text(section):
            ordinal += 1
            chunks.append(make_chunk(root, rel, category, "doc", heading, start + sub_start, start + sub_end, sub_text, ordinal))
    return chunks


def text_chunks(root: Path, rel: Path, category: str, kind: str = "text") -> list[Chunk]:
    text = read_text(root / rel)
    if text is None:
        return []

    chunks: list[Chunk] = []
    for ordinal, (start_line, end_line, chunk_text) in enumerate(split_long_text(text), start=1):
        chunks.append(make_chunk(root, rel, category, kind, rel.stem, start_line, end_line, chunk_text, ordinal))
    return chunks


def build_chunks(root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for rel in build_ai_index.iter_indexable_files(root):
        category = build_ai_index.assign_category(rel).name
        suffix = rel.suffix.lower()
        if suffix == ".py":
            chunks.extend(python_chunks(root, rel, category))
        elif suffix in build_ai_index.DOC_EXTENSIONS:
            chunks.extend(doc_chunks(root, rel, category))
        else:
            chunks.extend(text_chunks(root, rel, category, kind="config"))
    return chunks


def write_index(chunks: Iterable[Chunk], output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(asdict(chunk), ensure_ascii=False) + "\n")
            count += 1
    return count


def load_chunks(index_path: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    with index_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                chunks.append(Chunk(**json.loads(line)))
    return chunks


def build_doc_frequency(chunks: list[Chunk]) -> dict[str, int]:
    df: dict[str, int] = defaultdict(int)
    for chunk in chunks:
        text = " ".join([chunk.path, chunk.category, chunk.kind, chunk.symbol, chunk.text])
        for token in set(tokenize(text)):
            df[token] += 1
    return dict(df)


def score_chunk(chunk: Chunk, query_tokens: list[str], df: dict[str, int], total_docs: int) -> float:
    weighted_text = " ".join(
        [
            chunk.path,
            chunk.path,
            chunk.category,
            chunk.symbol,
            chunk.symbol,
            chunk.kind,
            chunk.text,
        ]
    )
    counts = Counter(tokenize(weighted_text))
    if not counts:
        return 0.0

    score = 0.0
    length_norm = 1.0 + (sum(counts.values()) / 900.0)
    for token in query_tokens:
        tf = counts.get(token, 0)
        if not tf:
            continue
        idf = math.log((total_docs + 1) / (df.get(token, 0) + 0.5)) + 1.0
        score += (1.0 + math.log(tf)) * idf / length_norm

    query_text = " ".join(query_tokens)
    searchable_meta = " ".join([chunk.path, chunk.category, chunk.symbol]).lower()
    if query_text and query_text in searchable_meta:
        score += 4.0
    return score


def filter_chunks(chunks: list[Chunk], category: str | None, path_contains: str | None) -> list[Chunk]:
    filtered = chunks
    if category:
        filtered = [chunk for chunk in filtered if category.lower() in chunk.category.lower()]
    if path_contains:
        filtered = [chunk for chunk in filtered if path_contains.lower() in chunk.path.lower()]
    return filtered


def search(chunks: list[Chunk], query: str, top: int, category: str | None, path_contains: str | None) -> list[tuple[float, Chunk]]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    filtered = filter_chunks(chunks, category, path_contains)
    df = build_doc_frequency(filtered)
    scored = [
        (score_chunk(chunk, query_tokens, df, len(filtered)), chunk)
        for chunk in filtered
    ]
    scored = [(score, chunk) for score, chunk in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], item[1].path, item[1].start_line))
    return scored[:top]


def excerpt(chunk: Chunk, query: str, max_chars: int = 700) -> str:
    text = re.sub(r"\s+", " ", chunk.text).strip()
    tokens = tokenize(query)
    lowered = text.lower()
    positions = [lowered.find(token) for token in tokens if lowered.find(token) >= 0]
    if positions:
        center = min(positions)
        start = max(0, center - max_chars // 3)
    else:
        start = 0
    snippet = text[start : start + max_chars].strip()
    if start > 0:
        snippet = "..." + snippet
    if start + max_chars < len(text):
        snippet += "..."
    return snippet


def render_search(results: list[tuple[float, Chunk]], query: str, include_text: bool) -> str:
    if not results:
        return "No matching chunks found."

    lines = [f"# RAG Search Results", "", f"Query: `{query}`", ""]
    for rank, (score, chunk) in enumerate(results, start=1):
        location = f"{chunk.path}:{chunk.start_line}"
        symbol = f" `{chunk.symbol}`" if chunk.symbol else ""
        lines.append(f"## {rank}. {location} ({chunk.category}, {chunk.kind}{symbol})")
        lines.append(f"Score: {score:.2f}")
        lines.append("")
        if include_text:
            lines.append("```text")
            lines.append(chunk.text[:3000].rstrip())
            lines.append("```")
        else:
            lines.append(excerpt(chunk, query))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def ensure_index(index_path: Path) -> None:
    if not index_path.exists():
        chunks = build_chunks(Path.cwd())
        count = write_index(chunks, index_path)
        print(f"[ai-rag] Built missing index {index_path} with {count} chunks.")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Build and search a lightweight local RAG index.")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build the local chunk index.")
    build_parser.add_argument("--quiet", action="store_true")

    search_parser = subparsers.add_parser("search", help="Search the local chunk index.")
    search_parser.add_argument("query")
    search_parser.add_argument("--top", type=int, default=8)
    search_parser.add_argument("--category")
    search_parser.add_argument("--path")
    search_parser.add_argument("--full", action="store_true", help="Include chunk text instead of compact excerpts.")

    context_parser = subparsers.add_parser("context", help="Write a markdown context pack for an AI task.")
    context_parser.add_argument("query")
    context_parser.add_argument("--top", type=int, default=10)
    context_parser.add_argument("--category")
    context_parser.add_argument("--path")
    context_parser.add_argument("--output", type=Path, default=DEFAULT_CONTEXT)

    args = parser.parse_args()

    if args.command == "build":
        chunks = build_chunks(Path.cwd())
        count = write_index(chunks, args.index)
        if not args.quiet:
            print(f"[ai-rag] Wrote {args.index} with {count} chunks.")
        return 0

    ensure_index(args.index)
    chunks = load_chunks(args.index)
    results = search(chunks, args.query, args.top, args.category, args.path)

    if args.command == "search":
        print(render_search(results, args.query, include_text=args.full))
        return 0

    if args.command == "context":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        rendered = render_search(results, args.query, include_text=True)
        header = [
            "# AI Context Pack",
            "",
            "Use these retrieved chunks as starting context. Open full files only when needed.",
            "",
        ]
        args.output.write_text("\n".join(header) + rendered, encoding="utf-8")
        print(f"[ai-rag] Wrote context pack {args.output} with {len(results)} chunks.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
