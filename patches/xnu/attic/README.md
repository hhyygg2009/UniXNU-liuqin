# patches/xnu/attic —— 不进补丁序列的历史补丁

构建侧的枚举是 `sorted(ROOT/'patches/xnu').glob('*.patch')`：**只看顶层、不递归**。
放在这里 = 不会被套用，纯粹留档。

## 2026-11-19-h17-trust-cache-lock-init.patch

来自本仓未推送分支 `localmain` 的 `2a9cb96`（H17）。它做两件事，今天命运不同：

- **`osfmk/kern/startup.c` 那半边：已作废。** 它的前提是"trust-cache 运行时被编到外面，
  所以 `trust_cache_rt_lock` 从没变成可睡眠锁，第一次 AMFI 查询就 panic
  `Taking non-sleepable RW lock with preemption enabled`"。Fly116 线用的是另一条路：
  `configs/liuqin_defconfig` 里 `CONFIG_XNU_TRUST_CACHE_RUNTIME=y`，配合本仓已有的
  `native-trust-cache-runtime.patch` 把门改成 `#if !defined(BCM2837) || XNU_NATIVE_TRUST_CACHE`。
  实测证据：本仓 2026-11-21 刷进实机的 v60 内核里有 `_trust_cache_runtime_init` 与
  `_load_static_trust_cache`，`startup.c` 的调用点在门内 —— 锁由真 init 路径初始化，
  这个 workaround 不再需要。
- **`bsd/kern/kern_trustcache.c` 的 `amfi == NULL` 护栏：仍未覆盖。** 合并后的构建源码里
  `kern_trustcache.c:630` 依旧是 `lck_rw_lock_shared(&trust_cache_rt_lock);` 之后**直接**
  `amfi->TrustCache.query(...)`。是否必要取决于 AMFI 是否一定注册；要启用它请**只取第二半**，
  并且注意顶层新增补丁会触发整套 XNU 重编（补丁序列按文件名排序并带 sha 戳）。
