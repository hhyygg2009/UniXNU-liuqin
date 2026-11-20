#!/usr/bin/env python3
"""Prove the trust-cache line is really in the image -- presence AND absence, like the audit.

This is the guard for a configuration flip (CONFIG_XNU_TRUST_CACHE_RUNTIME=y), and the lesson of
H17 is that a defconfig edit can be silently inert.  So every claim here is read out of the built
kernel, the payload and the bundle -- never out of a config file:

    K+ / K-   the branch that compiled (bootstrap string in, H17 lock string out)
    B         kernel_bootstrap_thread calls trust_cache_runtime_init AND load_static_trust_cache
    L         the lock that H17 hand-initialised is initialised inside trust_cache_runtime_init
    Q         load_static_trust_cache reaches AMFI through the interface table (blr), not a stub
    P         the runtime-init panic strings are in the image (they are what the panel will say)
    T         payload.json carries the bundle identity and the v44 argument policy
    C         the bundle bytes are physically inside payload.img
    A         the ADT memory-map TrustCache property is the 16 zero bytes DarwinBoot requires

Run after:  ./unixnu build kernel && ./unixnu build payload --trust-cache ...

    python3 scripts/check-trust-cache-image.py [--report]
"""
import argparse
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location('audit', ROOT / 'scripts/audit-h16f-image.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

BOOTSTRAP_ON = b'trust_cache_init\0'          # kernel_bootstrap_log of the #if branch
BOOTSTRAP_OFF = b'trust_cache_lock_init\0'    # the H17 milestone of the #else branch
# XNU bakes the site into the literal: panic() strings are stored as "message @%s:%d", so these
# needles stop before the format suffix -- searching for the message plus a NUL finds nothing.
RUNTIME_MESSAGES = [
    b'image4 interface not available',           # kern_trustcache.c:472, the mondrian landmine
    b'amfi interface not available',             # :477
    b'amfi interface is stale: %u',              # :479, AMFI's TrustCache.version < 2
    b'unable to load static trust cache: interface not supported',   # load_static_trust_cache, version < 1
    b'no external trust caches found (segment length is zero)',      # the loader never filled the ADT entry
]
CALL = re.compile(r'\tbl\t?(_?[A-Za-z_][\w.]*)')


def board():
    return tomllib.loads((ROOT / 'board.toml').read_text())


def adt_trustcache_property(data):
    """Every memory-map `TrustCache` property in a blob.

    make-liuqin-adt.py writes properties as a fixed 32-byte name field + u32 length + value
    (`struct.pack('<32sI', key, len(data))`), and DarwinBoot.AdtNode reads the same shape -- that
    is why the value must be exactly 16 zero bytes: the loader refuses to touch a placeholder that
    is not empty (DarwinBoot.c:911) and the kernel reads it as DTTrustCacheRange{paddr,length}.
    """
    hits = []
    at = data.find(b'TrustCache\0')
    while at != -1:
        if at + 36 <= len(data) and data[at + 10:at + 32] == bytes(22):
            value_len, = struct.unpack_from('<I', data, at + 32)
            if value_len == 16 and at + 36 + 16 <= len(data):
                hits.append(data[at + 36:at + 52])
        at = data.find(b'TrustCache\0', at + 1)
    return hits


def main():
    ap = argparse.ArgumentParser(description=sys.modules['__main__'].__doc__.splitlines()[0])
    ap.add_argument('--kernel', default=str(ROOT / '../UniXNU/out/liuqin/kernel/kernel.macho'))
    ap.add_argument('--payload', default=str(ROOT / '../UniXNU/out/liuqin/payload/payload.img'))
    ap.add_argument('--meta', default=str(ROOT / '../UniXNU/out/liuqin/payload/payload.json'))
    ap.add_argument('--adt', default=str(ROOT / '../UniXNU/out/liuqin/payload/stage/EFI/UniXNU/Payloads/kernel.macho.adt'))
    ap.add_argument('--bundle', default=str(ROOT / '.cache/firmware/trustcache/original-static-system.bundle'))
    ap.add_argument('--report', action='store_true', help='print a JSON summary at the end')
    args = ap.parse_args()

    kernel, payload = Path(args.kernel), Path(args.payload)
    if not kernel.is_file():
        raise SystemExit(f'no kernel at {kernel} (run ./unixnu build kernel first)')
    failures, summary = [], {'checks': 0}

    def check(label, ok, detail=''):
        summary['checks'] += 1
        print(f'{"PASS" if ok else "FAIL"}  {label}' + (f'  {detail}' if detail else ''))
        if not ok:
            failures.append(label)

    blob = kernel.read_bytes()
    nm = subprocess.run(['llvm-nm', str(kernel)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True).stdout
    addrs = {}
    for line in nm.splitlines():
        parts = line.split()
        if len(parts) == 3 and len(parts[0]) == 16:
            addrs[parts[2]] = int(parts[0], 16)

    # --------------------------------------------------------------- K branch identity
    check('K+ the trust_cache_init bootstrap string is in the image (the #if branch compiled)',
          blob.count(BOOTSTRAP_ON) >= 1, f'{blob.count(BOOTSTRAP_ON)}x')
    check('K- the H17 trust_cache_lock_init milestone is gone (the #else branch did not compile)',
          blob.count(BOOTSTRAP_OFF) == 0, f'{blob.count(BOOTSTRAP_OFF)}x')

    # --------------------------------------------------------------- the disassembly
    windows, missing = [], []
    for sym, fallback in (('_kernel_bootstrap_thread', 0x4000), ('_trust_cache_runtime_init', 0x200),
                          ('_load_static_trust_cache', 0x800)):
        if sym not in addrs:
            missing.append(sym)
            continue
        lo, hi = audit.window_of(addrs, addrs[sym], fallback)
        windows.append((lo, hi, sym))
    if missing:
        check('B  every symbol this guard needs is in the image', False, ', '.join(missing))
    else:
        check('B  every symbol this guard needs is in the image', True,
              ', '.join(f'{s}={addrs[s]:#x}' for _, _, s in windows))
    out = audit.disassemble(kernel, windows)
    boot = out.get('_kernel_bootstrap_thread', [])
    rt_init = out.get('_trust_cache_runtime_init', [])
    load_static = out.get('_load_static_trust_cache', [])

    calls_boot = {m.group(1) for line in boot for m in [CALL.search(line)] if m}
    check('B  kernel_bootstrap_thread calls _trust_cache_runtime_init',
          '_trust_cache_runtime_init' in calls_boot, f'{len(calls_boot)} calls')
    check('B  kernel_bootstrap_thread calls _load_static_trust_cache',
          '_load_static_trust_cache' in calls_boot)

    # the lock: same operand proof the H17 audit asked for, now in the function that owns it
    grp = addrs.get('__ZL20trust_cache_lck_grp') or addrs.get('_trust_cache_lck_grp')
    lock = addrs.get('_trust_cache_rt_lock')
    if not lock or not grp:
        check('L  the trust-cache lock and group symbols exist', False,
              f'lock={lock and hex(lock)} grp={grp and hex(grp)}')
    else:
        pair = audit.materialises(rt_init, lock)
        tail = any(re.search(r'\bbl\n?\t_lck_rw_init|\bb\t?_lck_rw_init', line) for line in rt_init)
        built_grp = audit.materialises(rt_init, grp)
        check('L  trust_cache_runtime_init builds &trust_cache_rt_lock and calls lck_rw_init',
              pair[0] >= 0 and tail, f'lock at {lock:#x} adrp/add={pair}, tail-call lck_rw_init={tail}')
        check('L  ... with &trust_cache_lck_grp as the group', built_grp[0] >= 0,
              f'group at {grp:#x} adrp/add={built_grp}')

    # the query path still answers through AMFI, with the same NULL guard the audit proved
    calls_static = {m.group(1) for line in load_static for m in [CALL.search(line)] if m}
    check('Q  load_static_trust_cache reads the device tree (SecureDTGetProperty)',
          any('SecureDTGetProperty' in c for c in calls_static), ', '.join(sorted(calls_static)[:4]))
    check('Q  ... and maps the segment with phystokv',
          any('phystokv' in c for c in calls_static))
    check('Q  load_static_trust_cache calls out through a loaded interface (blr)',
          any(re.search(r'\bblr\t?x\d+', line) for line in load_static))

    for s in RUNTIME_MESSAGES:
        check(f'P  message {s.decode(errors="replace")!r} is in the image (the panel can say it)',
              blob.count(s) >= 1, f'{blob.count(s)}x')

    # --------------------------------------------------------------- the payload side
    meta = json.loads(Path(args.meta).read_text()) if Path(args.meta).is_file() else {}
    pins = board()['payload'].get('trust_cache_sha256') or []
    bundle = Path(args.bundle)
    if not bundle.is_file():
        check('T  the verified bundle exists', False, f'{bundle} missing: run scripts/fetch-liuqin-trust-caches.py')
    else:
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        check('T  the bundle on disk is one of the board pins', digest in pins, digest[:16])
        check('T  payload.json reports that same bundle', meta.get('trust_cache_sha256') == digest,
              str(meta.get('trust_cache_sha256'))[:16])
        if payload.is_file():
            head = bundle.read_bytes()[:256]
            check('C  the bundle bytes are physically inside payload.img',
                  payload.read_bytes().find(head) != -1,
                  f'at {payload.read_bytes().find(head):#x}' if payload.is_file() else 'no payload')
    check('T  the refused argument stayed out (cs_enforcement_disable)',
          'cs_enforcement_disable=1' not in meta.get('kernel_args', ''), 'absent from kernel_args')
    check('T  the bring-up switch is still there (one variable per round)',
          'amfi_get_out_of_my_way=1' in meta.get('kernel_args', ''),
          f"{len(meta.get('kernel_args', ''))} bytes of args")
    adt_blob = Path(args.adt).read_bytes() if Path(args.adt).is_file() else (
        payload.read_bytes() if payload.is_file() else b'')
    props = adt_trustcache_property(adt_blob)
    check('A  the shipped ADT carries a 16-byte zeroed memory-map TrustCache placeholder',
          any(p == bytes(16) for p in props),
          f'{len(props)} candidate(s), ' + ('zeroed: the loader will fill it' if any(p == bytes(16) for p in props) else 'none zeroed'))

    print()
    if failures:
        print(f'TRUST-CACHE IMAGE CHECK FAILED ({len(failures)}/{summary["checks"]}): '
              + '; '.join(failures))
    else:
        print(f'TRUST-CACHE IMAGE CHECK PASS ({summary["checks"]} checks): the =y branch is '
              f'compiled, it loads the bundle the payload carries, and the refused argument is out.')
    if args.report:
        summary['failures'] = failures
        summary['kernel_sha256'] = hashlib.sha256(blob).hexdigest()
        summary['payload_kernel_sha256'] = meta.get('kernel_sha256')
        summary['trust_cache_sha256'] = meta.get('trust_cache_sha256')
        print(json.dumps(summary, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
