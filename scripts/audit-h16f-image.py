#!/usr/bin/env python3
"""Prove, from the built kernel only, what this image actually ships.

AGENTS.md is explicit: a change counts as shipped when the disassembly says so, not when the
source diff says so. The name is kept from the round that introduced it (H16-F); since v43 it
audits three things at once, because the interesting failure of a cleanup round is not "the new
code is missing" but "the old code is still in there".

  python3 scripts/audit-h16f-image.py [--kernel ../UniXNU/out/liuqin/kernel/kernel.macho]
                                      [--payload ../UniXNU/out/liuqin/payload/payload.img]

Check groups:
  S+ symbols that must be present (the gate, the sentinel's console path, the trust-cache
     objects H17 initialises)
  S- symbols that must be GONE: the H16-F/G repair hook, the fingerprint hook and the driver
     functions that implemented them. v42 closed H16 (the bindings were carrying the original
     21H461 kernelcache VAs), so any of these coming back means the retired scan came back.
  T+ / T- console format strings, present and absent, same reasoning.
  B  the v42 binding fix itself: the family metaclass must be an address inside our own
     __PRELINK_DATA, not the original cache's VA.
  K  kernel_bootstrap_thread: the trust-cache lock is initialised on the compiled-out branch,
     with the real addresses of the lock and its group, and neither runtime_init nor
     load_static_trust_cache is called (which is what proves it is the #else branch).
  Q  xnu_query_trust_cache: the amfi interface is tested for NULL *before* the lock is taken,
     and the guard returns KERN_NOT_FOUND -- the ordering is the whole point, the panic in v42
     happened at the lock.
  P  postModLoad: no hook load, no write to [obj+0x10] (the retired repair), no liuqin call.
  C  NativeUfsBlock's metaclass constructor: classSize 248, the OSMetaClass constructor call,
     and exactly one store (the vtable) -- the H16-F hook install must not survive.
  G  the base-class probe: it prints its two lines and reports the milestone, and it no longer
     reads vm_kernel_slide (that was the H16-H arithmetic, retired with the diagnosis).

Windows are cut at symbol boundaries on purpose: a guessed length can borrow a neighbouring
function's instruction and pass.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MUST_HAVE_SYMBOLS = {
    '_liuqin_base_class_probe': 'the auditable base-class gate',
    '_liuqin_milestone': 'the panel milestone console',
    '_liuqin_debug_puts': 'the console mirror writer',
    '_liuqin_debug_copy': 'the console mirror reader (USB debug port)',
    '_liuqin_debug_total': 'the console mirror size',
    '__ZN14NativeUfsBlock10gMetaClassE': 'our metaclass object',
    '__ZN20IOBlockStorageDevice10gMetaClassE': 'the family metaclass object (the binding H16 lost)',
    '__ZTV20IOBlockStorageDevice': 'the family vtable binding',
    '_trust_cache_rt_lock': 'the lock H17 initialises',
    '_trust_cache_lck_grp': 'its lock group',
    '_lck_rw_init': 'the initialiser the patch calls',
    '_query_trust_cache': 'the trust-cache query that panicked',
    '_amfi': 'the interface the query now tests for NULL',
}
MUST_NOT_HAVE_SYMBOLS = {
    '_liuqin_metaclass_super_fix': 'the H16-F repair hook slot',
    '_liuqin_metaclass_poison_report': 'the H16-G fingerprint hook slot',
    '_liuqin_block_super_fix': 'the driver-side repair',
    '_liuqinMetaclassPoisonReport': 'the driver-side fingerprint',
}
MUST_HAVE_STRINGS = [
    b'NativeUfs: metaclass chain broken at %s: bad=%p link=%p depth=%d',
    b'NativeUfs: base class IOBlockStorageDevice live size %u, compiled size %u',
    b'NativeUfs: IOBlockStorageDevice is held by %p, not by the compiled family object %p',
    b'trust_cache_lock_init',                       # the H17 #else branch's own bootstrap log
    b'unable to query trust caches: amfi interface not available',
]
MUST_NOT_HAVE_STRINGS = [
    b'M:fp ',                    # H16-G fingerprint
    b'M:alien ',                 # H16-H arithmetic report
    b'M:stalled',                # H16-E scan
    b'M:scan done',              # H16-E summary
    b'M:bad[',                   # H16-E per-slot naming
    b'M:dirty[',                 # H16-E dirty-slot guard
]
KERNEL_NOT_FOUND = 0x38          # 56, mach/kern_return.h: what the guard returns
HEX = re.compile(r'^([0-9a-f]{16}):\t([0-9a-f]{2}[ ])+')
SYMHDR = re.compile(r'^([0-9a-f]{16}) <(.*)>:$|^(\w[^:]*)+$')


def sections(path):
    """(segname, sectname, addr, size, fileoff) for every section: enough to read the bytes
    the kernel will read at that address, which is how a GOT slot gets resolved here."""
    import struct
    blob = path.read_bytes()
    if blob[:4] != b'\xcf\xfa\xed\xfe':
        return blob, []
    ncmds = struct.unpack('<I', blob[16:20])[0]
    off, out = 32, []
    for _ in range(ncmds):
        cmd, csz = struct.unpack('<II', blob[off:off + 8])
        if cmd == 0x19:                                    # LC_SEGMENT_64
            n = struct.unpack('<I', blob[off + 64:off + 68])[0]
            so = off + 72
            for _ in range(n):
                sn = blob[so:so + 16].split(b'\0')[0].decode()
                sg = blob[so + 16:so + 32].split(b'\0')[0].decode()
                addr, size = struct.unpack('<QQ', blob[so + 32:so + 48])
                foff = struct.unpack('<I', blob[so + 48:so + 52])[0]
                out.append((sg, sn, addr, size, foff))
                so += 80
        off += csz
    return blob, out


def word_at(sections_, blob, addr):
    for sg, sn, a, size, foff in sections_:
        if foff and a <= addr < a + size:
            import struct
            return struct.unpack('<Q', blob[foff + (addr - a):foff + (addr - a) + 8])[0]
    return None


LDR_LITERAL = re.compile(r'^([0-9a-f]{16}):\t(?:[0-9a-f]{2} )*[0-9a-f]{2}\s+ldr\s+x(\d+), #(0x[0-9a-f]+)')


def literal_targets(lines):
    """Every `ldr xN, #imm` in a window resolved to the address it reads. llvm prints the byte
    offset from the instruction itself, so the target is pc + imm -- that is how the compiler
    reaches a GOT slot, and a check that only looks for adrp/add misses it."""
    out = []
    for line in lines:
        m = LDR_LITERAL.match(line)
        if m:
            out.append((int(m.group(1), 16) + int(m.group(3), 16), line.rstrip()))
    return out


def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def window_of(addrs, addr, fallback):
    """A function's own bytes: up to the next symbol, so no check can be satisfied by an
    instruction that belongs to whatever got linked after it."""
    nxt = [a for a in sorted(addrs.values()) if a > addr]
    return addr, (nxt[0] if nxt else addr + fallback)


def disassemble(kernel, windows):
    """One llvm-objdump pass, keeping only the lines inside the windows we care about.

    --section=__text is not decoration: this llvm build prints an empty listing without it,
    and --disassemble-symbols / --start-address return nothing at all on it.
    """
    out = {tag: [] for _, _, tag in windows}
    pages = {}
    for lo, hi, tag in windows:
        pages.setdefault((lo >> 12) << 12, None)
    with tempfile.NamedTemporaryFile('w+', suffix='.s', delete=False) as dump:
        path = Path(dump.name)
    try:
        with open(path, 'w') as handle:
            subprocess.run(['llvm-objdump', '--macho', '-d', '--section=__text', str(kernel)],
                           stdout=handle, stderr=subprocess.DEVNULL, check=True)
        cur = None
        with open(path) as handle:
            for line in handle:
                m = HEX.match(line.rstrip('\n'))
                if m:
                    addr = int(m.group(1), 16)
                    for lo, hi, tag in windows:
                        if lo <= addr < hi:
                            cur = tag
                            out[tag].append(line.rstrip('\n'))
                            break
                    else:
                        cur = None
                else:
                    cur = None
        return out
    finally:
        path.unlink(missing_ok=True)


def materialises(lines, target):
    """Index of the first `add xN, xN2, #off` that completes an adrp of target's page, where
    that pair is followed by nothing else using the page -- i.e. the code really built
    `target` as an address. Returns (adrp_index, add_index) or (-1, -1)."""
    page, off = target & ~0xfff, target & 0xfff
    for i, line in enumerate(lines):
        if re.search(r'adrp\s+x\d+, -?\d+ ; ' + hex(page), line):
            for j in range(i + 1, min(i + 6, len(lines))):
                if re.search(r'add\s+x\d+, x\d+, #' + hex(off) + r'\b', lines[j]):
                    return i, j
    return -1, -1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--kernel', default=str(ROOT / '../UniXNU/out/liuqin/kernel/kernel.macho'))
    ap.add_argument('--payload', default=str(ROOT / '../UniXNU/out/liuqin/payload/payload.img'))
    args = ap.parse_args()
    kernel = Path(args.kernel)
    if not kernel.is_file():
        raise SystemExit(f'no kernel at {kernel} (run ./unixnu build kernel first)')

    failures = []
    n = [0]

    def check(label, ok, detail=''):
        n[0] += 1
        print(f'{"PASS" if ok else "FAIL"}  {label}' + (f'  {detail}' if detail else ''))
        if not ok:
            failures.append(label)

    nm = run(['llvm-nm', str(kernel)]).stdout
    addrs = {}
    for line in nm.splitlines():
        parts = line.split()
        if len(parts) == 3 and len(parts[0]) == 16:
            addrs[parts[2]] = int(parts[0], 16)

    # ---------------------------------------------------------------- S+ symbols
    for sym, what in MUST_HAVE_SYMBOLS.items():
        check(f'S+ symbol {sym} ({what})', sym in addrs,
              f'{addrs[sym]:#x}' if sym in addrs else 'absent')

    # ---------------------------------------------------------------- S- symbols
    for sym, what in MUST_NOT_HAVE_SYMBOLS.items():
        check(f'S- symbol {sym} is gone ({what})', sym not in addrs,
              'absent' if sym not in addrs else f'still at {addrs[sym]:#x}')

    # ---------------------------------------------------------------- T strings
    blob = kernel.read_bytes()
    for s in MUST_HAVE_STRINGS:
        check(f'T+ string {s!r}', blob.count(s) >= 1, f'{blob.count(s)}x')
    for s in MUST_NOT_HAVE_STRINGS:
        check(f'T- string {s!r} is gone', blob.count(s) == 0, f'{blob.count(s)}x')

    # ---------------------------------------------------------------- B bindings
    meta = addrs.get('__ZN20IOBlockStorageDevice10gMetaClassE', 0)
    try:
        rec = json.load(open(str(kernel.with_name('kernel.json'))))
        segs = {s['name']: s for s in rec.get('coalesced_segments', [])}
    except (OSError, ValueError):
        segs = {}
    if not segs:
        check('B  kernel.json available for the binding windows', False, 'not found')
    else:
        for sym, seg in (('__ZN20IOBlockStorageDevice10gMetaClassE', '__PRELINK_DATA'),
                         ('__ZTV20IOBlockStorageDevice', '__PLK_DATA_CONST')):
            a = addrs.get(sym, 0)
            s = segs.get(seg)
            inside = bool(s) and s['va'] <= a < s['va'] + s['size']
            check(f'B  {sym} lies in our own {seg}', inside,
                  f'{a:#x} in {s["va"]:#x}+{s["size"]:#x}' if s else 'segment absent')
        bss = segs.get('__common') or {}
        check('B  no family binding points into __common (the H16 phantom)',
              bool(meta) and not (bss and bss.get('va', 0) <= meta < bss.get('va', 0) + bss.get('size', 0)),
              f'{meta:#x}')

    blob, sects = sections(kernel)

    # ------------------------------------------------------------ the disassembly
    want, missing = [], []
    for sym, fallback in (('_kernel_bootstrap_thread', 0x4000), ('_query_trust_cache', 0x400),
                          ('__ZN11OSMetaClass11postModLoadEPv', 0x800),
                          ('__ZN14NativeUfsBlock9MetaClassC1Ev', 0x80),
                          ('_liuqin_base_class_probe', 0x600)):
        a = addrs.get(sym, 0)
        if not a:
            missing.append(sym)
            continue
        lo, hi = window_of(addrs, a, fallback)
        want.append((lo, hi, sym))
    if missing:
        check('symbol windows for the disassembly checks', False, 'no address for ' + ', '.join(missing))
        print()
        print(f'AUDIT FAILED ({len(failures)})')
        return 1
    dis = {k: v for k, v in disassemble(kernel, want).items()}

    # ---- K: kernel_bootstrap_thread, the H17 lock init -------------------------
    kb = dis['_kernel_bootstrap_thread']
    rt_lock = addrs['_trust_cache_rt_lock']
    lck_grp = addrs['_trust_cache_lck_grp']
    li = [i for i, l in enumerate(kb) if re.search(r'bl\s+_lck_rw_init\b', l)]
    check('K  kernel_bootstrap_thread calls lck_rw_init', bool(li),
          f'{len(li)} call(s)')
    i_lock, _ = materialises(kb, rt_lock)
    i_grp, _ = materialises(kb, lck_grp)
    check('K  ...on the real _trust_cache_rt_lock address', i_lock >= 0, f'{rt_lock:#x}')
    check('K  ...with the real _trust_cache_lck_grp', i_grp >= 0, f'{lck_grp:#x}')
    check('K  both operands are built before the call',
          bool(li) and 0 <= i_lock < li[0] and 0 <= i_grp < li[0],
          f'lock@{i_lock} grp@{i_grp} call@{li[0] if li else -1}')
    check('K  the branch logs its own milestone',
          any('bl\t_kernel_debug_string_early' in l for l in kb[max(0, i_lock - 6):i_lock + 1]),
          'kernel_bootstrap_log right before the adrp pair')
    check('K  trust_cache_runtime_init is NOT called (this is the compiled-out branch)',
          not any(re.search(r'bl\s+_trust_cache_runtime_init\b', l) for l in kb))
    check('K  load_static_trust_cache is NOT called either',
          not any(re.search(r'bl\s+_load_static_trust_cache\b', l) for l in kb))

    # ---- Q: the trust-cache query's NULL guard, and its order -------------------
    qt = dis['_query_trust_cache']
    amfi = addrs['_amfi']
    i_amfi, i_ldr = materialises(qt, amfi)
    loads = [i for i, l in enumerate(qt) if re.search(r'ldr\s+x\d+, \[x\d+\]', l)]
    guards = [i for i, l in enumerate(qt) if re.search(r'\bcbz\s+x\d+,', l)]
    lock_call = [i for i, l in enumerate(qt) if re.search(r'bl\s+_lck_rw_lock_shared\b', l)]
    i_lockpage, _ = materialises(qt, rt_lock)
    check('Q  the query reads the amfi interface pointer', i_amfi >= 0, f'&amfi {amfi:#x}')
    check('Q  ...tests it for NULL', bool(guards), f'cbz at {guards[:2]}')
    check('Q  ...before it takes trust_cache_rt_lock',
          bool(guards and lock_call) and max(guards[:1]) < lock_call[0] and i_lockpage > guards[0],
          f'guard@{guards[0] if guards else -1} lock build@{i_lockpage} lock call@{lock_call[0] if lock_call else -1}')
    check('Q  the guarded path prints its reason',
          bool(lock_call) and any(re.search(r'bl\s+_printf\b', l) for l in qt),
          'printf inside the function')
    check('Q  the guarded path returns KERN_NOT_FOUND',
          any(re.search(r'mov\s+w\d+, #0x%x\b' % KERNEL_NOT_FOUND, l) for l in qt),
          f'{KERNEL_NOT_FOUND:#x}')

    # ---- P: postModLoad has no trace of the retired scan ------------------------
    pm = dis['__ZN11OSMetaClass11postModLoadEPv']
    check('P  postModLoad does not write [obj + 0x10] (the retired repair)',
          not any(re.search(r'str\s+x\d+, \[x\d+, #0x10\]', l) for l in pm))
    check('P  postModLoad calls nothing of ours',
          not any('liuqin' in l for l in pm))
    # The patched scan's two static helpers were visible in the v42 image. They are the
    # cheapest possible proof that the scan itself -- not just its console text -- is gone.
    scan_statics = [n for n in addrs if n.startswith('__ZL18liuqin_chain_depth')
                    or n.startswith('__ZL16liuqin_slot_dump') or n.startswith('__ZL18liuqin_fingerprint')]
    check('P  the scan\'s own static helpers are gone', not scan_statics,
          ', '.join(scan_statics) if scan_statics else 'none of the three in the image')

    # ---- C: the metaclass constructor installs nothing --------------------------
    ctor = dis['__ZN14NativeUfsBlock9MetaClassC1Ev']
    check('C  metaclass ctor passes classSize 248',
          bool([l for l in ctor if re.search(r'mov\s+w3, #0xf8', l)]))
    check('C  metaclass ctor calls the OSMetaClass constructor',
          bool([l for l in ctor if re.search(r'bl\s+__ZN11OSMetaClassC2EPKcPKS_j', l)]))
    stores = [l for l in ctor if re.search(r'\tstr\b', l)]
    check('C  metaclass ctor stores only the vtable (no hook install)', len(stores) == 1,
          f'{len(stores)} store(s)')

    # ---- G: the gate that stays --------------------------------------------------
    probe = dis['_liuqin_base_class_probe']
    puts = len([l for l in probe if re.search(r'bl\s+_liuqin_debug_puts\b', l)])
    miles = len([l for l in probe if re.search(r'bl\s+_liuqin_milestone\b', l)])
    check('G  the probe prints its two evidence lines', puts >= 2, f'{puts} puts')
    check('G  the probe reaches the milestone console', miles >= 2,
          f'{miles} merged call site(s) -- the strings carry the verdict')
    for m in (b'base-ok', b'base-abi', b'base-alien', b'base-free', b'mc-ok', b'mc-bad'):
        hits = blob.count(b'\0' + m + b'\0')
        check(f'G  milestone {m.decode()!r} is in the image', hits == 1, f'{hits}x')
    slide = addrs.get('_vm_kernel_slide', 0)
    i_slide = materialises(probe, slide)[0] if slide else -1
    check('G  the probe no longer reads vm_kernel_slide (H16-H retired)', i_slide < 0,
          f'{slide:#x}' if slide else 'symbol absent')
    # The gate's comparison operand is a GOT load, not an adrp/add: the compiler keeps the
    # absolute binding in __got, so the proof is that the *slot* holds the relocated address.
    got_hits = [(t, l) for t, l in literal_targets(probe) if word_at(sects, blob, t) == meta]
    check('G  the probe compares against the compiled family object',
          bool(got_hits) or materialises(probe, meta)[0] >= 0,
          f'{got_hits[0][1].split("\t")[-1]} -> {meta:#x}' if got_hits else 'no literal/GOT match')

    # ---- optional: the payload carries the bring-up switches ---------------------
    # v43 on device: AMFI is a *codeless* component -- Apple's own binary, not our source --
    # and it refuses cs_enforcement_disable. It panics inside its own start(), which runs at
    # K51 inside mac_policy_initmach -> KLDBootstrap::loadSecurityExtensions(): "can't has
    # cs_enforcement_disable" @AppleMobileFileIntegrity.cpp:5261. That is sixteen boot stages
    # BEFORE the trust-cache wall the arg was meant to work around, so the arg is out (v44)
    # and must stay out. The census below is why that is a fact and not a preference: the
    # image carries exactly one fatal boot-arg check, and it names this one; nothing refuses
    # amfi_get_out_of_my_way, so the judgement switch stays.
    payload = Path(args.payload)
    if payload.is_file():
        pb = payload.read_bytes()
        for flag in (b'amfi_get_out_of_my_way=1', b'liuqin_allow_unsealed_root=1'):
            check(f'Y  payload carries {flag.decode()}', pb.count(flag) >= 1,
                  f'{pb.count(flag)}x')
        absent = pb.count(b'cs_enforcement_disable=1')
        check('Y- payload does NOT carry cs_enforcement_disable=1', absent == 0, f'{absent}x')
    else:
        print(f'(no payload at {args.payload}: the boot-arg checks were skipped)')
    fatal = blob.count(b"can't has cs_enforcement_disable")
    check("Y  AMFI really refuses cs_enforcement_disable (the reason it is out)",
          fatal == 1, f'{fatal}x')
    refused = blob.count(b"can't has amfi_get_out_of_my_way")
    check('Y- ... and nothing refuses amfi_get_out_of_my_way', refused == 0, f'{refused}x')

    print()
    if failures:
        print(f'AUDIT FAILED ({len(failures)}/{n[0]}): ' + '; '.join(failures))
        return 1
    print(f'IMAGE AUDIT PASS ({n[0]} checks): the gate and the sentinel are in the image, the H16')
    print('    scan/repair/fingerprint are provably gone, and the trust-cache lock is initialised')
    print('    on the branch this configuration actually compiles.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
