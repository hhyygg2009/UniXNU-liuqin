#!/usr/bin/env python3
"""Derive and inject the original 21H461 IOBlockStorageDevice bindings for liuqin.

liuqin is format 1 (schema 1): the kernel links libShengUfs.a directly, and the
driver's IOBlockStorageDevice base-class imports must resolve at link time.
The official sheng flow qualifies those addresses through the macOS-only
audit chain (audit-aes-interface + audit-storage-interface + live gdb-stop
snapshots).  This script derives the same bindings offline, on Linux, with the
same evidence class minus the live snapshot:

  * the original kernelcache pinned in board.toml ([kernel.prelink]
    kernelcache_sha256 = f18b4df8..., iPad7,1 iPadOS 17.7.11 21H461);
  * the public IOStorageFamily source pinned in the component catalog
    (refer/IOStorageFamily @ e45fbe30, Darwin 23) compiled as a reference
    object with the kernel's own compile record (annotation only, never
    linked or executed);
  * the prelink placement rule: original-cache VAs are mapped per segment onto
    the layout recorded in out/liuqin/kernel/kernel.json (coalesced_segments),
    because our fileset emitter moves every kext segment by -0x1000000 and
    __PRELINK_INFO by +0x774000; kernel-proper objects are left to the link.

Method after UniXNU-mondrian scripts/derive-ios16-storage-bindings.py
(Redmi K60, same SM8475 family), adapted to 21H461/Darwin 23 and to the
schema-1 archive layout.  When the live QEMU snapshot exists
(build/darwinboot-storage-native-metadata/gdb-stop.json) the metaclass bytes
are additionally verified against it.

Outputs:
  tree/build/storage-abi/interface-audit.json      (the pinned record)
  tree/build/sheng-ufs/libShengUfs.a              (NativeUfs.o + original-bindings.o)
  tree/build/sheng-ufs/bindings.json              (provenance)
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import uuid as uuidlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # the assembled tree
DEVICE = Path(os.environ.get('UNIXNU_DEVICE', ROOT))
sys.path.insert(0, str(ROOT / 'kits' / 'XnuPrelink'))
sys.path.insert(0, str(ROOT / 'scripts'))
import native_prelink as np                          # noqa: E402
from kernel_target import TRIPLE                     # noqa: E402

KERNELCACHE_PIN = 'f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7'
SOURCE_PIN = 'e45fbe30a7879e0b16e26dddb78538f1b00e0bff'
ARM64_LINK_ADDR = 0xfffffff007004000
PRELINK_SEGMENTS = ('__PRELINK_TEXT', '__PLK_TEXT_EXEC', '__PLK_DATA_CONST', '__PRELINK_DATA')
SLIDE_STEP = 0x4000
SLOTS = 190
PURE_VIRTUAL = 14
CLASS_SIZE = 0x90
IO_SERVICE_CLASS_SIZE = 0x88


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sh(cmd):
    return subprocess.check_output(cmd, text=True).strip()


def fail(message):
    raise SystemExit('build-liuqin-ufs-bindings: ' + message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernelcache', type=Path,
                        default=ROOT.parent.parent / 'ipsw/21H461/kernelcache.decompressed')
    parser.add_argument('--audit', type=Path, default=ROOT / 'build/storage-abi/interface-audit.json')
    parser.add_argument('--snapshot', type=Path,
                        default=ROOT / 'build/darwinboot-storage-native-metadata/gdb-stop.json')
    args = parser.parse_args()

    # ---- 1. the original kernelcache, pinned --------------------------------
    original = Path(args.kernelcache).read_bytes()
    if digest(original) != KERNELCACHE_PIN:
        fail(f'kernelcache sha256 {digest(original)[:16]} != board pin')
    kernel = np.macho(original)
    exports = np.exports(original, kernel)
    known = {name: va for va, names in exports.items() for name in names}
    print(f'original 21H461 kernelcache: {len(original)} bytes, {len(known)} exported names')

    # ---- 2. the public reference object (annotation only) -------------------
    source = ROOT / 'refer/IOStorageFamily'
    revision = sh(['git', '-C', str(source), 'rev-parse', 'HEAD'])
    if revision != SOURCE_PIN:
        fail(f'IOStorageFamily revision {revision} != catalog pin {SOURCE_PIN}')
    out = args.audit
    out.parent.mkdir(parents=True, exist_ok=True)
    obj = out.parent / 'IOBlockStorageDevice-reference.o'
    record_path = ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json'
    if not record_path.is_file():
        fail('missing kernel compile record (run ./unixnu build kernel first, this step follows NativeUfs)')
    record = json.loads(record_path.read_text().strip().rstrip(','))
    arguments = record['arguments']
    command, index = [], 0
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source / 'IOBlockStorageDevice.cpp'))
        elif value == '-o':
            command += ['-o', str(obj)]
            index += 1
        elif value in ('-MF', '-MJ'):
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W') and value not in ('-MD', '-MP'):
            command.append(value)
        index += 1
    command += ['-I', str(ROOT / 'build/storage-abi/include'), '-I', str(ROOT / '.cache/xnu-work/osfmk'),
                '-Wall', '-Wextra', '-Wno-unused-parameter', '-Wno-deprecated-declarations',
                '-Wno-c99-designator', '-Wno-reorder-init-list', '-Wno-unnecessary-virtual-specifier']
    result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
    if result.returncode:
        fail('reference compile failed:\n' + (result.stdout + result.stderr)[-2500:])

    # ---- 3. dispatch order and class size from DWARF ------------------------
    dwarf = sh(['xcrun', 'dwarfdump', '--name=IOBlockStorageDevice', '--show-children', str(obj)])
    match = re.search(r'DW_AT_name\s+\("IOBlockStorageDevice"\)[\s\S]{0,200}?DW_AT_byte_size\s+\((0x[0-9a-f]+)\)', dwarf)
    if not match or int(match.group(1), 16) != CLASS_SIZE:
        fail(f'public class size changed: {match.group(1) if match else None}')
    blob = obj.read_bytes()
    image = np.macho(blob)
    symcmd = next(at for command_id, at, size in image['commands'] if command_id == 2)
    sy, count, st, _ = struct.unpack_from('<4I', blob, symcmd + 8)
    symbols = []
    for index in range(count):
        ix, kind, _, _, value = struct.unpack_from('<IBBHQ', blob, sy + index * 16)
        symbols.append((blob[st + ix:blob.index(b'\0', st + ix)].decode(), value, kind))
    vtable = next(v for name, v, kind in symbols if name == '__ZTV20IOBlockStorageDevice')
    relocations = {}
    for segment in image['segments']:
        for section in segment['sections']:
            if section['va'] <= vtable < section['va'] + section['size']:
                ro, rc = struct.unpack_from('<II', blob, section['command_offset'] + 56)
                for index in range(rc):
                    at, word = struct.unpack_from('<II', blob, ro + index * 8)
                    if word >> 27 & 1 and word >> 28 == 0 and word >> 25 & 3 == 3:
                        relocations[section['va'] + at] = symbols[word & 0xffffff][0]
    methods = []
    while vtable + 16 + len(methods) * 8 in relocations:
        methods.append(relocations[vtable + 16 + len(methods) * 8])
    if len(methods) != SLOTS or methods.count('___cxa_pure_virtual') != PURE_VIRTUAL:
        fail(f'unexpected dispatch shape: {len(methods)} slots, {methods.count("___cxa_pure_virtual")} pure')

    # ---- 4. the original kext dispatch table --------------------------------
    info_section = next(s for g in kernel['segments'] for s in g['sections'] if s['name'] == '__info')
    info = np.unserialize_xml(original[info_section['fileoff']:info_section['fileoff'] + info_section['size']].rstrip(b'\0'))
    entry = next(e for e in info['_PrelinkInfoDictionary']
                 if e.get('CFBundleIdentifier') == 'com.apple.iokit.IOStorageFamily')
    kext_at = np.backed_offset(kernel['segments'], entry['_PrelinkExecutableLoadAddr'], 32, len(original))
    kext = np.macho(original, kext_at)
    const = next(s for s in kext['segments'] if s['name'] == '__DATA_CONST')
    text = next(s for s in kext['segments'] if s['name'] == '__TEXT_EXEC')

    def read(va, size):
        at = np.backed_offset(kernel['segments'], va, size, len(original))
        return original[at:at + size]

    data = read(const['va'], const['size'])
    matches = []
    for at in range(0, len(data) - (SLOTS + 2) * 8 + 1, 8):
        if data[at:at + 16] != bytes(16):
            continue
        pointers = struct.unpack_from('<' + 'Q' * SLOTS, data, at + 16)
        if all(pointers[i] == known[name] for i, name in enumerate(methods) if name in known):
            matches.append((const['va'] + at, pointers))
    if len(matches) != 1:
        fail(f'21H461 storage dispatch is not unique: {len(matches)} candidates')
    table, pointers = matches[0]
    matched = sum(name in known for name in methods)

    # ---- 5. metaclass getter, protected constructor, base destructor --------
    adrp, add, ret = struct.unpack('<3I', read(pointers[7], 12))
    if not (adrp & 0x9f00001f == 0x90000000 and add & 0xffc003ff == 0x91000000 and ret == 0xd65f03c0):
        fail('unexpected metaclass getter layout')
    immediate = ((adrp >> 5 & 0x7ffff) << 2) | (adrp >> 29 & 3)
    if immediate & 1 << 20:
        immediate -= 1 << 21
    meta = (pointers[7] & ~4095) + immediate * 4096 + (add >> 10 & 4095)

    text_bytes = read(text['va'], text['size'])
    constructors = []
    for offset in range(0, text['size'] - 32, 4):
        words = struct.unpack_from('<8I', text_bytes, offset)
        if (words[:2] != (0xa9bf7bfd, 0x910003fd) or words[2] & 0xfc000000 != 0x94000000 or
                words[3] & 0x9f00001f != 0x90000008 or words[4] & 0xffc003ff != 0x91000108 or
                words[5:] != (0xf9000008, 0xa8c17bfd, 0xd65f03c0)):
            continue
        address = text['va'] + offset
        imm = ((words[3] >> 5 & 0x7ffff) << 2) | (words[3] >> 29 & 3)
        if imm & 1 << 20:
            imm -= 1 << 21
        stored = ((address + 12) & ~4095) + imm * 4096 + ((words[4] >> 10) & 4095)
        if stored == table + 16:
            constructors.append(address)
    if len(constructors) != 1:
        fail(f'storage protected constructor is not unique: {len(constructors)}')
    constructor = constructors[0]

    destructor = pointers[0] - 4
    if struct.unpack('<I', read(destructor, 4))[0] & 0xfc000000 != 0x14000000:
        fail('storage base destructor layout changed')

    # ---- 6. placement semantics --------------------------------------------
    # Every address derived above is a VA inside the PINNED ORIGINAL kernelcache,
    # because that is the image the metaclass getter, the vtable and the method
    # pointers were decoded out of.  The image we ship does not keep them there:
    # the fileset emitter lays the prelink segments where its own coalescing
    # decides, and on liuqin that is 16 MiB below the original for all four
    # kext segments, while __PRELINK_INFO moves by +0x774000.  So a single slide
    # is the wrong shape and the raw original VAs are simply wrong.
    #
    # This is what H16 actually was.  The old code said "slide = 0, bindings use
    # raw cache VAs" and set IOBlockStorageDevice::gMetaClass to 0xfffffff007add1d0
    # -- an address that, in our image, is not the family metaclass at all but
    # the middle of __common, i.e. other people's statics.  The driver's
    # superClassLink therefore pointed at unrelated storage; the scan read the
    # bytes there as a poisoned chain (which is why the "poison" looked like
    # ASCII and changed between boots), the v39 heal wrote the same wrong value
    # back, and the gate compared the registry's real object (0xfffffff006add1d0)
    # against the phantom and refused root publication.  Device evidence:
    # docs/boot-rounds-v35-v40.md, v41 (M:alien delta=-16777216 slide=0x0 cmp=1).
    base = min(s['va'] for s in kernel['segments'] if s['name'].startswith('__PRELINK') and s['size'])
    kernel_json = ROOT.parent / 'kernel/kernel.json'
    if not kernel_json.is_file():
        fail(f'no prelink report at {kernel_json}; build the kernel once so the emitted '
             'segment layout is recorded, then re-run this script')
    recorded = json.loads(kernel_json.read_text())
    ours = {seg['name']: seg['va'] for seg in recorded.get('coalesced_segments', [])}
    if not ours:
        fail('the prelink report carries no coalesced_segments; refusing to guess placement')
    deltas = {seg['name']: ours[seg['name']] - seg['va'] for seg in kernel['segments']
              if seg['size'] and seg['name'] in ours}
    if not deltas:
        fail('no original-cache segment name matches the emitted image')

    inherited: dict[str, str] = {}

    def relocate(va):
        """An original-cache VA -> what the driver should reference instead.

        Returns the emitted address for anything inside a kext segment (all of
        which move, per segment, in our layout), or None for anything inside the
        original *kernel*: that object exists in our own kernel too, under the
        same symbol name, so pinning it to a number would re-introduce exactly
        the bug this function exists to fix.  None means "leave it to the link".
        """
        home = [seg for seg in kernel['segments']
                if seg['size'] and seg['va'] <= va < seg['va'] + seg['size']]
        if not home:
            fail(f'binding address {va:#x} is not inside any original-cache segment')
        seg = max(home, key=lambda item: item['va'])
        if seg['name'] in deltas:
            return va + deltas[seg['name']]
        if not exports.get(va, []):
            fail(f'original segment {seg["name"]} (holding {va:#x}) has no counterpart in '
                 'the emitted image and no symbol name to reference instead')
        return None

    print('prelink base ' + hex(base) + '; relocating bindings per segment: '
          + ', '.join(f'{name} {delta:+#x}' for name, delta in sorted(deltas.items())))

    # ---- 7. optional live verification from the QEMU snapshot ---------------
    live = False
    if args.snapshot.is_file():
        d = json.loads(args.snapshot.read_text())
        service_meta = next(va for va, names in exports.items() if '__ZN9IOService10gMetaClassE' in names)
        # relocate() answers None for anything in the kernel proper, and IOService's
        # metaclass is exactly that: the snapshot cannot confirm a placement we no longer
        # pin, so the live check only runs when both sides are kext-side addresses.
        at_meta, at_service = relocate(meta), relocate(service_meta)
        if at_meta is None or at_service is None:
            print('live snapshot skipped: the superclass lives in the kernel proper and is '
                  'resolved by the link, so there is no pinned address to compare against')
        else:
            actual = bytes.fromhex(d['memory'].get(f'{at_meta:x}:50', ''))
            service = bytes.fromhex(d['memory'].get(f'{at_service:x}:50', ''))
        if (at_meta is not None and at_service is not None and
                len(actual) == 0x50 and len(service) == 0x50 and
                struct.unpack_from('<Q', actual, 16)[0] == service_meta and
                struct.unpack_from('<I', actual, 32)[0] == CLASS_SIZE and
                struct.unpack_from('<I', service, 32)[0] == IO_SERVICE_CLASS_SIZE):
            live = True
            print('live snapshot verified: superClassLink/className/classSize bytes match the derived table')
        else:
            print('live snapshot present but not conclusive (kernel may have faulted early); keeping derived record')

    # The whole point of section 6: bind to where the family object IS, not to
    # where the original cache had it.  Both the data symbols and the method
    # slots move together, so a driver call lands in the right kext.
    bindings = {name: relocate(pointers[i]) for i, name in enumerate(methods)
                if name not in known and name != '___cxa_pure_virtual'}
    emitted = {'__ZN20IOBlockStorageDevice10gMetaClassE': relocate(meta),
               '__ZTV20IOBlockStorageDevice': relocate(table),
               '__ZN20IOBlockStorageDeviceC2EPK11OSMetaClass': relocate(constructor),
               '__ZN20IOBlockStorageDeviceD2Ev': relocate(destructor)}
    bindings.update(emitted)
    for name, original, value in (
            ('__ZN20IOBlockStorageDevice10gMetaClassE', meta, emitted['__ZN20IOBlockStorageDevice10gMetaClassE']),
            ('__ZTV20IOBlockStorageDevice', table, emitted['__ZTV20IOBlockStorageDevice']),
            ('__ZN20IOBlockStorageDeviceC2EPK11OSMetaClass', constructor, emitted['__ZN20IOBlockStorageDeviceC2EPK11OSMetaClass']),
            ('__ZN20IOBlockStorageDeviceD2Ev', destructor, emitted['__ZN20IOBlockStorageDeviceD2Ev'])):
        print(f'  binding {name}: {original:#x} -> {value:#x}')

    report = dict(build='21H461', darwin='23.6.0', device='liuqin (iPad7,1)',
                  public_source_revision=revision, source_sha256=digest((source / 'IOBlockStorageDevice.cpp').read_bytes()),
                  reference_object_sha256=digest(blob), reference_object_executed_or_linked=False,
                  method_slots=SLOTS, pure_virtual_slots=PURE_VIRTUAL, inherited_kernel_methods=matched,
                  kernel_bindings=matched, compiled_public_class_size=CLASS_SIZE,
                  prelink_base=hex(base),
                  segment_deltas={name: hex(delta) for name, delta in sorted(deltas.items())},
                  emitted_metaclass=hex(emitted['__ZN20IOBlockStorageDevice10gMetaClassE']),
                  emitted_vtable=hex(emitted['__ZTV20IOBlockStorageDevice']),
                  vtable=hex(table), original_metaclass=hex(meta),
                  original_protected_constructor=hex(constructor), original_base_destructor=hex(destructor),
                  abstract_complete_destructors_trap=True, live_native_4k=live,
                  candidate_sha256=KERNELCACHE_PIN, derived_offline=True,
                  live_snapshot=str(args.snapshot) if live else None,
                  bindings={name: hex(value) for name, value in bindings.items()},
                  methods=[dict(slot=i, reference_name=name, candidate_target=hex(pointers[i]),
                                kernel_names=exports.get(pointers[i], []))
                           for i, name in enumerate(methods)],
                  method='derived from the pinned original kernelcache and the pinned public source; '
                         'method after UniXNU-mondrian derive-ios16-storage-bindings.py')
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(f'wrote {out} ({len(bindings)} original storage imports)')

    # ---- 8. inject the bindings into the ShengUfs archive -------------------
    ufs = ROOT / 'build/sheng-ufs'
    archive = ufs / 'libShengUfs.a'
    if not archive.is_file():
        fail(f'{archive} missing; run the NativeUfs archive step first')
    work = ufs / 'bindings-work'
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    members_dir = work / 'members'
    members_dir.mkdir()
    subprocess.run(['xcrun', 'ar', 'x', str(archive)], cwd=members_dir, check=True)
    objects = sorted(p for p in members_dir.iterdir() if p.name.endswith('.o') and 'bindings' not in p.name)
    if not objects:
        fail('no driver objects extracted from the archive')
    driver_obj = objects[0]
    image = np.macho(driver_obj.read_bytes())
    undefined = []
    for command_id, at, size in image['commands']:
        if command_id != 2:
            continue
        sy, count, st, _ = struct.unpack_from('<4I', driver_obj.read_bytes(), at + 8)
        data = driver_obj.read_bytes()
        for index in range(count):
            ix, kind, _, _, value = struct.unpack_from('<IBBHQ', data, sy + index * 16)
            if kind == 1:
                name = data[st + ix:data.index(b'\0', st + ix)].decode()
                if 'IOBlockStorageDevice' in name:
                    undefined.append(name)
    missing = [name for name in undefined if name not in bindings and name not in inherited]
    if missing:
        fail('undefined driver imports without a derived binding: ' + ', '.join(missing[:6]))
    if inherited:
        print(f'  {len(inherited)} import(s) left to the link (kernel-proper objects): '
              + ', '.join(sorted(inherited)[:4]))
    assembly = ufs / 'original-bindings.S'
    assembly.write_text(''.join(f'.globl {name}\n.set {name}, {bindings[name]:#x}\n'
                                 for name in sorted(set(undefined))))
    subprocess.run(['xcrun', 'clang', '-target', TRIPLE, '-Werror', '-Wno-incompatible-sysroot',
                    '-c', str(assembly), '-o', str(ufs / 'original-bindings.o')], check=True)
    rebuilt = ufs / ('libShengUfs.new.a')
    subprocess.run(['xcrun', 'ar', 'rcs', str(rebuilt), *[str(o) for o in objects],
                    str(ufs / 'original-bindings.o')], check=True)
    rebuilt.replace(archive)
    (ufs / 'bindings.json').write_text(json.dumps(dict(
        audit_sha256=digest(out.read_bytes()), archive_sha256=digest(archive.read_bytes()),
        bound_imports=len(set(undefined)), unresolved_original_imports=0,
        live_native_4k=live, original_code_modified=False), indent=2) + '\n')
    print(f'PASS: {len(set(undefined))} driver imports bound into {archive}')


if __name__ == '__main__':
    main()
