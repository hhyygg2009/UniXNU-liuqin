# XNU changes applied by script

`patches/xnu/*.patch` are applied by hand with `patch -p1` into the prepared work
tree. The two changes below sit on top of that same tree and only append or
replace a few well-known lines, so they are recorded as an idempotent script:

```bash
python3 patches/xnu/manual/0001-liuqin-waiters-and-resource-notify.py \
        /root/work/UniXNU-new/out/liuqin/tree/.cache/xnu-work
```

Both are ported from the working SM8475 reference (UniXNU-pong -> zizhan):

1. `osfmk/kern/thread.c`: `liuqin_dump_waiting_threads()` (zizhan patch 0005).
   One line per waiting thread with wait event, block hint, continuation and four
   frames, written into a caller buffer so it can travel inside the panic text.
2. `iokit/Kernel/IOService.cpp`: `publishResource()` delivers the matched
   notification immediately when `IOResources` is still configuring (zizhan patch
   0006), and `liuqin_iokit_job_counts()` exposes the configuration-thread
   accounting.
