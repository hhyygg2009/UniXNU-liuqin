static void liuqinMetaclassWitness (const char *phase)
{
  liuqin_meta_shim *sh = reinterpret_cast<liuqin_meta_shim *> (&NativeUfsBlock::gMetaClass);
  const void       *bad = nullptr;
  int               depth = liuqinChainDepth (sh, &bad);
  const char       *verdict = "ok";

  if (depth < 0) {
    const void *wasBad = bad;              // the walk resets badOut once the chain is fine
    const void *fix = liuqin_block_super_fix ("NativeUfsBlock");
    verdict = "unfixable";
    if (fix) {
      sh->superClassLink = fix;
      depth = liuqinChainDepth (sh, &bad);
      if (depth > 0) {
        verdict = "healed";
      }
    }
    char badline[128];
    __builtin_snprintf (badline, sizeof badline, "M:mc %s was=%p now=%p depth=%d\n",
        phase, wasBad, sh->superClassLink, depth);
    liuqin_debug_puts (badline);
  }
  char line[192];
  __builtin_snprintf (line, sizeof line, "M:mc %s %s p=%p link=%p vt=%p sz=%u depth=%d\n",
      phase, verdict, (const void *) sh, sh->superClassLink, sh->vtable, sh->classSize, depth);
  liuqin_debug_puts (line);
  liuqin_milestone ("U", depth < 0 ? "mc-bad" : "mc-ok");
}
