#!/usr/bin/env python3
"""Install a unixnu SHNGROOT userdata image into a GPT partition, offline by default.

The root driver (drivers/ShengUfs/RootLayout.h) finds a GPT partition (``userdata`` by
default, or the one named by the ``native-ufs-root-partition`` boot argument), reads its first
4 KiB block and, when the magic is ``SHNGROOT``, treats it as the directory of the iOS root
image: a manifest followed by 1 MiB-aligned windows (OS, SystemOS, ...).  This script plans and,
only with ``--apply``, performs the write of a prepared unixnu image
(``unixnu build userdata`` -> ``userdata-root.img``) into such a partition.

What it checks before anything is written
  * the image is a valid SHNGROOT v1/v2 directory and its manifest hash matches;
  * every window/object in that manifest hashes to the SHA-256 recorded for it;
  * the destination partition is large enough for the staged layout (1 MiB alignment and a
    16 MiB guard between an existing directory and the staged generation);
  * an existing SHNGROOT directory on the target, when supplied, stays consistent with the new
    manifest (windows with the same name are replaced, other entries are kept).

Partition geometry
  Give it either ``--first-lba/--last-lba`` (plus optional ``--lba-bytes``, default 4096) or a
  GPT dump with ``--gpt-dump FILE``.  The dump may be:
    * ``sgdisk -p`` text output (the sectors are the GPT LBAs sgdisk prints -- for this board,
      4096-byte LBAs); or
    * a raw dump of the first GPT sectors, e.g. ``dd if=/dev/block/sda of=sda-head.bin bs=4096
      count=34`` (header at LBA 1, primary entry array at LBA 2, CRCs verified).
  Both are auto-detected; the partition named by ``--partition`` is looked up by name.

Two layouts
  * fresh (default when the target has no usable SHNGROOT directory): the image is written
    verbatim at the start of the partition, so the manifest block is at partition offset 0 and
    the first window at the image's own 1 MiB offset.  This is exactly what
    ``fastboot flash <partition> <image>`` would do, and that command is printed as the
    fastboot equivalent.  Nothing precedes the image, so no guard is inserted.
  * append: when the target already holds a SHNGROOT directory, the new generation's windows
    are staged after the end of the existing one (``occupied_end + 16 MiB``, rounded up to a
    1 MiB boundary) and a merged manifest is committed last.  The old windows stay on disk as
    the rollback copy; restoring the saved 4 KiB directory rolls back.  fastboot cannot express
    an offset, so append mode only supports the adb/dd path and says so instead of printing a
    command that would do the wrong thing.

Transports
  * ``--adb`` (a Windows path such as ``D:\\PhoneTools\\tool\\adb.exe`` works from WSL: it is
    converted with ``wslpath``): commands are printed in plan mode; with ``--apply`` they run
    under recovery/root adb.  Two write methods are offered:
      - ``--method push`` (default): the image is cut into chunks (``--chunk-mib``, 1 GiB by
        default), each chunk is ``adb push``-ed to ``--device-tmp`` and written with
        ``dd conv=notrunc,fsync`` at the right offset, then deleted.  This needs only one
        chunk of free space on the device, as in the K60 reference script.
      - ``--method stream``: ``adb shell dd`` reads the image from stdin, no device temporary
        file at all; requires an adb whose shell does not allocate a pty for the remote command
        (shell protocol v2, adb >= 1.0.41).
  * ``--fastboot``: only the ``fastboot flash <partition> <image>`` line is printed (fresh
    layout only); this script never invokes fastboot.
  * ``--partition-offset <bytes>``: put the image at that 1 MiB-aligned byte offset inside the
    partition (plan C: the space the Android f2fs no longer uses after a shrink). The manifest
    and every window then sit at ``partition_offset + image offset``, and the driver needs the
    matching ``native-ufs-root-offset=<bytes>`` boot argument. fastboot cannot express an
    offset, so the TWRP/root ``dd`` command is printed instead.

Safety
  * plan is the default; ``--apply`` is required to write, and also requires ``--ack-backup``
    and a live adb target;
  * live partition size, mounts and device-mapper holders are checked before the first write;
  * a live SHNGROOT directory is never overwritten in place by a fresh-layout write: the script
    refuses and asks for a replan (or ``--force-in-place``);
  * ``--self-test`` builds fake images and GPT dumps in a temporary directory and never talks
    to a device.

Read docs/userdata-carve-plan.md before the first write (T1): it covers the Android full-device
backup, the two partition plans (A: reuse linux_root in place, B: new partition from free space),
the risks and the rollback.

Examples
  # plan only, geometry from a GPT dump (read-only)
  ./scripts/install-liuqin-ufs-data.py --image out/userdata-root.img \\
      --partition userdata --gpt-dump ~/liuqin-gpt/sgdisk-p.txt

  # append plan against the directory the device currently advertises (read-only adb read)
  ./scripts/install-liuqin-ufs-data.py --image out/userdata-root.img --partition userdata \\
      --gpt-dump sgdisk-p.txt --adb 'D:\\PhoneTools\\tool\\adb.exe' --serial <serial>

  # the T1 write (recovery/root adb): explicit apply + backup acknowledgement
  ./scripts/install-liuqin-ufs-data.py ... --apply --ack-backup

  # fake-image self test, no device
  ./scripts/install-liuqin-ufs-data.py --self-test
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

MIB = 1 << 20
GIB = 1 << 30
SECTOR = 4096                 # the SHNGROOT format and the driver work in 4 KiB blocks
ENTRY = 128
HEADER = 4096
MAGIC = b"SHNGROOT"
ALIGN = MIB                   # placement of a staged generation is 1 MiB aligned
GUARD = 16 * MIB              # gap kept between an existing directory and a staged generation
CHUNK = 8 * MIB
DEFAULT_PARTITION = "userdata"
DEFAULT_CHUNK_MIB = 1024
PLAN_DOC = "docs/userdata-carve-plan.md"


class PlanError(Exception):
    """A check that refuses the plan or the write, with a message meant for the user."""


# ---------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------
def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def human_bytes(count: int) -> str:
    if count >= GIB:
        return f"{count / GIB:.2f} GiB ({count:,} bytes)"
    if count >= MIB:
        return f"{count / MIB:.2f} MiB ({count:,} bytes)"
    return f"{count:,} bytes"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            data = handle.read(CHUNK)
            if not data:
                break
            digest.update(data)
    return digest.hexdigest()


def le32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def le64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def parse_int(value: str) -> int:
    return int(value, 0)


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def wsl_to_windows(path: str) -> str:
    """adb/fastboot are Windows binaries: their file arguments need a Windows path."""
    try:
        out = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True)
    except OSError:
        return path
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else path


def windows_to_wsl(path: str) -> str:
    if is_wsl() and re.match(r"^[A-Za-z]:[\\/]", path):
        try:
            out = subprocess.run(["wslpath", "-u", path], capture_output=True, text=True)
        except OSError:
            return path
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    return path


def resolve_executable(name_or_path: str) -> str:
    """Accept a WSL path, a Windows path (converted) or a command found on PATH."""
    candidate = windows_to_wsl(name_or_path)
    path = Path(candidate)
    if path.is_file():
        return str(path)
    found = shutil.which(candidate)
    if found:
        return found
    raise PlanError(f"cannot find executable {name_or_path!r} (tried {candidate!r} and PATH)")


# ---------------------------------------------------------------------------------------------
# SHNGROOT manifest
# ---------------------------------------------------------------------------------------------
@dataclass
class Entry:
    name: str
    offset: int
    size: int
    block_size: int
    flags: int
    sha256: bytes
    source: str = "image"

    @property
    def is_window(self) -> bool:
        return self.block_size == 512 and self.flags == 1

    @property
    def is_object(self) -> bool:
        return self.block_size == 4096 and self.flags == 3

    @property
    def padded_end(self) -> int:
        return self.offset + align_up(self.size, SECTOR)

    def record(self) -> dict:
        return {"name": self.name, "offset": self.offset, "size": self.size,
                "block_size": self.block_size, "flags": self.flags,
                "sha256": self.sha256.hex(), "source": self.source}


@dataclass
class Directory:
    version: int
    image_bytes: int
    entries: List[Entry]
    raw: bytes
    where: str = "manifest"

    @property
    def windows(self) -> List[Entry]:
        return [entry for entry in self.entries if entry.is_window]

    @property
    def objects(self) -> List[Entry]:
        return [entry for entry in self.entries if entry.is_object]

    def record(self) -> dict:
        return {"version": self.version, "image_bytes": self.image_bytes,
                "count": len(self.entries),
                "entries": [entry.record() for entry in self.entries],
                "manifest_sha256": hashlib.sha256(self.raw).hexdigest()}


def parse_directory(block: bytes, where: str = "manifest") -> Directory:
    """Validate one 4 KiB SHNGROOT directory block exactly like the driver does."""
    if len(block) != HEADER:
        raise PlanError(f"{where}: expected a {HEADER}-byte block, got {len(block)}")
    if block[:8] != MAGIC:
        raise PlanError(f"{where}: magic is {block[:8]!r}, expected {MAGIC!r}")
    version, count, image_bytes = struct.unpack_from("<IIQ", block, 8)
    if version not in (1, 2):
        raise PlanError(f"{where}: unsupported version {version} (expected 1 or 2)")
    if not 1 <= count <= 8:
        raise PlanError(f"{where}: entry count {count} outside 1..8")
    if version == 1 and count > 4:
        raise PlanError(f"{where}: version 1 holds at most 4 entries, count is {count}")
    if image_bytes < HEADER or image_bytes % SECTOR:
        raise PlanError(f"{where}: image_bytes {image_bytes} is not a positive 4 KiB multiple")
    if hashlib.sha256(block[:4064]).digest() != block[4064:4096]:
        raise PlanError(f"{where}: manifest SHA-256 mismatch over bytes 0..4063")

    entries: List[Entry] = []
    windows = objects = 0
    for index in range(count):
        record = block[24 + ENTRY * index:24 + ENTRY * (index + 1)]
        name_bytes = record[:32]
        nul = name_bytes.find(b"\0")
        if nul <= 0:
            raise PlanError(f"{where}: entry {index} has an empty or unterminated name")
        if any(byte < 0x20 or byte > 0x7E for byte in name_bytes[:nul]):
            raise PlanError(f"{where}: entry {index} name is not printable ASCII")
        if any(name_bytes[nul + 1:]):
            raise PlanError(f"{where}: entry {index} name has data after its NUL terminator")
        name = name_bytes[:nul].decode("ascii")
        offset, size = struct.unpack_from("<QQ", record, 32)
        block_size, flags = struct.unpack_from("<II", record, 48)
        window = block_size == 512 and flags == 1
        object_entry = version == 2 and block_size == 4096 and flags == 3
        if not window and not object_entry:
            raise PlanError(f"{where}: entry {name!r} has block_size={block_size} flags={flags}, "
                            f"expected 512/1 (window) or, for version 2, 4096/3 (object)")
        if offset % SECTOR or offset < HEADER:
            raise PlanError(f"{where}: entry {name!r} offset {offset} is not a valid 4 KiB boundary")
        if not size or offset >= image_bytes or size > image_bytes - offset:
            raise PlanError(f"{where}: entry {name!r} (offset {offset}, size {size}) is outside "
                            f"image_bytes {image_bytes}")
        if window and size % 512:
            raise PlanError(f"{where}: window {name!r} size {size} is not a 512-byte multiple")
        if align_up(size, SECTOR) > image_bytes - offset:
            raise PlanError(f"{where}: entry {name!r} (offset {offset}, size {size}) does not fit "
                            f"inside image_bytes {image_bytes}")
        for prior in entries:
            if prior.name == name:
                raise PlanError(f"{where}: duplicate entry name {name!r}")
            if offset < prior.padded_end and prior.offset < align_up(size, SECTOR) + offset:
                raise PlanError(f"{where}: entries {prior.name!r} and {name!r} overlap")
        windows += 1 if window else 0
        objects += 1 if object_entry else 0
        if windows > 4 or objects > 4:
            raise PlanError(f"{where}: more than 4 windows or data objects")
        entries.append(Entry(name, offset, size, block_size, flags, record[56:88], where))
    if not windows:
        raise PlanError(f"{where}: the directory has no read-only window (a root image needs one)")
    return Directory(version, image_bytes, entries, block, where)


def verify_entry_data(image: Path, directory: Directory, progress=sys.stderr) -> None:
    """Hash every window/object against the SHA-256 its manifest entry records."""
    size_bytes = image.stat().st_size
    if size_bytes != directory.image_bytes:
        raise PlanError(f"{image}: file is {size_bytes} bytes but the manifest records "
                        f"image_bytes={directory.image_bytes}")
    with image.open("rb") as handle:
        for entry in directory.entries:
            digest = hashlib.sha256()
            handle.seek(entry.offset)
            left = entry.size
            while left:
                data = handle.read(min(CHUNK, left))
                if not data:
                    raise PlanError(f"{image}: short read at offset "
                                    f"{entry.offset + entry.size - left} for {entry.name!r}")
                digest.update(data)
                left -= len(data)
            actual = digest.digest()
            if actual != entry.sha256:
                raise PlanError(
                    f"{image}: {entry.name!r} at offset {entry.offset} size {entry.size} hashes to "
                    f"{actual.hex()}, manifest says {entry.sha256.hex()}")
            print(f"  verify {entry.name}: {human_bytes(entry.size)} sha256 ok "
                  f"({entry.sha256.hex()[:16]}...)", file=progress)


def build_manifest_block(layout: "Layout") -> bytes:
    """Serialize the destination directory (used by append; fresh writes the image verbatim)."""
    entries = layout.entries
    objects = [entry for entry in entries if entry.is_object]
    version = 2 if objects else 1
    block = bytearray(HEADER)
    block[:8] = MAGIC
    struct.pack_into("<IIQ", block, 8, version, len(entries), layout.image_bytes)
    for index, entry in enumerate(entries):
        struct.pack_into("<32sQQII32s40s", block, 24 + ENTRY * index,
                         entry.name.encode("ascii"), entry.offset, entry.size,
                         entry.block_size, entry.flags, entry.sha256, bytes(40))
    block[4064:4096] = hashlib.sha256(bytes(block[:4064])).digest()
    return bytes(block)


# ---------------------------------------------------------------------------------------------
# partition geometry: --first-lba/--last-lba or a GPT dump
# ---------------------------------------------------------------------------------------------
@dataclass
class Partition:
    name: str
    first_lba: int
    last_lba: int
    lba_bytes: int = SECTOR

    @property
    def lba_count(self) -> int:
        return self.last_lba - self.first_lba + 1

    @property
    def nbytes(self) -> int:
        return self.lba_count * self.lba_bytes

    def record(self) -> dict:
        return {"name": self.name, "first_lba": self.first_lba, "last_lba": self.last_lba,
                "lba_bytes": self.lba_bytes, "bytes": self.nbytes}


def _decode_gpt_name(record: bytes) -> str:
    name = record[56:128]
    chars = []
    for offset in range(0, 72, 2):
        unit = name[offset] | (name[offset + 1] << 8)
        if not unit:
            break
        chars.append(chr(unit) if 0x20 <= unit <= 0x7E else "?")
    return "".join(chars)


def _parse_sgdisk_text(text: str, lba_bytes: int) -> List[Partition]:
    partitions: List[Partition] = []
    for line in text.splitlines():
        tokens = line.split()
        if len(tokens) < 5 or not all(token.isdigit() for token in tokens[:3]):
            continue
        first, last = int(tokens[1], 10), int(tokens[2], 10)
        rest = tokens[3:]
        if rest and re.fullmatch(r"\d+(\.\d+)?", rest[0]):
            rest = rest[1:]
            if rest and re.fullmatch(r"(?i)([kmgtpe]i?b|b)", rest[0]):
                rest = rest[1:]
        if rest and re.fullmatch(r"[0-9A-Fa-f]{4}", rest[0]):
            rest = rest[1:]
        partitions.append(Partition(" ".join(rest), first, last, lba_bytes))
    return partitions


def _parse_raw_gpt(data: bytes, lba_bytes: Optional[int]) -> List[Partition]:
    candidates = [lba_bytes] if lba_bytes else [4096, 512]
    header_lba = None
    for candidate in candidates:
        if len(data) >= candidate + 92 and data[candidate:candidate + 8] == b"EFI PART":
            header_lba = candidate
            break
    if header_lba is None:
        raise PlanError("--gpt-dump: no GPT header (EFI PART) at byte 4096 or 512; pass the raw "
                        "first sectors of the disk or sgdisk -p text, or set --lba-bytes")
    block_size = lba_bytes or header_lba
    header = data[header_lba:header_lba + block_size]
    if len(header) < 92:
        raise PlanError("--gpt-dump: truncated GPT header")
    if le32(header, 8) != 0x00010000:
        raise PlanError(f"--gpt-dump: GPT revision {le32(header, 8):#x} is not 1.0")
    header_size = le32(header, 12)
    if not 92 <= header_size <= block_size:
        raise PlanError(f"--gpt-dump: implausible GPT header size {header_size}")
    crc_bytes = bytearray(header[:header_size])
    stored = le32(header, 16)
    crc_bytes[16:20] = b"\0\0\0\0"
    if zlib.crc32(bytes(crc_bytes)) & 0xFFFFFFFF != stored:
        raise PlanError("--gpt-dump: GPT header CRC32 mismatch (truncated or modified dump)")
    if le64(header, 24) != 1:
        raise PlanError(f"--gpt-dump: current header LBA is {le64(header, 24)}, expected 1 "
                        f"(pass the primary GPT, not the backup)")
    entry_size = le32(header, 84)
    if entry_size != ENTRY:
        raise PlanError(f"--gpt-dump: entry size {entry_size}, expected {ENTRY}")
    entries_lba = le64(header, 72)
    count = le32(header, 80)
    if not 1 <= count <= 128:
        raise PlanError(f"--gpt-dump: entry count {count} outside 1..128")
    base = entries_lba * header_lba
    if len(data) < base + count * ENTRY:
        raise PlanError(f"--gpt-dump: dump ends before the {count} entries at LBA {entries_lba}; "
                        f"need at least {base + count * ENTRY} bytes, have {len(data)}")
    entries = data[base:base + count * ENTRY]
    if zlib.crc32(entries) & 0xFFFFFFFF != le32(header, 88):
        raise PlanError("--gpt-dump: GPT entry array CRC32 mismatch")
    partitions: List[Partition] = []
    for index in range(count):
        record = entries[index * ENTRY:(index + 1) * ENTRY]
        if not any(record[:16]):
            continue
        partitions.append(Partition(_decode_gpt_name(record), le64(record, 32),
                                    le64(record, 40), header_lba))
    return partitions


def load_gpt_dump(path: Path, lba_bytes: Optional[int]) -> List[Partition]:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise PlanError(f"--gpt-dump: cannot read {path}: {error}") from error
    if len(data) >= 520 and (data[512:520] == b"EFI PART" or
                             (len(data) >= 4104 and data[4096:4104] == b"EFI PART")):
        return _parse_raw_gpt(data, lba_bytes)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    partitions = _parse_sgdisk_text(text, lba_bytes or SECTOR)
    if not partitions:
        raise PlanError("--gpt-dump: no partition rows found; expected sgdisk -p text or a raw "
                        "GPT header dump")
    return partitions


def resolve_partition(args) -> Partition:
    if args.gpt_dump:
        if args.first_lba is not None or args.last_lba is not None:
            raise PlanError("--gpt-dump and --first-lba/--last-lba are mutually exclusive")
        partitions = load_gpt_dump(Path(args.gpt_dump).expanduser(), args.lba_bytes)
        matches = [partition for partition in partitions if partition.name == args.partition]
        if not matches:
            listing = "\n".join(f"  {partition.first_lba:>12}..{partition.last_lba:<12} "
                                f"{partition.name!r}" for partition in partitions)
            raise PlanError(f"--partition {args.partition!r} is not in the GPT dump; entries are:\n"
                            f"{listing}")
        if len(matches) > 1:
            raise PlanError(f"--partition {args.partition!r} appears {len(matches)} times in the "
                            f"GPT dump")
        partition = matches[0]
        if args.lba_bytes is not None and partition.lba_bytes != args.lba_bytes:
            raise PlanError(f"GPT dump says LBA size {partition.lba_bytes}, --lba-bytes says "
                            f"{args.lba_bytes}")
        return partition
    if args.first_lba is None or args.last_lba is None:
        raise PlanError("give the partition geometry with --gpt-dump FILE or both --first-lba and "
                        "--last-lba")
    lba_bytes = args.lba_bytes or SECTOR
    if lba_bytes <= 0 or lba_bytes % 512:
        raise PlanError(f"--lba-bytes {lba_bytes} is not a positive 512-byte multiple")
    if args.first_lba < 0 or args.last_lba < args.first_lba:
        raise PlanError(f"bad partition geometry: first-lba={args.first_lba} "
                        f"last-lba={args.last_lba}")
    return Partition(args.partition, args.first_lba, args.last_lba, lba_bytes)


# ---------------------------------------------------------------------------------------------
# destination layout
# ---------------------------------------------------------------------------------------------
@dataclass
class Layout:
    mode: str                     # "fresh" or "append"
    partition: Partition
    source: Directory
    existing: Optional[Directory]
    entries: List[Entry]          # destination entries (offsets relative to the root base)
    image_bytes: int              # destination manifest image_bytes (relative to the root base)
    placement: int                # absolute byte offset of the staged generation's first entry
    occupied_end: int             # absolute end of the existing directory's data
    guard: int
    write_start: int              # source image byte the write starts at
    write_end: int                # source image byte the write ends at
    dest_write_start: int         # absolute destination byte the write starts at
    partition_offset: int         # byte offset of the root base inside the partition
    root_base: int                # == partition_offset; the manifest block lives here
    manifest: bytes               # the 4 KiB directory block for the destination

    @property
    def windows(self) -> List[Entry]:
        return [entry for entry in self.entries if entry.is_window]

    @property
    def objects(self) -> List[Entry]:
        return [entry for entry in self.entries if entry.is_object]

    @property
    def image_end(self) -> int:
        return self.root_base + self.image_bytes

    def record(self) -> dict:
        return {
            "mode": self.mode,
            "partition": self.partition.record(),
            "partition_offset": self.partition_offset,
            "root_base": self.root_base,
            "image_bytes": self.image_bytes,
            "image_end": self.image_end,
            "placement": self.placement,
            "occupied_end": self.occupied_end,
            "guard": self.guard,
            "write": {"source_start": self.write_start, "source_end": self.write_end,
                      "dest_start": self.dest_write_start},
            "entries": [entry.record() for entry in self.entries],
            "manifest_sha256": hashlib.sha256(self.manifest).hexdigest(),
            "existing": self.existing.record() if self.existing else None,
        }


def build_layout(source: Directory, partition: Partition,
                 existing: Optional[Directory], partition_offset: int = 0) -> Layout:
    if partition_offset < 0 or partition_offset % ALIGN:
        raise PlanError(f"--partition-offset {partition_offset} is not 1 MiB aligned")
    partition_bytes = partition.nbytes
    if partition_offset + HEADER > partition_bytes:
        raise PlanError(f"--partition-offset {human_bytes(partition_offset)} leaves no room for "
                        f"the {HEADER}-byte directory block in partition {partition.name!r} "
                        f"({human_bytes(partition_bytes)})")
    if partition_bytes < HEADER or partition_bytes % SECTOR:
        raise PlanError(f"partition {partition.name!r} is {human_bytes(partition_bytes)}, but the "
                        f"SHNGROOT directory needs a positive 4 KiB multiple of at least {HEADER}")
    source_base = min(entry.offset for entry in source.entries)
    if source_base % ALIGN:
        raise PlanError(f"image: first entry offset {source_base} is not 1 MiB aligned")
    for entry in source.entries:
        if entry.offset % ALIGN:
            raise PlanError(f"image: entry {entry.name!r} offset {entry.offset} is not 1 MiB "
                            f"aligned; the liuqin layout keeps every window on a 1 MiB boundary")

    root_base = partition_offset
    if existing is None:
        mode = "fresh"
        placement = root_base + source_base
        occupied_end = root_base
        guard = 0
        entries = [Entry(entry.name, entry.offset, entry.size, entry.block_size, entry.flags,
                         entry.sha256, "image") for entry in source.entries]
        image_bytes = source.image_bytes
        write_start, write_end = 0, source.image_bytes
        dest_write_start = root_base
    else:
        mode = "append"
        occupied_end = root_base + max([existing.image_bytes] +
                                       [entry.padded_end for entry in existing.entries])
        guard = GUARD
        placement = align_up(occupied_end + GUARD, ALIGN)
        local_shift = placement - root_base - source_base
        relocated = [Entry(entry.name, entry.offset + local_shift, entry.size, entry.block_size,
                           entry.flags, entry.sha256, "image") for entry in source.entries]
        merged: Dict[str, Entry] = {entry.name: entry for entry in existing.entries}
        for entry in relocated:
            merged[entry.name] = entry
        entries = list(merged.values())
        image_bytes = max(existing.image_bytes, local_shift + source.image_bytes)
        write_start, write_end = source_base, source.image_bytes
        dest_write_start = placement

    # What the driver's version 1/2 parser accepts is what the merged directory may hold.
    windows = sum(1 for entry in entries if entry.is_window)
    objects = sum(1 for entry in entries if entry.is_object)
    if not windows:
        raise PlanError("destination directory would have no read-only window")
    if windows > 4 or objects > 4:
        raise PlanError(f"destination directory would hold {windows} windows and {objects} data "
                        f"objects; the manifest holds at most 4 of each")
    if objects == 0 and len(entries) > 4:
        raise PlanError(f"destination directory would hold {len(entries)} entries; a version 1 "
                        f"manifest holds at most 4 (data objects would make it version 2, but a "
                        f"foreign directory with more windows cannot be extended here)")
    image_bytes = max([image_bytes] + [entry.padded_end for entry in entries])
    image_bytes = align_up(image_bytes, SECTOR)

    image_end = root_base + image_bytes
    if image_end > partition_bytes:
        detail = (f"  partition {partition.name}: {human_bytes(partition_bytes)} "
                  f"(LBAs {partition.first_lba}..{partition.last_lba}, "
                  f"{partition.lba_bytes}-byte LBA)\n"
                  f"  --partition-offset: {human_bytes(root_base)}\n")
        if mode == "append":
            detail += (f"  existing directory occupies up to {human_bytes(occupied_end)}\n"
                       f"  16 MiB guard + 1 MiB alignment -> new generation at "
                       f"{human_bytes(placement)}\n")
        detail += (f"  image ends at {human_bytes(image_end)}\n"
                   f"  short by {human_bytes(image_end - partition_bytes)}")
        raise PlanError("the staging does not fit in the partition:\n" + detail)

    layout = Layout(mode, partition, source, existing, entries, image_bytes, placement,
                    occupied_end, guard, write_start, write_end, dest_write_start,
                    partition_offset, root_base, b"")
    if mode == "fresh":
        manifest = source.raw
    else:
        manifest = build_manifest_block(layout)
        # Re-parse what will be committed: the same checks the driver will run.
        check = parse_directory(manifest, "destination manifest")
        if check.image_bytes != image_bytes or len(check.entries) != len(entries):
            raise PlanError("internal error: built destination manifest does not round-trip")
    layout.manifest = manifest
    return layout


def read_existing_directory(block: bytes, where: str) -> Optional[Directory]:
    """Return the existing directory, or None when the block is not a SHNGROOT directory."""
    if block[:8] != MAGIC:
        return None
    return parse_directory(block, where)


# ---------------------------------------------------------------------------------------------
# adb transport
# ---------------------------------------------------------------------------------------------
class Adb:
    def __init__(self, executable: str, serial: Optional[str]):
        self.executable = resolve_executable(executable)
        self.serial = serial

    def base(self) -> List[str]:
        return [self.executable] + (["-s", self.serial] if self.serial else [])

    def run(self, arguments: Sequence[str], stdin=None, text: bool = False,
            check: bool = True, capture: bool = True):
        command = self.base() + list(arguments)
        result = subprocess.run(command, stdin=stdin, text=text, capture_output=capture,
                                check=False)
        if check and result.returncode:
            stderr = ""
            if capture:
                stderr = (result.stderr or "").strip() if text else (result.stderr or b"").decode(
                    "utf-8", "replace").strip()
            raise PlanError(f"adb command failed ({result.returncode}): {' '.join(command)}"
                            + (f"\n{stderr}" if stderr else ""))
        return result

    def state(self) -> str:
        return (self.run(["get-state"], text=True).stdout or "").strip()

    def shell(self, command: str) -> str:
        return (self.run(["shell", command], text=True).stdout or "").strip()

    def push(self, local: Path, remote: str) -> None:
        source = str(local)
        if self.executable.lower().endswith(".exe"):
            source = wsl_to_windows(source)
        self.run(["push", source, remote], text=True)

    def exec_out(self, command: str) -> bytes:
        result = subprocess.run(self.base() + ["exec-out", command], capture_output=True,
                                check=False)
        if result.returncode:
            raise PlanError(f"adb exec-out failed ({result.returncode}): {command}")
        return result.stdout or b""

    def stream(self, command: str, payload: Path, offset: int) -> None:
        with payload.open("rb") as handle:
            handle.seek(offset)
            result = subprocess.run(self.base() + ["shell", command], stdin=handle,
                                    stdout=None, stderr=None, check=False)
        if result.returncode:
            raise PlanError(f"adb shell write failed ({result.returncode}): {command}")


@dataclass
class Target:
    device: str
    name: str
    bytes: int


def adb_open_target(adb: Adb, partition: Partition) -> Target:
    state = adb.state()
    if state not in ("recovery", "device"):
        raise PlanError(f"adb state is {state!r}; recovery (or a rooted device) is required")
    name = partition.name
    device = adb.shell(f"readlink -f /dev/block/by-name/{name}")
    if not device or not device.startswith("/dev/block/"):
        raise PlanError(f"partition {name!r} is not exposed by /dev/block/by-name on the device")
    block_name = device.rsplit("/", 1)[-1]
    size = adb.shell(f"blockdev --getsize64 {device}")
    live_bytes = int(size) if size.isdigit() else None
    if live_bytes is None:
        sectors = adb.shell(f"cat /sys/class/block/{block_name}/size")
        live_bytes = int(sectors) * 512 if sectors.isdigit() else None
    if live_bytes is None:
        raise PlanError(f"cannot read the size of {device} (blockdev and sysfs both failed)")
    if live_bytes != partition.nbytes:
        raise PlanError(
            f"partition {name!r} on the device is {human_bytes(live_bytes)}, but the planned "
            f"geometry ({partition.first_lba}..{partition.last_lba} x {partition.lba_bytes}) is "
            f"{human_bytes(partition.nbytes)}.\n"
            f"The GPT dump or explicit LBAs are stale -- re-probe with native-ufs-probe=15 or "
            f"sgdisk -p and replan")
    if adb.shell(f"ls /sys/class/block/{block_name}/holders"):
        raise PlanError(f"{device} has device-mapper holders; unmount it first")
    for line in adb.shell("cat /proc/mounts").splitlines():
        fields = line.split()
        if fields and fields[0] in (device, f"/dev/block/by-name/{name}"):
            where = fields[1] if len(fields) > 1 else "?"
            raise PlanError(f"partition {name!r} is mounted at {where}; unmount it first")
    return Target(device, name, live_bytes)




def read_target_directory(adb: Adb, target: Target, offset: int = 0) -> bytes:
    """Read the directory block: the partition's block 0, or the block at the in-partition offset."""
    block = adb.exec_out(f"dd if={target.device} bs={SECTOR} skip={offset // SECTOR} count=1 "
                         f"2>/dev/null")
    if len(block) != HEADER:
        raise PlanError(f"could not read the 4 KiB block at {human_bytes(offset)} of "
                        f"{target.device} (got {len(block)} bytes)")
    return block



# ---------------------------------------------------------------------------------------------
# plan printing and execution
# ---------------------------------------------------------------------------------------------
def stream_command(target_device: str, offset: int, length: int) -> str:
    """dd command for one slice: offset/length are bytes, dd works in 4 KiB blocks."""
    return (f"dd of={target_device} bs={SECTOR} skip=0 seek={offset // SECTOR} "
            f"count={length // SECTOR} conv=notrunc,fsync")


def commit_command(target_device: str, root_base: int = 0) -> str:
    """Write the 4 KiB directory block at the root base (partition offset for an offset image)."""
    return (f"dd of={target_device} bs={SECTOR} seek={root_base // SECTOR} count=1 "
            f"conv=notrunc,fsync")


def chunk_ranges(layout: Layout, chunk_bytes: int) -> List[Tuple[int, int, int]]:
    """Return (source offset, destination offset, length) for every pushed chunk."""
    ranges = []
    done = 0
    total = layout.write_end - layout.write_start
    while done < total:
        length = min(chunk_bytes, total - done)
        ranges.append((layout.write_start + done, layout.dest_write_start + done, length))
        done += length
    return ranges


def fastboot_command(args, layout: Layout) -> str:
    tool = args.fastboot or "fastboot"
    image = str(Path(args.image).expanduser().resolve())
    if str(tool).lower().endswith(".exe"):
        image = wsl_to_windows(image)
    return f"{tool} flash {layout.partition.name} {image}"


def print_plan(args, layout: Layout) -> None:
    partition = layout.partition
    source = layout.source
    target_path = f"/dev/block/by-name/{partition.name}"
    adb_prefix = f"{args.adb or 'adb'}" + (f" -s {args.serial}" if args.serial else "")
    offset_mib = layout.partition_offset // MIB
    root_base_lba = partition.first_lba + layout.partition_offset // SECTOR
    print("liuqin userdata install plan (no device write unless --apply)")
    print(f"  image        {Path(args.image).expanduser().resolve()}")
    print(f"               {human_bytes(source.image_bytes)} image_bytes, manifest "
          f"v{source.version}, {len(source.windows)} window(s)"
          + (f", {len(source.objects)} object(s)" if source.objects else ""))
    for entry in source.entries:
        kind = "window" if entry.is_window else "object"
        print(f"    {entry.name:<21} {kind:<6} offset 0x{entry.offset:08x} "
              f"size {human_bytes(entry.size)} sha256 {entry.sha256.hex()[:16]}...")
    print(f"  partition    {partition.name}: LBAs {partition.first_lba}..{partition.last_lba} "
          f"x {partition.lba_bytes} bytes = {human_bytes(partition.nbytes)}")
    print(f"  offset       {human_bytes(layout.partition_offset)} "
          f"({offset_mib} MiB, 1 MiB aligned) -> root manifest base LBA {root_base_lba}, "
          f"byte {layout.root_base}")
    if layout.existing is None:
        print("  target state no SHNGROOT directory read (fresh layout)")
    else:
        existing = layout.existing
        print(f"  target state existing SHNGROOT v{existing.version}, {len(existing.entries)} "
              f"entries, occupied through {human_bytes(layout.occupied_end)}")
        for entry in existing.entries:
            print(f"    {entry.name:<21} offset 0x{entry.offset:08x} "
                  f"size {human_bytes(entry.size)}")
    print("  destination layout")
    print(f"    mode       {layout.mode}" +
          (" (image written verbatim at the offset; fastboot-flash equivalent only at offset 0)"
           if layout.mode == "fresh" else
           " (new generation staged after the existing directory)"))
    print(f"    root base  partition byte {layout.root_base} = LBA {root_base_lba}")
    print(f"    placement  first entry at {human_bytes(layout.placement)} (1 MiB aligned)")
    if layout.mode == "append":
        print(f"    guard      {human_bytes(layout.guard)} after the existing directory")
    print(f"    manifest   {human_bytes(HEADER)} directory block at partition byte "
          f"{layout.root_base}, committed after the data")
    for entry in layout.entries:
        note = "new"
        if layout.existing is not None:
            if any(other.name == entry.name for other in layout.existing.entries):
                note = "replaced"
            elif entry.source != "image":
                note = "kept"
        print(f"    {entry.name:<21} offset 0x{entry.offset:08x} (from root base) "
              f"size {human_bytes(entry.size)} ({note})")
    print(f"    image_bytes {human_bytes(layout.image_bytes)} from the root base; image ends at "
          f"partition byte {layout.image_end}; slack "
          f"{human_bytes(partition.nbytes - layout.image_end)}")

    transport = args.transport
    if transport in ("auto", "fastboot") and layout.mode == "fresh" and layout.partition_offset == 0:
        print(f"\n  fastboot equivalent: {fastboot_command(args, layout)}")
        print("  (run it yourself from the bootloader; this script never invokes fastboot)")
    elif transport in ("auto", "fastboot") and layout.mode == "fresh":
        print("\n  fastboot cannot express --partition-offset; use the TWRP/root dd command below.")
    if transport == "fastboot":
        print(f"\n  driver boot argument after the write: native-ufs-root-offset="
              f"{layout.partition_offset}")
        return
    if layout.mode == "append" and transport == "auto":
        print("\n  fastboot cannot express an offset: it would overwrite the directory block before "
              "the windows are staged.\n  Use the adb path below for an append install; erase the "
              "partition for a fastboot/flashable fresh install instead.")

    if layout.mode == "fresh":
        print(f"\n  TWRP/root dd equivalent (image placed at partition offset {offset_mib} MiB):")
        print(f"    {adb_prefix} push <image> /tmp/userdata-root.img")
        print(f"    {adb_prefix} shell 'dd if=/tmp/userdata-root.img of={target_path} bs=1M "
              f"seek={offset_mib} conv=fsync'")
        print(f"    {adb_prefix} shell sync")
    print(f"\n  driver boot argument after the write: native-ufs-root-offset="
          f"{layout.partition_offset}"
          + ("" if layout.partition_offset else " (0 is the default; only needed for offset layouts)"))

    print("\n  adb run book (plan only -- --apply executes exactly this)")
    print(f"    1. {adb_prefix} get-state                  # recovery or device")
    print(f"    2. {adb_prefix} shell readlink -f {target_path}")
    print(f"    3. {adb_prefix} shell blockdev --getsize64 <device>   "
          f"# must equal {partition.nbytes} bytes")
    if args.method == "push":
        chunk = args.chunk_mib * MIB
        ranges = chunk_ranges(layout, chunk)
        print(f"    4. {adb_prefix} push <image slice> {args.device_tmp}/liuqin-ufs-chunk.img"
              f"        # {len(ranges)} chunk(s) of {human_bytes(chunk)}")
        print(f"    5. {adb_prefix} shell "
              f"'{stream_command(target_path, ranges[0][1], ranges[0][2])}'"
              f"   # then each next chunk at its own destination offset")
        if layout.mode == "append":
            print(f"    6. {adb_prefix} shell '{commit_command(target_path, layout.root_base)}' "
                  f"< manifest-new.bin          # last: commit the directory block")
            print(f"    7. {adb_prefix} shell sync; read back the directory block and compare "
                  f"its SHA-256")
        else:
            print(f"    6. {adb_prefix} shell sync; read back the directory block and compare "
                  f"its SHA-256")
    else:
        stream = stream_command(target_path, layout.dest_write_start,
                                layout.write_end - layout.write_start)
        start = layout.write_start
        print(f"    4. {adb_prefix} shell '{stream}' < <(tail -c +{start + 1} image)"
              f"   # stream, no device temp file")
        if layout.mode == "append":
            print(f"    5. {adb_prefix} shell '{commit_command(target_path, layout.root_base)}' "
                  f"< manifest-new.bin")
            print(f"    6. {adb_prefix} shell sync; read back the directory block and compare "
                  f"its SHA-256")
        else:
            print(f"    5. {adb_prefix} shell sync; read back the directory block and compare "
                  f"its SHA-256")

    print("\n  T1 precondition: no write may happen before the Android full-device backup and "
          "the go/no-go in " + PLAN_DOC + ".")
    print("  Nothing was written. Re-run with --apply --ack-backup --adb <path> to execute the "
          "adb path.")


@dataclass
class Outcome:
    layout: Layout
    target: Optional[Target] = None
    manifest_before: Optional[bytes] = None
    output_dir: Optional[Path] = None


def apply_plan(args, adb: Adb, layout: Layout) -> Outcome:
    if not args.ack_backup:
        raise PlanError("--apply needs --ack-backup: complete the Android full-device backup "
                        "described in " + PLAN_DOC + " first (T1 is the first device write)")
    target = adb_open_target(adb, layout.partition)
    if layout.root_base + layout.image_bytes > target.bytes:
        raise PlanError(f"--partition-offset {human_bytes(layout.root_base)} + image_bytes "
                        f"{human_bytes(layout.image_bytes)} exceeds the live partition size "
                        f"{human_bytes(target.bytes)}")
    live = read_target_directory(adb, target, layout.partition_offset)
    live_directory = read_existing_directory(live, "device directory")
    if args.existing_header:
        if Path(args.existing_header).expanduser().read_bytes() != live:
            raise PlanError("--existing-header does not match the device block at "
                            "--partition-offset; re-read it before planning")
    if args.empty and live_directory is not None:
        raise PlanError("--empty was requested but the device block at --partition-offset already "
                        "holds a SHNGROOT directory. Replan against it (drop --empty and let this "
                        "script read it) so the new generation is staged behind the guard; "
                        "--force-in-place overwrites it in place and is not crash-safe")
    if live_directory is None and layout.existing is not None:
        raise PlanError("the plan expected an existing SHNGROOT directory at --partition-offset "
                        "but the device's block there is not one; replan")
    if live_directory is not None and layout.existing is None and not args.force_in_place:
        raise PlanError("the device already holds a SHNGROOT directory at --partition-offset but "
                        "the plan was built for a fresh write; replan without --empty (the script "
                        "then stages behind the guard)")
    live_layout = build_layout(layout.source, layout.partition, live_directory,
                               layout.partition_offset)
    if (live_layout.mode, live_layout.image_bytes, live_layout.manifest,
            live_layout.entries) != (layout.mode, layout.image_bytes, layout.manifest,
                                     layout.entries):
        raise PlanError("the device state changed since the plan was built; re-run the plan")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-off{layout.partition_offset // MIB}MiB" if layout.partition_offset else ""
    output_dir = Path(args.output_dir) if args.output_dir else Path(
        f"ufs-install-{layout.partition.name}{suffix}-{stamp}")
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_before = live if layout.existing is not None else None
    if manifest_before is not None:
        (output_dir / "manifest-before.bin").write_bytes(manifest_before)
    (output_dir / "manifest-new.bin").write_bytes(layout.manifest)
    (output_dir / "plan.json").write_text(json.dumps(layout.record(), indent=2) + "\n")
    image_path = Path(args.image).expanduser()
    image_sha = sha256_file(image_path)

    ranges = chunk_ranges(layout, args.chunk_mib * MIB) if args.method == "push" else \
        [(layout.write_start, layout.dest_write_start, layout.write_end - layout.write_start)]
    print(f"\nWRITING {layout.partition.name} at {target.device}, root base "
          f"{layout.root_base} ({human_bytes(layout.partition_offset)})")
    print(f"  image sha256 {image_sha}")
    print(f"  manifest sha256 {hashlib.sha256(layout.manifest).hexdigest()}")
    print(f"  write order  data first, directory last; artifacts in {output_dir}")
    if args.method == "push":
        remote = f"{args.device_tmp}/liuqin-ufs-chunk.img"
        with tempfile.TemporaryDirectory(prefix="liuqin-ufs-chunks-") as scratch:
            scratch_path = Path(scratch)
            for index, (source_offset, dest_offset, length) in enumerate(ranges):
                chunk = scratch_path / f"chunk-{index:04d}.img"
                with image_path.open("rb") as handle, chunk.open("wb") as out:
                    handle.seek(source_offset)
                    left = length
                    while left:
                        data = handle.read(min(CHUNK, left))
                        if not data:
                            raise PlanError(f"{image_path}: short read at chunk {index}")
                        out.write(data)
                        left -= len(data)
                print(f"  chunk {index + 1}/{len(ranges)}: {human_bytes(length)} at source "
                      f"0x{source_offset:x} -> device 0x{dest_offset:x}", flush=True)
                adb.push(chunk, remote)
                adb.shell(stream_command(target.device, dest_offset, length))
                adb.shell(f"rm -f {remote}")
    else:
        print(f"  stream {human_bytes(layout.write_end - layout.write_start)} at source "
              f"0x{layout.write_start:x} -> device 0x{layout.dest_write_start:x}", flush=True)
        adb.stream(stream_command(target.device, layout.dest_write_start,
                                  layout.write_end - layout.write_start),
                   image_path, layout.write_start)
    if layout.mode == "append":
        commit_remote = f"{args.device_tmp}/liuqin-ufs-manifest.bin"
        adb.push(output_dir / "manifest-new.bin", commit_remote)
        adb.shell(f"dd if={commit_remote} of={target.device} bs={SECTOR} "
                  f"seek={layout.root_base // SECTOR} count=1 conv=notrunc,fsync")
        adb.shell(f"rm -f {commit_remote}")
    adb.shell("sync")
    readback = read_target_directory(adb, target, layout.partition_offset)
    if readback != layout.manifest:
        raise PlanError(
            f"read-back of the directory block does not match the directory just written "
            f"({hashlib.sha256(readback).hexdigest()[:16]}... vs "
            f"{hashlib.sha256(layout.manifest).hexdigest()[:16]}...)")
    print("  read-back: the directory block matches the written one")
    print("  installed. No read-back of the window data was performed; every window hash was "
          "verified against the image before the write.")
    return Outcome(layout, target, manifest_before, output_dir)


def rollback_hint(outcome: Outcome) -> str:
    if outcome.manifest_before is None or outcome.target is None:
        return ("fresh install: rollback means restoring the partition's previous content from "
                "the Android full-device backup (there is no on-partition copy)")
    path = outcome.output_dir / "manifest-before.bin" if outcome.output_dir else "manifest-before.bin"
    return (f"append install: restore the old directory block at the same root base with\n"
            f"    adb shell '{commit_command(outcome.target.device, outcome.layout.root_base)}' "
            f"< {path}\n"
            f"  the old windows are still on disk; only the directory block changed")


# ---------------------------------------------------------------------------------------------
# self test: fake images and GPT dumps in a temporary directory, no device
# ---------------------------------------------------------------------------------------------
def _make_image(path: Path, windows: Sequence[Tuple[str, int]],
                objects: Sequence[Tuple[str, int]] = (), seed: int = 1) -> None:
    import random
    rng = random.Random(seed)
    cursor = MIB
    entries: List[Tuple[str, int, int, int, int, bytes, bytes]] = []
    for name, size in windows:
        data = rng.randbytes(size)
        entries.append((name, cursor, size, 512, 1, hashlib.sha256(data).digest(), data))
        cursor = align_up(cursor + size, MIB)
    for name, size in objects:
        data = rng.randbytes(size)
        entries.append((name, cursor, size, 4096, 3, hashlib.sha256(data).digest(), data))
        cursor = align_up(cursor + size, MIB)
    image_bytes = cursor
    block = bytearray(HEADER)
    block[:8] = MAGIC
    struct.pack_into("<IIQ", block, 8, 2 if objects else 1, len(entries), image_bytes)
    for index, (name, offset, size, block_size, flags, digest, _) in enumerate(entries):
        struct.pack_into("<32sQQII32s40s", block, 24 + ENTRY * index, name.encode(), offset,
                         size, block_size, flags, digest, bytes(40))
    block[4064:4096] = hashlib.sha256(bytes(block[:4064])).digest()
    with path.open("wb") as handle:
        handle.truncate(image_bytes)
        handle.write(block)
        for (_, offset, _, _, _, _, data) in entries:
            handle.seek(offset)
            handle.write(data)


def _make_directory(entries: Sequence[Tuple[str, int, int, int, int]], image_bytes: int) -> bytes:
    block = bytearray(HEADER)
    block[:8] = MAGIC
    version = 2 if any(flags == 3 for _, _, _, _, flags in entries) else 1
    struct.pack_into("<IIQ", block, 8, version, len(entries), image_bytes)
    for index, (name, offset, size, block_size, flags) in enumerate(entries):
        digest = hashlib.sha256(f"sha-{name}".encode()).digest()
        struct.pack_into("<32sQQII32s40s", block, 24 + ENTRY * index, name.encode(), offset,
                         size, block_size, flags, digest, bytes(40))
    block[4064:4096] = hashlib.sha256(bytes(block[:4064])).digest()
    return bytes(block)


def _make_gpt_dump(path: Path, partitions: Sequence[Partition], lba_bytes: int = SECTOR) -> None:
    count = 32
    data = bytearray(34 * lba_bytes)
    header = bytearray(lba_bytes)
    header[0:8] = b"EFI PART"
    struct.pack_into("<I", header, 8, 0x00010000)
    struct.pack_into("<I", header, 12, 92)
    struct.pack_into("<Q", header, 24, 1)
    struct.pack_into("<Q", header, 32, 34)
    struct.pack_into("<Q", header, 40, 6)
    struct.pack_into("<Q", header, 48, 1000)
    struct.pack_into("<Q", header, 72, 2)
    struct.pack_into("<I", header, 80, count)
    struct.pack_into("<I", header, 84, ENTRY)
    entries = bytearray(count * ENTRY)
    for index, partition in enumerate(partitions):
        base = index * ENTRY
        entries[base:base + 16] = bytes([0x11]) * 16
        struct.pack_into("<Q", entries, base + 32, partition.first_lba)
        struct.pack_into("<Q", entries, base + 40, partition.last_lba)
        for char_index, char in enumerate(partition.name.encode("ascii")[:35]):
            struct.pack_into("<H", entries, base + 56 + 2 * char_index, char)
    struct.pack_into("<I", header, 88, zlib.crc32(bytes(entries)) & 0xFFFFFFFF)
    crc_header = bytearray(header[:92])
    crc_header[16:20] = b"\0\0\0\0"
    struct.pack_into("<I", header, 16, zlib.crc32(bytes(crc_header)) & 0xFFFFFFFF)
    data[lba_bytes:2 * lba_bytes] = header[:lba_bytes]
    data[2 * lba_bytes:2 * lba_bytes + len(entries)] = entries
    path.write_bytes(bytes(data))


def _expect_error(fragment: str, callable_object) -> None:
    try:
        callable_object()
    except PlanError as error:
        if fragment not in str(error):
            raise AssertionError(f"error {error!r} does not mention {fragment!r}") from error
        return
    raise AssertionError(f"expected a PlanError mentioning {fragment!r}")


def self_test() -> int:
    checks = 0
    devnull = open(os.devnull, "w")
    with tempfile.TemporaryDirectory(prefix="liuqin-ufs-selftest-") as scratch:
        tmp = Path(scratch)
        print("self-test: fake images and GPT dumps under a temporary directory, no device")
        image = tmp / "userdata-root.img"
        _make_image(image, [("OS", 8192), ("SystemOS", 5120)])
        parsed = parse_directory(image.open("rb").read(HEADER), "self-test image")
        assert parsed.image_bytes == image.stat().st_size
        assert [entry.name for entry in parsed.entries] == ["OS", "SystemOS"]
        assert parsed.entries[0].offset == MIB and parsed.entries[1].offset == 2 * MIB
        verify_entry_data(image, parsed, progress=devnull)
        checks += 1
        print("PASS  image parse and window SHA-256 verification")

        corrupt = tmp / "corrupt.img"
        data = bytearray(image.read_bytes())
        data[parsed.entries[0].offset + 16] ^= 0xFF
        corrupt.write_bytes(bytes(data))
        _expect_error("hashes to", lambda: verify_entry_data(
            corrupt, parsed, progress=devnull))
        checks += 1
        corrupt_manifest = tmp / "corrupt-manifest.img"
        data = bytearray(image.read_bytes())
        data[80] ^= 0xFF
        corrupt_manifest.write_bytes(bytes(data))
        _expect_error("manifest SHA-256 mismatch", lambda: parse_directory(
            corrupt_manifest.open("rb").read(HEADER), "corrupt"))
        checks += 1
        print("PASS  corrupt window data / corrupt manifest refused")

        userdata = Partition("userdata", 100, 2147)
        linux_root = Partition("linux_root", 3000, 11191)
        raw_gpt = tmp / "sda-head.bin"
        _make_gpt_dump(raw_gpt, [userdata, linux_root])
        text_gpt = tmp / "sgdisk-p.txt"
        text_gpt.write_text(
            "Disk /dev/block/sda: 1.0 TiB, 1000 bytes\n"
            "Number  Start (sector)    End (sector)  Size       Code  Name\n"
            f"  35        {userdata.first_lba:>12}        {userdata.last_lba:>12}"
            f"   8.0 MiB    0700  userdata\n"
            f"  36        {linux_root.first_lba:>12}        {linux_root.last_lba:>12}"
            f"   32.0 GiB   8300  linux_root\n")
        for dump in (raw_gpt, text_gpt):
            partitions = load_gpt_dump(dump, None)
            names = {partition.name: partition for partition in partitions}
            assert names["userdata"].nbytes == 8 * MIB, names["userdata"].record()
            assert names["linux_root"].nbytes == 32 * MIB, names["linux_root"].record()
        checks += 1
        print("PASS  sgdisk text and raw GPT header dumps parse identically")

        fresh = build_layout(parsed, userdata, None)
        assert fresh.mode == "fresh" and fresh.placement == MIB
        assert fresh.image_bytes == parsed.image_bytes and fresh.manifest == parsed.raw
        assert fresh.write_start == 0 and fresh.write_end == parsed.image_bytes
        command = stream_command("/dev/block/by-name/userdata", fresh.dest_write_start,
                                 fresh.write_end - fresh.write_start)
        assert f"seek=0 count={parsed.image_bytes // SECTOR}" in command, command
        tiny = Partition("userdata", 100, 100)
        _expect_error("does not fit", lambda: build_layout(parsed, tiny, None))
        checks += 1
        print("PASS  fresh layout (verbatim, block 0 manifest) and fit refusal")

        existing_block = _make_directory([("OS", MIB, 8192, 512, 1),
                                          ("Data", 2 * MIB, 4096, 512, 1)], 3 * MIB)
        existing = read_existing_directory(existing_block, "self-test existing")
        assert existing is not None and len(existing.entries) == 2
        appended = build_layout(parsed, linux_root, existing)
        assert appended.mode == "append"
        assert appended.placement == align_up(3 * MIB + GUARD, ALIGN)
        assert [entry.name for entry in appended.entries] == ["OS", "Data", "SystemOS"]
        assert appended.entries[0].offset == appended.placement  # OS replaced and relocated
        assert appended.entries[1].offset == 2 * MIB             # Data kept in place
        assert appended.entries[0].sha256 == parsed.windows[0].sha256
        assert appended.manifest == build_manifest_block(appended)
        reparsed = parse_directory(appended.manifest, "self-test destination")
        assert reparsed.image_bytes == appended.image_bytes
        ranges = chunk_ranges(appended, 1 * MIB)
        assert len(ranges) == 2, ranges  # image data is 1 MiB + 8 KiB from the first window
        assert ranges[0] == (MIB, appended.placement, MIB), ranges
        assert ranges[1] == (2 * MIB, appended.placement + MIB,
                             appended.write_end - appended.write_start - MIB), ranges
        small = Partition("linux_root", 3000, 3000 + (20 * MIB) // SECTOR - 1)
        _expect_error("does not fit", lambda: build_layout(parsed, small, existing))
        checks += 1
        print("PASS  append placement (1 MiB aligned, 16 MiB guard), merge and fit refusal")

        image_v2 = tmp / "userdata-root-v2.img"
        _make_image(image_v2, [("OS", 8192)], objects=[("Trust", 4096)], seed=2)
        parsed_v2 = parse_directory(image_v2.open("rb").read(HEADER), "self-test v2")
        verify_entry_data(image_v2, parsed_v2, progress=devnull)
        with_object = build_layout(parsed_v2, linux_root, existing)
        assert parse_directory(with_object.manifest, "self-test v2 destination").version == 2
        assert len(with_object.objects) == 1
        checks += 1
        print("PASS  version 2 data objects stay version 2 in the merged manifest")

        assert read_existing_directory(bytes(HEADER), "zero") is None
        assert read_existing_directory(b"\x53\xef" + bytes(HEADER - 2), "ext4") is None
        checks += 1
        print("PASS  non-SHNGROOT first block selects the fresh layout")

        # Plan C: the image written at a byte offset inside the partition (after the f2fs tail).
        offset = 64 * MIB
        big = Partition("userdata", 100, 100 + (128 * MIB) // SECTOR - 1)
        shifted = build_layout(parsed, big, None, partition_offset=offset)
        assert shifted.mode == "fresh"
        assert shifted.partition_offset == offset and shifted.root_base == offset
        assert shifted.dest_write_start == offset
        assert shifted.write_start == 0 and shifted.write_end == parsed.image_bytes
        assert offset + shifted.image_bytes <= big.nbytes
        command = stream_command("/dev/block/by-name/userdata", shifted.dest_write_start,
                                 shifted.write_end - shifted.write_start)
        assert f"seek={offset // SECTOR}" in command, command
        checks += 1
        print("PASS  in-partition offset layout (image after the fs tail)")

        narrow = Partition("userdata", 100, 100 + (72 * MIB) // SECTOR - 1)
        _expect_error("does not fit", lambda: build_layout(parsed, narrow, None,
                                                          partition_offset=70 * MIB))
        checks += 1
        print("PASS  offset beyond the partition end is refused")

        _expect_error("1 MiB aligned", lambda: build_layout(parsed, big, None,
                                                            partition_offset=offset + 4096))
        checks += 1
        print("PASS  non-1 MiB-aligned offset is refused")

        broken = bytearray(parsed.raw)
        struct.pack_into("<Q", broken, 24 + 32, parsed.image_bytes)
        broken[4064:4096] = hashlib.sha256(bytes(broken[:4064])).digest()
        _expect_error("outside image_bytes", lambda: parse_directory(bytes(broken), "broken"))
        checks += 1
        print("PASS  out-of-bounds manifest entry refused")

    devnull.close()
    print(f"SELF-TEST PASS ({checks} checks, temporary files only, no device touched)")
    return 0


# ---------------------------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------------------------
def parse_args(argv: Sequence[str]):
    parser = argparse.ArgumentParser(
        description="Plan/apply the unixnu SHNGROOT userdata image on a liuqin GPT partition.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples", 1)[1] if "Examples" in __doc__ else None)
    parser.add_argument("--self-test", action="store_true",
                        help="build fake images/GPT dumps in a temporary directory and stop")
    parser.add_argument("--image", type=Path, help="SHNGROOT image from unixnu build userdata")
    parser.add_argument("--partition", default=DEFAULT_PARTITION,
                        help=f"target GPT partition name (default {DEFAULT_PARTITION!r})")
    parser.add_argument("--first-lba", type=parse_int, help="partition first LBA (inclusive)")
    parser.add_argument("--last-lba", type=parse_int, help="partition last LBA (inclusive)")
    parser.add_argument("--lba-bytes", type=int, default=None,
                        help="bytes per LBA for the geometry (default 4096)")
    parser.add_argument("--gpt-dump", type=Path,
                        help="sgdisk -p text or raw primary-GPT dump to read the geometry from")
    parser.add_argument("--existing-header", type=Path,
                        help="local 4 KiB dump of the target block at --partition-offset "
                             "(default: read via adb)")
    parser.add_argument("--empty", action="store_true",
                        help="plan the fresh layout even if adb could show a directory")
    parser.add_argument("--partition-offset", type=parse_int, default=0,
                        help="byte offset inside the partition the image is written at "
                             "(default 0; must be 1 MiB aligned, e.g. the f2fs tail start)")
    parser.add_argument("--adb", help="adb executable (Windows path is converted with wslpath)")
    parser.add_argument("--serial", help="adb serial (-s)")
    parser.add_argument("--method", choices=("push", "stream"), default="push",
                        help="how --apply transfers the image over adb (default push chunks)")
    parser.add_argument("--chunk-mib", type=int, default=DEFAULT_CHUNK_MIB,
                        help=f"chunk size for --method push (default {DEFAULT_CHUNK_MIB} MiB)")
    parser.add_argument("--device-tmp", default="/tmp",
                        help="device directory for pushed chunks (default /tmp, a recovery tmpfs)")
    parser.add_argument("--transport", choices=("auto", "adb", "fastboot"), default="auto",
                        help="which command set to print (default auto: both where valid)")
    parser.add_argument("--fastboot", default="fastboot",
                        help="fastboot executable used only for the printed command")
    parser.add_argument("--apply", action="store_true", help="actually write the target (adb)")
    parser.add_argument("--ack-backup", action="store_true",
                        help="confirm the Android full-device backup is done (required by --apply)")
    parser.add_argument("--force-in-place", action="store_true",
                        help="allow a fresh-layout write over an existing SHNGROOT directory")
    parser.add_argument("--output-dir", type=Path,
                        help="where --apply stores plan.json/manifest-before.bin")
    args = parser.parse_args(argv)
    if args.self_test:
        return args
    if not args.image:
        parser.error("--image is required (or use --self-test)")
    if args.transport == "fastboot" and args.apply:
        parser.error("--transport fastboot only prints commands; writes go through adb")
    if args.apply and not args.adb:
        parser.error("--apply needs --adb (the write runs on the device under recovery/root adb)")
    if args.chunk_mib <= 0:
        parser.error("--chunk-mib must be positive")
    if args.partition_offset < 0 or args.partition_offset % MIB:
        parser.error("--partition-offset must be a non-negative 1 MiB multiple")
    return args


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if args.self_test:
        return self_test()
    if args.partition_offset < 0 or args.partition_offset % MIB:
        raise PlanError("--partition-offset must be a non-negative 1 MiB multiple")

    warnings: List[str] = []
    source_image = Path(args.image).expanduser()
    try:
        with source_image.open("rb") as handle:
            directory_block = handle.read(HEADER)
    except OSError as error:
        raise PlanError(f"--image: cannot read {source_image}: {error}") from error
    if len(directory_block) != HEADER:
        raise PlanError(f"--image: {source_image} is shorter than one 4 KiB block")
    print(f"image: {source_image.resolve()} ({human_bytes(source_image.stat().st_size)})")
    source = parse_directory(directory_block, "image")
    print(f"manifest: version {source.version}, {len(source.entries)} entries, image_bytes "
          f"{human_bytes(source.image_bytes)}")
    verify_entry_data(source_image, source)
    partition = resolve_partition(args)
    print(f"partition: {partition.name} LBAs {partition.first_lba}..{partition.last_lba} x "
          f"{partition.lba_bytes} = {human_bytes(partition.nbytes)}")

    # Where does the existing directory come from?  An explicit dump, a live read-only adb
    # read, or nothing (fresh; warned about).
    existing: Optional[Directory] = None
    live_header: Optional[bytes] = None
    adb: Optional[Adb] = None
    if args.adb:
        adb = Adb(args.adb, args.serial)
        try:
            target = adb_open_target(adb, partition)
            live_header = read_target_directory(adb, target, args.partition_offset)
        except PlanError as error:
            raise PlanError(f"cannot plan against the device: {error}\n"
                            f"Drop --adb for an offline plan (--existing-header/--empty), or fix "
                            f"the device connection") from error
    elif args.existing_header:
        try:
            live_header = Path(args.existing_header).expanduser().read_bytes()
        except OSError as error:
            raise PlanError(f"--existing-header: cannot read: {error}") from error
        if len(live_header) != HEADER:
            raise PlanError(f"--existing-header: expected {HEADER} bytes, got {len(live_header)}")
    if args.empty:
        if live_header is not None and read_existing_directory(live_header, "device directory"):
            warnings.append("--empty was given but the target's first block is a SHNGROOT "
                            "directory; the fresh layout overwrites it in place (not crash-safe)")
    elif live_header is not None:
        existing = read_existing_directory(live_header, "device directory")
    else:
        warnings.append("no --adb and no --existing-header given: assuming the target holds no "
                        "SHNGROOT directory (fresh layout). Pass --existing-header or --adb to "
                        "plan against what the device actually has before --apply.")

    layout = build_layout(source, partition, existing, args.partition_offset)
    if args.transport == "fastboot" and layout.mode == "append":
        raise PlanError("--transport fastboot cannot stage behind an existing directory "
                        "(fastboot flash always starts at block 0); use the adb/dd path")
    print_plan(args, layout)
    for warning in warnings:
        print(f"\n  WARNING: {warning}")
    if not args.apply:
        return 0

    assert adb is not None
    outcome = apply_plan(args, adb, layout)
    print("\nrollback: " + rollback_hint(outcome))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except PlanError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
