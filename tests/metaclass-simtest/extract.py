#!/usr/bin/env python3
"""Regenerate the simtest .inc files from the production sources, and fail on drift.

The harness includes these files verbatim, so what the test exercises is the code the
kernel runs. One single-line substitution is applied and reported below; everything else
is byte-for-byte the production text. Run this from the device repository root before
editing either side; a stale .inc exits non-zero.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / 'drivers/ShengUfs/NativeUfs.cpp'
SIM = Path(__file__).resolve().parent

# The harness has no kernel address space, so the image-floor test needs a ceiling too.
# This is the only edit made to the extracted text; it is asserted, not assumed.
TRANSFORMS = [
    ('  return p == nullptr || reinterpret_cast<uintptr_t> (p) >= LIUQIN_KERNEL_TEXT_LO;',
     '  return liuqinSimPtrOk (p);'),
]

def span(text, begin, end, label='span'):
    if text.count(begin) != 1:
        raise SystemExit(f'{label}: begin marker is not unique in {DRIVER.name}')
    i = text.index(begin)
    j = text.index(end, i) + len(end)
    return text[i:j]

def transform(text, label):
    for old, new in TRANSFORMS:
        n = text.count(old)
        if n:
            assert n == 1, f'{label}: transform target appears {n} times'
            text = text.replace(old, new)
            print(f'transform[{label}]: {old.strip()} -> {new.strip()}')
    return text

src = DRIVER.read_text()

pieces = {
    # shim + chain + repair hook + base-class policy: one contiguous region
    'helper.inc': span(src, 'struct liuqin_meta_shim',
                       'extern "C" int liuqin_base_class_probe (void) { return liuqinBaseClassCheck () ? 1 : 0; }'),
    # startRoot's H16-F prologue: install the hook, witness the field, then the policy
    'gate.inc': span(src, '  const char *why = "";\n  // Normally installed by the metaclass constructor',
                     '    return false;\n  }\n'),
    # the witness itself, which is also the repair path used at U59
    'witness.inc': span(src, 'static void liuqinMetaclassWitness (const char *phase)',
                        '  liuqin_milestone ("U", depth < 0 ? "mc-bad" : "mc-ok");\n}\n'),
}

write = '--write' in sys.argv[1:]
stale = []
for name, body in pieces.items():
    body = transform(body, name).rstrip() + '\n'
    path = SIM / name
    if not path.is_file() or path.read_text() != body:
        stale.append(name)
        if write:
            path.write_text(body)
    print(f'{name}: {len(body.splitlines())} lines from {DRIVER.name}')

if stale:
    print(('REWROTE' if write else 'DRIFT (re-run with --write)') + ': ' + ', '.join(stale),
          file=sys.stderr)
    sys.exit(1)
print('all simtest includes match the production sources')
