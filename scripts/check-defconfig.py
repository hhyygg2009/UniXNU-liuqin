#!/usr/bin/env python3
"""Guard the three places a liuqin kernel configuration is written down.

H17 came from these three disagreeing: board.toml declared ``liuqin_defconfig``
while the config the build actually consumed was
``out/liuqin/.config`` -- byte-identical to ``configs/liuqin_boot_defconfig``,
which has ``CONFIG_XNU_TRUST_CACHE_RUNTIME=n``.  A defconfig edit is silently
inert when the build reads ``.config`` instead, and reasoning from the
declaration led us five rounds astray (docs/boot-rounds-v35-v43.md, H17).

So the declaration is now the truth (``liuqin_boot_defconfig``) and this script
proves the three agree.  It checks nothing about the image: for whether a
CONFIG_XNU_* switch took effect, audit the built kernel
(scripts/audit-h16f-image.py), as AGENTS.md requires.

Usage: python3 scripts/check-defconfig.py [--out <unixnu out dir>] [-q]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def config_switches(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('CONFIG_') and '=' in line:
            name, _, value = line.partition('=')
            out[name] = value
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--out', default=None, help='unixnu out root (default ../UniXNU/out)')
    ap.add_argument('-q', '--quiet', action='store_true')
    args = ap.parse_args()

    board = tomllib.loads((REPO / 'board.toml').read_text())
    name = board['board']['name']
    declared = board.get('kernel', {}).get('defconfig')
    schema = board.get('schema_version', 1)

    out_root = Path(args.out) if args.out else REPO.parent / 'UniXNU' / 'out'
    problems: list[str] = []
    notes: list[str] = []

    if schema != 1:
        notes.append(f'schema_version = {schema}: out/<board>/<os>/<config> layout, not checked here')
    if not declared:
        problems.append('board.toml has no [kernel] defconfig')

    # 1. the declared defconfig is a real file in this repository
    declared_path = REPO / 'configs' / str(declared)
    if declared and not declared_path.is_file():
        problems.append(f'declared defconfig missing: configs/{declared}')

    # 2. the selected defconfig recorded by `unixnu defconfig` agrees
    state_path = out_root / 'state.json'
    selected = None
    if state_path.is_file():
        selected = json.loads(state_path.read_text()).get('configurations', {}).get(str(REPO))
        if selected and selected != declared:
            problems.append(f'selected ({selected}) != declared ({declared})')
    else:
        notes.append(f'no {state_path}; cannot check the selected defconfig')

    # 3. the .config the build reads is byte-identical to the declared file
    config_path = out_root / name / '.config'
    if config_path.is_file() and declared_path.is_file():
        if config_path.read_bytes() != declared_path.read_bytes():
            problems.append(
                f'{config_path} is not configs/{declared} '
                f'({sha(config_path)} != {sha(declared_path)})')
        else:
            notes.append(f'{config_path} == configs/{declared} ({sha(config_path)})')
    elif config_path.is_file():
        notes.append(f'{config_path} exists but the declared defconfig file does not')
    else:
        notes.append(f'no {config_path} yet (run ./unixnu defconfig {declared})')

    # 4. report what the sibling defconfigs would have changed, so nobody
    #    reads a switch out of the wrong file again
    for other in sorted((REPO / 'configs').glob('*_defconfig')):
        if other == declared_path:
            continue
        diff = []
        a = config_switches(declared_path.read_text()) if declared_path.is_file() else {}
        b = config_switches(other.read_text())
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                diff.append(f'{key}={b.get(key, "<unset>")} (declared: {a.get(key, "<unset>")})')
        notes.append(f'configs/{other.name} differs in {len(diff)} switch(es)')
        if 0 < len(diff) <= 12:
            notes.extend('    ' + d for d in diff)

    if not args.quiet:
        for line in notes:
            print(line)
    for line in problems:
        print(f'FAIL: {line}', file=sys.stderr)
    if problems:
        print('the declaration and the build input disagree -- fix board.toml or run '
              './unixnu defconfig <declared> before reasoning about CONFIG_XNU_*',
              file=sys.stderr)
        return 1
    print('PASS: declared == selected == .config')
    return 0


if __name__ == '__main__':
    sys.exit(main())
