// Host simulation of the liuqin H16 metaclass family: the base-class policy, the
// scan that used to drop the poisoned slot, and the H16-F repair that keeps it.
//
// Three files are extracted verbatim from the production driver by extract.py
// (helper.inc, witness.inc, gate.inc); the only substitution that script makes is
// liuqinPtrOk's address floor, which cannot mean anything on a host address space.
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
static void liuqin_debug_puts(const char *s) { fputs(s, stdout); }
static volatile const char *native_ufs_root_failure = nullptr;
static unsigned simSkipGate = 0;                 // boot-arg stand-in, see below
static void PE_parse_boot_argn(const char *, unsigned *out, unsigned) { *out = simSkipGate; }

// The extracted production code (driver side, H16-F):
#include "helper.inc"
#include "witness.inc"

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
        if (me && !liuqinPtrOk(me)) { stalled[i] = nullptr; bad++; continue; }   // M:dirty
        if (!me) continue;
        for (auto *c : all) if (c->className && me->className &&
                                !strcmp(c->className, me->className)) {
            printf("PANIC would fire: duplicate %s\n", me->className); return -2;
        }
        if (naive) {                              // the v37 fault shape
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
            // verbatim in intent with the patched scan: ask the driver for the link-time
            // superclass, validate, write it, re-walk; keep the class when that works.
            const void *fix = (gHealInScan && liuqin_metaclass_super_fix)
                ? liuqin_metaclass_super_fix(me->className) : nullptr;
            if (fix && liuqinPtrOk(fix)) {
                me->superClassLink = const_cast<OSMetaClass*>((const OSMetaClass*)fix);
                badLink = nullptr;
                depth = liuqinChainDepth(me, &badLink);
                if (depth > 0) {
                    printf("M:bad[%zu] fixed n=%s bad=%p\n", i, me->className, fix);
                    healed++;
                }
            }
            if (depth < 0) {
                printf("M:bad[%zu] bad-chain n=%s bad=%p\n", i, me->className, badLink);
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
    printf("ALL SIM ASSERTIONS PASS\n");
    return 0;
}
