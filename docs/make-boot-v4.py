#!/usr/bin/env python3
"""Assemble a flashable Android boot v4 image around a PONGFAT1 payload.

Inputs:
  --base     an existing v4 image whose kernel slot holds gzip(BootShim + UEFI FD)
             followed by the appended DTB (boot-v106-updtb.img works)
  --payload  the bare FAT payload volume (payload/payload.img from a package run)
  --dtb      optional replacement for the DTB appended to the kernel slot; the stock
             Cape SoC DTB the ABL selects by msm-id is what upstream's chain uses
  --out      output image

The ramdisk is the payload wrapped in the PONGFAT1 envelope the firmware scans for:
4096-byte header (magic, version 1, header bytes, volume bytes, CRC32 of the volume)
followed by the FAT volume unchanged.
"""
import argparse
import struct
import sys
import zlib
from pathlib import Path

PAGE = 4096
MAGIC = b'PONGFAT1'
HEADER_BYTES = 4096
SIGNATURE_BYTES = 4096


def parse_boot(data):
    if data[:8] != b'ANDROID!':
        sys.exit('base image is not an Android boot image')
    version = struct.unpack_from('<I', data, 40)[0]
    kernel_size, ramdisk_size = struct.unpack_from('<2I', data, 8)
    kernel = data[PAGE:PAGE + kernel_size]
    start = PAGE + ((kernel_size + PAGE - 1) // PAGE) * PAGE
    signature = data[start + ((ramdisk_size + PAGE - 1) // PAGE) * PAGE:][:SIGNATURE_BYTES]
    stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
    stream.decompress(kernel)
    if not stream.eof:
        sys.exit('base kernel slot does not start with a gzip stream')
    gz, old_dtb = kernel[:len(kernel) - len(stream.unused_data)], stream.unused_data
    print(f'base v{version}: gzip {len(gz)} B + dtb {len(old_dtb)} B, '
          f'ramdisk {ramdisk_size} B, signature {len(signature)} B')
    return version, gz, old_dtb, signature


def envelope(fat):
    if len(fat) % 512 or fat[510:512] != b'\x55\xaa' or struct.unpack_from('<H', fat, 11)[0] != 512:
        sys.exit('payload is not a FAT volume with 512-byte sectors')
    sectors = struct.unpack_from('<H', fat, 19)[0] or struct.unpack_from('<I', fat, 32)[0]
    if sectors != len(fat) // 512:
        sys.exit(f'payload declares {sectors} sectors, holds {len(fat) // 512}')
    header = bytearray(HEADER_BYTES)
    struct.pack_into('<8sIIQII', header, 0, MAGIC, 1, HEADER_BYTES, len(fat),
                     zlib.crc32(fat) & 0xffffffff, 0)
    print(f'payload FAT {len(fat)} B, crc32 {zlib.crc32(fat) & 0xffffffff:#010x}')
    return bytes(header) + fat


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--payload', type=Path, required=True)
    ap.add_argument('--dtb', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()

    version, gz, dtb, signature = parse_boot(a.base.read_bytes())
    if a.dtb:
        dtb = a.dtb.read_bytes()
        if dtb[:4] != b'\xd0\x0d\xfe\xed' or struct.unpack_from('>I', dtb, 4)[0] != len(dtb):
            sys.exit(f'{a.dtb} is not a flattened device tree')
        print(f'kernel slot dtb -> {a.dtb} ({len(dtb)} B)')
    kernel = gz + dtb
    ramdisk = envelope(a.payload.read_bytes())

    header = bytearray(PAGE)
    header[0:8] = b'ANDROID!'
    struct.pack_into('<4I', header, 8, len(kernel), len(ramdisk), 0, 1584)
    struct.pack_into('<I', header, 40, version)
    struct.pack_into('<I', header, 1580, 0)  # v4 signature size, unsigned
    image = (bytes(header) + kernel + bytes(-len(kernel) % PAGE) + ramdisk
             + bytes(-len(ramdisk) % PAGE) + signature)
    a.out.write_bytes(image)
    print(f'wrote {a.out} ({len(image)} B): kernel {len(kernel)} B, ramdisk {len(ramdisk)} B')


if __name__ == '__main__':
    main()
