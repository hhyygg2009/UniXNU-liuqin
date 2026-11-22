# 2026-11-21：VM bootstrap panic 的变量排除与根因收敛

> 设备：Xiaomi Pad 6 Pro（liuqin / SM8475）。栈：pong 栈（iPadOS 18.7.10 / 22H374），
> 引导链 = Android boot v4（kernel 槽 = gzip(BootShim + UEFI FD) + 追加 DTB，
> ramdisk 槽 = PONGFAT1 payload）→ UEFI → UniXNU 菜单 → DarwinBoot → XNU。

## 1. 现象

`VM bootstrap done: 5 maps, 115 entries and 117 holes left` 之后立即 panic：

```
panic(cpu 0 caller <sleh_synchronous_sp1>): Invalid kernel stack pointer (probable corruption)
  esr 0x9600004f   far 0xfffffff00027fc60   sp 0xfffffff00027fbe0
```

- `esr 0x9600004f` = EL1 数据异常、写、permission fault level 3
- `far = sp + 0x80`：写自己的异常栈时页权限错误
- `sleh.c:2352 sleh_invalid_stack()`：起因是嵌套异常（异常处理中再次异常）
- SP 来自 `start.s:start_first_cpu`：`excepstack_top + BA_VIRT_BASE - BA_PHYS_BASE`
  （即 `__BOOTDATA` 里的异常栈，见 `osfmk/arm64/start.s:502-523`）

## 2. 变量排除（全部实机验证）

| 版本 | 内核 | kernel 槽 DTB | ADT / 容器 | 结果 |
|---|---|---|---|---|
| v106 | prelinked，slide=-0x1000000 | 8,965 B 上游树 | 旧 | ❌ 同一 panic |
| v107 | **native**（未 prelink，18,694,488 B） | 8,965 B | 旧 | ✅ 越过 VM bootstrap，跑到 IONVRAM/AMFI，停在 `read_random` 的 NULL 函数表 |
| v108 | prelinked，slide=0x5ed0000（`Kernel.Base=0xfffffff007004000`、`__PRELINK_TEXT=0xfffffff00b000000`） | 8,965 B | 旧 | ❌ 同一 panic（sp/far/esr 一字不差） |
| v109 | 同 v108 | **原厂 Cape 480,704 B** | 旧 | ❌ 同一 panic |
| v110 | 旧 prelinked | 原厂 Cape | **官方 `finalize-pong-adt.py`**（ADT 14,480 B、`ufs_probe`、dram 8 GiB / window 4 GiB / cpus 1） | ❌ 同一 panic |

结论：slide 值、kernel 槽 DTB、ADT/容器**都不是**这个 panic 的变量；
**prelinked 内核必崩，native 内核不崩**。差异在 prelinked 内核走的装载/映射路径。

## 3. native 内核的新 panic（另一个问题）

```
Pong userland: development AMFI Interface registered
panic: Kernel instruction fetch abort at pc 0x0, lr _read_random+0x34
```

`prng_random.c:299` 的 `read_random()` 直接调 `prng_funcs.refresh(prng_ctx)`，
而 `prng_funcs` 只有 `register_and_init_prng()` 被调用过才非空；native 内核没有
prelink 的 corecrypto/pthread/apfs，函数表为 NULL。上游 `package.yml` 的注释也写了同一件事：
"the native kernel stopped at pthread_init: the function tables the kernel expects
(pthread, corecrypto, apfs) only exist once the stock kexts are prelinked in"。

## 4. slide 推导（若继续用 prelink 路线）

| 量 | 值 | 依据 |
|---|---|---|
| CI 实际使用的 slide | `-0x1000000`（21H461 的数字） | `kernel.json:slide`、`kernel-build.log` |
| 候选镜像 `__PRELINK_TEXT` | `0xfffffff004130000` | kernel.macho 段表 |
| 原始 kernelcache `__PRELINK_TEXT` | `0xfffffff005130000` | 候选 VA − slide |
| 内核内建绑定基址 | `0xfffffff00b000000` | `scripts/prepare-ipados18-storage.py:76`（`fixed=0xfffffff00b000000`） |
| 正确 slide | `0x5ed0000` | `0xb000000 − 0x5130000` |

上游最新 `board.toml` 已改成 `slide = "auto"`（由 `scripts/prelink/emit_prelink.py:37 auto_slide()`
从 kernelcache 派生到 `FIXED_PRELINK_BASE = 0xfffffff00b000000`）。
但实测 v108 表明：**即使 slide 正确，prelinked 内核仍在 VM bootstrap 崩**，
所以 prelink 路线还需要解决装载/映射问题（或采用上游最新内核源码验证）。

## 5. 产物与脚本

| 产物 | 路径 |
|---|---|
| v107（native，已证明 VM bootstrap 可过） | `F:\liuqin_ios\boot-v107-native.img` |
| v108（correct slide） | `F:\liuqin_ios\boot-v108-slide.img` |
| v109（correct slide + Cape DTB） | `F:\liuqin_ios\boot-v109-cape.img` |
| v110（官方 finalize ADT/容器） | `F:\liuqin_ios\boot-v110-officialadt.img` |
| v4 打包脚本 | `docs/make-boot-v4.py` |
| 官方口径 finalize 组装脚本 | `docs/make-v112.sh` |
| CI 取产物脚本 | `docs/ci-fetch-kernel.sh` |
| 上游源码内核 CI | `Fly116/liuqin-ipados18-ci` 工作流 `kernel-upstream.yml` |

## 6. 当前判断

1. native 内核验证了"固件 + payload + ADT + boot_args 这条链能过 VM bootstrap"；
2. prelinked 内核的崩溃与 boot_args/ADT 无关，剩下的可能就是 prelinked 映像本身
   （段布局、`MH_DYLIB_IN_CACHE` 分支重算、或工具链产物的装载假设），需要上游最新源码
   或对 `start.s`/`arm_vm_init` 加打印进一步定位；
3. 备选路线：native 内核 + 用上游 patch 把 corecrypto/pthread/apfs 的必需部分编进内核
   （上游 `zzzzzzzzzzzzzzz-liuqin-crypto-random-fallback.patch` 等已有部分尝试）。
