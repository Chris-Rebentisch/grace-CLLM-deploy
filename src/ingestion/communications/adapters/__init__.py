"""Live email adapter subpackage — auto-import triggers @register_adapter side-effects.

Chunk 57, D424. CP6 adds IMAP; CP7 adds Exchange; CP8 adds Gmail.
"""

from . import imap_adapter  # noqa: F401
from . import exchange_adapter  # noqa: F401
from . import gmail_adapter  # noqa: F401
