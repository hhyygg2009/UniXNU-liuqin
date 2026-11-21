# 2026-11-19 bring-up session: what was found and fixed

This file records the engineering conclusions of one long on-device session. Every
claim below was observed on the tablet (Xiaomi Pad 6 Pro, `liuqin`, SM8475) via
`fastboot boot` of an in-memory image; nothing here was written to any partition.

## Boot progress that was reached

```
U139:probe-nub            IOKit matches NativeUfs a second time, after bsd_init
U140:start-enter          NativeUfs::start() entered
U141:start-root
U142:blk-registered       NativeUfsBlock::start(): registerService() returned
U143:blk-matched          startMatching() returned
U146:root-pub-SystemOS    the SystemOS window was started as a block device
U147:root-media-timeout   no IOMedia appeared for it -> UFS ROOT FAILURE
```

Before this session the boot never got past the storage bring-up; now it runs the
whole of `bsd_init` up to the buffer cache and then publishes block devices.

## Fixes that are confirmed on hardware

1. **`liuqin_usb=1` blocked the boot at `bsd_init.c:usb_start`.**
   `liuqin_usb_debug_start()` hangs on this platform; the boot arg is now removed.
   Milestone counter went 114 -> 136 once it was off.

2. **`IOSleep()` never returns on this platform.**
   The publisher waited for IOMedia with `300 x IOSleep(100)`. The first sleep never
   woke, so the wait neither succeeded nor timed out and the panel showed no further
   progress at all. Replaced with a spin + `thread_yield_to_preemption()` loop, with
   `IOService::startDeferredMatches()` poked between passes. The wait now terminates
   and reports `root-media-timeout` instead of hanging forever.

3. **`IOBlockStorageDevice` relocations needed the prelink slide.**
   `scripts/build-liuqin-ufs-bindings.py` bound the storage-family symbols to the raw
   21H461 kernelcache VAs, while `emit_prelink.py` places the kext at `cache VA + slide`
   (`-0x1000000`). `new NativeUfsBlock` therefore branched 16 MiB above the real
   constructor, into zero-filled `__PLK_TEXT_EXEC` padding (`UDF #0`). The constructor
   never ran and block publication could not start. Fixed by applying the board-pinned
   slide to the bindings; the same stale value is what the H16 metaclass "scribble" was.

4. **A device-tree parent cannot publish services.**
   `IOService::registerService()` silently returns when the service has no path to the
   registry root in `gIOServicePlane`. The windows were attached to `NativeUfs`, which is
   attached to the device-tree node `xiaomi,liuqin` — that node has no service-plane
   parent, so nothing was ever registered (`state=0x0`, plane counts all zero).
   The windows are now attached to, and started with, the platform expert
   (`ArmDTPlatform`), which does reach the root: `REGPUBLISH ok` is printed for both
   windows.

## Instrumentation that proved useful (and one that lies)

* `liuqin_milestone(tag, msg)` writes the panel directly. It is the only reliable
  console very early, and its counter makes the last reached step unambiguous.
* A persistent journal page in the unused tail of the `unixnu` partition works, but
  **only during early boot**: UFS writes start to hang after `bsd_init`'s buffer-cache
  setup, so journal probes placed later block the very path they are meant to observe.
  Milestone journaling was implemented, then removed again for this reason.
* `IOService::getMatchingServices()` returns zero even for classes that definitely have
  registered instances at this point, so plane counts are **not** a valid signal here.

## Correction: startRoot runs once, and publication timing is not the problem

A reliable state dump was added to the panic summary (the panel buffer loses and reorders
lines, so the panic block is the only trustworthy channel on this board):

```
PUBSTATE entries=1 flag=1 this=0xfffffff27cab47e0 first=0xfffffff27cab47e0 published=1 busy=0
```

* `entries=1` — `startRoot()` runs **once**. The earlier "it runs twice" reading came from the
  panel reordering lines (`U143:start-root` followed by `U14:blk-registered`).
* `flag=1` — the idempotence guard is set; nothing is wrong with it.
* `published=1` — only the first window (SystemOS) was started, then its media wait expired.

Deferring publication was then tried: the publisher waits for the ufshc nub match
(`gLatePublishGate`), which happens after `bsd_init` when matching is known to work. The gate
was already open when the publisher ran, publication still happened late, and the media still
never appeared — so "published too early" is not the explanation either.

## What is left to determine

Exactly one question: after `IOBlockStorageDevice::start()` succeeds and the service is
registered (`REGPUBLISH ok`), why does no `IOBlockStorageDriver` claim the device and create an
`IOMedia`?

Diagnostics that exist but are not yet conclusive:

* `IOService::getMatchingServices()` returns zero even for classes with live instances here, so
  plane counts are meaningless.
* `IORegistryIterator` over the platform expert should show what is really published, but the
  panel loses that line before a photograph can capture it.

**Next step (do this first):** build the registry-walk string into `gRootDiagnosticText` so it is
printed inside the panic block, the one output that survives on this panel. That single line
says whether an `IOBlockStorageDriver` exists, whether any `IOMedia` exists, or neither.

Depending on the answer:

* no driver at all -> check that IOStorageFamily's driver personality is registered and that
  matching runs for a service created this early; possibly publish only after the platform
  expert has published `IOResources`.
* driver but no media -> the driver's start path is failing; check that reads issued through
  `doAsyncReadWrite` still work after `bsd_init` (UFS writes are known to stall at that point,
  and reads share the same transport slots).

An idempotence guard (`gRootPublicationDone`) was added and is present in the kernel
image (the `root-already-published` string is in the binary) but did not fire on the
second call, so the early pass apparently does not reach the same code path that sets
it. Next step: print the flag value at `startRoot()` entry, and if the two calls really
belong to different driver instances, move the state out of a translation-unit static
(e.g. a kernel global keyed by provider) or make publication a property of the
`NativeUfs` instance that owns the windows.
