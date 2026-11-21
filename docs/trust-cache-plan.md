# TC 线（v45）：把原厂 trust cache 装进内核 —— 计划、证据、判读表

日期：2026-11-20（平板不在手边，全部主机侧）。分支：`v45-trust-cache`（= `main` + 一个 defconfig 开关）。

## 0. 这条线要解决什么，和 H17 是什么关系

v42 挂上根卷之后死在 `exec init`：AMFI 问"这个 cdhash 可信吗"，`query_trust_cache` 去拿
`trust_cache_rt_lock`，而那把锁只有 `trust_cache_runtime_init()` 会 `lck_rw_init()`——它被
`CONFIG_XNU_TRUST_CACHE_RUNTIME=n` 整块编译掉了。H17（v43 的补丁）是**绕过**：在 `#else` 分支里
手工把锁初始化，于是查询会返回"不可信"而不是炸内核。v44 把 AMFI 拒绝的 `cs_enforcement_disable=1`
撤掉，专测这条绕过。

TC 线是**正经**那一半：`CONFIG_XNU_TRUST_CACHE_RUNTIME=y` + 把 21H461 的两份原厂 trust cache
打进 payload，让 AMFI 真的有 2,554 条 cdhash 可判。做完才有资格撤 `amfi_get_out_of_my_way=1`。

**两个分支互斥编译，v45 不能替 v44 作证。** H17（=n 分支）至今唯一的实机证据还是 0，v44 的照片
该拍还是要拍。v45 只是把"另一条路能不能走通"提前准备好。

## 1. 链条，以及每一环是从哪儿证明的（不是猜的）

| 环节 | 事实 | 证据 |
| --- | --- | --- |
| 开关 | `CONFIG_XNU_TRUST_CACHE_RUNTIME=y` → `XNU_TRUST_CACHE_RUNTIME=1` → `-DXNU_NATIVE_TRUST_CACHE=1` 只加在 `startup.o` | `UniXNU/scripts/build-xnu-4k-baseline.sh:277,288`；且 `.trust-cache-mode` 戳会删掉旧的 `startup.o`（H17 那种"改了不生效"在这里被机器挡住了） |
| 分支 | `native-trust-cache-runtime.patch` 把 `startup.c` 的条件改成 `!defined(BCM2837) \|\| XNU_NATIVE_TRUST_CACHE` | `patches/xnu/native-trust-cache-runtime.patch`（设备仓与 parent 同一份，只改这一行） |
| 运行时初始化 | `trustCacheInitializeRuntime` 是 **`static inline`**，不是外部库调用 | `EXTERNAL_HEADERS/TrustCache/API.h:28-51`；反汇编 `_trust_cache_runtime_init` 里只有清内存 + `b _lck_rw_init`，没有任何 `bl` 到 libTrustCache |
| 判决走接口表 | `load_static_trust_cache` 的前 55 条只调 `SecureDTLookupEntry`/`SecureDTGetProperty`/`phystokv`，之后是 `blr x8`（从 amfi 接口表取函数指针） | 反汇编 `buildtmp/v44-text.s`，符号表 `buildtmp/panel-v43/v43.nm.txt` |
| 装载交接 | DarwinBoot 读 `kernel.macho.trustcache`，**要求 ADT 里 memory-map/TrustCache 的 16 字节必须全 0**，然后由它填 paddr/length；缺任意一边 → `EFI_COMPROMISED_DATA`，面板停在 payload 打不开 | `drivers/DarwinBoot/DarwinBootPkg/Driver/DarwinBoot.c:906-918`、`ValidTrustCaches()` |
| 打包 | `unixnu build payload --trust-cache F` 会 ①校验 F 的 sha256 必须在 `board.toml [payload] trust_cache_sha256` 里 ②拷成 `kernel.macho.trustcache` ③给 ADT 脚本加 `--trust-cache` | `UniXNU/scripts/unixnu/payload.py:122-129,188,212-213` |
| 结构 | bundle = `<模块数, 各模块偏移>` + 逐字节的 v2 模块；XNU 读到的正是 `trust_cache_offsets_t` | `scripts/fetch-liuqin-trust-caches.py` 与 `UniXNU/scripts/bundle-ipsw-trust.py` 产出逐字节相同 |

## 2. 今天新增的主机侧事实

1. **两份 cache 抽得出来，且逐字节对得上板子声明的 pin。**
   `python3 scripts/fetch-liuqin-trust-caches.py`（本地 IPSW `UniXNU/out/ipsw/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw`，6.8 GB，11-15 已下好，零网络）：
   - `StaticTrustCache` = `Firmware/044-76040-038.dmg.trustcache` → IM4P 解出 **59,496 B / 2,478 条**，sha256 `da88460e…`
   - `Cryptex1,SystemTrustCache` = `Firmware/044-76034-038.dmg.trustcache` → **1,848 B / 76 条**，sha256 `d121ba89…`
   - bundle **61,356 B，sha256 `7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7`**
     ——**正好等于 `board.toml` 的第一条 pin**。在此之前那条 pin 从来没被验证过（`payload.json` 里
     `trust_cache_sha256` 一直是 null），现在它是被证明的。
     第二条 pin `8900d6a9…` **不是这些输入能产出的**：两个仓里没有任何文件是这个 sha，它和第一条一起
     从共享的 OS profile `profiles/os/ipados-17.7.11-21H461/profile.toml` 抄过来（`UniXNU-qemu/board.toml`
     也带着同一对），语义只能是"别处签出来的 owner bundle 也允许上板"。v45 用的是第一条，脚本也只对第一条负责。
2. **换分支会把驱动的 legacy 别名弄没，`archive-09` 必炸。**
   上一轮 22:44 为了试 `test-pr1`，把驱动子模块的 `origin` 指到了 `Fly116/ShengUfs`；
   `components.official_alias()` 只承认 `github.com/UniXNU/*`，于是 `./unixnu tree` 不再创建
   `drivers/NativeUfs` 别名与 `build/native-ufs/libNativeUfs.a` 兼容链，而 `board.toml`
   的 `[kernel].archives` 写的正是 `build-arm-driver.py NativeUfs` →
   `clang: error: no such file or directory: .../tree/drivers/NativeUfs/NativeUfs.cpp`。
   修法（已做）：`git -C drivers/ShengUfs remote set-url origin https://github.com/UniXNU/ShengUfs.git`，
   维护者的仓改名 `fly116`，我们自己那条线继续用 `mine`（`hhyygg2009`，`.gitmodules` 也仍然指它）。
3. **v44 的可复现性顺便验了两层。** ① 恢复到 `main` 之后重打 payload→MBR→bootimg，与已发布的
   `boot-liuqin-v44-no-cs-disable.img` **只差 71 个字节**：FAT 卷的创建时间戳 + ADT 里
   `chosen/random-seed`（本来就该每次随机）。② 更强的那条：`=n` 基准配置下**重跑 `./unixnu build kernel`
   直接回到同一个 `e8233d2e…`**（逐字节），audit 仍是 **64/64 PASS**——所以 v45 那个 `d19280cf…`
   的差值，确实只有"一个开关"那么多。
4. **`out/` 曾经停在上游那一轮。** 上一轮 22:47–23:49 是从 `test-pr1` 构建的，`payload.json` 里的
   参数连 `amfi_get_out_of_my_way` 都没有——那种状态下 audit 会以 `Y payload carries
   amfi_get_out_of_my_way=1` 失败。**上机前先看 `payload.json` 的 `kernel_args`**，别看镜像文件名。

## 3. v45 改什么（单变量）

1. `configs/liuqin_boot_tc_defconfig`＝`liuqin_boot_defconfig` 只把 `CONFIG_XNU_TRUST_CACHE_RUNTIME`
   翻成 `y`（其余逐字节相同）；
2. `board.toml` 的 `defconfig` 声明跟着改，并用 `unixnu defconfig liuqin_boot_tc_defconfig` 真正生成
   `.config`（`scripts/check-defconfig.py` 守"声明==选定==.config"）；
3. `unixnu build payload … --trust-cache .cache/firmware/trustcache/original-static-system.bundle`。

**不动**：启动参数（`amfi_get_out_of_my_way=1` 留着，942 B）、驱动、补丁序列、ADT 其余部分。
`patches/xnu/zzzz-liuqin-trust-cache-lock-init.patch` 留在序列里——=y 时它的 `#else` 不参与编译，
它守的 `kern_trustcache.c` 判空仍然有效。

## 4. v45 判读表（可证伪）

| 面板 | 结论 | 下一件事 |
| --- | --- | --- |
| `K5x:trust_cache_init` 出现（`dtrace_early_init` 与 `OSKext…KextBootstrap` 之间） | =y 分支进了镜像并跑到 | 看下面几行 |
| `image4 interface not available`（`kern_trustcache.c:472`） | v43 推断的"K51 已 start AppleImage4"在这块板上**不成立** | 查 `loadSecurityExtensions()` 里 AppleImage4 的 start 返回码；TC 线降级 |
| `amfi interface is stale: N`（:479）/ `unable to load static trust cache: interface not supported` | AMFI 注册的 `TrustCache.version < 2` | 只能走 =n + H17（v44）那条路 |
| `no external trust caches found (segment length is zero)` | 装载交接没成：ADT 占位在、loader 没填 | 查 DarwinBoot 是否读到 sidecar（`ValidTrustCaches`、文件在不在 ESP 里） |
| 停在 UEFI 菜单，一个内核里程碑都没有 | sidecar 与 ADT 占位不成对 → `EFI_COMPROMISED_DATA` | `unzip`/挂载 payload.img 看 `kernel.macho.trustcache` 在不在 |
| 越过 K52 到 `B19:at_mountroot`，之后 `exec init` 不再炸 `lock_rw.c:1400` | TC 线成立，H17 可退役 | 单独一轮撤 `amfi_get_out_of_my_way`（v46） |
| 出现 `validation of code signature failed through MACF policy: N` | cache 装上了但 launchd 的 cdhash 不在那 2,554 条里 | 用 bundle 查 launchd 的 hash，再谈第二份 cache |

## 5. 主机侧守卫

`python3 scripts/check-trust-cache-image.py`（与 `audit-h16f-image.py` 同一套反汇编工具函数）：
`K+` 有 `trust_cache_init` 串、`K-` 没有 H17 的 `trust_cache_lock_init` 串、`B` 两个调用都在
`kernel_bootstrap_thread` 里、`L` 锁的初始化改由 `trust_cache_runtime_init` 自己做、
`Q` `load_static_trust_cache` 真读 DT 并经接口表 `blr` 出去、`P` 五条 panic/printf 串都在、
`T`/`C`/`A` payload 侧（bundle==pin、bundle 字节真在 payload.img 里、ADT 占位 16 字节全 0）。

## 6. 怎么退回 v44 的基准

```
git checkout main
cd ../UniXNU && ./unixnu defconfig liuqin_boot_defconfig
./unixnu build kernel     # 期望 sha256 e8233d2e047e4a768218ad789de0f17b4bafa8b3033449c77675fd4faa55250e
```
备份：`buildtmp/v44-kernel-e8233d2e.macho`（万一构建不可复现，直接放回去）。
