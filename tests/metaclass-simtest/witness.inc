static void liuqinMetaclassWitness (const char *phase)
{
  liuqin_meta_shim *sh = reinterpret_cast<liuqin_meta_shim *> (&NativeUfsBlock::gMetaClass);
  const void       *bad = nullptr;
  int               depth = liuqinChainDepth (sh, &bad);

  if (depth < 0) {
    char line[160];
    __builtin_snprintf (line, sizeof line,
        "NativeUfs: metaclass chain broken at %s: bad=%p link=%p depth=%d\n", phase, bad,
        sh->superClassLink, depth);
    liuqin_debug_puts (line);
  }
  liuqin_milestone ("U", depth < 0 ? "mc-bad" : "mc-ok");
}
