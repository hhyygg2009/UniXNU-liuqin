#!/usr/bin/env python3
"""Prove, from the built kernel only, that the H16-F metaclass repair is really in the image.

AGENTS.md is explicit: a patch counts as shipped when the disassembly says so, not when the
source diff says so. This script is that proof, re-runnable in one command for every round.

  python3 scripts/audit-h16f-image.py [--kernel ../UniXNU/out/liuqin/kernel/kernel.macho]

Checks, all against the kernelcache as built:
  S  the symbols the patch and the driver must export
  T  the console format strings the new code prints
  P  OSMetaClass::postModLoad's scan: hook slot load, call, image-floor test, the write to
     [obj+0x10], the re-walk, and both naming branches
  C  NativeUfsBlock's metaclass constructor: name string, sizeof 248, and the store of
     &liuqin_block_super_fix into the hook slot (i.e. the install is not optimised away)
  G  H16-G, the fingerprint: the second hook slot exists and is installed, the scan calls it
     strictly before it tries the repair, the report reads the console mirror through the
     kernel's own copy/total entry points, and the witness can replay the evidence line.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SYMBOLS = {
    '_liuqin_metaclass_super_fix': 'the libkern hook slot',
    '_liuqin_metaclass_poison_report': 'the libkern fingerprint hook slot (H16-G)',
    '_liuqinMetaclassPoisonReport': 'the driver-side fingerprint (H16-G)',
    '_liuqin_debug_copy': 'the console mirror reader the search uses',
    '_liuqin_debug_total': 'the mirror size the search bounds itself with',
    '_liuqin_block_super_fix': 'the driver-supplied repair',
    '_liuqin_base_class_probe': 'the auditable base-class probe',
    '__ZN14NativeUfsBlock10gMetaClassE': 'our metaclass object',
    '__ZN20IOBlockStorageDevice10gMetaClassE': 'the family metaclass object',
}
STRINGS = [
    b'M:scan done bad=%u healed=%u cnt=%u deep=%u',
    b'M:fp %s p=%p w1=%p w2=%p w3=%p bad=%016llx asc="%s"',
    b'M:fp %s ring hits=%u self=%u lost=%llu at=%llu after=%lld total=%llu',
    b'M:fp %s ring %s self=%u lost=%llu total=%llu',
    b'M:fp %s ctx|%s|',
    b'M:bad[%u]%s p=%p vt=%p sz=%u n=',
    b'M:mc %s was=%p now=%p depth=%d',
    b'M:mc %s %s p=%p link=%p vt=%p sz=%u depth=%d',
    b'NativeUfs: base class IOBlockStorageDevice live size %u, compiled size %u',
    b'NativeUfs: IOBlockStorageDevice is held by %p, not by the compiled family object %p',
    b' fixed',
]
HEX = re.compile(r'^([0-9a-f]{16}):\t([0-9a-f]{2}[ ])+')


def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def instructions(kernel, wanted):
    """One llvm-objdump pass; keep only the lines inside the windows we care about.

    --section=__text is not decoration: this llvm build prints an empty listing without it.
    """
    import tempfile
    windows = [(lo, hi, tag) for lo, hi, tag in wanted]
    out = {tag: [] for _, _, tag in windows}
    with tempfile.NamedTemporaryFile('w+', suffix='.s', delete=False) as dump:
        path = Path(dump.name)
    try:
        with open(path, 'w') as handle:
            subprocess.run(['llvm-objdump', '--macho', '-d', '--section=__text', str(kernel)],
                           stdout=handle, stderr=subprocess.DEVNULL, check=True)
        with open(path) as handle:
            for line in handle:
                m = HEX.match(line.strip())
                if not m:
                    continue
                addr = int(m.group(1), 16)
                for lo, hi, tag in windows:
                    if lo <= addr < hi:
                        out[tag].append(line.rstrip())
    finally:
        path.unlink(missing_ok=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--kernel', default=str(ROOT / '../UniXNU/out/liuqin/kernel/kernel.macho'))
    args = ap.parse_args()
    kernel = Path(args.kernel)
    if not kernel.is_file():
        raise SystemExit(f'no kernel at {kernel} (run ./unixnu build kernel first)')

    failures = []

    def check(label, ok, detail=''):
        print(f'{"PASS" if ok else "FAIL"}  {label}' + (f'  {detail}' if detail else ''))
        if not ok:
            failures.append(label)

    nm = run(['llvm-nm', str(kernel)]).stdout
    addrs = {}
    for line in nm.splitlines():
        parts = line.split()
        if len(parts) == 3 and len(parts[0]) == 16:
            addrs[parts[2]] = int(parts[0], 16)
    for sym, what in SYMBOLS.items():
        check(f'symbol {sym} ({what})', sym in addrs,
              f'{addrs.get(sym, 0):#x}' if sym in addrs else 'absent')

    blob = kernel.read_bytes()
    for s in STRINGS:
        check(f'string {s!r}', blob.count(s) >= 1, f'{blob.count(s)}x')

    hook_slot = addrs.get('_liuqin_metaclass_super_fix', 0)
    fix_fn = addrs.get('_liuqin_block_super_fix', 0)
    post = addrs.get('__ZN11OSMetaClass11postModLoadEPv', 0)
    ctor = addrs.get('__ZN14NativeUfsBlock9MetaClassC1Ev', 0)
    if not (post and ctor and hook_slot and fix_fn):
        check('symbol window addresses', False, 'cannot disassemble without them')
        raise SystemExit(1 if failures else 0)

    report_addr = addrs.get('_liuqinMetaclassPoisonReport', 0)
    fp_helper = addrs.get('__ZL18liuqin_fingerprintPKcPK11OSMetaClassPKv', 0)
    witness_fn = addrs.get('__ZL22liuqinMetaclassWitnessPKc', 0)
    ordered = sorted(addrs.values())

    def window(addr, fallback):
        """A function's own bytes: up to the next symbol, so a check can never be satisfied
        by an instruction that belongs to whatever got linked after it."""
        nxt = [a for a in ordered if a > addr]
        return addr, (nxt[0] if nxt else addr + fallback)

    want = [(post, post + 0x700, 'postModLoad'), (ctor, ctor + 0x100, 'ctor')]
    for addr, tag, fallback in ((report_addr, 'report', 0xa00), (fp_helper, 'fp-helper', 0x60),
                                (witness_fn, 'witness', 0x200)):
        if addr:
            lo, hi = window(addr, fallback)
            want.append((lo, hi, tag))
    dis = instructions(kernel, want)

    pml = '\n'.join(dis['postModLoad'])
    # the hook slot is addressed by an adrp of its page; the call is a blr off that load
    page = hook_slot & ~0xfff
    off = hook_slot & 0xfff
    load = re.search(r'adrp\s+x(\d+), .*; ' + hex(page), pml)
    slot_load = re.search(r'ldr\s+x\d+, \[x\d+, #' + hex(off) + r'\]', pml)
    check('postModLoad reads the hook slot', bool(load and slot_load),
          f'adrp/ldr of {hook_slot:#x}')
    check('postModLoad calls the hook through blr', bool(re.search(r'\bblr\s+x\d+', pml)))
    check('postModLoad tests the answer against the image floor',
          bool(re.search(r'mov\s+x\d+, #-0x1000000000', pml)) and
          bool(re.search(r'b\.lo|b\.hs', pml)))
    check('postModLoad writes [obj + 0x10] (the superClassLink field)',
          bool(re.search(r'str\s+x\d+, \[x\d+, #0x10\]', pml)))
    check('postModLoad re-walks the chain after the write',
          pml.count('bl\t__ZL18liuqin_chain_depthPK11OSMetaClassPPKv') >= 2)
    check('postModLoad names both outcomes',
          pml.count('bl\t__ZL16liuqin_slot_dumpjPK11OSMetaClassPKvPKc') >= 2)

    c = '\n'.join(dis['ctor'])
    check('metaclass ctor passes classSize 248', bool(re.search(r'mov\s+w3, #0xf8', c)))
    check('metaclass ctor calls the OSMetaClass constructor',
          bool(re.search(r'bl\s+__ZN11OSMetaClassC2EPKcPKS_j', c)))
    # find the store that writes into the hook slot from the ctor
    store = re.search(r'str\s+x\d+, \[x\d+, #' + hex(off) + r'\]', c)
    check('metaclass ctor installs the repair (store into the hook slot)', bool(store),
          f'store to {hook_slot:#x}')

    # ADR is PC-relative with a 21-bit signed immediate; decode it from the printed bytes
    # rather than trusting the annotation, and see whether any of them lands on the repair.
    def adr_targets(lines):
        """Every ADR in a window, resolved from its own encoding, not from the annotation."""
        out = []
        for line in lines:
            fields = line.split('\t')      # addr, bytes, mnemonic, operands
            if len(fields) < 4 or fields[2] != 'adr':
                continue
            words = fields[1].split()
            if len(words) != 4:
                continue
            w = int.from_bytes(bytes(int(x, 16) for x in words), 'little')
            imm = (((w >> 5) & 0x7ffff) << 2) | ((w >> 29) & 0x3)
            if imm & (1 << 20):
                imm -= 1 << 21
            out.append(int(fields[0].rstrip(':'), 16) + imm)
        return out

    ctor_adrs = adr_targets(dis['ctor'])
    check('the installed address is &liuqin_block_super_fix', fix_fn in ctor_adrs,
          f'{fix_fn:#x}' if fix_fn in ctor_adrs else 'no ADR in the ctor resolves to it')

    # ---------------------------------------------------------------- H16-G section
    # v40 fingerprints the poison: a second hook, a search of the console mirror, and a
    # witness that can replay what it found. Each piece is proven separately, because the
    # interesting failures are "folded away", "ordered the other way round" and "in the
    # image but never reachable".
    fp_slot = addrs.get('_liuqin_metaclass_poison_report', 0)
    fp_off = fp_slot & 0xfff
    c_all = '\n'.join(dis['ctor'])

    if not fp_slot:
        check('H16-G: the fingerprint hook slot exists', False, 'symbol absent')
    else:
        # 1. reachability and order: the scan asks the helper, and asks it before it tries
        #    to repair -- a fingerprint taken after the write describes our own value.
        ask_fp = pml.find('bl\t__ZL18liuqin_fingerprint')
        ask_fix = re.search(r'ldr\s+x\d+, \[x\d+, #' + hex(hook_slot & 0xfff) + r'\]', pml)
        check('H16-G: the scan calls the fingerprint helper', ask_fp >= 0,
              f'{fp_helper:#x}' if fp_helper else 'helper has no symbol')
        check('H16-G: the fingerprint happens strictly before the repair',
              ask_fp >= 0 and ask_fix is not None and ask_fp < ask_fix.start(),
              f'helper call at +{ask_fp}, repair slot load at '
              f'+{ask_fix.start() if ask_fix else -1}')

        # 2. the helper is not a stub: it reads its own slot, can bail, counts, and jumps
        #    through the pointer it loaded.
        fh = '\n'.join(dis.get('fp-helper', []))
        check('H16-G: the helper loads the report slot',
              bool(re.search(r'ldr\s+x\d+, \[x\d+, #' + hex(fp_off) + r'\]', fh)),
              f'ldr [x,#0x{fp_off:x}] of {fp_slot:#x}')
        check('H16-G: the helper bails on a missing hook or an exhausted budget',
              bool(fh) and 'ret' in fh and bool(re.search(r'b\.ls|cbz', fh)))
        check('H16-G: the helper counts the fingerprint it is about to make',
              bool(re.search(r'add\s+w\d+, w\d+, #0x1', fh)) and
              bool(re.search(r'str\s+w\d+, \[x\d+, #0x', fh)))
        check('H16-G: the helper dispatches through the loaded pointer',
              bool(re.search(r'\tbr\s+x\d+', fh)), 'br xN (tail call through the hook)')

        # 3. somebody actually installs it: the same ctor that installs the repair.
        check('H16-G: the metaclass ctor stores into the report slot',
              bool(re.search(r'str\s+x\d+, \[x\d+, #' + hex(fp_off) + r'\]', c_all)),
              f'store to {fp_slot:#x}')
        check('H16-G: what it stores is &liuqinMetaclassPoisonReport',
              bool(report_addr) and report_addr in ctor_adrs,
              f'{report_addr:#x}' if report_addr else 'report symbol absent')

        # 4. the report reads the mirror through the kernel entry points and prints what it
        #    found -- all of it inside its own function body.
        r = '\n'.join(dis.get('report', []))
        ncopy = r.count('bl\t_liuqin_debug_copy')
        nputs = r.count('bl\t_liuqin_debug_puts')
        ntotal = r.count('bl\t_liuqin_debug_total')
        check('H16-G: the report reads the mirror in bounded chunks', ncopy >= 3,
              f'{ncopy} calls to liuqin_debug_copy (chunks, rewind, context, own-field probe)')
        check('H16-G: the report bounds the search by the mirror size', ntotal >= 1,
              f'{ntotal} call(s) to liuqin_debug_total')
        check('H16-G: the report prints its findings to the console', nputs >= 3,
              f'{nputs} lines through liuqin_debug_puts')

        # 5. the evidence replay: the witness builds two M:mc lines, so a third puts in its
        #    own body can only be the replayed ctx line for a panel that already scrolled.
        w = '\n'.join(dis.get('witness', []))
        check('H16-G: the witness can replay a line it is not building now',
              w.count('bl\t_liuqin_debug_puts') >= 3,
              f'{w.count("bl\t_liuqin_debug_puts")} puts in the witness body')


    print()
    if failures:
        print(f'AUDIT FAILED ({len(failures)}): ' + '; '.join(failures))
        return 1
    print('H16-F+G AUDIT PASS: the repair and the fingerprint are in the image, provably,\n    without booting it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
