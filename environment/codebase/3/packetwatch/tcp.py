"""TCP segment header parsing.

Layout (without options), network byte order:

    0       4       8       12      16      20
    +-------+-------+-------+-------+-------+
    | src   | dst   | seq           | ack           | off/flags | win |
    +-------+-------+-------+-------+-------+-------+
"""

import struct


class TCPHeader:
    def __init__(self, src_port, dst_port, seq, ack, flags):
        self.src_port = src_port
        self.dst_port = dst_port
        self.seq = seq
        self.ack = ack
        self.flags = flags


def parse(data: bytes):
    if len(data) < 20:
        raise ValueError("TCP header must be at least 20 bytes")
    src_port = struct.unpack(">H", data[0:2])[0]
    dst_port = struct.unpack(">H", data[2:4])[0]
    seq = struct.unpack(">I", data[4:8])[0]
    ack = struct.unpack(">I", data[8:12])[0]
    # The flags occupy the low 9 bits of the 13th/14th bytes.
    flags = struct.unpack(">H", data[12:14])[0] & 0x01FF
    return TCPHeader(src_port, dst_port, seq, ack, flags)
