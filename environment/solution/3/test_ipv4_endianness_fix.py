"""Oracle tests for environment/codebase/3 (PacketWatch).

Bug: the IPv4 ``total_length`` field is unpacked with little-endian ``"<H"``
instead of network byte order ``">H"`` (for example ``00 14`` parses as 5120
instead of 20).

These tests import the real ``packetwatch`` package from the codebase, so they
fail while the byte order is wrong and pass once it is fixed.
"""

import os
import struct
import sys
import unittest


def _add_codebase_to_path(marker, number):
    """Put the directory containing ``marker`` for codebase ``number`` on sys.path."""
    candidates = []
    directory = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        candidates.append(os.path.join(directory, "codebase", str(number)))
        candidates.append(os.path.join(directory, str(number)))
        candidates.append(directory)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    directory = os.path.abspath(os.getcwd())
    for _ in range(6):
        candidates.append(os.path.join(directory, "codebase", str(number)))
        candidates.append(directory)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, marker)):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
    raise RuntimeError(f"could not locate codebase {number} containing {marker}")


_add_codebase_to_path(os.path.join("packetwatch", "__init__.py"), 3)

from packetwatch import ipv4  # noqa: E402


def make_header(
    total_length_bytes,
    identification=0x1C46,
    ttl=64,
    protocol=6,
    src=(127, 0, 0, 1),
    dst=(127, 0, 0, 1),
):
    """Build a 20-byte IPv4 header with an explicit total-length byte pair."""
    assert len(total_length_bytes) == 2
    return (
        bytes([0x45, 0x00])
        + bytes(total_length_bytes)
        + struct.pack(">H", identification)
        + b"\x00\x00"
        + bytes([ttl, protocol])
        + b"\x00\x00"
        + bytes(src)
        + bytes(dst)
    )


class TestIPv4TotalLengthByteOrder(unittest.TestCase):
    def test_total_length_reads_network_byte_order(self):
        self.assertEqual(ipv4.parse(make_header((0x00, 0x14))).total_length, 20)

    def test_larger_total_length(self):
        self.assertEqual(ipv4.parse(make_header((0x00, 0x3C))).total_length, 60)

    def test_total_length_256(self):
        # Big-endian 0x0100 == 256; little-endian would be 1.
        self.assertEqual(ipv4.parse(make_header((0x01, 0x00))).total_length, 256)

    def test_asymmetric_value_is_not_byte_swapped(self):
        # 0x1234 == 4660; little-endian would be 0x3412 == 13330.
        self.assertEqual(ipv4.parse(make_header((0x12, 0x34))).total_length, 4660)

    def test_maximum_total_length(self):
        self.assertEqual(ipv4.parse(make_header((0xFF, 0xFF))).total_length, 65535)

    def test_minimal_total_length(self):
        self.assertEqual(ipv4.parse(make_header((0x00, 0x01))).total_length, 1)


class TestIPv4OtherFieldsUnaffected(unittest.TestCase):
    def test_identification_is_still_big_endian(self):
        self.assertEqual(
            ipv4.parse(make_header((0x00, 0x14), identification=0x1C46)).identification,
            0x1C46,
        )

    def test_other_header_fields(self):
        header = make_header(
            (0x00, 0x3C),
            identification=0x0000,
            ttl=64,
            protocol=17,
            src=(192, 168, 0, 1),
            dst=(192, 168, 0, 2),
        )
        pkt = ipv4.parse(header)
        self.assertEqual(pkt.ttl, 64)
        self.assertEqual(pkt.protocol, 17)
        self.assertEqual(pkt.src, "192.168.0.1")
        self.assertEqual(pkt.dst, "192.168.0.2")

    def test_short_header_is_rejected(self):
        with self.assertRaises(ValueError):
            ipv4.parse(b"\x45\x00" + b"\x00" * 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
