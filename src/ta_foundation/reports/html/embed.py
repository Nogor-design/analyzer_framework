from __future__ import annotations

import base64
from io import BytesIO

import base64
import mimetypes
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt


def fig_to_base64_png(fig) -> str:
    """
    Convert a matplotlib figure to a base64-encoded PNG data URI (no files written).
    """
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{data}"

def file_to_data_uri(path: str | Path) -> Optional[str]:
    """
    Read a local file and return a data URI suitable for embedding in self-contained HTML.
    Returns None if file does not exist.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    mime, _ = mimetypes.guess_type(str(p))
    if not mime:
        # default fallback
        mime = "application/octet-stream"
    raw = p.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"