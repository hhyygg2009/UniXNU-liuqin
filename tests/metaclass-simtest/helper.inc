struct liuqin_meta_shim
{
  const void   *vtable;          // +0x00
  const void   *reserved;        // +0x08  ExpansionData*
  const void   *superClassLink;  // +0x10  the field the scribble lands on
  const void   *className;       // +0x18  a C string until postModLoad's second pass
  unsigned int  classSize;       // +0x20
};

#define LIUQIN_KERNEL_TEXT_LO 0xfffffff000000000ULL
#define LIUQIN_CHAIN_MAX      64

static bool liuqinPtrOk (const void *p)
{
  return liuqinSimPtrOk (p);
}

// Walk an ancestor chain without ever dereferencing an unvalidated pointer. Returns the
// number of hops on success, or -hops (with the bad value in *badOut) when the chain
// leaves the image -- the exact shape that killed v35 through v37.
static int liuqinChainDepth (const void *entry, const void **badOut)
{
  const void *cur = entry;
  int hops = 0;

  *badOut = nullptr;
  while (cur) {
    if (!liuqinPtrOk (cur)) {
      *badOut = cur;
      return -hops;
    }
    cur = reinterpret_cast<const liuqin_meta_shim *> (cur)->superClassLink;
    if (++hops > LIUQIN_CHAIN_MAX) {
      *badOut = cur;            // a chain this long is a cycle through poisoned memory
      return -hops;
    }
  }
  return hops;
}

// Defined by the patched OSMetaClass.cpp (H16-F) and read there during the stalled scan.
// Installing it from this file's metaclass constructor puts it in place strictly before
// that scan can run: the scan only ever looks at what the constructors recorded.
extern "C" const void *(*liuqin_metaclass_super_fix) (const char *className);

extern "C" const void *liuqin_block_super_fix (const char *className)
{
  if (!className || !liuqinPtrOk (className)) {
    return nullptr;
  }
  const char *wanted = "NativeUfsBlock";
  unsigned i = 0;
  while (wanted[i] != '\0' && className[i] == wanted[i]) {
    i++;
  }
  if (wanted[i] != '\0' || className[i] != '\0') {
    return nullptr;             // not our class: XNU must fall back to dropping the slot
  }
  const void *fix = &IOBlockStorageDevice::gMetaClass;   // link-time address, no memory read
  const void *bad = nullptr;
  if (!liuqinPtrOk (fix) || liuqinChainDepth (fix, &bad) <= 0) {
    return nullptr;             // the family object is not walkable: hand back nothing
  }
  return fix;
}

// liuqin (H16-G): the fingerprint. v38 proved the word sitting in superClassLink is eight
// bytes of *text*, which changes what is worth measuring. A pointer printed as hex has to be
// decoded by hand and the hand-decoding is exactly where a byte-order mistake hides; the same
// eight bytes printed as characters name their own producer. And the console mirror
// (bsd/dev/liuqin_debug.c, one megabyte, fed by every liuqin_debug_puts) still holds
// everything this kernel has printed: if the writer copied a line that went through it, the
// mirror still holds those same eight bytes and the offset says how long before the scan they
// were printed. Reachable from the patched scan through a second hook, so a board without this
// driver keeps the shorter H16-F line-for-line.
#define LIUQIN_RING_CHUNK      256u
#define LIUQIN_RING_MAX_ROUNDS 4300u          // 4300 * (256 - 7) = 1,070,700 > the 1 MiB mirror
#define LIUQIN_CTX_BYTES       64u

extern "C" void (*liuqin_metaclass_poison_report) (const char *tag, const void *obj,
                                                   unsigned long long bad);

// The last producer line this report found, kept so the final witness can put it back on a
// panel that has already scrolled past it.
static char liuqinFpEvidence[LIUQIN_CTX_BYTES + 40];

// Memory order, not register order: out[0] is the byte at the low address, so the panel can
// be read left to right. Newline becomes '|' and anything unprintable becomes '.', with '"'
// and '\\' escaped so the text cannot break the quoting of the line that carries it.
static void liuqinEightText (unsigned long long value, char *out)
{
  for (int i = 0; i < 8; i++) {
    unsigned c = static_cast<unsigned> ((value >> (8 * i)) & 0xff);
    if (c == '\n') {
      out[i] = '|';
    } else if (c == '"' || c == '\\') {
      out[i] = '\'';
    } else if (c >= 0x20 && c < 0x7f) {
      out[i] = static_cast<char> (c);
    } else {
      out[i] = '.';
    }
  }
  out[8] = '\0';
}

struct liuqinRingMatch
{
  unsigned long long total;    // bytes ever printed to the mirror
  unsigned long long lost;     // bytes the mirror no longer holds (it wraps at 1 MiB)
  unsigned           hits;     // candidates that are not this report's own output
  unsigned           self;     // candidates sitting inside a line we printed ourselves
  unsigned long long first;    // absolute offset of the first hit
  long long          after;    // bytes printed after the hit (0 == the mirror ended on it)
  int                complete; // every byte still held was searched
  char               context[LIUQIN_CTX_BYTES + 8];
};

// One candidate is worth nothing until it is known whose bytes they are. This report prints
// the needle twice -- once as bad=%016llx and, whenever all eight bytes are printable, once
// verbatim inside asc="..." -- so its own lines are exactly what a search over the mirror
// will find. Those are the report talking to itself, and pointing the panel at them would
// be worse than saying nothing: a real offset at the end of a line that has no producer in
// it. Look at what precedes the candidate; this report's own fields are the only thing with
// asc=" or bad= within 32 bytes of it.
static int liuqinHasOwnFieldBefore (unsigned long long at)
{
  char     probe[LIUQIN_CTX_BYTES];
  uint64_t from = at > 32 ? at - 32 : 0;
  uint64_t pos  = from;

  unsigned got = liuqin_debug_copy (&pos, probe, (unsigned) (at - from));
  for (unsigned i = 0; i < got; i++) {
    const unsigned left = got - i;
    if (left >= 5 && probe[i] == 'a' && probe[i + 1] == 's' && probe[i + 2] == 'c' &&
        probe[i + 3] == '=' && probe[i + 4] == '"') {
      return 1;
    }
    if (left >= 4 && probe[i] == 'b' && probe[i + 1] == 'a' && probe[i + 2] == 'd' &&
        probe[i + 3] == '=') {
      return 1;
    }
  }
  return 0;
}

// Search the mirror for eight bytes without dereferencing a single pointer the kernel did not
// hand us: every byte arrives through liuqin_debug_copy into a stack buffer, so the bounds of
// the search are the bounds of that buffer. A hit may straddle a chunk, so each round rewinds
// by seven and only scans the first (got - 7) offsets -- each position is then tested once.
static void liuqinRingFind (unsigned long long needle, struct liuqinRingMatch *result)
{
  char          chunk[LIUQIN_RING_CHUNK];
  unsigned char pat[8];
  uint64_t      pos = 0;

  result->hits = 0;
  result->self = 0;
  result->lost = 0;
  result->first = 0;
  result->after = -1;
  result->complete = 0;
  result->context[0] = '\0';
  result->total = liuqin_debug_total ();
  if (result->total == 0) {
    result->complete = 1;                       // nothing has been printed: the search is over
    return;
  }
  for (int i = 0; i < 8; i++) {
    pat[i] = static_cast<unsigned char> ((needle >> (8 * i)) & 0xff);
  }
  for (unsigned round = 0; round < LIUQIN_RING_MAX_ROUNDS; round++) {
    uint64_t started = pos;
    unsigned got = liuqin_debug_copy (&pos, chunk, LIUQIN_RING_CHUNK);
    if (got == 0) {
      return;                       // mirror lock busy, or nothing left: never spin here
    }
    // The mirror is circular, so a position older than what it still holds is moved
    // forward by the copy itself. Counting that jump is the only way to learn that early
    // output is gone, and offsets must be taken from where the chunk really starts --
    // started was the position we asked for, which may no longer exist.
    const uint64_t base = pos - got;
    if (base > started) {
      result->lost += base - started;
    }
    unsigned scan = got >= 8 ? got - 7 : 0;
    for (unsigned i = 0; i < scan; i++) {
      int at = 0;
      while (at < 8 && static_cast<unsigned char> (chunk[i + at]) == pat[at]) {
        at++;
      }
      if (at == 8) {
        if (liuqinHasOwnFieldBefore (base + i)) {
          result->self++;           // our own asc="..."/bad=... line, not a producer
          continue;
        }
        if (result->hits == 0) {
          result->first = base + i;
        }
        result->hits++;
      }
    }
    if (got < 8) {
      break;
    }
    pos = base + got - 7;           // rewind the overlap, relative to the real base
    if (pos >= result->total) {
      break;
    }
    if (round + 1 == LIUQIN_RING_MAX_ROUNDS) {
      return;                       // capped: leave complete == 0 so the photo says so
    }
  }
  result->complete = 1;
  if (result->hits == 0) {
    return;
  }
  // Re-read around the first hit so the line that produced it is on the panel, not just an
  // offset. Clamped below by zero; liuqin_debug_copy clamps the length by total.
  char   raw[LIUQIN_CTX_BYTES + 8];
  uint64_t from = result->first > 32 ? result->first - 32 : 0;
  pos = from;
  unsigned got = liuqin_debug_copy (&pos, raw, LIUQIN_CTX_BYTES);
  for (unsigned i = 0; i < got; i++) {
    unsigned c = static_cast<unsigned char> (raw[i]);
    result->context[i] = (c == '\n') ? '|'
        : (c >= 0x20 && c < 0x7f) ? static_cast<char> (c) : '.';
  }
  result->context[got] = '\0';
  result->after = static_cast<long long> (result->total)
      - static_cast<long long> (result->first + 8);
}

extern "C" void liuqinMetaclassPoisonReport (const char *tag, const void *obj,
                                             unsigned long long bad)
{
  const liuqin_meta_shim *sh = reinterpret_cast<const liuqin_meta_shim *> (obj);
  char  line[192];
  char  asc[16];
  struct liuqinRingMatch match;

  // ORDER, NOT COSMETICS: the search has to happen before the first byte of this report
  // reaches the mirror, because the mirror is what it searches. asc[] renders the needle
  // verbatim whenever its eight bytes are printable, so printing first would make the very
  // next search find this line and report a "hit" for a producer that is only us. A hit
  // that cannot be told from a self-hit is worse than no hit: it points at the wrong line
  // with a real offset. liuqinRingFind only copies through liuqin_debug_copy, so nothing
  // here writes to the ring before the scan is done.
  liuqinRingFind (bad, &match);

  // w1 is the word at +0x08 (reserved). v39 could only see vtable, className and classSize,
  // so the photo could not tell a single 8-byte store from a 16-byte pair store. This can.
  liuqinEightText (bad, asc);
  __builtin_snprintf (line, sizeof line,
      "M:fp %s p=%p w1=%p w2=%p w3=%p bad=%016llx asc=\"%s\"\n",
      tag, obj, sh->reserved, sh->superClassLink, sh->className, bad, asc);
  liuqin_debug_puts (line);
  if (match.hits == 0) {
    // "lost" is not "clean": the ring wrapped, so the bytes that could have answered are
    // gone and the panel must not read this as a negative finding.
    __builtin_snprintf (line, sizeof line, "M:fp %s ring %s self=%u lost=%llu total=%llu\n",
        tag, match.lost ? "lost" : (match.complete ? "clean" : "gaveup"), match.self,
        match.lost, match.total);
    liuqin_debug_puts (line);
    return;
  }
  __builtin_snprintf (line, sizeof line,
      "M:fp %s ring hits=%u self=%u lost=%llu at=%llu after=%lld total=%llu\n", tag,
      match.hits, match.self, match.lost, match.first, match.after, match.total);
  liuqin_debug_puts (line);
  __builtin_snprintf (line, sizeof line, "M:fp %s ctx|%s|\n", tag, match.context);
  liuqin_debug_puts (line);
  // The panel keeps only the last few lines (v37 lost M:stalled post that way) and this one
  // is printed while the scan is still running. Keep a copy: the witness at U59 replays it,
  // so a photo taken after the boot has moved on still carries the producer text.
  if (match.context[0]) {
    __builtin_snprintf (liuqinFpEvidence, sizeof liuqinFpEvidence, "M:fp %s ctx|%s|\n", tag,
        match.context);
  }
}

static void liuqinInstallMetaclassFix (void)
{
  liuqin_metaclass_super_fix = &liuqin_block_super_fix;
  liuqin_metaclass_poison_report = &liuqinMetaclassPoisonReport;   // H16-G
}

static bool liuqinBaseClassCheck (void)
{
  const OSSymbol *name = OSSymbol::withCString ("IOBlockStorageDevice");
  if (!name) {
    liuqin_milestone ("U", "base-symfail");   // cannot observe: stay closed
    return false;
  }
  const OSMetaClass *existing = OSMetaClass::getMetaClassWithName (name);
  name->release ();
  char line[192];
  if (!existing) {
    liuqin_milestone ("U", "base-free");      // closed world: no family in this closure
    return true;
  }
  // The dictionary key is the class name, so a lookup for "IOBlockStorageDevice" can only
  // come back with that name; what actually distinguishes the family from a second class
  // registered under the same name is *which object* holds it. In this image the family's
  // metaclass is the object we compiled against, so anything else is the duplicate v36
  // was afraid of -- and refusing is the right call, not a false positive.
  if (existing != reinterpret_cast<const OSMetaClass *> (&IOBlockStorageDevice::gMetaClass)) {
    __builtin_snprintf (line, sizeof line,
        "NativeUfs: IOBlockStorageDevice is held by %p, not by the compiled family object %p; root publication refused\n",
        (const void *) existing,
        (const void *) &IOBlockStorageDevice::gMetaClass);
    liuqin_debug_puts (line);   // H11: panel milestone console, never the silent KDB path
    liuqin_milestone ("U", "base-alien");
    return false;
  }
  const unsigned int compiled = static_cast<unsigned int> (sizeof (IOBlockStorageDevice));
  const unsigned int live = existing->getClassSize ();
  __builtin_snprintf (line, sizeof line,
      "NativeUfs: base class IOBlockStorageDevice live size %u, compiled size %u\n", live, compiled);
  liuqin_debug_puts (line);
  liuqin_milestone ("U", live == compiled ? "base-ok" : "base-abi");   // skew is reported, not fatal
  return true;
}

// liuqin (H16 verification): non-static so the built kernel can be audited with
// llvm-nm and forced in QEMU. Returns whether root publication may go ahead.
extern "C" int liuqin_base_class_probe (void) { return liuqinBaseClassCheck () ? 1 : 0; }
