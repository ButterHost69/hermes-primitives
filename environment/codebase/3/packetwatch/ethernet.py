"""Ethernet II frame parsing.

Layout (without preamble/CRC), network byte order:

    0       6       12      14
    +-------+-------+-------+
    |  dst  |  src  | type  |
    +-------+-------+-------+
"""

import struct


class EthernetFrame:
    def __init__(self, dst, src, ethertype):
        self.dst = dst
        self.src = src
        self.ethertype = ethertype


def _format_mac(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


def parse(data: bytes):
    if len(data) < 14:
        raise ValueError("Ethernet frame must be at least 14 bytes")
    dst = _format_mac(data[0:6])
    src = _format_mac(data[6:12])
    ethertype = struct.unpack(">H", data[12:14])[0]
    return EthernetFrame(dst, src, ethertype)
