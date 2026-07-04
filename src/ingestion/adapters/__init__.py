"""Auto-import all adapter modules to trigger @register_adapter side-effects."""

import src.ingestion.adapters.mbox_adapter  # noqa: F401
import src.ingestion.adapters.eml_adapter  # noqa: F401
import src.ingestion.adapters.msg_adapter  # noqa: F401
import src.ingestion.adapters.pst_adapter  # noqa: F401
