from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_OUTPUT = Path("docs/AI_REPO_INDEX.md")

IGNORE_DIRS = {
    ".git",
    ".idea",
    ".pytest_cache",
    ".ta_artifacts",
    ".venv",
    "__pycache__",
    "None",
}

IGNORE_PREFIXES = (
    ".venv",
    "outputs",
)

IGNORE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".pyd",
    ".duckdb",
    ".log",
)

IGNORE_FILES = {
    "docs/AI_REPO_INDEX.md",
}

DOC_EXTENSIONS = {".md", ".txt"}
CONFIG_EXTENSIONS = {".yaml", ".yml", ".toml", ".json"}
INDEX_EXTENSIONS = {".py", *DOC_EXTENSIONS, *CONFIG_EXTENSIONS}


@dataclass
class PythonSummary:
    path: Path
    doc: str = ""
    classes: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)


@dataclass
class Category:
    name: str
    description: str
    roots: tuple[str, ...]
    files: list[Path] = field(default_factory=list)
    python: list[PythonSummary] = field(default_factory=list)


CATEGORIES: tuple[Category, ...] = (
    Category(
        "Project Context And Architecture",
        "High-level guidance for humans and AI agents.",
        ("README", "PROJECT_CONTEXT", "ARCHITECTURE", "CLAUDE", "CONTRIBUTING", "docs/"),
    ),
    Category(
        "CLI And Pipeline",
        "Command entry points, ingest orchestration, and run assembly.",
        ("src/ta_foundation/cli/", "src/ta_foundation/pipeline/"),
    ),
    Category(
        "Parsers",
        "NinjaTrader and input artifact parsers.",
        ("src/ta_foundation/parsers/",),
    ),
    Category(
        "Core Model And Metrics",
        "Shared models, assets, KPIs, and derived core metrics.",
        ("src/ta_foundation/core/", "src/ta_foundation/utils/"),
    ),
    Category(
        "Market Data",
        "Minute/tick stores, resampling, and daily levels.",
        ("src/ta_foundation/marketdata/",),
    ),
    Category(
        "Analysis Engines",
        "Feature engineering, MA structure, strategy discovery, regimes, and edge validation.",
        ("src/ta_foundation/analysis/",),
    ),
    Category(
        "Entry Strategies",
        "Specific entry strategy sweeps, features, and signals.",
        ("src/ta_foundation/analysis/entry_strategies/",),
    ),
    Category(
        "Prediction And Horizon System",
        "Prediction agents, horizon scoring, calibration, outcomes, and stores.",
        ("src/ta_foundation/prediction/",),
    ),
    Category(
        "Reports",
        "HTML/text report builders, sections, boards, and exports.",
        ("src/ta_foundation/reports/", "src/ta_foundation/plots/"),
    ),
    Category(
        "Persistence",
        "DuckDB and durable experiment storage code.",
        ("src/ta_foundation/persistence/",),
    ),
    Category(
        "Execution Bridge",
        "NinjaTrader execution bridge, runtime client, and bridge tests.",
        ("src/ta_foundation/strategies/TaFoundationExecutionBridge/", "tests/execution_shell/"),
    ),
    Category(
        "Web App",
        "Flask capability workbench: Backtest Reports, Prediction, Strategy Templates, Strategy Discovery, jobs, report presets, and dashboard-facing code.",
        ("src/ta_foundation/web/", "market_data_dashboard.py"),
    ),
    Category(
        "Tests",
        "Unit and integration tests grouped by feature area.",
        ("tests/", "src/ta_foundation/tests/"),
    ),
    Category(
        "Scripts",
        "Developer utilities and repo maintenance scripts.",
        ("scripts/",),
    ),
    Category(
        "Configuration And Prompts",
        "Report configs, strategy configs, prompts, and design notes not covered above.",
        (),
    ),
)


def should_ignore(path: Path) -> bool:
    if path.as_posix() in IGNORE_FILES:
        return True
    parts = set(path.parts)
    if parts & IGNORE_DIRS:
        return True
    if any(part.startswith(IGNORE_PREFIXES) for part in path.parts):
        return True
    return path.suffix.lower() in IGNORE_SUFFIXES


def iter_indexable_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if should_ignore(rel):
            continue
        if path.is_file() and path.suffix.lower() in INDEX_EXTENSIONS:
            files.append(rel)
    return sorted(files, key=lambda item: item.as_posix().lower())


def summarize_python(root: Path, rel: Path) -> PythonSummary:
    summary = PythonSummary(path=rel)
    try:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return summary

    summary.doc = (ast.get_docstring(tree) or "").strip().splitlines()[0:1]
    summary.doc = summary.doc[0] if summary.doc else ""

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            summary.classes.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            summary.functions.append(node.name)
    return summary


def matches_root(rel: Path, root_pattern: str) -> bool:
    rel_posix = rel.as_posix()
    if root_pattern.endswith("/"):
        return rel_posix.startswith(root_pattern)
    return rel_posix == root_pattern or rel.stem == root_pattern


def assign_category(rel: Path) -> Category:
    for category in CATEGORIES:
        if category.name == "Configuration And Prompts":
            continue
        if any(matches_root(rel, root) for root in category.roots):
            return category
    return CATEGORIES[-1]


def reset_categories() -> None:
    for category in CATEGORIES:
        category.files.clear()
        category.python.clear()


def add_files(root: Path, files: list[Path]) -> None:
    for rel in files:
        category = assign_category(rel)
        category.files.append(rel)
        if rel.suffix.lower() == ".py":
            category.python.append(summarize_python(root, rel))


def format_path(path: Path) -> str:
    return path.as_posix()


def render_category(category: Category, max_python: int) -> list[str]:
    lines = [
        f"## {category.name}",
        category.description,
        "",
        f"- Files indexed: {len(category.files)}",
    ]

    docs = [path for path in category.files if path.suffix.lower() in DOC_EXTENSIONS]
    configs = [path for path in category.files if path.suffix.lower() in CONFIG_EXTENSIONS]
    py_files = [path for path in category.files if path.suffix.lower() == ".py"]

    if docs:
        lines.append("- Key docs: " + ", ".join(f"`{format_path(path)}`" for path in docs[:12]))
    if configs:
        lines.append("- Key configs: " + ", ".join(f"`{format_path(path)}`" for path in configs[:12]))
    if py_files:
        lines.append("- Python files: " + str(len(py_files)))

    summaries = [item for item in category.python if item.doc or item.classes or item.functions]
    if summaries:
        lines.extend(["", "Representative modules:"])
        for item in summaries[:max_python]:
            details: list[str] = []
            if item.doc:
                details.append(item.doc)
            if item.classes:
                details.append("classes: " + ", ".join(item.classes[:6]))
            if item.functions:
                details.append("functions: " + ", ".join(item.functions[:8]))
            lines.append(f"- `{format_path(item.path)}` - {'; '.join(details)}")
        if len(summaries) > max_python:
            lines.append(f"- ... {len(summaries) - max_python} more summarized modules")

    lines.append("")
    return lines


def render_index(root: Path, files: list[Path], max_python: int) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    python_count = sum(1 for path in files if path.suffix.lower() == ".py")
    doc_count = sum(1 for path in files if path.suffix.lower() in DOC_EXTENSIONS)
    config_count = sum(1 for path in files if path.suffix.lower() in CONFIG_EXTENSIONS)

    lines = [
        "# AI Repo Index",
        "",
        "This file is generated by `scripts/build_ai_index.py`. It is meant to help Codex, Claude, and other AI coding tools route tasks before loading detailed code.",
        "",
        "## Usage",
        "- Start new AI sessions by reading this file plus `CLAUDE.md`.",
        "- Pick the smallest relevant category before opening source files.",
        "- Prefer targeted tests from the matching test category.",
        "- Do not index or paste generated outputs, virtualenvs, caches, databases, or logs unless the task explicitly requires them.",
        "",
        "## Global Ignore Policy",
        "- Ignore: `.git/`, `.idea/`, `.pytest_cache/`, `.ta_artifacts/`, `.venv*/`, `outputs*/`, `__pycache__/`, `None/`.",
        "- Ignore file types: `*.pyc`, `*.pyo`, `*.pyd`, `*.duckdb`, `*.log`.",
        "",
        "## Snapshot",
        f"- Repo root: `{root}`",
        f"- Generated: {generated_at}",
        f"- Indexed files: {len(files)}",
        f"- Python files: {python_count}",
        f"- Docs/text files: {doc_count}",
        f"- Config files: {config_count}",
        "",
    ]

    for category in CATEGORIES:
        if category.files:
            lines.extend(render_category(category, max_python=max_python))

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a compact AI-friendly repo index.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-python-per-category", type=int, default=20)
    args = parser.parse_args()

    root = Path.cwd()
    reset_categories()
    files = iter_indexable_files(root)
    add_files(root, files)

    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_index(root, files, args.max_python_per_category), encoding="utf-8")
    print(f"[ai-index] Wrote {output} with {len(files)} indexed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
