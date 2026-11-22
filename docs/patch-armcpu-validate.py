#!/usr/bin/env python3
"""Patch AppleARMCPU::_validateConfiguration to return true in a prelinked kernelcache.

The prelinked Apple kexts come from the stock 22H374 kernelcache, so their virtual
layout is stable across our kernel builds; only the file offsets inside the aggregate
move. Patch by virtual address:

  * known VA 0xfffffff00b80dad4 (found by hand on the 2026-11-21 build) and check that
    its first instructions are the expected prologue
    `sub sp, sp, #0xc0 ; stp x28, x27, [sp, #0x60]`;
  * fallback: locate the panic string, find adrp/add references, skip the shared
    panic-formatting helper, and patch the prologue of the remaining reference.

usage: patch-armcpu-validate.py <kernel.macho> [<output>]
"""
import struct
import sys
from pathlib import Path

KNOWN_VA = 0xfffffff00b80dad4
PANIC = b'"CPU configuration in device tree is invalid"'
PROLOGUE = bytes.fromhex('ff0303d1fc6f06a9')   # sub sp,sp,#0xc0 ; stp x28,x27,[sp,#0x60]
PATCH = bytes.fromhex('20008052c0035fd6')      # mov w0,#1 ; ret
EXEC_SEGMENTS = ('__PRELINK_TEXT', '__PLK_TEXT_EXEC', '__TEXT', '__TEXT_EXEC')


def macho_segments(data):
    magic, ncmds = struct.unpack_from('<II', data, 0)
    if magic != 0xfeedfacf:
        sys.exit('not a 64-bit Mach-O')
    off = 32
    segments = {}
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, off)
        if cmd == 0x19:  # LC_SEGMENT_64
            name = data[off + 8:off + 24].split(b'\0')[0].decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', data, off + 24)
            segments[name] = (vmaddr, vmsize, fileoff, filesize)
        off += cmdsize
    return segments


def va_to_file(segments, va):
    for name, (vmaddr, vmsize, fileoff, filesize) in segments.items():
        if filesize and vmaddr <= va < vmaddr + filesize:
            return fileoff + (va - vmaddr)
    return None


def file_to_va(segments, file_off):
    for name, (vmaddr, vmsize, fileoff, filesize) in segments.items():
        if filesize and fileoff <= file_off < fileoff + filesize:
            return vmaddr + (file_off - fileoff)
    return None


def adrp_target(word, pc):
    if (word & 0x9F000000) != 0x90000000:
        return None
    imm = ((word >> 5) & 0x7FFFF) << 2 | ((word >> 29) & 3)
    if imm & (1 << 20):
        imm -= 1 << 21
    return (pc & ~0xFFF) + (imm << 12)


def patch_at(data, segments, va, target):
    off = va_to_file(segments, va)
    if off is None:
        return False
    if bytes(data[off:off + 8]) != PROLOGUE.replace(b'\r\n', b'\n'):
        return False
    data[off:off + 8] = PATCH
    target.write_bytes(data)
    print(f'patched {target.name}: {va:#x} (file {off:#x}) -> mov w0,#1 ; ret')
    return True


def main():
    source = Path(sys.argv[1])
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else source
    data = bytearray(source.read_bytes())
    segments = macho_segments(data)

    if patch_at(data, segments, KNOWN_VA, target):
        return

    print(f'known VA {KNOWN_VA:#x} does not hold the prologue; falling back to a scan')
    string_off = data.find(PANIC)
    if string_off < 0:
        sys.exit('panic string not found: is this the prelinked kernel.macho?')
    string_va = file_to_va(segments, string_off)
    page, page_off = string_va & ~0xFFF, string_va & 0xFFF
    print(f'panic string va {string_va:#x}')

    candidates = []
    for name in EXEC_SEGMENTS:
        if name not in segments:
            continue
        vmaddr, vmsize, fileoff, filesize = segments[name]
        if not filesize:
            continue
        blob = data[fileoff:fileoff + filesize]
        words = struct.unpack_from(f'<{filesize // 4}I', blob, 0)
        for index in range(len(words) - 1):
            pc = vmaddr + index * 4
            if adrp_target(words[index], pc) != page:
                continue
            nxt = words[index + 1]
            if (nxt & 0xFFC00000) != 0x91000000:
                continue
            if ((nxt >> 5) & 31) != (nxt & 31) or ((nxt >> 10) & 0xFFF) != page_off:
                continue
            ref_off = fileoff + index * 4
            hit = data.rfind(PROLOGUE, max(fileoff, ref_off - 0x4000), ref_off)
            if hit >= 0:
                candidates.append((vmaddr + (hit - fileoff), pc, name))

    # The shared panic-formatting helper is the reference inside the panic stub
    # area; the validation function sits in the Apple kext region far below it.
    candidates.sort(key=lambda item: item[1])
    for func_va, ref_pc, name in candidates:
        print(f'candidate: function {func_va:#x} (ref {ref_pc:#x} in {name})')
    for func_va, ref_pc, name in candidates:
        if patch_at(data, segments, func_va, target):
            return
    sys.exit('no candidate prologue could be patched')


if __name__ == '__main__':
    main()
