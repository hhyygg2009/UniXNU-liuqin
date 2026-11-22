#!/bin/bash
# Assemble a flashable liuqin boot image from one kernel-upstream run.
#
#   kernel slot: gzip(BootShim + UEFI FD) + the stock Cape SoC DTB. The FD is the
#                local sandbox build with the menu-rotation patch.
#   ramdisk:     the existing UniXNU payload FAT with the run's kernel.macho put in
#                place of the old one, wrapped in the PONGFAT1 envelope.
#   ADT:         official finalize-pong-adt.py with upstream's parameters.
#
# usage: make_v112.sh <run-id> <out.img> [kernel.macho path]
set -euo pipefail

RUN="${1:?usage: make_v112.sh <run-id> <out.img> [kernel]}"
OUT="${2:?usage: make_v112.sh <run-id> <out.img> [kernel]}"
KERNEL="${3:-}"

FD=/root/work/uefi-liuqin-d97/repo/Build/liuqinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd
UEFI_D97=/root/work/uefi-liuqin-d97/repo
CAPE=/root/work/uefi-liuqin/Resources/DTBs/liuqin.dtb
HW_DTB=/root/work/up-liuqin/build/liuqin/liuqin.dtb
LAYOUT=/mnt/f/liuqin_ios/_dl/ci/meta/volume-layout.json
BASE_PAYLOAD=/root/work/UniXNU-new/out/liuqin/payload/payload.img
WORK=/root/work/v112-${RUN}

PRODUCTS="/root/work/ci-kernel-${RUN}/products/products"
if [ ! -d "$PRODUCTS" ]; then
  bash /mnt/f/liuqin_ios/scratch/ci_fetch_kernel.sh "$RUN" kernel-upstream-encrypted
fi
if [ -z "$KERNEL" ]; then
  KERNEL="$(find "$PRODUCTS" -maxdepth 3 -type f -name kernel.macho | head -1)"
fi
[ -f "$KERNEL" ] || { echo "no kernel.macho found" >&2; exit 1; }
echo "kernel: $KERNEL ($(stat -c%s "$KERNEL") B)"
sha256sum "$KERNEL"

rm -rf "$WORK"
mkdir -p "$WORK/payload" "$WORK/boot"
cp "$BASE_PAYLOAD" "$WORK/payload/payload.img"
echo "replacing kernel.macho inside the payload FAT"
MTOOLS_SKIP_CHECK=1 mdel -i "$WORK/payload/payload.img" ::/EFI/UniXNU/Payloads/kernel.macho 2>/dev/null || true
MTOOLS_SKIP_CHECK=1 mcopy -o -i "$WORK/payload/payload.img" "$KERNEL" ::/EFI/UniXNU/Payloads/kernel.macho
MTOOLS_SKIP_CHECK=1 mdir -i "$WORK/payload/payload.img" ::/EFI/UniXNU/Payloads/

# The release boot.img only has to carry the firmware in its kernel slot: finalize
# replaces the appended DTB with the stock Cape DTB and the ramdisk with the
# PONGFAT1 payload, so it is built with a throwaway ramdisk.
cd /root/work/UniXNU-new
python3 ./unixnu bootimg --uefi "$UEFI_D97" --fd "$FD" --dtb "$CAPE" \
  --ramdisk /dev/null -D /root/work/device-new 2>&1 | tail -3 || true
cp /root/work/UniXNU-new/out/liuqin/boot/boot.img "$WORK/boot/boot.img"

cd /root/work/up-liuqin
PONG_HARDWARE_DTB="$HW_DTB" UNIXNU_UEFI="$UEFI_D97" \
python3 scripts/finalize-pong-adt.py \
  --work-dir "$WORK" \
  --uefi "$UEFI_D97" \
  --board liuqin \
  --abl-dtb "$CAPE" \
  --unique-chip-id 84750a0b \
  --ufs-probe \
  --volume-layout "$LAYOUT" \
  --hardware-dtb "$HW_DTB"

cp "$WORK/boot/liuqin-xnu-initial.img" "$OUT"
ls -l "$OUT"
sha256sum "$OUT"
