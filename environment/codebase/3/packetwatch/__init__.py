"""PacketWatch: a standard-library-only network packet parsing and
monitoring library.

Exposes submodules for parsing Ethernet frames, IPv4 headers, and TCP
segments, plus a small throughput monitor.
"""

from . import ethernet
from . import ipv4
from . import tcp
from . import monitor

__all__ = ["ethernet", "ipv4", "tcp", "monitor"]
