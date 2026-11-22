#!/usr/bin/env python3
"""Build a boot image that differs from a base image in exactly one variable.

v107: replace the payload's kernel.macho (prelinked 52,686,848 B) with the native
un-prelinked kernel (18,694,488 B) that upstream's board.toml says the liuqin boot
image carries (`--skip-prelink`). Everything else -- UEFI FD, appended DTB, ADT,
unixnu.toml args -- is byte-identical to the base image.

usage:
  python3 v107_native_kernel.py --base boot-v106-updtb.img \
      --payload /root/work/UniXNU-new/out/liuqin/payload/payload.img \
      --kernel <mach.development.pong> --out boot-v107-native.img
"""
import argparse
import struct
import subprocess
import sys
import tempfile
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
    ramdisk = data[start:start + ramdisk_size]
    end = start + ((ramdisk_size + PAGE - 1) // PAGE) * PAGE
    signature = data[end:end + SIGNATURE_BYTES]
    print(f'base: v{version} kernel={kernel_size} ramdisk={ramdisk_size} '
          f'sig={len(signature)} file={len(data)}')
    assert end + SIGNATURE_BYTES == len(data), 'unexpected base layout'
    stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
    combined = stream.decompress(kernel)
    dtb = stream.unused_data
    print(f'base kernel slot: gzip->{len(combined)} B, appended dtb {len(dtb)} B '
          f'sha {__import__("hashlib").sha256(dtb).hexdigest()[:16]}')
    return version, kernel, ramdisk, signature


def envelope(fat):
    if len(fat) % 512 or fat[510:512] != b'\x55\xaa' or struct.unpack_from('<H', fat, 11)[0] != 512:
        sys.exit('payload is not a FAT volume with 512-byte sectors')
    sectors = struct.unpack_from('<H', fat, 19)[0] or struct.unpack_from('<I', fat, 32)[0]
    if sectors != len(fat) // 512:
        sys.exit(f'payload declares {sectors} sectors, holds {len(fat)//512}')
    header = bytearray(HEADER_BYTES)
    struct.pack_into('<8sIIQII', header, 0, MAGIC, 1, HEADER_BYTES, len(fat),
                     zlib.crc32(fat) & 0xffffffff, 0)
    return bytes(header) + fat


def mtools(args):
    subprocess.run(args, check=True,
                   env={'MTOOLS_SKIP_CHECK': '1', 'TZ': 'UTC', 'PATH': '/usr/bin:/bin'})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', type=Path, required=True)
    ap.add_argument('--payload', type=Path, required=True, help='superfloppy FAT payload.img')
    ap.add_argument('--kernel', type=Path, required=True, help='native mach.development.pong')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--dtb', type=Path,
                    help='replace the DTB appended to the kernel slot (stock Cape SoC DTB)')
    a = ap.parse_args()

    version, kernel, _old_ramdisk, signature = parse_boot(a.base.read_bytes())
    if a.dtb:
        stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
        stream.decompress(kernel)
        gz = kernel[:len(kernel) - len(stream.unused_data)]
        cape = a.dtb.read_bytes()
        if cape[:4] != b'\xd0\x0d\xfe\xed' or struct.unpack_from('>I', cape, 4)[0] != len(cape):
            sys.exit(f'{a.dtb} is not a flattened device tree')
        kernel = gz + cape
        print(f'kernel slot: gzip {len(gz)} B + cape dtb {len(cape)} B')
    native = a.kernel.read_bytes()
    print(f'native kernel: {len(native)} B')
    with tempfile.TemporaryDirectory(prefix='v107-') as name:
        fat = Path(name) / 'payload.img'
        fat.write_bytes(a.payload.read_bytes())
        mtools(['mdel', '-i', str(fat), '::/EFI/UniXNU/Payloads/kernel.macho'])
        mtools(['mcopy', '-o', '-i', str(fat), str(a.kernel), '::/EFI/UniXNU/Payloads/kernel.macho'])
        got = subprocess.run(['mtype', '-i', str(fat), '::/EFI/UniXNU/Payloads/kernel.macho'],
                             capture_output=True, env={'MTOOLS_SKIP_CHECK': '1'}).stdout
        if got != native:
            sys.exit('kernel.macho did not read back as written')
        listing = subprocess.run(['mdir', '-i', str(fat), '::/EFI/UniXNU/Payloads/'],
                                 capture_output=True, text=True).stdout
        print(listing.strip())
        ramdisk = envelope(fat.read_bytes())

    header = bytearray(PAGE)
    header[0:8] = b'ANDROID!'
    struct.pack_into('<4I', header, 8, len(kernel), len(ramdisk), 0, 1584)
    struct.pack_into('<I', header, 40, version)
    struct.pack_into('<I', header, 1580, 0)  # v4 signature size
    image = (bytes(header) + kernel + bytes(-len(kernel) % PAGE) + ramdisk
             + bytes(-len(ramdisk) % PAGE) + signature)
    a.out.write_bytes(image)
    print(f'wrote {a.out} ({len(image)} bytes), ramdisk {len(ramdisk)} B')


if __name__ == '__main__':
    main()
