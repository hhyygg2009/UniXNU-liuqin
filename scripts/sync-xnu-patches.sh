#!/bin/sh
# Re-sync the device patches/xnu module from the base UniXNU checkout.
# Device repositories override module directories, so the whole base set must be
# present here next to our own patches. Run after updating the base repository.
#
# Device-only patches (kept across syncs, absent from the base checkout):
#   zzz-liuqin-usb-debug*.patch        framebuffer/USB milestone console (regenerate as
#                                      the INCREMENT against the rest of the series; a
#                                      git diff HEAD export bakes in other patches and
#                                      then collides when the build replays the series)
#   native-volatile-panic-region-liuqin.patch   static panic log fallback (liuqin ADT
#                                      has no pram node; generated against the base blob
#                                      of native-dt-panic-region.patch and sorted after it)
#   zzzz-liuqin-trust-cache-lock-init.patch     sleepable trust-cache lock when the runtime
#                                      block is compiled out (must sort after every patch
#                                      that touches startup.c, or the export bakes them in)
#   native-cryptex-os-view.patch, offline-*, ...  any other device patch: add its exact
#                                      name to KEEP below or it is deleted by this sync.
set -e
here=$(cd "$(dirname "$0")/.." && pwd)
base="${1:-$here/../UniXNU}/patches/xnu"
test -d "$base" || { echo "base patches not found: $base" >&2; exit 1; }

# KEEP lists device-only patch basenames to preserve. zzz-liuqin-usb-debug* covers the
# numbered follow-ups (2,4,5) via the explicit glob entries below.
tmp=$(mktemp -d)
saved=0
for pat in 'zzz-liuqin-usb-debug*.patch' 'native-volatile-panic-region-liuqin.patch' \
           'zzzz-liuqin-trust-cache-lock-init.patch'; do
    for f in "$here"/patches/xnu/$pat; do
        [ -f "$f" ] || continue
        cp "$f" "$tmp/"
        saved=$((saved+1))
    done
done
test "$saved" -gt 0 || { echo "no device patches found to keep; refusing to wipe $here/patches/xnu" >&2; exit 1; }

rm -f "$here"/patches/xnu/*.patch
cp "$base"/*.patch "$here/patches/xnu/"
cp "$tmp"/*.patch "$here/patches/xnu/"
rm -rf "$tmp"
echo "synced $(ls "$here"/patches/xnu/*.patch | wc -l) patches from $base (kept $saved device patches)"
echo "note: the patch stamp .cache/xnu-work/.patches.sha256 changes with any patch edit;"
echo "      the next kernel build replays the series. If the tree was hand-reset, replay"
echo "      it first (same loop as build-xnu-4k-baseline.sh) before the archive builders run."
