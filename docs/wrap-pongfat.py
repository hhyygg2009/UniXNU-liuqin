#!/usr/bin/env python3
"""Wrap a raw FAT image in the PONGFAT1 envelope PongBootMemoryDxe looks for."""
import binascii
import struct
import sys
from pathlib import Path

fat_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
fat = fat_path.read_bytes()

if fat[510:512] != b'\x55\xaa':
    sys.exit('FAT image lacks the 0x55AA boot signature at 510')
bps = struct.unpack_from('<H', fat, 11)[0]
sectors16 = struct.unpack_from('<H', fat, 19)[0]
sectors32 = struct.unpack_from('<I', fat, 32)[0]
sectors = sectors16 or sectors32
print(f'FAT: {len(fat)} bytes, bytes/sector={bps}, sectors={sectors}, expected={len(fat)//512}')
if sectors * bps != len(fat):
    sys.exit('FAT sector count does not match the file size')

crc = binascii.crc32(fat) & 0xffffffff
header = bytearray(4096)
header[0:8] = b'PONGFAT1'
struct.pack_into('<I', header, 8, 1)
struct.pack_into('<I', header, 12, 4096)
struct.pack_into('<Q', header, 16, len(fat))
struct.pack_into('<I', header, 24, crc)
out_path.write_bytes(bytes(header) + fat)
print(f'wrote {out_path} ({out_path.stat().st_size} bytes), CRC32 {crc:#010x}')
