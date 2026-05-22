# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Server-side atomic stats counters.
# Optimized: simpler fast-path counters for hot poll loop.

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class Stats:
    polls_served: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    active_conns: int = 0
    decode_failures: int = 0
    start_time: float = field(default_factory=time.monotonic)
