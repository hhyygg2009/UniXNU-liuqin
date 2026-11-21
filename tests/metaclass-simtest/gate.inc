  const char *why = "";
  // Normally the first thing startRoot does is look at its own metaclass chain: everything
  // below allocates, and an allocation walks that field through isMemberOf().
  liuqinMetaclassWitness ("pre");
  // liuqin (H16-F): v36 refused root publication whenever the name "IOBlockStorageDevice"
  // was taken. The family is prelinked in every iOS 17 closure, so that was always true,
  // and v36c/v37/v38 all ended in I60:base-dup with an unmountable root -- the gate was a
  // false positive (see the H16-F block at the top of this file). The probe stays; it now
  // accepts the family itself and only refuses a name held by a class that is not the
  // family. liuqin-native-ufs-skip-gate=1 skips even that, for a closure without
  // com.apple.iokit.IOStorageFamily in [kernel.prelink] roots.
  unsigned nativeSkipGate = 0;
  PE_parse_boot_argn ("liuqin-native-ufs-skip-gate", &nativeSkipGate, sizeof (nativeSkipGate));
  if (!nativeSkipGate && !liuqin_base_class_probe ()) {
    native_ufs_root_failure = "IOBlockStorageDevice held by a class that is not the family";
    return false;
  }
