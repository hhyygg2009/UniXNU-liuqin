#!/bin/bash
# Build the pong firmware with their EDK2 and their Mu patches (applied by hand:
# the patch files are LF, the EDK2 tree is CRLF, so `git apply` refuses them).
set -e
cd /root/work/UniXNU-new
BOARD=/root/work/device-new/board.toml
cp "$BOARD" /tmp/board.toml.bak
trap 'cp /tmp/board.toml.bak "$BOARD"; echo "board restored"' EXIT
python3 - <<'PY'
from pathlib import Path
p = Path('/root/work/device-new/board.toml')
s = p.read_text()
start = s.index('packages = [')
end = s.index(']', start) + 1
packages = '''packages = [
  "{uefi}",
  "{uefi}/Platforms/Xiaomi",
  "{uefi}/Platforms/Nothing",
  "{edk2}",
  "{uefi}/Common/Mu",
  "{uefi}/Common/Mu_OEM_Sample",
  "{uefi}/Silicon/Qualcomm",
  "{uefi}/Silicon/Silicium",
  "{uefi}/Silicium-ACPI",
  "{uefi}/Silicium-ACPI/Platforms/Nothing",
  "{uefi}/Silicium-ACPI/Silicon/Qualcomm",
]'''
s = s[:start] + packages + s[end:]
start = s.index('patches = [')
end = s.index(']', start) + 1
s = s[:start] + 'patches = []' + s[end:]
p.write_text(s)
print('board: their packages, no patches (already applied to the EDK2 tree)')
PY
export UNIXNU_PATCH_LIST=manual
export XNU_OWNER_ROOT_VOLUME_UUID=18AB5557-DC2D-469B-8758-855E60F5B0DF
timeout 2700 python3 ./unixnu build firmware \
  --uefi /root/work/uefi-liuqin-d97/repo \
  --edk2 /root/work/mu_basecore-d97 \
  -D /root/work/device-new 2>&1 | tail -12
FD=/root/work/uefi-liuqin-d97/repo/Build/liuqinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd
ls -l "$FD"
sha256sum "$FD"
