#!/usr/bin/env python3
"""Manual XNU changes for liuqin v60 -- ported from UniXNU-pong / zizhan.

Run against a prepared XNU work tree (UNIXNU_PATCH_LIST=manual):

    python3 0001-liuqin-waiters-and-resource-notify.py /root/work/UniXNU-new/out/liuqin/tree/.cache/xnu-work

It is idempotent: re-running only reports "already patched".

What it does, and why:

1. osfmk/kern/thread.c -- liuqin_dump_waiting_threads()

   Writes one line per waiting thread (tid, K/U, block hint, wait event,
   continuation, saved lr and four frame-pointer frames) into a caller buffer.
   Ported from zizhan's patch 0005, which they used to diagnose a stalled root
   mount. Two changes from the original: the text goes to a buffer instead of
   printf, because on this board only the panic block survives to a photograph,
   and the thread count is capped at 10.

2. iokit/Kernel/IOService.cpp -- publishResource()

   Ported from zizhan's patch 0006. When a driver start() never returns,
   IOResources' configuration stays running, so the re-match requested by
   publishResource() is deferred forever and any earlier
   waitForMatchingService() -- APFS waiting for "IOBSD" -- never wakes. The fix
   delivers the matched notification immediately when IOResources is still
   configuring.

3. iokit/Kernel/IOService.cpp -- liuqin_iokit_job_counts()

   Exposes gOutstandingJobs / gNumConfigThreads / gNumWaitingThreads so the
   driver can tell "the matching job was queued but no configuration thread
   ever took it" from "the job ran and produced nothing". That distinction is
   exactly what our v58 measurement could not make.
"""
import sys
from pathlib import Path

if len(sys.argv) > 1:
    X = Path(sys.argv[1])
else:
    X = Path('/root/work/UniXNU-new/out/liuqin/tree/.cache/xnu-work')

THREAD_C = X / 'osfmk/kern/thread.c'
IOSERVICE_CPP = X / 'iokit/Kernel/IOService.cpp'

WAITERS = r'''
/*
 * liuqin (ported from UniXNU-pong / zizhan patch 0005): one line per waiting thread with its
 * wait event, block hint, continuation and the first frames of its saved kernel stack.
 *
 * The text is written into a caller buffer instead of printf, because on this board the only
 * output that reliably survives is the panic block, and the driver appends it there. Frames are
 * read only while they stay inside the thread's own kernel stack; the kernel carries no slide,
 * so the addresses symbolicate directly against the built kernel Mach-O.
 */
void liuqin_dump_waiting_threads(char *out, size_t size);

#if defined(__arm64__)
#if __has_feature(ptrauth_calls)
#define LIUQIN_STRIP(p) ((uintptr_t)ptrauth_strip((void *)(p), ptrauth_key_return_address))
#else
#define LIUQIN_STRIP(p) ((uintptr_t)(p))
#endif

void
liuqin_dump_waiting_threads(char *out, size_t size)
{
	thread_t thread;
	unsigned shown = 0;
	int used;

	if (out == NULL || size < 64) {
		return;
	}
	used = snprintf(out, size, "WAITERS begin\n");
	lck_mtx_lock(&tasks_threads_lock);
	queue_iterate(&threads, thread, thread_t, threads) {
		char line[192];
		int n;

		if (!(thread->state & TH_WAIT) || shown >= 10) {
			continue;
		}
		shown++;
		n = snprintf(line, sizeof(line), "W %llu%s h=%d ev=%llx cont=%lx",
		    (unsigned long long)thread_tid(thread),
		    get_threadtask(thread) == kernel_task ? "k" : "u",
		    (int)thread->block_hint, (unsigned long long)thread->wait_event,
		    (unsigned long)LIUQIN_STRIP(thread->continuation));
		if (thread->kernel_stack != 0 && thread->machine.kstackptr != NULL) {
			struct arm_kernel_saved_state *ss =
			    &((thread_kernel_state_t)thread->machine.kstackptr)->machine.ss;
			uintptr_t lo = thread->kernel_stack, hi = lo + kernel_stack_size;
			uintptr_t fp = ss->fp;

			n += snprintf(line + n, sizeof(line) - n, " lr=%lx bt",
			    (unsigned long)LIUQIN_STRIP(ss->lr));
			for (int frame = 0; frame < 4 && fp >= lo && fp + 16 <= hi && (fp & 7) == 0 &&
			    n > 0 && n < (int)sizeof(line) - 12; frame++) {
				uintptr_t next = ((uintptr_t *)fp)[0];

				n += snprintf(line + n, sizeof(line) - n, " %lx",
				    (unsigned long)LIUQIN_STRIP(((uintptr_t *)fp)[1]));
				if (next <= fp) {
					break;
				}
				fp = next;
			}
		}
		if (used > 0 && used < (int)size) {
			used += snprintf(out + used, size - used, "%s\n", line);
		}
	}
	lck_mtx_unlock(&tasks_threads_lock);
	if (used > 0 && used < (int)size) {
		snprintf(out + used, size - used, "WAITERS end %u\n", shown);
	}
}
#endif /* __arm64__ */
'''

PUBLISH_OLD = '''	gIOResourceGenerationCount++;
	gIOResources->registerService();
}'''

PUBLISH_NEW = '''	gIOResourceGenerationCount++;
	gIOResources->registerService();

	/*
	 * liuqin (ported from UniXNU-pong / zizhan patch 0006): a driver start() that never returns
	 * keeps IOResources' configuration running, so the re-match requested above is deferred for
	 * good and a waitForMatchingService() that began before this publish -- APFS waiting for
	 * "IOBSD" -- never wakes. Deliver the matched notification now, exactly as that deferred
	 * re-match would have. deliverNotification() is a panic stub, so do what doServiceMatch does.
	 */
	{
		bool configRunning;

		gIOResources->lockForArbitration();
		configRunning = (0 != (gIOResources->__state[1] & kIOServiceConfigRunning));
		gIOResources->unlockForArbitration();
		if (configRunning) {
			OSArray * notifiers;

			notifiers = gIOResources->copyNotifiers(gIOMatchedNotification,
			    kIOServiceMatchedState, 0xffffffff);
			gIOResources->invokeNotifiers(&notifiers);
		}
	}
}'''

JOB_COUNTS = '''
/*
 * liuqin: the IOKit configuration-thread accounting, so a driver can tell "the matching job was
 * queued but no configuration thread ever took it" from "the job ran and produced nothing".
 * Written for the pre-panic report; returns gCPUsRunning.
 */
extern "C" unsigned
liuqin_iokit_job_counts(unsigned * outstanding, unsigned * configThreads, unsigned * waitingThreads,
    unsigned * maxConfigThreads)
{
	if (outstanding) {
		*outstanding = (unsigned)gOutstandingJobs;
	}
	if (configThreads) {
		*configThreads = (unsigned)gNumConfigThreads;
	}
	if (waitingThreads) {
		*waitingThreads = (unsigned)gNumWaitingThreads;
	}
	if (maxConfigThreads) {
		*maxConfigThreads = (unsigned)gMaxConfigThreads;
	}
	return (unsigned)gCPUsRunning;
}
'''


def patch_thread_c():
    src = THREAD_C.read_text()
    if 'liuqin_dump_waiting_threads' in src:
        print('thread.c: already patched')
        return
    if '#include <kern/thread_kernel_state.h>' not in src:
        anchor = '#include <kern/thread.h>'
        assert anchor in src, 'thread.c include anchor missing'
        src = src.replace(anchor, anchor + '\n#include <kern/thread_kernel_state.h>', 1)
    THREAD_C.write_text(src.rstrip('\n') + '\n' + WAITERS)
    print('thread.c: liuqin_dump_waiting_threads appended')


def patch_ioservice():
    src = IOSERVICE_CPP.read_text()
    if 'zizhan patch 0006' in src:
        print('IOService.cpp: publishResource already patched')
    else:
        assert src.count(PUBLISH_OLD) == 1, 'publishResource anchor missing'
        src = src.replace(PUBLISH_OLD, PUBLISH_NEW, 1)
        print('IOService.cpp: publishResource patched')
    if 'liuqin_iokit_job_counts' not in src:
        src = src.rstrip('\n') + '\n' + JOB_COUNTS
        print('IOService.cpp: liuqin_iokit_job_counts appended')
    IOSERVICE_CPP.write_text(src)


if __name__ == '__main__':
    assert THREAD_C.is_file() and IOSERVICE_CPP.is_file(), f'not an XNU tree: {X}'
    patch_thread_c()
    patch_ioservice()
