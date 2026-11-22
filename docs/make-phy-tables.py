#!/usr/bin/env python3
"""Emit BOTH QMP UFS PHY table sets (v5/sm8350 and v6/sm8475) plus a probe helper.

Linux facts:
  sm8450 PHY: reg <0 0x01d87000 0 0x1000>, offsets qmp_ufs_offsets
              serdes 0, pcs 0xc00, tx 0x400, rx 0x600, tx2 0x800, rx2 0xa00
              tables sm8350_ufsphy_* , regs layout v5 (start 0, ready 0x1a8,
              reset 8, power-down 4)
  sm8550/sm8475 PHY (v6): offsets serdes 0, pcs 0x400, tx 0x1000, rx 0x1200,
              tx2 0x1800, rx2 0x1a00, tables sm8475/sm8550 series.

The liuqin board description places the host at 0x1d84000; the PHY is one of
0x1d80000 (upstream pong's claim) or 0x1d87000 (mainline sm8450).  The kernel
probes both radii and only programmes the block that answers a write.
"""
import re
from pathlib import Path

WORK = Path('/root/work')
HEADERS = ['phy-qcom-qmp-qserdes-com-v6.h',
           'phy-qcom-qmp-qserdes-txrx-ufs-v6.h',
           'phy-qcom-qmp-pcs-ufs-v6.h',
           'phy-qcom-qmp-pcs-ufs-v5.h',
           'phy-qcom-qmp-qserdes-com-v5.h',
           'phy-qcom-qmp-qserdes-txrx-ufs-v5.h',
           'phy-qcom-qmp-qserdes-txrx-v5.h',
           'phy-qcom-qmp-qserdes-txrx-v5_5nm.h']
# v5 header is only needed for TX/RX register names if they differ; the sm8350
# tables are written in terms of the same QSERDES_UFS_V6_* names.

defines = {}
for header in HEADERS:
    text = (WORK / header).read_text()
    for name, value in re.findall(r'#define\s+(Q[A-Z0-9_]+)\s+(0x[0-9a-fA-F]+)', text):
        defines[name] = int(value, 16)

source = (WORK / 'qmp-ufs.c').read_text()

def table(name):
    match = re.search(r'struct qmp_phy_init_tbl\s+' + name + r'\[\]\s*=\s*\{(.*?)\};', source, re.S)
    if not match:
        raise SystemExit(f'table not found: {name}')
    out = []
    for symbol, value in re.findall(r'QMP_PHY_INIT_CFG(?:_LANE)?\(\s*([A-Z0-9_]+)\s*,\s*([^)]+)\)', match.group(1)):
        if symbol not in defines:
            raise SystemExit(f'unknown register {symbol} in {name}')
        out.append((defines[symbol], int(value.strip(), 16)))
    return out

groups = {
    # v6 / SM8475 (upstream pong's assumption)
    'V6Serdes': table('sm8475_ufsphy_serdes'),
    'V6Tx': table('sm8550_ufsphy_tx'),
    'V6Rx': table('sm8550_ufsphy_rx'),
    'V6Pcs': table('sm8550_ufsphy_pcs'),
    'V6SerdesG4': table('sm8475_ufsphy_g4_serdes'),
    'V6TxG4': table('sm8550_ufsphy_g4_tx'),
    'V6RxG4': table('sm8550_ufsphy_g4_rx'),
    'V6PcsG4': table('sm8475_ufsphy_g4_pcs'),
    # v5 / SM8450 (mainline)
    'V5Serdes': table('sm8350_ufsphy_serdes'),
    'V5Tx': table('sm8350_ufsphy_tx'),
    'V5Rx': table('sm8350_ufsphy_rx'),
    'V5Pcs': table('sm8350_ufsphy_pcs'),
    'V5SerdesG4': table('sm8350_ufsphy_hs_b_serdes'),
    'V5TxG4': table('sm8350_ufsphy_g4_tx'),
    'V5RxG4': table('sm8350_ufsphy_g4_rx'),
    'V5PcsG4': table('sm8350_ufsphy_g4_pcs'),
}

lines = ['// Generated from Linux drivers/phy/qualcomm/phy-qcom-qmp-ufs.c by',
         '// docs/make-phy-tables.py. Offsets are block-relative.',
         'struct PongPhyReg { uint32_t offset; uint32_t value; };', '']
for name, entries in groups.items():
    lines.append(f'static const PongPhyReg kPhyTable{name}[] = {{')
    for offset, value in entries:
        lines.append(f'  {{ 0x{offset:03x}, 0x{value:02x} }},')
    lines.append('};')
    lines.append('')

Path('/root/work/PongUfsPhyTables.h').write_text('\n'.join(lines) + '\n')
print('wrote /root/work/PongUfsPhyTables.h')
for name, entries in groups.items():
    print(f'  {name}: {len(entries)}')
