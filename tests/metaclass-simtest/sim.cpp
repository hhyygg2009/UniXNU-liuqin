// Host simulation of the liuqin H16 metaclass family: the base-class policy, the
// scan that used to drop the poisoned slot, and the H16-F repair that keeps it.
//
// Five files are extracted verbatim by extract.py: helper.inc, witness.inc and gate.inc
// come from the production driver, ring.inc is the kernel's console mirror and report.inc is
// the libkern half of H16-G, both pulled out of the XNU patches. The only substitution the
// script makes is liuqinPtrOk's address floor, which cannot mean anything on a host address
// space. Everything below the includes is the harness: the IOKit batch, a mirror of
// postModLoad's first pass, and the console the fingerprint searches.
// The scan loop below mirrors OSMetaClass::postModLoad's first pass in
// patches/xnu/zzzzzzzzzz-liuqin-metaclass-stalled-diagnostics.patch, and the
// harness deliberately calls the production liuqin_block_super_fix() through the
// same function-pointer variable the patched kernel uses.
#include <cstdarg>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cassert>
#include <string>
#include <vector>

// ---- stub IOKit world ------------------------------------------------------
struct OSSymbol;

// OSMetaClass with the arm64 field order the v37 disassembly confirmed:
// vtable, reserved, superClassLink (+0x10), className (+0x18), classSize (+0x20).
struct OSMetaClass {
    void        *vtable = nullptr;
    void        *reserved = nullptr;          // ExpansionData*
    OSMetaClass *superClassLink = nullptr;
    const char  *className = nullptr;         // a C string until the second pass
    unsigned int classSize = 0;

    const char *getClassName() const { return className; }
    unsigned int getClassSize() const { return classSize; }
    static const OSMetaClass *getMetaClassWithName(const OSSymbol *name);
};

// The family the driver is compiled against. sizeof() is the "compiled" side of the ABI
// check and the recorded classSize the "live" side; the panel says sz=248.
struct IOBlockStorageDevice {
    static OSMetaClass gMetaClass;
    char pad[248];   // sizeof(IOBlockStorageDevice) == 248 == the panel's sz=
};
struct NativeUfsBlock {
    static OSMetaClass gMetaClass;
};
OSMetaClass IOBlockStorageDevice::gMetaClass;
OSMetaClass NativeUfsBlock::gMetaClass;

struct OSSymbol {
    const char *s = nullptr;
    void release() const {}
    static const OSSymbol *withCString(const char *c);
};

static std::vector<OSMetaClass*> gAllClasses;              // sAllClassesDict
// the global the patched OSMetaClass.cpp defines; the driver writes it, the scan reads it
extern "C" const void *(*liuqin_metaclass_super_fix)(const char *className) = nullptr;  // -Wextern-initializer is expected: the harness owns this global

const OSMetaClass *OSMetaClass::getMetaClassWithName(const OSSymbol *name)
{
    for (auto *c : gAllClasses) if (c->className && name && !strcmp(c->className, name->s)) return c;
    return nullptr;
}
const OSSymbol *OSSymbol::withCString(const char *c)
{
    static OSSymbol st;
    st.s = c;
    return &st;
}

// The synthetic image: ptr_ok means "an object this image actually contains", which is
// what the kernel's "above the kernel text base" test means on device. The ASCII word
// 0x6131373030666666 from the panel is deliberately not in it.
static std::vector<const void*> gImage;
static void addToImage(const void *p) { gImage.push_back(p); }
static inline bool liuqinSimPtrOk(const void *p)
{
    if (!p) return true;
    for (auto *q : gImage) if (q == p) return true;
    return false;
}

static const char *gMilestoneTag = "", *gMilestoneMsg = "";
static void liuqin_milestone(const char *t, const char *m)
{
    gMilestoneTag = t; gMilestoneMsg = m;
    printf("milestone %s:%s\n", t, m);
}
// ---- the console mirror the H16-G fingerprint searches ----------------------
// ring.inc is lq_ring / lq_append / lq_copy exactly as bsd/dev/liuqin_debug.c gets them, so
// the bytes the search reads are the bytes the kernel prints. log_lock is stood in for by
// simRingLockBusy, whose two production consequences are reproduced together: an append is
// dropped and counted, a copy simply returns 0. The panel is a *separate* channel: it always
// sees the text and it keeps only its last lines, which is why v37 lost a conclusion and why
// the driver replays evidence -- assertions about "what a photo would show" read simPanel.
#include "ring.inc"

static struct lq_ring simRing;                    // one megabyte, the kernel's own size
static bool simRingLockBusy = false;
static unsigned long long simRingDropped = 0;
static std::string simPanel;
static const size_t SIM_PANEL_KEEP = 512;         // the panel only ever shows the tail

static void simConsole(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

static void liuqin_debug_append(const char *data, int size)
{
    if (size <= 0) return;
    if (simRingLockBusy) { simRingDropped += (unsigned long long) size; return; }
    lq_append(&simRing, data, (unsigned) size);
}

static bool simQuiet = false;                    // filler lines are state, not test output

static void liuqin_debug_puts(const char *s)
{
    liuqin_debug_append(s, (int) strlen(s));      // production order: mirror, then panel
    simPanel += s;
    if (simPanel.size() > SIM_PANEL_KEEP)
        simPanel = simPanel.substr(simPanel.size() - SIM_PANEL_KEEP);
    if (!simQuiet) fputs(s, stdout);
}

static unsigned liuqin_debug_copy(uint64_t *pos, char *out, unsigned max)
{
    if (!max || simRingLockBusy) return 0;        // cannot take log_lock: report, never spin
    return lq_copy(&simRing, pos, out, max);
}

static uint64_t liuqin_debug_total(void) { return simRing.total; }

static void simConsole(const char *fmt, ...)
{
    char line[192];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    liuqin_debug_puts(line);                      // what liuqin_slot_dump/puts do in kernel
}

// Test-side readers. mirrorFindAt searches the whole readable ring through the production
// copy path and returns the offset of the first occurrence, so "this line was printed before
// that line" is an assertion about the mirror rather than about stdout.
static std::string mirrorText()
{
    std::string all;
    uint64_t pos = 0;
    char chunk[256];
    for (;;) {
        unsigned got = liuqin_debug_copy(&pos, chunk, sizeof chunk);
        if (!got) break;
        all.append(chunk, got);
    }
    return all;
}

static long long mirrorFindAt(const char *needle)
{
    size_t f = mirrorText().find(needle);
    return f == std::string::npos ? -1 : (long long) f;
}

static unsigned panelCount(const char *needle)
{
    unsigned n = 0;
    for (size_t f = simPanel.find(needle); f != std::string::npos; f = simPanel.find(needle, f + 1))
        n++;
    return n;
}

static void simEmitLines(unsigned count)          // filler that walks the mirror forward
{
    static const std::string filler = std::string(63, 'x') + "\n";   // exactly 64 bytes
    simQuiet = true;
    for (unsigned i = 0; i < count; i++) liuqin_debug_puts(filler.c_str());
    simQuiet = false;                              // not printed: 1 MiB of it is not evidence
}

// A line the boot printed but the harness does not echo: still mirrored, still on the
// panel, just not test output. Used where the point is what came after it.
static void simEmitLinesQuiet(const char *s)
{
    simQuiet = true;
    liuqin_debug_puts(s);
    simQuiet = false;
}

static bool panelHas(const char *line) { return simPanel.find(line) != std::string::npos; }
static bool mirrorHas(const char *line) { return mirrorFindAt(line) >= 0; }

static unsigned long long pack8(const char *s)    // the word as memory holds it: s[0] low
{
    unsigned long long v = 0;
    for (int i = 7; i >= 0; i--) v = (v << 8) | (unsigned char) s[i];
    return v;
}

static volatile const char *native_ufs_root_failure = nullptr;
static unsigned simSkipGate = 0;                 // boot-arg stand-in, see below
static void PE_parse_boot_argn(const char *, unsigned *out, unsigned) { *out = simSkipGate; }

// The extracted production code: driver side (H16-F/G) and the libkern call site (H16-G).
#include "helper.inc"
#include "witness.inc"
#include "report.inc"

// Production keeps one mirror and one fingerprint budget per boot; the harness runs many
// boots inside one process, so every H16-G case wipes all three pieces of state (the mirror
// here, the counter in report.inc, the replayed line in helper.inc).
static void simBootReset()
{
    memset(&simRing, 0, sizeof simRing);
    simRingLockBusy = false;
    simRingDropped = 0;
    simPanel.clear();
    liuqinFingerprints = 0;
    liuqinFpEvidence[0] = '\0';
}

// ---- the mirrored first pass of OSMetaClass::postModLoad --------------------
static unsigned gDeepest = 0;
static bool gHealInScan = true;                  // the H16-F branch
// naive = the unpatched walk (v35..v37), guarded = the shipped scan (v38), and with
// gHealInScan the v39 behaviour. Returns the number of dropped slots, -1 when the naive
// walk actually faulted, -2 when the duplicate branch fired.
static int scanPass(std::vector<OSMetaClass*> &stalled, std::vector<OSMetaClass*> &all,
                    bool naive, unsigned *healedOut = nullptr)
{
    int bad = 0, healed = 0;
    for (size_t i = 0; i < stalled.size(); i++) {
        OSMetaClass *me = stalled[i];
        if (me && !liuqinPtrOk(me)) {                 // the M:dirty branch of the patch
            simConsole("M:dirty[%zu] p=%p\n", i, (const void *) me);
            stalled[i] = nullptr; bad++; continue;
        }
        if (!me) continue;
        for (auto *c : all) if (c->className && me->className &&
                                !strcmp(c->className, me->className)) {
            printf("PANIC would fire: duplicate %s\n", me->className); return -2;
        }
        if (naive) {                              // the v37 fault shape (no guard existed)
            OSMetaClass *walk = me;
            while ((walk = walk->superClassLink)) {
                if (!liuqinPtrOk(walk)) {
                    printf("FAULT: data abort at postModLoad+0x2d4, x24=%p (far=x24+0x10)\n",
                           (void*)walk);
                    return -1;
                }
            }
            continue;
        }
        const void *badLink = nullptr;
        int depth = liuqinChainDepth(me, &badLink);
        if (depth < 0) {
            // The patch calls the fingerprint strictly before it tries to repair anything,
            // so the order of these two lines is itself a mirrored fact (F1 asserts it).
            liuqin_fingerprint("scan", me, badLink);
            // verbatim in intent with the patched scan: ask the driver for the link-time
            // superclass, validate, write it, re-walk; keep the class when that works.
            const void *fix = (gHealInScan && liuqin_metaclass_super_fix)
                ? liuqin_metaclass_super_fix(me->className) : nullptr;
            if (fix && liuqinPtrOk(fix)) {
                me->superClassLink = const_cast<OSMetaClass*>((const OSMetaClass*)fix);
                badLink = nullptr;
                depth = liuqinChainDepth(me, &badLink);
                if (depth > 0) {
                    simConsole("M:bad[%zu] fixed n=%s bad=%p\n", i, me->className, fix);
                    healed++;
                }
            }
            if (depth < 0) {
                simConsole("M:bad[%zu] bad-chain n=%s bad=%p\n", i, me->className, badLink);
                stalled[i] = nullptr; bad++; continue;
            }
        }
        if ((unsigned)depth > gDeepest) gDeepest = (unsigned)depth;
    }
    if (!naive) {
        for (auto *me : stalled) if (me) all.push_back(me);
        stalled.clear();
    }
    if (healedOut) *healedOut = (unsigned)healed;
    return bad;
}

// startRoot's prologue, extracted verbatim (calls the witness and the policy above).
static bool startRootPrologue()
{
    native_ufs_root_failure = nullptr;
#include "gate.inc"
    return true;
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    addToImage(&IOBlockStorageDevice::gMetaClass);
    addToImage(&NativeUfsBlock::gMetaClass);
    // Class names are pointers the image must contain (on device they are __TEXT cstrings;
    // the repair hook validates them before comparing). Own the storage so the addresses
    // are stable, and let the pool do whatever it likes with literals.
    char nameOurs[] = "NativeUfsBlock", nameFamily[] = "IOBlockStorageDevice";
    char nameShort[] = "NativeUfsBlo", nameOther[] = "SomethingElse";
    addToImage(nameOurs); addToImage(nameFamily); addToImage(nameShort); addToImage(nameOther);
    // witness.inc passes the literal "NativeUfsBlock" exactly as the driver does; identical
    // literals share one address within a translation unit, so registering it here models
    // the __TEXT cstring the kernel would be pointing at.
    addToImage("NativeUfsBlock");

    // ---- scenario A: the dead theory, kept as documentation -----------------
    // v35..v36c believed a second "IOBlockStorageDevice" name in the batch was the crash:
    // postModLoad's duplicate branch panics, and the panic format dereferences
    // orig->reserved->kext after Apple freed that ExpansionData. Model it once, so the
    // record of why the gate existed is executable.
    {
        OSMetaClass family{};
        family.className = nameFamily;
        std::vector<OSMetaClass*> all{&family};
        OSMetaClass dup{}; dup.className = nameFamily;               // the aliased name
        addToImage(&family); addToImage(&dup);
        std::vector<OSMetaClass*> stalled{&dup};
        int r = scanPass(stalled, all, /*naive=*/false);
        printf("A(duplicate theory): result=%d -> the panic branch, not the chain\n", r);
        assert(r == -2);
    }

    // ---- scenario B: the v36 gate was a false positive; v39's policy is not ----
    // Same closure state that produced I60:base-dup on v36c/v37/v38: the name is taken,
    // by the family itself. The policy must now OPEN, and report the ABI sizes.
    {
        gAllClasses.clear();
        IOBlockStorageDevice::gMetaClass.className = nameFamily;
        IOBlockStorageDevice::gMetaClass.classSize = 248;      // the sz= the panel printed
        IOBlockStorageDevice::gMetaClass.superClassLink = nullptr;
        gAllClasses.push_back(&IOBlockStorageDevice::gMetaClass);
        liuqinInstallMetaclassFix();                            // as the metaclass ctor does
        NativeUfsBlock::gMetaClass.superClassLink = nullptr;    // v39 has not scribbled yet
        assert(startRootPrologue());
        assert(!native_ufs_root_failure);
        assert(std::string(gMilestoneMsg) == "base-ok");
        printf("B(family prelinked): publication allowed, milestone %s:%s\n", gMilestoneTag, gMilestoneMsg);

        // size skew against the same name: reported on the panel, still not fatal
        IOBlockStorageDevice::gMetaClass.classSize = 232;
        assert(startRootPrologue());
        assert(std::string(gMilestoneMsg) == "base-abi");
        IOBlockStorageDevice::gMetaClass.classSize = 248;

        // a second class registered under the family's name still refuses: the dictionary
        // key matches (that is what a duplicate looks like), only the object differs
        OSMetaClass alien{}; alien.className = nameFamily; alien.classSize = 248;
        gAllClasses.clear(); gAllClasses.push_back(&alien); addToImage(&alien);
        assert(!startRootPrologue());
        assert(native_ufs_root_failure);
        assert(std::string(gMilestoneMsg) == "base-alien");
        printf("B(alien holder): refused, milestone %s:%s\n", gMilestoneTag, gMilestoneMsg);
    }

    // ---- scenario C: closed world (no IOStorageFamily in roots) ---------------
    {
        gAllClasses.clear();
        assert(startRootPrologue());
        assert(!native_ufs_root_failure);
        assert(std::string(gMilestoneMsg) == "base-free");
        printf("C(closed-world): gate opens when the name is unclaimed\n");
    }

    // ---- scenario D: the v37/v38 shapes --------------------------------------
    // The batch is the real one: the family object that __kernel__ registers, and ours.
    // D1 must reproduce the abort; D2 (v38's policy, repair switched off) must drop exactly
    // the poisoned slot and keep the rest of the batch registered.
    OSMetaClass ioService{}; ioService.className = nameOther; ioService.classSize = 152;
    ioService.superClassLink = nullptr;
    addToImage(&ioService);
    OSMetaClass &family = IOBlockStorageDevice::gMetaClass;
    family.className = nameFamily;
    family.classSize = 248;
    NativeUfsBlock::gMetaClass.className = nameOurs;
    NativeUfsBlock::gMetaClass.classSize = 248;
    const void *poison = (const void*)0x6131373030666666ULL;          // "ffff007a" on the panel

    {
        ioService.superClassLink = nullptr;
        family.superClassLink = &ioService;
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass*)poison;   // v38's readout
        std::vector<OSMetaClass*> batch{&family, &NativeUfsBlock::gMetaClass}, dict;
        int r1 = scanPass(batch, dict, /*naive=*/true);
        printf("D1(v37 XNU): scan result=%d -> %s\n", r1,
               r1 == -1 ? "DIES at postModLoad+0x2d4" : "survives");
        assert(r1 == -1);

        // v38's policy with the repair switched off: name it, drop it, carry on.
        gHealInScan = false;
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass*)poison;
        dict.clear(); batch = {&family, &NativeUfsBlock::gMetaClass}; gDeepest = 0;
        int r2 = scanPass(batch, dict, false);
        printf("D2(v38 policy): bad=%d registered=%zu deepest=%u -> boot continues\n",
               r2, dict.size(), gDeepest);
        assert(r2 == 1);
        assert(dict.size() == 1 && dict[0] == &family);
        assert(gDeepest == 2);
        gHealInScan = true;
    }

    // ---- scenario E: H16-F ---------------------------------------------------
    // E1: the scan heals, so the class is registered instead of lost.
    {
        family.superClassLink = &ioService;
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass*)poison;
        std::vector<OSMetaClass*> batch{&family, &NativeUfsBlock::gMetaClass}, dict;
        gDeepest = 0;
        unsigned healed = 0;
        int bad = scanPass(batch, dict, false, &healed);
        printf("E1(v39 scan): bad=%d healed=%u registered=%zu deepest=%u\n", bad, healed,
               dict.size(), gDeepest);
        assert(bad == 0 && healed == 1);
        assert(dict.size() == 2);
        assert(NativeUfsBlock::gMetaClass.superClassLink == &family);   // the link-time object
        assert(gDeepest == 3);
    }
    // E2: the scribble is still there at U59 (nothing healed it): the witness repairs it,
    // so the first isMemberOf() cannot fault.
    {
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass*)poison;
        liuqinInstallMetaclassFix();
        liuqinMetaclassWitness("pre");
        assert(NativeUfsBlock::gMetaClass.superClassLink == &family);
        assert(std::string(gMilestoneMsg) == "mc-ok");
        printf("E2(witness at U59): field repaired before any allocation\n");
    }
    // E3: never hand back an unverifiable superclass. If the family object's own chain is
    // the poisoned one, the fix must refuse and the scan must fall back to dropping.
    {
        family.superClassLink = (OSMetaClass*)poison;
        const void *fix = liuqin_block_super_fix(nameOurs);
        printf("E3(unwalkable family): fix=%p\n", fix);
        assert(fix == nullptr);
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass*)poison;
        std::vector<OSMetaClass*> batch{&family, &NativeUfsBlock::gMetaClass}, dict;
        unsigned healed = 99;
        int bad = scanPass(batch, dict, false, &healed);
        assert(bad == 2 && healed == 0 && dict.empty());
        family.superClassLink = &ioService;
        printf("E3: both slots dropped, nothing registered, boot still alive (bad=%d)\n", bad);
    }
    // E4: another class must not be able to borrow our superclass.
    {
        family.superClassLink = &ioService;
        assert(liuqin_block_super_fix(nameFamily) == nullptr);
        assert(liuqin_block_super_fix(nameShort) == nullptr);
        assert(liuqin_block_super_fix(nameOther) == nullptr);
        assert(liuqin_block_super_fix(nullptr) == nullptr);
        assert(liuqin_block_super_fix(nameOurs) == &family);
        printf("E4(name match is exact): foreign and truncated names get nothing\n");
    }
    // ---- scenario F: H16-G, the fingerprint and the mirror it searches ------------
    // v38 proved the poison is text, so the interesting object is no longer the pointer but
    // the console mirror it came from. These cases exercise the production search (the
    // driver's liuqinRingFind over the kernel's lq_copy) against a mirror that is filled the
    // way a real boot fills it: with the producer, with our own report, with a wrapped ring.
    const unsigned long long tok1 = pack8("abc12345");     // producer line carries it raw
    const unsigned long long tok2 = pack8("zzQQzz77");     // pure printable: only we print it
    const char *producer = "M:stalled post h=0xfffffff568caffe0 cnt=172 tok=abc12345 tail\n";
    const unsigned long long PRODUCER_AT =
        (unsigned long long) (strstr(producer, "abc12345") - producer);   // not counted by hand
    OSMetaClass ours{};                                     // a second, independent slot

    // F1: a real producer, found through the scan path, with the numbers the panel needs.
    {
        simBootReset();
        liuqinInstallMetaclassFix();                        // both hooks, as the ctor does
        liuqin_debug_puts(producer);
        simEmitLinesQuiet("early UFS window line 1\n");
        simEmitLinesQuiet("early UFS window line 2\n");
        const unsigned long long totalAtSearch = simRing.total;
        NativeUfsBlock::gMetaClass.className = nameOurs;
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass *) tok1;
        family.superClassLink = &ioService;
        std::vector<OSMetaClass*> batch{&family, &NativeUfsBlock::gMetaClass}, dict;
        unsigned healed = 0;
        int bad = scanPass(batch, dict, false, &healed);

        char want[192];
        snprintf(want, sizeof want, "M:fp scan ring hits=1 self=0 lost=0 at=%llu after=%lld total=%llu",
                 PRODUCER_AT, (long long) (totalAtSearch - PRODUCER_AT - 8),
                 (unsigned long long) totalAtSearch);
        assert(mirrorHas(want));                            // exact numbers, not "a hit"
        assert(mirrorHas("ctx|"));
        assert(mirrorHas("tok=abc12345 tail"));             // the producer is quoted back
        // The patch fingerprints before it repairs; if that ever inverts, the search reads a
        // mirror that already contains this boot's own verdict.
        assert(mirrorFindAt("M:fp scan p=") < mirrorFindAt("M:bad[1] fixed"));
        assert(bad == 0 && healed == 1 && dict.size() == 2);
        printf("F1(producer found): at/after/total exact, fingerprint strictly before repair\n");
    }

    // F2: the trap. A pure-printable needle is written verbatim into asc="..." by our own
    // report, so the mirror does hold it -- after the search. A second report must call that
    // ours, never a producer: a self-hit with a real offset points at a line nobody wrote.
    {
        simBootReset();
        liuqinInstallMetaclassFix();
        NativeUfsBlock::gMetaClass.className = nameOurs;
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass *) tok2;
        std::vector<OSMetaClass*> batch{&family, &NativeUfsBlock::gMetaClass}, dict;
        unsigned healed = 0;
        assert(scanPass(batch, dict, false, &healed) == 0 && healed == 1);
        assert(mirrorHas("asc=\"zzQQzz77\""));             // the bytes are in the mirror now
        assert(mirrorHas("M:fp scan ring clean self=0"));   // ... and the search still said clean
        liuqinMetaclassPoisonReport("pub", &NativeUfsBlock::gMetaClass, tok2);
        assert(mirrorHas("M:fp pub ring clean self=1"));    // second look: ours, not a producer
        assert(!mirrorHas("M:fp pub ring hits"));
        printf("F2(no self-hit): first search clean, replay classifies itself as self=1\n");
    }

    // F3: the ring wrapped and the producer is gone. "clean" would be a false negative; the
    // only honest word is that the mirror lost the evidence.
    {
        simBootReset();
        liuqinInstallMetaclassFix();
        liuqin_debug_puts(producer);                        // offset 39, the first thing printed
        simEmitLines(16400);                                // 64 B each -> 1,049,600 B > 1 MiB
        const unsigned long long lost = simRing.total - LQ_RING_SIZE;
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass *) tok1;
        char want[160];
        snprintf(want, sizeof want, "M:fp scan ring lost self=0 lost=%llu total=%llu",
                 lost, (unsigned long long) simRing.total);
        liuqinMetaclassPoisonReport("scan", &NativeUfsBlock::gMetaClass, tok1);
        assert(mirrorHas(want));
        assert(!mirrorHas("M:fp scan ring clean"));
        printf("F3(evidence overwritten): lost=%llu reported, never read as clean\n", lost);
    }

    // F4: the ring wrapped and the producer survived. Offsets must come from where the copy
    // actually started, not from the position that was asked for: the clamp in lq_copy moves
    // it forward and an offset taken from the request points into memory the ring threw away.
    {
        simBootReset();
        liuqinInstallMetaclassFix();
        simEmitLines(16384);                                // exactly LQ_RING_SIZE bytes
        liuqin_debug_puts(producer);                        // token at 1,048,615
        simEmitLines(5);
        const unsigned long long at = LQ_RING_SIZE + PRODUCER_AT;
        const unsigned long long lost = simRing.total - LQ_RING_SIZE;   // 384 bytes of filler
        char want[160];
        snprintf(want, sizeof want, "M:fp scan ring hits=1 self=0 lost=%llu at=%llu after=%lld total=%llu",
                 lost, at, (long long) (simRing.total - at - 8), (unsigned long long) simRing.total);
        liuqinMetaclassPoisonReport("scan", &NativeUfsBlock::gMetaClass, tok1);
        assert(mirrorHas(want));
        printf("F4(wrapped ring, live producer): at=%llu with lost=%llu, offsets from the real base\n",
               at, lost);
    }

    // F5: the mirror's lock is held. The search must give up at once -- a scan that spins
    // while holding the metaclass lock is a new way to lose the boot.
    {
        simBootReset();
        liuqinInstallMetaclassFix();
        liuqin_debug_puts(producer);
        simRingLockBusy = true;
        const unsigned long long before = simRing.total, droppedBefore = simRingDropped;
        liuqinMetaclassPoisonReport("scan", &NativeUfsBlock::gMetaClass, tok1);
        simRingLockBusy = false;
        // Contention costs both halves of the console: nothing new reached the mirror (the
        // search could not read and the verdict could not be written), yet the panel still
        // shows the verdict, because painting never waits on the lock.
        assert(simRing.total == before);                  // the ring did not move at all
        assert(simRingDropped > droppedBefore);             // counted as dropped, not lost
        assert(panelHas("M:fp scan ring gaveup self=0"));
        printf("F5(lock busy): one copy attempt, verdict gaveup, nothing spun\n");
    }

    // F6: the cap, and the board without this driver. Two fingerprints per boot is the whole
    // budget; with no hook installed the output is H16-F's, line for line.
    {
        simBootReset();
        liuqinInstallMetaclassFix();
        OSMetaClass slot0{}, slot1{}, slot2{};
        slot0.className = nameOurs; slot1.className = nameOurs; slot2.className = nameOurs;
        addToImage(&slot0); addToImage(&slot1); addToImage(&slot2);
        slot0.superClassLink = (OSMetaClass *) pack8("aa112233");
        slot1.superClassLink = (OSMetaClass *) pack8("bb445566");
        slot2.superClassLink = (OSMetaClass *) pack8("cc778899");
        std::vector<OSMetaClass*> batch{&slot0, &slot1, &slot2}, dict;
        scanPass(batch, dict, false);
        assert(panelCount("M:fp scan p=") == 2);
        assert(panelCount("M:bad[") == 3);
        // and on a board where this driver never ran: no hooks at all, so the scan has to
        // read exactly like v39 -- the poison named, the slot lost, nothing fingerprinted.
        simBootReset();
        liuqin_metaclass_super_fix = nullptr;
        liuqin_metaclass_poison_report = nullptr;
        slot0.superClassLink = (OSMetaClass *) tok1;
        dict.clear(); batch = {&family, &slot0};
        family.superClassLink = &ioService;
        unsigned healed = 99;
        assert(scanPass(batch, dict, false, &healed) == 1 && healed == 0);
        assert(dict.size() == 1 && dict[0] == &family);     // v39 behaviour, unchanged
        assert(!mirrorHas("M:fp"));                         // no H16-G output at all
        assert(mirrorHas("M:bad[1] bad-chain n=NativeUfsBlock"));
        printf("F6(budget and absence): 2 fingerprints then silence; hookless board is v39\n");
    }

    // F7: the panel scrolls. The evidence must survive that, and only at the last witness --
    // v37 lost M:stalled post exactly this way.
    {
        simBootReset();
        liuqinInstallMetaclassFix();
        liuqin_debug_puts(producer);
        NativeUfsBlock::gMetaClass.className = nameOurs;
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass *) tok1;
        liuqinMetaclassWitness("pre");                      // repairs, and fingerprints
        assert(liuqinFpEvidence[0] != '\0');
        simEmitLines(20);                                   // the panel forgets the ctx line
        assert(!panelHas("ctx|"));
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass *) tok1;   // scribbled again
        liuqinMetaclassWitness("pre");                      // pre must NOT replay
        assert(!panelHas("ctx|abc12345"));
        liuqinMetaclassWitness("pub");                      // pub is the last chance
        assert(panelHas("ctx|"));
        assert(panelHas("tok=abc12345 tail"));
        printf("F7(panel scroll): evidence replayed at pub only, back on the photo\n");
    }

    family.superClassLink = &ioService;
    NativeUfsBlock::gMetaClass.superClassLink = &family;
    liuqin_metaclass_super_fix = nullptr;
    liuqin_metaclass_poison_report = nullptr;
    printf("ALL SIM ASSERTIONS PASS\n");
    return 0;
}
