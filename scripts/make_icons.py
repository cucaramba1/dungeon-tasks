"""Рисует пиксельную иконку (меч в арке подземелья) без внешних библиотек."""
import struct
import zlib
from pathlib import Path

ART = [
    "................",
    "......####......",
    "....##....##....",
    "...#........#...",
    "..#.......GG.#..",
    "..#......GGG.#..",
    ".#......GGG...#.",
    ".#.....GGG....#.",
    ".#..B.GGG.....#.",
    ".#...BGG......#.",
    ".#...HB.......#.",
    ".#..H..B......#.",
    ".#.H..........#.",
    ".#............#.",
    ".##############.",
    "................",
]
PAL = {
    ".": (15, 14, 19), "#": (60, 52, 78), "G": (227, 179, 77),
    "B": (139, 124, 246), "H": (255, 111, 79),
}


def png(size: int, path: Path) -> None:
    n = len(ART)
    scale = size / n
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            row += bytes(PAL[ART[int(y / scale)][int(x / scale)]])
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


if __name__ == "__main__":
    static = Path(__file__).resolve().parent.parent / "static"
    for s in (180, 192, 512):
        png(s, static / f"icon-{s}.png")
    print("icons ok")
