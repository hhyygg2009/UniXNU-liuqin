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
