import struct


class IPv4Header:
    def __init__(self, total_length, identification, ttl, protocol, src, dst):
        self.total_length = total_length
        self.identification = identification
        self.ttl = ttl
        self.protocol = protocol
        self.src = src
        self.dst = dst


def parse(data: bytes):
    if len(data) < 20:
        raise ValueError("IPv4 header must be at least 20 bytes")
    total_length = struct.unpack("<H", data[2:4])[0]
    identification = struct.unpack(">H", data[4:6])[0]
    ttl = data[8]
    protocol = data[9]
    src = ".".join(str(b) for b in data[12:16])
    dst = ".".join(str(b) for b in data[16:20])
    return IPv4Header(total_length, identification, ttl, protocol, src, dst)
