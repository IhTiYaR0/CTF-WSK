import sys

_TABLE = bytes([
    0x9d, 0xf4, 0xae, 0xbd, 0x2a, 0x3c, 0x56, 0x35,
    0x25, 0x90, 0xe4, 0xaf, 0xa4, 0x0b, 0x71, 0x06,
    0xdd, 0x24, 0xeb, 0x04, 0x5f,
])

def _process(data: bytes) -> bytes:
    out = bytearray()
    _s = 0xDE
    for _i, _b in enumerate(data):
        out.append(_b ^ _s ^ (_i & 0xFF))
        _s = (_s * 3 + 7) % 256
    return bytes(out)

def _check(password: str) -> bool:
    if len(password) != len(_TABLE):
        return False
    return _process(password.encode()) == _TABLE

def main():
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} <password>")
        sys.exit(1)

    if _check(sys.argv[1]):
        print("Access granted.")
        print("Flag:", sys.argv[1])
    else:
        print("Access denied.")
        sys.exit(1)

if __name__ == "__main__":
    main()
