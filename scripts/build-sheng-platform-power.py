#!/usr/bin/env python3
"""Build the opt-in, guarded sheng original-PMGR integration instrument."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/sheng-platform-power'
OUT.mkdir(parents=True, exist_ok=True)
# The liuqin-build tree still carries the native prelink kit as kits/NativePrelink;
# the newer tree renamed it to kits/XnuPrelink. Pick whichever one holds the module.
_KIT = next((ROOT / 'kits' / _name for _name in ('XnuPrelink', 'NativePrelink')
             if (ROOT / 'kits' / _name / 'native_prelink.py').is_file()),
            ROOT / 'kits' / 'NativePrelink')
sys.path.insert(0, str(_KIT))
from native_prelink import macho, backed_offset, exports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kernel_target import DEPLOYMENT, TRIPLE


def run(command, **kwargs):
    return subprocess.check_output([str(v) for v in command], timeout=90, **kwargs)


def require(value, message):
    if not value:
        raise SystemExit(message)


kernel_candidate = ROOT.parent / 'kernel/kernel.macho'   # liuqin: the built candidate cache
# The audit compares against a native prelink candidate that only a previous native build
# produces. The ABI below is fixed text in this script, so a missing reference changes the
# verification, not the code: say so and continue.
try:
    print(run(['python3', ROOT / 'scripts/audit-amfi-pmgr-contract.py',
               '--candidate', str(kernel_candidate)], text=True), end='')
except subprocess.CalledProcessError:
    print('build-sheng-platform-power: AMFI/PMGR audit unavailable '
          '(no native prelink candidate in this tree); generated ABI unchanged')
header = '''// Generated exact storage/slot ABI; private fields stay opaque.
#pragma once
#include <IOKit/IOPMGR.h>
class ApplePMGR : public IOPMGR {
 OSDeclareAbstractStructors(ApplePMGR)
 unsigned char originalPrivate[0x4b8a8-sizeof(IOPMGR)];
public:
'''
guards = ''
for i in range(177, 340):
    if i == 320:
        header += ' virtual uint32_t readAon();\n'
    elif i == 321:
        header += ' virtual void writeAon(uint64_t);\n'
    else:
        header += f' virtual void opaque{i}() = 0;\n'
        guards += f' void opaque{i}() override {{ unavailable({i}); }}\n'
header += '};\nstatic_assert(sizeof(ApplePMGR)==0x4b8a8,"Original provider storage");\n'
(OUT / 'OriginalPMGRABI.h').write_text(header)
(OUT / 'GuardedPMGRSlots.inc').write_text(guards)
source_kit = ROOT / 'kits/liuqin/ShengPlatformPower.cpp'
source = source_kit if source_kit.is_file() else ROOT / 'kits/sheng/ShengPlatformPower.cpp'
obj = OUT / 'ShengPlatformPower.o'
record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
command, i = [], 0
arguments = record['arguments']
while i < len(arguments):
    arg = arguments[i]
    if arg == record['file']:
        command.append(source)
    elif arg == '-o':
        command += ['-o', obj]
        i += 1
    elif arg == '-Xclang' and arguments[i + 1].startswith('-W'):
        i += 1
    elif not arg.startswith('-W'):
        command.append(arg)
    i += 1
command += ['-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-Wno-c99-designator',
            '-Wno-reorder-init-list', '-Wno-deprecated-enum-enum-conversion',
            '-Wno-unnecessary-virtual-specifier', '-I', OUT, '-I', ROOT / '.cache/xnu-work/osfmk']
run(command, cwd=record['directory'])
raw = obj.read_bytes()
info = macho(raw)
require(info['kind'] == 1, 'Require actual ARM64 subtype-0 Mach-O object')
build_version = [struct.unpack_from('<2I', raw, at + 8) for cmd, at, _ in info['commands'] if cmd == 0x32]
require(build_version == [DEPLOYMENT], 'Require the kernel deployment target')
sym_at = next(at for cmd, at, _ in info['commands'] if cmd == 2)
at, count, strings, _ = struct.unpack_from('<4I', raw, sym_at + 8)
symbols = []
for i in range(count):
    ix, kind, _, _, address = struct.unpack_from('<IBBHQ', raw, at + i * 16)
    symbols.append((raw[strings + ix:raw.index(b'\0', strings + ix)].decode(), address, kind))
vtable = next(va for name, va, _ in symbols if name == '__ZTV18ShengPlatformPower')
relocations = {}
for segment in info['segments']:
    for section in segment['sections']:
        if section['va'] <= vtable < section['va'] + section['size']:
            at, count = struct.unpack_from('<2I', raw, section['command_offset'] + 56)
            for i in range(count):
                offset, bits = struct.unpack_from('<2I', raw, at + i * 8)
                if bits >> 27 & 1 and bits >> 28 == 0 and bits >> 25 & 3 == 3:
                    relocations[section['va'] + offset] = symbols[bits & 0xffffff][0]
entries = [relocations.get(vtable + 16 + i * 8) for i in range(340)]
require(all(entries) and vtable + 16 + 340 * 8 not in relocations, 'Exact 340-entry provider table')
reference = ROOT / 'build/xnu-prelink/native-owner-ufs-rootauth3-candidate.macho'
if not reference.exists():
    # Build products are pruned: a later candidate built from the same original kernelcache has the identical original
    # ApplePMGR class and inherited native IOPMGR slots; every slot/pointer check below still runs against it.
    later = sorted((ROOT / 'build/xnu-prelink').glob('native-owner-ufs-cpu-desktop-*-candidate.macho'), key=lambda f: f.stat().st_mtime)
    if later:
        reference = later[-1]
    else:
        # No prelink candidate in this tree yet. The candidates are built from the pinned
        # original kernelcache, and unixnu keeps that image decompressed (its sha256 is the
        # board pin) under out/ipsw/<build>/kernelcache.decompressed. Use it as the
        # reference: the slots and pointers below are original-image addresses.
        found = sorted((ROOT.parent.parent / 'ipsw').glob('*/kernelcache.decompressed'),
                       key=lambda f: f.stat().st_mtime)
        reference = found[-1] if found else kernel_candidate
    require(reference.exists(), 'No reference candidate available')
candidate = reference.read_bytes()
meta = macho(candidate)
native_names = {n: va for va, names in exports(candidate, meta).items() for n in names}

# --- reference address space (2026-11-21, liuqin v60) -------------------------------------
# Every baked constant in this file -- the AON table base below and the ctor/dtor/gMetaClass
# and _sheng_original_pmgr_nub_* set -- is a VA in the *emitted* layout (cache VA + board
# slide), because that is where the shipped kernel maps the prelink and where these .set
# values get branched to at runtime.  A pipeline candidate already has that layout, but the
# fallback reference added in 71c6278 is out/ipsw/<build>/kernelcache.decompressed: the
# original, unslid image.  Reading an emitted VA out of an unslid image lands one slide off,
# inside __PLK_TEXT_EXEC, so ptr() hands back two instruction words, they get bound as
# function pointers, and the boot dies with
#     Kernel instruction fetch abort at pc 0x003f0100aa1503e0, lr = _sheng_pmgr_run+0x364
# where 0xd63f0100aa1503e0 is exactly `.set __ZN9ApplePMGR7readAonEv` -- `mov x0,x21` plus
# `blr x8` read as a pointer.
#
# A single slide is also the wrong shape: the prelink report moves every kext segment by
# -0x1000000 but __PRELINK_INFO by +0x77c000, so map per segment.  That is the H16 lesson
# (docs/boot-rounds-v35-v44.md, v41 "M:alien delta=-16777216 slide=0x0"), applied here.
PROT_EXEC = 4
AON_TABLE_BASE = 0xfffffff005ce26d8          # __ZTV...+0x10 for the inherited AON slots
_kernel_json = ROOT.parent / 'kernel/kernel.json'
EMITTED = ({seg['name']: (seg['va'], seg['size'])
            for seg in json.loads(_kernel_json.read_text()).get('coalesced_segments', [])}
           if _kernel_json.is_file() else {})
REF = {seg['name']: (seg['va'], seg['size'], seg['initprot']) for seg in meta['segments'] if seg['size']}


def _segment_of(va, table):
    for name, (base, size, *_rest) in table.items():
        if base <= va < base + size:
            return name, (base, size, _rest[0] if _rest else 0)
    return None, None


def _translate(va, source, target):
    """Map a VA between two layouts by segment name; identity when either side is unknown."""
    name, span = _segment_of(va, source)
    if name is None or name not in target:
        return va
    return va + (target[name][0] - span[0])


def aon_binding(table_va):
    """Read one inherited AON method pointer, with the address space checked both ways."""
    at = _translate(table_va, EMITTED, REF)          # emitted -> reference image
    name, span = _segment_of(at, REF)
    require(span is not None,
            f'AON table VA {table_va:#x} (reference {at:#x}) is not mapped in {reference.name}: '
            'the reference image and these baked constants disagree about the layout')
    require(not span[2] & PROT_EXEC,
            f'AON table VA {table_va:#x} resolves to {at:#x} inside the executable segment {name} of '
            f'{reference.name}; a vtable is data, so this reference is in a different address space '
            'than these constants and the read would return instruction words. Refusing to bind it.')
    value = ptr(at)
    vname, vspan = _segment_of(value, REF)
    require(vspan is not None and vspan[2] & PROT_EXEC,
            f'AON method pointer {value:#x} read at {at:#x} is not a mapped executable address in '
            f'{reference.name} (segment={vname}); refusing to bind it')
    return _translate(value, REF, EMITTED)           # reference -> emitted, i.e. runtime space


def ptr(va):
    return struct.unpack_from('<Q', candidate, backed_offset(meta['segments'], va, 8, len(candidate)))[0]


bindings = {'__ZN9ApplePMGRC2EPK11OSMetaClass': 0xfffffff0049f003c,
            '__ZN9ApplePMGRD2Ev': 0xfffffff0049f019c,
            '__ZN9ApplePMGR10gMetaClassE': 0xfffffff006adf490}
for slot, name in enumerate(entries):
    if slot in (0, 1, 7) or 168 <= slot < 340 and slot not in (320, 321):
        require('18ShengPlatformPower' in name, f'Own guard/lifecycle required at slot {slot}')
    elif slot in (320, 321):
        require('9ApplePMGR' in name, 'Only actual original AON implementations inherited')
        bindings[name] = aon_binding(AON_TABLE_BASE + slot * 8)
    else:
        require(native_names.get(name) == ptr(native_names['__ZTV6IOPMGR'] + 16 + slot * 8),
                f'Native IOPMGR/IOService entry differs at {slot}')
undefined = {n for n, _, k in symbols if k == 1 and 'ApplePMGR' in n}
require(undefined == set(bindings), 'All and only audited original base imports')
bindings.update({'_sheng_original_pmgr_nub_create': 0xfffffff004a22568,
                 '_sheng_original_pmgr_nub_read': 0xfffffff004a24adc,
                 '_sheng_original_pmgr_nub_write': 0xfffffff004a230ec})
asm = OUT / 'original-bindings.S'
asm.write_text(''.join(f'.globl {name}\n.set {name}, {va:#x}\n' for name, va in bindings.items()))
sdk = run(['xcrun', '--sdk', 'iphoneos', '--show-sdk-path'], text=True).strip()
run(['xcrun', 'clang', '-target', TRIPLE, '-isysroot', sdk, '-c', asm, '-o', OUT / 'original-bindings.o'])
late = OUT / 'ShengPlatformPower.late.o'
# llvm-objcopy takes --remove-section for Mach-O but refuses --globalize-symbol
# ("option is not supported for MachO"). The second edit is one bit in the nlist.
run(['llvm-objcopy', '--remove-section=__DATA,__mod_init_func', obj, late])


def globalize(path, name):
    data = bytearray(path.read_bytes())
    magic, _, _, _, commands, _, _, _ = struct.unpack_from('<8I', data, 0)
    if magic != 0xfeedfacf:
        raise SystemExit(f'{path}: not a 64-bit Mach-O')
    offset = 32
    for _ in range(commands):
        command, size = struct.unpack_from('<2I', data, offset)
        if command == 0x2:  # LC_SYMTAB
            symbols, count, strings, _ = struct.unpack_from('<4I', data, offset + 8)
            for index in range(count):
                entry = symbols + index * 16
                string_index, = struct.unpack_from('<I', data, entry)
                start = strings + string_index
                end = data.index(b'\0', start)
                if data[start:end] == name.encode():
                    data[entry + 4] |= 0x01      # N_EXT
                    path.write_bytes(data)
                    return True
            return False
        offset += size
    return False


if not globalize(late, '__GLOBAL__sub_I_ShengPlatformPower.cpp'):
    raise SystemExit('could not export __GLOBAL__sub_I_ShengPlatformPower.cpp')


def text_section(blob):
    section = next(s for seg in macho(blob)['segments'] for s in seg['sections'] if s['name'] == '__text')
    return blob[section['fileoff']:section['fileoff'] + section['size']]


require(text_section(raw) == text_section(late.read_bytes()), 'Late registration cannot change instructions')
archive = OUT / 'libShengPlatformPower.a'
archive.unlink(missing_ok=True)
run(['xcrun', 'ar', 'rcs', archive, late, OUT / 'original-bindings.o'])
report = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
          'bindings': {name: hex(va) for name, va in bindings.items()}, 'virtual_entries': 340,
          'mode': 'opt-in lifecycle/publication/refusal instrument; not a functional power backend',
          'runtime_qualified': False, 'original_executable_modified': False}
(OUT / 'build-report.json').write_text(json.dumps(report, indent=2) + '\n')
print('PASS: ordinary ARM64/iOS17 build; exact native and original bindings; unimplemented power slots stop')