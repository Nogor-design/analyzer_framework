from __future__ import annotations

"""Subprocess entry point for Strategy Lab jobs.

The ``nt_strategy_loop`` CLI uses non-zero exit codes for valid domain
decisions such as ``archive`` and ``halted``. That is useful at a shell prompt
but awkward for the web job manager, where those should mean "the loop
completed and wrote a decision artifact" rather than "the subprocess crashed".
"""

import sys

from ta_foundation.nt_strategy_loop.cli import main as strategy_loop_main


_DECISION_COMMANDS = {"full-loop", "repair-loop", "optimizer-bridge", "smoke-loop"}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else ""
    code = strategy_loop_main(args)
    if command in _DECISION_COMMANDS and code in {0, 2, 3}:
        return 0
    return code


if __name__ == "__main__":
    raise SystemExit(main())
