#!/usr/bin/env python3
"""Build the payload's trust-cache bundle from the target IPSW, and prove it is the original.

The bundle is the sidecar DarwinBoot loads as ``kernel.macho.trustcache``: module 0 is the IPSW's
static trust cache, module 1 is ``Cryptex1,SystemTrustCache``.  Both are taken byte for byte out of
the restore image -- no re-encoding, no hash edits -- because ``board.toml`` [payload]
``trust_cache_sha256`` pins the bundle and ``unixnu build payload --trust-cache`` refuses any file
whose sha256 is not one of those pins.  So this script is the gate for the whole trust-cache line:
if it says PASS, the caches can be shipped; if it says FAIL, the pins are wrong (or stale) and the
line stops here rather than on the panel.

    python3 scripts/fetch-liuqin-trust-caches.py                 # find the IPSW, write the bundle
    python3 scripts/fetch-liuqin-trust-caches.py --ipsw PATH     # explicit restore image
    python3 scripts/fetch-liuqin-trust-caches.py --report -      # JSON to stdout

Inputs: the IPSW named by board.toml [ipsw] (default search: $UNIXNU/out/ipsw, ../UniXNU/out/ipsw,
.cache/ipsw).  Output: .cache/firmware/trustcache/original-static-system.bundle (+ .json).
Nothing here touches a device.
"""
import argparse
import hashlib
import json
import plistlib
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def board():
    import tomllib
    return tomllib.loads((ROOT / 'board.toml').read_text())


def der(data, at):
    """One DER element: (tag, body, next offset)."""
    if at + 2 > len(data):
        raise ValueError('truncated DER')
    tag, size = data[at:at + 2]
    at += 2
    if size & 128:
        count = size & 127
        if not 0 < count <= 4 or at + count > len(data):
            raise ValueError('invalid DER length')
        size = int.from_bytes(data[at:at + count], 'big')
        at += count
    if at + size > len(data):
        raise ValueError('DER exceeds input')
    return tag, data[at:at + size], at + size


def im4p_payload(data):
    """Unwrap an IM4P to its raw payload; accept an already-unwrapped module unchanged."""
    if data[:1] != b'\x30':
        return data
    tag, sequence, end = der(data, 0)
    fields, cursor = [], 0
    while cursor < len(sequence):
        kind, value, cursor = der(sequence, cursor)
        fields.append((kind, value))
    if tag != 0x30 or end != len(data) or fields[0][1] != b'IM4P':
        raise ValueError('not an IM4P')
    return fields[3][1]


def check_module(data, label):
    version, = struct.unpack_from('<I', data)
    count, = struct.unpack_from('<I', data, 20)
    if version != 2 or len(data) != 24 + count * 24:
        raise SystemExit(f'{label}: not an exact version-2 trust module '
                         f'(version {version}, {len(data)} bytes, {count} entries)')
    return count


def default_search_dirs():
    dirs = []
    env = __import__('os').environ.get('UNIXNU')
    if env:
        dirs.append(Path(env) / 'out/ipsw')
    for guess in (ROOT.parent / 'UniXNU/out/ipsw', ROOT / '.cache/ipsw', ROOT.parent / 'ipsw'):
        dirs.append(guess)
    return dirs


def main():
    parser = argparse.ArgumentParser(description=sys.modules['__main__'].__doc__.splitlines()[0])
    parser.add_argument('--ipsw', type=Path, help='the restore image (default: search out/ipsw)')
    parser.add_argument('--output', type=Path, help='bundle path (default: .cache/firmware/trustcache)')
    parser.add_argument('--report', type=Path, help='write the JSON report here ("-" for stdout)')
    args = parser.parse_args()

    spec = board()
    ipsw_spec, payload_spec = spec['ipsw'], spec['payload']
    url, product, build = ipsw_spec['url'], ipsw_spec['product'], ipsw_spec['build']
    file_name = Path(url).name

    path = args.ipsw
    if path is None:
        for directory in default_search_dirs():
            guess = directory / file_name
            if guess.is_file():
                path = guess
                break
    if path is None or not path.is_file():
        raise SystemExit(f'no restore image for {file_name}: pass --ipsw PATH '
                         f'(searched: {", ".join(str(d) for d in default_search_dirs())})')

    with zipfile.ZipFile(path) as archive:
        manifest = plistlib.loads(archive.read('BuildManifest.plist'))
        if manifest['ProductBuildVersion'] != build or product not in manifest['SupportedProductTypes']:
            raise SystemExit(f'{path.name} is {manifest["ProductVersion"]}/'
                             f'{manifest["ProductBuildVersion"]} for {manifest["SupportedProductTypes"]}, '
                             f'board.toml wants {product} {build}')
        identities = [i for i in manifest['BuildIdentities']
                      if i['Info']['DeviceClass'] == ipsw_spec['device_class']]
        wanted = ['StaticTrustCache', 'Cryptex1,SystemTrustCache']
        modules = []
        for component in wanted:
            entry = next((i['Manifest'][component]['Info']['Path'] for i in identities
                          if component in i['Manifest']), None)
            if entry is None:
                raise SystemExit(f'{component} is not in the {ipsw_spec["device_class"]} build identities')
            raw = archive.read(entry)
            module = im4p_payload(raw)
            count = check_module(module, component)
            if len({i['Manifest'][component]['Info']['Path'] for i in identities if component in i['Manifest']}) != 1:
                raise SystemExit(f'{component} differs between build identities; refuse to guess')
            modules.append((component, entry, module, count))
            print(f'{component:26s} {entry}  {len(module)} bytes, {count} entries, '
                  f'sha256 {hashlib.sha256(module).hexdigest()[:16]}')

    header = 4 + 4 * len(modules)
    offsets, cursor = [], header
    for _, _, module, _ in modules:
        offsets.append(cursor)
        cursor += len(module)
    bundle = struct.pack(f'<{1 + len(modules)}I', len(modules), *offsets) + \
        b''.join(module for _, _, module, _ in modules)
    digest = hashlib.sha256(bundle).hexdigest()

    allowed = payload_spec.get('trust_cache_sha256') or []
    pinned = digest in allowed
    out = args.output or (ROOT / '.cache/firmware/trustcache')
    out.mkdir(parents=True, exist_ok=True)
    bundle_path = out / 'original-static-system.bundle'
    report_path = out / 'original-static-system.bundle.json'
    bundle_path.write_bytes(bundle)
    report = {
        'ipsw': str(path.resolve()), 'ipsw_bytes': path.stat().st_size,
        'product': product, 'build': build, 'device_class': ipsw_spec['device_class'],
        'modules': [{'component': c, 'member': m, 'bytes': len(d), 'entries': n,
                     'sha256': hashlib.sha256(d).hexdigest()} for c, m, d, n in modules],
        'bundle': str(bundle_path.resolve()), 'bundle_bytes': len(bundle),
        'bundle_sha256': digest, 'board_pins': allowed, 'pin_match': pinned,
        'matched_pin_index': allowed.index(digest) if pinned else None,
        'unexplained_pins': [p for p in allowed if p != digest],
        'modified': False, 'loader_contract': 'DarwinBoot ValidTrustCaches, version-2 modules only',
    }
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    if args.report:
        text = json.dumps(report, indent=2)
        if str(args.report) == '-':
            print(text)
        else:
            args.report.write_text(text + '\n')

    print(f'bundle: {bundle_path} ({len(bundle)} bytes) sha256 {digest}')
    if not pinned:
        raise SystemExit('FAIL: the bundle sha256 is not one of board.toml [payload] '
                         'trust_cache_sha256 -- the pin and the IPSW disagree, do not ship this')
    print(f'PASS: matches board.toml pin #{allowed.index(digest) + 1}; payload may carry it')
    others = [p for p in allowed if p != digest]
    if others:
        print('note: the other pin(s) ' + ', '.join(o[:12] + '...' for o in others) +
              ' are NOT produced by these inputs (they come from the shared OS profile '
              'profiles/os/ipados-17.7.11-21H461/profile.toml; nothing in either repository hashes to them). '
              'They are accepted by the board so an owner-signed bundle built elsewhere can also ship; '
              'this script makes no claim about them.')
    print(f'use:  ./unixnu build payload --kernel out/liuqin/kernel/kernel.macho '
          f'--unique-chip-id 0x84750A0B0C0D0001 --trust-cache {bundle_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
