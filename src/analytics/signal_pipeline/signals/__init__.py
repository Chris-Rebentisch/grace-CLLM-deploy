"""Six signal detectors (Chunk 32, D241/D242/D243/D245)."""

from src.analytics.signal_pipeline.signals.signal_a import SignalADetector
from src.analytics.signal_pipeline.signals.signal_b import SignalBDetector
from src.analytics.signal_pipeline.signals.signal_c import SignalCDetector
from src.analytics.signal_pipeline.signals.signal_d import SignalDDetector
from src.analytics.signal_pipeline.signals.signal_e import SignalEDetector
from src.analytics.signal_pipeline.signals.signal_f import SignalFDetector

__all__ = [
    "SignalADetector",
    "SignalBDetector",
    "SignalCDetector",
    "SignalDDetector",
    "SignalEDetector",
    "SignalFDetector",
]


ALL_DETECTOR_CLASSES = (
    SignalADetector,
    SignalBDetector,
    SignalCDetector,
    SignalDDetector,
    SignalEDetector,
    SignalFDetector,
)
