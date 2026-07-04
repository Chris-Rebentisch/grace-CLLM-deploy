import re
from typing import Any
import structlog

logger = structlog.get_logger(__name__)

_WINDOW_RE = re.compile(r'\[([0-9]+)([smhdw])\]')
_THRESHOLD_SECONDS = 14 * 86_400  # 14 days

_UNIT_TO_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

def query_with_coldstart_hint(
    query: str,
    result: Any,
) -> Any:
    """Wrap a PromQL result; emit INFO hint on possible cold-start."""
    if result:  # non-empty -> passthrough
        return result
    for match in _WINDOW_RE.finditer(query):
        value, unit = int(match.group(1)), match.group(2)
        if value * _UNIT_TO_SECONDS[unit] >= _THRESHOLD_SECONDS:
            logger.info(
                "promql returned zero series — possibly Prometheus cold-start",
                window=match.group(0),
            )
            break
    return result
