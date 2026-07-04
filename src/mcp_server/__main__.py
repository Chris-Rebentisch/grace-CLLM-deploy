"""Package entrypoint for ``python -m src.mcp_server``.

Thin shim: delegates to :func:`src.mcp_server.cli.main`. Required
because ``python -m <pkg>`` only executes a ``__main__`` submodule;
the real startup logic (env validation, tool module imports, stdio
transport) lives in :mod:`src.mcp_server.cli`.
"""

from __future__ import annotations

from src.mcp_server.cli import main


if __name__ == "__main__":
    main()
