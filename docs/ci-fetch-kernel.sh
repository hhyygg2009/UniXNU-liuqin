#!/bin/bash
# Fetch and decrypt the kernel artifact of one Fly116/liuqin-ipados18-ci run.
# usage: ci_fetch_kernel.sh <run-id> [artifact-name]
#   artifact-name defaults to kernel-encrypted; kernel-upstream.yml uploads
#   kernel-upstream-encrypted.
set -euo pipefail

RUN="${1:?usage: ci_fetch_kernel.sh <run-id> [artifact-name]}"
NAME="${2:-kernel-encrypted}"
TOKEN="$(tr -d '\r\n' < /mnt/f/liuqin_ios/_gh/token.txt)"
PW="$(tr -d '\r\n' < /mnt/f/liuqin_ios/_gh/artifact_password.txt)"
REPO="Fly116/liuqin-ipados18-ci"
OUT="/root/work/ci-kernel-${RUN}"

unset http_proxy https_proxy all_proxy

mkdir -p "$OUT"
cd "$OUT"

URL="$(curl -sS -H "Authorization: Bearer $TOKEN" -H "User-Agent: dsh" \
  "https://api.github.com/repos/${REPO}/actions/runs/${RUN}/artifacts" |
  python3 -c "import json,sys;d=json.load(sys.stdin);m=[a['archive_download_url'] for a in d['artifacts'] if a['name']==sys.argv[1]];print(m[0] if m else '')" "$NAME")"
if [ -z "$URL" ]; then echo "no $NAME artifact for run $RUN" >&2; exit 1; fi

SIGNED="$(curl -sS -o /dev/null -w '%{redirect_url}' \
  -H "Authorization: Bearer $TOKEN" -H "User-Agent: dsh" "$URL")"
echo "downloading ${SIGNED:0:80}..."
# The results blob host is slow from this network (34 KB/s direct, 560 KB/s via
# the local proxy), so take the artifact through the proxy only.
https_proxy=http://172.21.64.1:7897 curl -sSL --retry 3 -o artifact.zip "$SIGNED"
ls -l artifact.zip

# No unzip on this image; 7z reads the actions artifact zip as well.
7z x -y artifact.zip >/dev/null
ls -l ./*.7z
7z x -y -p"$PW" -o"$OUT/products" ./*.7z >/dev/null
find "$OUT/products" -maxdepth 3 -type f -printf '%10s %p\n' | sort -k2
echo "products: $OUT/products"
