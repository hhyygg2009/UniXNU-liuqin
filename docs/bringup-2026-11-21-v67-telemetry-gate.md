# 2026-11-21 实机第五轮：v67（telemetry 门，单变量）

**结论先说：v66 那堵 `copyio.c:94` PAN 断言消失了。** 一个 boot-arg（`telemetry_sample_all_tasks=0`）
就把它摘掉，内核字节逐字节不变 —— 所以"DEVELOPMENT 构建默认开着全任务微堆栈采样、采样路径里
`user_access_enable()` 的 `assert(pan != 0)` 撞上嵌套不配对"这条判读成立。
新的墙在更前面一步之外：**launchd 被 SIGKILL**。

## 构建出处（v60–v66 缺的那份记录，这轮起补上了）

| 项 | 值 |
|---|---|
| 分支 / 提交 | `v67-telemetry-gate`（本轮记录提交见文末） |
| 声明的 defconfig | `configs/liuqin_boot_tc_defconfig`，sha256 `46514741353d00c4…`（= 实际生效的 `out/liuqin/.config`，逐字节） |
| 内核 | `out/liuqin/kernel/kernel.macho` sha256 `b91c756729611065…`（**与 v66 同一份，未重编**） |
| DTB | `uefi-liuqin/Resources/DTBs/liuqin.dtb` sha256 `ce90f88b645ffdcc…`（proprietary-files.txt 第 29 行） |
| FD | `uefi-liuqin/Build/liuqinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd` |
| 产物 | `build/boot-liuqin-fly116-v67-telemetry-gate.img`，69,525,504 B，sha256 `e7193ca11d33b24ffcaf3f4009f5a8147dbc3582eea124a4d90dd1a26885cca2` |
| cmdline | 46 条 / **976 B**（限制 1000；v66 是 980 B，腾出的 32 B 来自那条重复且惰性的 `native-ufs-root-partition=unixnu`） |
| 构建目录 | 隔离在 `out-v67/`（builder 的 `out/liuqin` 未被写入产物） |

命令（与 `build/NOTES.md` 的 payload-only 配方一致）：

```bash
export UNIXNU_DEVICE=<v67 worktree> UNIXNU_UEFI=<base>/uefi-liuqin UNIXNU_EDK2=<base>/uefi-liuqin/Mu_Basecore
export XNU_OWNER_ROOT_VOLUME_UUID=18AB5557-DC2D-469B-8758-855E60F5B0DF
./unixnu defconfig liuqin_boot_tc_defconfig -D "$UNIXNU_DEVICE" --out <iso-out>
python3 unixnu build payload --kernel <iso-out>/liuqin/kernel/kernel.macho --unique-chip-id 0x84750A0B0C0D0001 …
python3 $UNIXNU_DEVICE/scripts/wrap-payload-mbr.py <iso>/liuqin/payload/payload.img <iso>/liuqin/payload/payload-mbr.img
python3 unixnu bootimg --fd <FD> --dtb <liuqin.dtb> --ramdisk <payload-mbr.img>
```

⚠️ 副作用与恢复：`build payload` 会重排 **builder 真实的** `out/liuqin/tree`（我用软链把 tree 接进隔离目录，
结果 tree 步骤写穿到了真树：9 个链接指向本工作区、`drivers/NativeUfs` 别名丢失 —— 正是 AGENTS 里那条
"别名要靠 official_alias 才建"的坑）。已用 `unixnu tree -D <规范克隆> --out <builder out>` 复原，
`state.json` 用备份原样写回；复核：指向我工作区的链接 0 个、`drivers/NativeUfs` 与 `patches/xnu`（60 条）
都回到规范克隆。**教训：不要用符号链接把 builder 的 tree 接进隔离 out。**

## 上机

```
21:19:34  fastboot -s 19562650 boot build/boot-liuqin-fly116-v67-telemetry-gate.img
          Sending 'boot.img' (67896 KB)  OKAY [1.883s] / Booting OKAY [0.287s]
```

照片：`IMG_7253`（固件菜单与交接，sha256 `65147e8556a8ffca…`）、`IMG_7254`（内核正文，
`40b7ed7a5e3a8de7…`）、`IMG_7255`（终点屏，`793dadecb33aed45…`）。串口：21:21:37 前没有新 `/dev/ttyACM*`。

### 固件侧（第一次成屏读全）

`config: \EFI\UniXNU\unixnu.toml` → `READY TO ENTERQUEST` → 菜单 `1 liuqin boot: 512M win… xnu` →
`UNIXNU: menu: timeout, booting default 0`（**3000 ms 超时确认**）→
`boot: entry "xnu" (liuqin boot: 512M window (test))` → `xnu: handing off 0 staged kext(s) to loader` →
`FSOpen … kernel.macho' Success` / `kernel.macho.adt' Success` → `DARWINBOOT: hand-off to the kernel`；
`kernel va 0xFFFFFFFF00427C000 pa 0xBB07C000`、`args pa 0xBF224000 window 0x20000000`、
`video base 0xB8000000 1800x2880 row 7200 bytes 32 bpp`、`entry words D2B7031A 529FFFFFB 72BFE01B D2A0003C`、
`CurrentEL 1 SCTLR_EL1 0x30D0198D TCR_EL1 0x280803518`、
`ID_AA64MMFR0_EL1 0x22200101122 (PARange 2, 4 KiB granule supported)`、
`ID_AA64PFR0_EL1 0x1201101003111111`、`trampoline pa 0xBF22C000 (196 bytes)`、
`Exiting boot services. A green bar means the loader trampoline ran; payload entry is not yet confirmed.`、
`SecurityLock::LockType: SOFTWARE_LOCK, Module: 42B57F0A-13F2-4B21-8A23-53D3…F714B840,
Function: LockCapsuleInterface` → `Capsule Interface Locked!!` → `MU_CHANGE 161994` → `acpi 0`。

### 内核侧仍然成立的部分

`vm_page_bootstrap: 12327 free pages, 18745 wired pages`、`VM bootstrap done: 39 maps, 12 entries and
15 holes left`、`Unable to find vm tag for btlog__`、Log queues / OSLog / Firehose 配好、
`Serial requested, … configuring debugging over serial`（串口静默，符合 AGENTS）、
`NativeUfs: M:reg pre p=0xfffffff007ac36b0 link=0xfffffff006add1d0` ×3（绊线仍报绑定正确）、
`REGPROBE class=NativeUfsBlock castsDevice=1 castsService=1 state=0x1e`、
`CHAINPROBE devParent=ArmDTPlatform provParent=xiaomi,liuqin`、
`MEDIA start IOBlockStorageDevice=1 … IOMedia=3 IOMediaBSDClient=3` → `IOBlockStorageDevice=2 … IOMedia=7 IOMediaBSDClient=7`、
`V58 sync st=0x1e dev=2 drv=2 med=3 bsd=3 mc=111 n=55`；仍然没有任何 `M:stalled`/`M:bad[`/`M:fp`/`M:alien`。

### 新的终点屏

```
unexpected SIGKILL of launchd with reason — namespace 9 code 0x8 description none
frames fffffff00773a904 fffffff007719bac fffffff00771a6bc fffffff00771addc fffffff00771ac00
       fffffff0075ee3f8 fffffff00773b960 fffffff00276688 fffffff00741a9fc fffffff00741a638
       fffffff00741f664
pages=0x000000000001b6c7
free=0x00000000000019c9d
```

## 帧符号化（`llvm-nm` 对 `b91c7567…` 那份内核，51,271 符号）

| 帧 | 符号 | 备注 |
|---|---|---|
| `fffffff00773a904` | `_psignal_internal +0x990` | 发信号 |
| `fffffff007719bac` | `_process_signature +0x1dc` | exec 时的代码签名判定 |
| `fffffff00771a6bc` | `___mac_execve +0xf4` | |
| `fffffff00771addc` | `_load_init_program_at_path +0x124` | |
| `fffffff00771ac00` | `_load_init_program +0x180` | launchd 装载 |
| `fffffff00773b960` | `_bsd_ast +0x328` | 回用户态时收信号 |
| `fffffff00741a9fc` | `user_take_ast +0xc` | |
| `fffffff00741a638` | `_fleh_irq +0x48` | |
| `fffffff00741f664` | `_Call_continuation +0x44` | |
| `fffffff0075ee3f8` | （落在 `_igmp_change_state` 与 `_igmp_init` 之间） | **栈扫描残留**，不在链上 |
| `fffffff00276688` | （数据区 `BRK_AUTDA_FAILURE` 之后） | **栈扫描残留**，不在链上 |

可信链：`_load_init_program → _load_init_program_at_path → __mac_execve → _process_signature →
_psignal_internal` —— **launchd 起来了，但在 exec 的签名判定处被 SIGKILL**。`namespace 9 code 0x8`
的语义要查（下一步 1），别当结论用。

## 判读

1. telemetry 假设成立：删掉采样 = 断言消失，且这是唯一变量（内核字节相同、cmdline 只差这一条 +
   一条惰性重复项被回收）。
2. 进展是实质的：v66 死在**回到用户态之前**的采样路径；v67 走到了 **launchd 的 exec 与签名判定**，
   也就是 v44/v45 那条线一直想过的"H17 之后"。之前所有轮次都没让 launchd 被 exec 起来还活着返回。
3. 新墙属于代码签名/AMFI 域，不属于存储：块栈、根分区、绑定绊线都在屏上报了健康。

## 下一步（按代价）

1. 查 `namespace 9 code 0x8`：在 XNU 里定位 SIGKILL reason 的 namespace 枚举与 code 值
   （`proc_signal_with_reason` 一侧），先确定它是 CS_KILL（签名策略杀）还是 exec 失败杀。
   主机侧可先做：`strings -a kernel.macho | grep -n "namespace"` + 找 `_psignal_internal` 的调用者。
2. 用互斥证明法把嫌疑变量一个个关掉（AGENTS 那条"翻一个开关=造一对互斥证据"）：
   `amfi_get_out_of_my_way`（v46 那条线）、`liuqin_allow_unsealed_root=1`、
   `native-ufs-unauth-root=1`、`-apfs_shared_datavolume` —— 每次只动一个，且都记 cmdline 字节数。
3. 决定要不要把 `telemetry_sample_all_tasks=0` 并进 main（它已经证明能摘掉一堵墙，且是单变量），
   以及把 `configs/liuqin_boot_tc_defconfig` 作为 main 的声明（main 现在声明的是 `liuqin_defconfig`，
   与实际生效那份差 10 个开关）。
4. 若继续做 schema 2：4 处同名不同内容补丁重基 + 完整 `unixnu fetch`（让 lock 记下载体）不变。
