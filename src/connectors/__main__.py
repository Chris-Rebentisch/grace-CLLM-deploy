"""Package-level CLI entry: ``python -m src.connectors``.

Delegates to ``sync_pipeline.main()``.
"""

from src.connectors.sync_pipeline import main

if __name__ == "__main__":
    main()
