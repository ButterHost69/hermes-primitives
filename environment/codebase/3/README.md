# PacketWatch

PacketWatch is a standard-library-only Python library for parsing network
packets from raw bytes. It understands Ethernet II frames, IPv4 headers, and
TCP segments, and includes a small throughput monitor for counting bytes over
time. There are no external dependencies.

## Package layout

```
packetwatch/
  __init__.py    exports the submodules
  ethernet.py    Ethernet II frame parsing
  ipv4.py        IPv4 header parsing
  tcp.py         TCP segment header parsing
  monitor.py     throughput calculator
tests/
  __init__.py
  test_ipv4.py   IPv4 parsing tests
```

## Header layouts

### Ethernet II frame (14 bytes, network byte order)

| Offset | Size | Field      |
|-------:|-----:|-----------|
| 0      | 6    | dst MAC    |
| 6      | 6    | src MAC    |
| 12     | 2    | ethertype  |

### IPv4 header (20 bytes minimum, network byte order)

| Offset | Size | Field          |
|-------:|-----:|---------------|
| 0      | 1    | version + IHL  |
| 2      | 2    | total length   |
| 4      | 2    | identification |
| 8      | 1    | TTL            |
| 9      | 1    | protocol       |
| 12     | 4    | source address |
| 16     | 4    | dest address   |

### TCP segment header (20 bytes minimum, network byte order)

| Offset | Size | Field       |
|-------:|-----:|------------|
| 0      | 2    | src port   |
| 2      | 2    | dst port   |
| 4      | 4    | seq number |
| 8      | 4    | ack number |
| 12     | 2    | offset + flags |

## Usage

```python
from packetwatch import ipv4

header = bytes.fromhex(
    "45 00 00 14 1c 46 40 06 00 00 7f 00 00 01 7f 00 00 01".replace(" ", "")
)
pkt = ipv4.parse(header)
print(pkt.src, pkt.dst, pkt.protocol)
```

## Running the tests

With `unittest`:

```
python3 -m unittest discover -s tests -t .
```

or with `pytest`:

```
python3 -m pytest
```
