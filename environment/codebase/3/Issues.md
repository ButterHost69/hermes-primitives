# Issue — IPv4 total length is parsed incorrectly

## Summary

The IPv4 header parser reports a wrong `total_length` for every packet, which
breaks payload slicing and any downstream length-based monitoring.

## Reproduce

```bash
python3 -m unittest discover -s tests -t .
```

Both IPv4 parser tests fail.

## Expected behaviour

For a header whose total-length field bytes are `00 14`, `total_length` should
be `20`. For `00 3c` it should be `60`.

## Actual behaviour

The parser reports `5120` and `15360` respectively — the 2-byte field is being
read with the wrong byte order.

## Where to look

The IPv4 header parsing lives in `packetwatch/ipv4.py`.
