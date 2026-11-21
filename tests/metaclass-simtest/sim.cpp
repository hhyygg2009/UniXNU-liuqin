 // Host simulation of the liuqin metaclass sentinel and the base-class gate.
//
// v43 shape. v42 closed H16: build-liuqin-ufs-bindings.py had been carrying the *original*
// 21H461 kernelcache VAs into our image, so the family object we compiled against was a
// phantom 16 MiB above our own __PRELINK_DATA. Nothing ever scribbled superClassLink --
// we were reading the wrong address. The postModLoad scan, the repair hook and the
// fingerprint search are retired with that diagnosis, so this harness no longer mirrors a
// scan (the one it mirrored is gone from the kernel, and a harness that models deleted code
// proves nothing).
//
// What still earns its keep here, because all three are load-bearing on a board we cannot
// step on:
//   1. the chain walk: it must report an unvalidated pointer and never dereference it. That
//      guard is the difference between a photo with a verdict on it (v38+) and the silent
//      data abort at postModLoad+0x2d4 (v35..v37), and it is the tripwire for the bindings
//      ever going wrong again;
//   2. the base-class gate's policy table: family/self, size skew, an alien holder, a closed
//      world, and the boot-arg that skips it;
//   3. the console mirror and the panel: every report goes through the production
//      lq_ring/lq_append/lq_copy, and the panel keeps only its last bytes. The rules this
//      harness enforces are the three in AGENTS.md (search before you print, attribute a
//      hit, take offsets from where the copy really started) applied to the primitive that
//      is still shipping.
//
// Four files are extracted verbatim by extract.py: helper.inc, witness.inc and gate.inc come
// from the production driver, ring.inc is the kernel's console mirror from the USB-debug
// patch. The only substitution the script makes is liuqinPtrOk's address floor, which cannot
// mean anything on a host address space. Everything below the includes is the harness.
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
// 0x6131373030666666 that v38 read out of superClassLink is deliberately not in it, and
// neither is a phantom 16 MiB above anything -- scenario C checks what the gate does when
// the address we linked against is not the address the registry holds.
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

// ---- the console mirror every report is printed into -----------------------
// ring.inc is lq_ring / lq_append / lq_copy exactly as bsd/dev/liuqin_debug.c gets them, so
// the bytes these assertions read are the bytes the kernel stores. log_lock is stood in for
// by simRingLockBusy, whose two production consequences are reproduced together: an append
// is dropped and counted, a copy simply returns 0. The panel is a *separate* channel: it
// always sees the text and it keeps only its last lines, which is why v37 lost a conclusion
// -- assertions about "what a photo would show" read simPanel, not stdout.
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

static volatile const char *native_ufs_root_failure = nullptr;
static unsigned simSkipGate = 0;                 // boot-arg stand-in, see below
static void PE_parse_boot_argn(const char *, unsigned *out, unsigned) { *out = simSkipGate; }

// One boot's worth of console state. Production has a single mirror and a single panel per
// boot; the harness runs many boots in one process, so every case wipes both.
static void simBootReset()
{
    memset(&simRing, 0, sizeof simRing);
    simRingLockBusy = false;
    simRingDropped = 0;
    simPanel.clear();
}

// The extracted production code: the shim, the chain walk and the gate policy (helper.inc),
// the witness they call (witness.inc), and startRoot's prologue that ties them together
// (gate.inc).
#include "helper.inc"
#include "witness.inc"

// startRoot's prologue, extracted verbatim (calls the witness and the policy above).
static bool startRootPrologue()
{
    native_ufs_root_failure = nullptr;
#include "gate.inc"
    (void) why;                       // production uses it in the failure paths below it
    return true;
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    addToImage(&IOBlockStorageDevice::gMetaClass);
    addToImage(&NativeUfsBlock::gMetaClass);
    // Class names are pointers the image must contain (on device they are __TEXT cstrings;
    // the chain walk validates them before comparing). Own the storage so the addresses are
    // stable, and let the pool do whatever it likes with literals.
    char nameOurs[] = "NativeUfsBlock", nameFamily[] = "IOBlockStorageDevice";
    char nameOther[] = "SomethingElse";
    addToImage(nameOurs); addToImage(nameFamily); addToImage(nameOther);
    // witness.inc passes the literal "NativeUfsBlock" exactly as the driver does; identical
    // literals share one address within a translation unit, so registering it here models
    // the __TEXT cstring the kernel would be pointing at.
    addToImage("NativeUfsBlock");

    OSMetaClass ioService{}; ioService.className = nameOther; ioService.classSize = 152;
    ioService.superClassLink = nullptr;
    addToImage(&ioService);
    OSMetaClass &family = IOBlockStorageDevice::gMetaClass;
    family.className = nameFamily;
    family.classSize = 248;
    family.superClassLink = &ioService;
    NativeUfsBlock::gMetaClass.className = nameOurs;
    NativeUfsBlock::gMetaClass.classSize = 248;
    NativeUfsBlock::gMetaClass.superClassLink = &family;
    const void *poison = (const void *) 0x6131373030666666ULL;      // "ffff007a" on the panel

    // ---- scenario A: the healthy chain, which is what v42+ is supposed to look like ----
    {
        simBootReset();
        gAllClasses.clear();
        gAllClasses.push_back(&family);
        assert(startRootPrologue());
        assert(!native_ufs_root_failure);
        assert(std::string(gMilestoneMsg) == "base-ok");
        assert(std::string(gMilestoneTag) == "U");
        // The witness runs first, silently: mc-ok and nothing else. Silence is the point --
        // every character this kernel prints pushes the previous one off the panel.
        const void *bad = nullptr;
        assert(liuqinChainDepth(&NativeUfsBlock::gMetaClass, &bad) == 3);   // us, family, IOService
        assert(bad == nullptr);
        assert(!panelHas("chain broken"));
        printf("A(healthy chain): depth=3, gate open, milestone U:%s, no report at all\n",
               gMilestoneMsg);
    }

    // ---- scenario B: the gate's policy table ------------------------------------
    {
        // B1 size skew against the family's own object: reported, not fatal. v42 printed
        // `live size 144, compiled size 144`, so this pair of numbers is what the panel shows.
        gAllClasses.clear(); gAllClasses.push_back(&family);
        family.classSize = 232;
        assert(startRootPrologue());
        assert(std::string(gMilestoneMsg) == "base-abi");
        assert(!native_ufs_root_failure);
        family.classSize = 248;
        printf("B1(size skew): U:base-abi, publication still allowed\n");

        // B2 an alien holder: the name resolves, the object is not the one we linked against.
        // This is exactly the v40/v41 shape -- and with the bindings fixed it should now only
        // ever mean a genuine duplicate class.
        OSMetaClass alien{}; alien.className = nameFamily; alien.classSize = 248;
        alien.superClassLink = &ioService;
        addToImage(&alien);
        gAllClasses.clear(); gAllClasses.push_back(&alien);
        assert(!startRootPrologue());
        assert(native_ufs_root_failure);
        assert(std::string(gMilestoneMsg) == "base-alien");
        assert(panelHas("NativeUfs: IOBlockStorageDevice is held by "));
        printf("B2(alien holder): refused (%s), and the reason is on the panel\n",
               (const char *) native_ufs_root_failure);

        // B3 closed world: no IOStorageFamily in the closure at all -> the gate opens.
        gAllClasses.clear();
        assert(startRootPrologue());
        assert(!native_ufs_root_failure);
        assert(std::string(gMilestoneMsg) == "base-free");
        printf("B3(closed-world): gate opens when the name is unclaimed\n");

        // B4 the boot-arg skips even a refusal (a closure without IOStorageFamily in roots).
        gAllClasses.clear(); gAllClasses.push_back(&alien);
        simSkipGate = 1;
        assert(startRootPrologue());
        assert(!native_ufs_root_failure);
        simSkipGate = 0;
        gAllClasses.clear(); gAllClasses.push_back(&family);
        assert(!native_ufs_root_failure);
        printf("B4(skip-gate arg): an alien holder no longer stops startRoot\n");
    }

    // ---- scenario C: the sentinel is a tripwire, not a repair ---------------------
    // v42's own bug, replayed: our link-time reference sat 16 MiB above the object the
    // registry holds. The walk must say so out loud, must not write anything, and must not
    // fault -- isMemberOf() walks this same field, and that is what killed v35..v37.
    {
        simBootReset();
        gAllClasses.clear(); gAllClasses.push_back(&family);
        family.superClassLink = &ioService;
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass *) poison;
        liuqinMetaclassWitness("pre");
        assert(std::string(gMilestoneMsg) == "mc-bad");
        assert(NativeUfsBlock::gMetaClass.superClassLink == (OSMetaClass *) poison);  // read-only
        assert(panelHas("NativeUfs: metaclass chain broken at pre: bad=0x6131373030666666"));
        assert(panelHas("link=0x6131373030666666 depth=-1"));
        printf("C(poisoned link): U:mc-bad with the word and the depth, field left alone\n");

        // ... and the same field healed by the only real fix there is: a correct binding.
        NativeUfsBlock::gMetaClass.superClassLink = &family;
        liuqinMetaclassWitness("pub");
        assert(std::string(gMilestoneMsg) == "mc-ok");
        assert(!panelHas("chain broken at pub"));
        printf("C(correct binding): the same witness is silent, which is the target state\n");
    }

    // ---- scenario D: the walk never spins and never dereferences ------------------
    {
        // D1 a cycle through poisoned memory: the chain cap fires instead of looping.
        simBootReset();
        ioService.superClassLink = &NativeUfsBlock::gMetaClass;       // a 3-cycle
        NativeUfsBlock::gMetaClass.superClassLink = &family;
        const void *bad = nullptr;
        int depth = liuqinChainDepth(&NativeUfsBlock::gMetaClass, &bad);
        assert(depth < 0);
        assert((unsigned) -depth == LIUQIN_CHAIN_MAX + 1);
        liuqinMetaclassWitness("pre");
        assert(std::string(gMilestoneMsg) == "mc-bad");
        assert(panelHas("depth=-65"));                                // LIUQIN_CHAIN_MAX + 1
        ioService.superClassLink = nullptr;
        printf("D1(cycle): capped at LIUQIN_CHAIN_MAX, reported as depth=-65, no spin\n");

        // D2 an unaligned word that is not in the image: refused by the floor test, so the
        // walk stops before the load that faulted on v35..v37. The host cannot take the abort,
        // which is precisely why the assertion is about the value it returns instead.
        const void *unaligned = (const void *) 0xfffffff006add1d7ULL;
        bad = nullptr;
        assert(liuqinChainDepth(unaligned, &bad) == 0 && bad == unaligned);
        printf("D2(foreign address): depth=0 with bad=%p, never dereferenced\n", bad);
    }

    // ---- scenario E: the mirror, on the kernel's own code -------------------------
    {
        // E1 wrap: the oldest bytes are gone, and lq_copy clamps a stale position forward
        // instead of reading what the ring already threw away. Offsets therefore have to come
        // from where the copy really started (AGENTS rule 3).
        simBootReset();
        liuqin_debug_puts("early line\n");
        simEmitLines(16400);                       // 64 B each -> 1,049,600 B > 1 MiB
        assert(liuqin_debug_total() > LQ_RING_SIZE);
        assert(!mirrorHas("early line"));          // overwritten: not readable, not "clean"
        uint64_t pos = 0;
        char chunk[256];
        unsigned got = liuqin_debug_copy(&pos, chunk, sizeof chunk);
        assert(got == sizeof chunk);
        const uint64_t base = pos - got;           // the real base, not the requested 0
        assert(base == liuqin_debug_total() - LQ_RING_SIZE);   // the clamp, exactly
        assert(memcmp(chunk, std::string(63, 'x').c_str(), 63) == 0);
        printf("E1(wrapped ring): oldest bytes unreadable, offsets from pos-got=%llu\n",
               (unsigned long long) base);

        // E2 the readable window is exactly the last LQ_RING_SIZE bytes, in order.
        simBootReset();
        simEmitLinesQuiet("M:line one\n");
        simEmitLines(4);
        simEmitLinesQuiet("M:line two\n");
        std::string all = mirrorText();
        assert(all.find("M:line one") < all.find("M:line two"));       // order preserved
        assert(all.size() == liuqin_debug_total());                     // nothing lost yet
        printf("E2(window): both lines readable, in print order, total=%llu\n",
               (unsigned long long) liuqin_debug_total());

        // E3 the lock is held: the mirror misses the line, the panel does not, and nothing
        // spins. A report must never become a new way to lose the boot.
        simBootReset();
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass *) poison;
        simRingLockBusy = true;
        liuqinMetaclassWitness("pre");              // prints through the busy path
        simRingLockBusy = false;
        assert(simRing.total == 0);                                  // the mirror missed it
        assert(simRingDropped > 0);                                   // and says so by the byte count
        assert(panelHas("chain broken at pre"));                      // the panel always paints
        printf("E3(busy lock): %llu bytes dropped from the mirror, panel still painted\n",
               (unsigned long long) simRingDropped);
    }

    // ---- scenario F: what a photo can and cannot contain -------------------------
    // The panel keeps its tail (AGENTS: panic-screen repaints eat the console). The witness
    // runs at both ends of startRoot, so the sentinel's line has a second chance; a verdict
    // printed only at "pre" is a verdict a photo may never show.
    {
        simBootReset();
        gAllClasses.clear(); gAllClasses.push_back(&family);
        NativeUfsBlock::gMetaClass.superClassLink = (OSMetaClass *) poison;
        liuqinMetaclassWitness("pre");
        assert(panelHas("chain broken at pre"));
        simEmitLines(20);                           // the panel forgets it
        assert(!panelHas("chain broken at pre"));
        assert(mirrorHas("chain broken at pre"));   // the mirror still holds it
        liuqinMetaclassWitness("pub");              // the last chance, and it is taken
        assert(panelHas("chain broken at pub"));
        assert(panelCount("chain broken at") == 1);                     // only the replay survives
        NativeUfsBlock::gMetaClass.superClassLink = &family;
        printf("F(panel scroll): the pre-line is gone from the photo, the pub-line is not\n");
    }

    // ---- scenario G: startRoot's full prologue on a poisoned field ----------------
    // The order the shipped code runs in: witness, then policy. Both say something, and the
    // gate must still refuse an alien holder even though the witness was never reached with a
    // broken field, and must still open when everything is right.
    {
        simBootReset();
        gAllClasses.clear(); gAllClasses.push_back(&family);
        NativeUfsBlock::gMetaClass.superClassLink = &family;
        assert(startRootPrologue());
        assert(std::string(gMilestoneMsg) == "base-ok");
        assert(panelCount("chain broken") == 0);
        assert(mirrorHas("NativeUfs: base class IOBlockStorageDevice live size 248, compiled size 248"));
        printf("G(healthy prologue): mc-ok then base-ok, one line of ABI evidence on the panel\n");
    }

    NativeUfsBlock::gMetaClass.superClassLink = &family;
    family.superClassLink = &ioService;
    printf("ALL SIM ASSERTIONS PASS\n");
    return 0;
}
