import unittest

from packetwatch import ipv4


class TestIPv4(unittest.TestCase):
    def test_parse_header_fields(self):
        header = bytes.fromhex(
            "45 00 00 14 1c 46 40 00 40 06 00 00 7f 00 00 01 7f 00 00 01".replace(" ", "")
        )
        pkt = ipv4.parse(header)
        self.assertEqual(pkt.total_length, 20)
        self.assertEqual(pkt.identification, 0x1c46)
        self.assertEqual(pkt.ttl, 64)
        self.assertEqual(pkt.protocol, 6)
        self.assertEqual(pkt.src, "127.0.0.1")
        self.assertEqual(pkt.dst, "127.0.0.1")

    def test_larger_total_length(self):
        header = bytes.fromhex(
            "45 00 00 3c 00 00 40 00 40 11 00 00 c0 a8 00 01 c0 a8 00 02".replace(" ", "")
        )
        self.assertEqual(ipv4.parse(header).total_length, 60)


if __name__ == "__main__":
    unittest.main()
