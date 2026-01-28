from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class ExportResult:
    output_dir: Path
    exported: List[Path]
    skipped: List[str]


def export_exec_cards_to_png(
    html_path: Path,
    output_dir: Path,
    *,
    selector: str = ".ta-exec-card",
    device_scale_factor: float = 2.0,
    timeout_ms: int = 20_000,
) -> ExportResult:
    """
    Export each Executive Profile card (identified by CSS selector) into its own PNG.

    Uses Playwright (Chromium) to render HTML faithfully and screenshot each element.

    Output files:
      <output_dir>/<run_id>.png  where run_id comes from data-run-id attribute.
    """
    html_path = Path(html_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    exported: List[Path] = []
    skipped: List[str] = []

    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError(
            "Playwright is required to export cards as images. "
            "Install with: pip install playwright && playwright install chromium"
        ) from e

    if not html_path.exists():
        raise FileNotFoundError(html_path)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=device_scale_factor)

        # Use file:// URL so relative resources resolve (though your report is self-contained)
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle", timeout=timeout_ms)

        cards = page.query_selector_all(selector)

        for idx, el in enumerate(cards, start=1):
            run_id = None
            try:
                run_id = el.get_attribute("data-run-id")
            except Exception:
                run_id = None

            if not run_id:
                run_id = f"card_{idx}"

            # Sanitize filename
            safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in run_id).strip("_")
            out_path = output_dir / f"{safe}_Card.png"

            try:
                el.scroll_into_view_if_needed()
                el.screenshot(path=str(out_path))
                exported.append(out_path)
            except Exception as e:
                skipped.append(f"{run_id}: {e}")

        browser.close()

    return ExportResult(output_dir=output_dir, exported=exported, skipped=skipped)
