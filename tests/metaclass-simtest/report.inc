extern "C" void (*liuqin_metaclass_poison_report) (const char *tag, const void *obj,
    unsigned long long bad) = NULL;

#define LIUQIN_FINGERPRINT_MAX 2
static unsigned int liuqinFingerprints;

__attribute__((noinline)) static void
liuqin_fingerprint(const char *tag, const OSMetaClass *me, const void *bad)
{
	if (liuqin_metaclass_poison_report == NULL ||
	    liuqinFingerprints >= LIUQIN_FINGERPRINT_MAX) {
		return;
	}
	liuqinFingerprints++;
	liuqin_metaclass_poison_report(tag, me, (unsigned long long) (uintptr_t) bad);
}
