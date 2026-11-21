# UniXNU-liuqin（Xiaomi Pad 6 Pro 上的 XNU）

Xiaomi Pad 6 Pro / liuqin / SM8475 的 UniXNU 设备仓库，由 UniXNU-sheng 派生。

状态：**内核、UEFI 固件、payload、boot.img 都能在 Linux 主机上构建**（2026-11-13 验证）；
实机 `fastboot boot` 已反复验证（v33→v38，纯内存运行，不写分区）。
**2026-11-17（v38）：内核第一次走完 IOKit 引导进入 BSD，一路到达挂载决策**；
剩下的阻塞点已经在实机上钉死到具体对象、具体字段（见「进度」一节）。
**2026-11-21：`unixnu` 分区已在实机落地并逐字节校验，构建改用 PR #1 的 v60，
卡点前移到 `bsd_init` 之后的 platform-power（根因已定位，见「进度（2026-11-21…）」一节）。**
板级关键数值已经从 stock 固件里核实完了，board.toml 里每个 TODO 都有明确的取值方法和当前状态。

    board.toml                 板级描述（userdata LBA、splash、FD 窗口已填实测值）
    configs/liuqin_defconfig   内核配置（从 sheng 派生，PMGR/Adreno/UUID 标了 TODO）
    docs/liuqin-hardware.md    硬件底账：已核实 / 仍未知，每条带来源
    docs/vendor-inputs.md      stock UEFI 抽出的 154 个厂商输入 + uefiplat.cfg 硬数据
    docs/porting-plan.md       四阶段计划、通过标准、当前阻塞、立刻可做的事
    docs/liuqin-usb-debug.md   内核 USB CDC-ACM 调试口（LIQDBG001）：开关、四道自检门、移植对照表
    docs/boot-rounds-v35-v38.md  实机逐轮判读：改了什么、照片看到什么、哪些假设被证伪
    drivers/ShengUfs/          SM8475 UFS/SMMU/根存储驱动 fork（分支 liuqin-sm8475），
                               tests/check_legacy_descriptors.py 是上游 descriptor oracle
    patches/xnu/               基础补丁 + zzz-liuqin-usb-debug*.patch（USB 调试口、bsd_init 里程碑）
                               + zzzzzzzzzz-liuqin-metaclass-stalled-diagnostics.patch（H16 系列，见「进度」）
    scripts/                   板级构建/打包/补丁脚本；token-stats.py 复现「AI 用量统计」
    tests/liuqin_usb_host_test.c  调试口逻辑的主机侧测试（gcc，不需要内核）
    tests/metaclass-simtest/   postModLoad 扫描/gate 行为的主机侧仿真（./run.sh，含场景 D）
    tools/capture-liuqin-debug.ps1  主机侧抓取脚本（按 VID/PID+序列号定位 COM，置 DTR）

配套（同级目录）
    ../UniXNU-sheng            设备仓库模板
    ../UniXNU                  构建驱动
    ../uefi-liuqin-seed        Mu-Silicium 设备配置种子（Resources/Configs/liuqin.toml）
    ../liuqin-firmware         从 uefi.elf 抽出的 93 个 PE32 + 58 个 Raw
    ../liuqin-nixos            主线 Linux 侧参考（面板/触控/ABL 身份/DTBO 资料）
    ../xiaomipad-6pro-mainline 主线内核与设备脚本参考

## 构建与打包（Linux，2026-11-13 打通，2026-11-17 复验）

    cd ../UniXNU
    export UNIXNU_DEVICE=$PWD/../UniXNU-liuqin UNIXNU_UEFI=$PWD/../uefi-liuqin \
           UNIXNU_EDK2=$PWD/../uefi-liuqin/Mu_Basecore UNIXNU_TOOLCHAIN=$PWD/../toolchain

    ./unixnu build kernel                 # out/liuqin/kernel/kernel.macho（含 prelink）
    ./unixnu build firmware               # uefi-liuqin/Build/liuqinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd
    ./unixnu build payload --kernel out/liuqin/kernel/kernel.macho \
        --unique-chip-id 0x84750A0B0C0D0001
    python3 ../UniXNU-liuqin/scripts/wrap-payload-mbr.py \
        out/liuqin/payload/payload.img out/liuqin/payload/payload-mbr.img
    ./unixnu bootimg --fd ../uefi-liuqin/Build/liuqinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd \
        --dtb ../uefi-liuqin/Resources/DTBs/liuqin.dtb \
        --ramdisk out/liuqin/payload/payload-mbr.img

产物 `out/liuqin/boot/boot.img`（约 70 MiB = `gzip(BootShim+UEFI FD)` + Cape DTB +
68 MiB payload ramdisk）。`fastboot boot` 这个镜像即可，内存运行，不写分区。

对账尺寸：v36c/v37/v38 都是 **69,525,504 B**。如果打出 68,476,928 B，就是漏了
`wrap-payload-mbr.py` 那步、直接塞了裸 `payload.img` —— 少 1 MiB，而且 PartitionDxe
认不出分区，面板会停在 payload 打不开。

内核补丁套用：schema 1 的补丁序列现在由 `xnu-work/.patches.sha256` 戳保证只套一次
（补丁互相重叠，第二遍既不能 apply 也不能 reverse-check）；删掉 `.cache/xnu-work`
或改动任一补丁才会重新套用。

## 进度（截至 2026-11-17，v38）

打通的：全链路 Linux 构建与实机 `fastboot boot`（不写分区）；UFS 控制器/SMMU/根窗口读取；
userdata 挂载与非密封根策略（v34b/c/d）；panic 上屏（v35，`--panic-only`）；
**v38 第一次走完 IOKit 引导**：`K49:PE_init_iokit` → `K50…K57` → `B01:bsd_init` → … →
`B101:at_parse_rd` → `U59:start-root` → 可读的 `Failed to mount root device @IOKitBSDInit.cpp:989`。

H16 系列（把 v35 起卡在 `K49:PE_init_iokit` 的 data abort 追到底）：

| 轮 | 改动 | 结果 |
|---|---|---|
| v35 | panic 漏斗上屏 | 崩点第一次可读：`OSMetaClass::postModLoad+0x288` |
| v36 / v36c | 驱动侧 gate（名字被占就拒绝发布）+ probe 抗内联 | 同一 pc；`U:*` 从未出现 ⇒ 崩在 startRoot 之前 |
| v37 | XNU 侧 `M:stalled pre/post` + 脏槽位护栏 | 崩点后移到 `+0x2d4`；面板证明数组本身是干净的 |
| v38 | 逐槽命名 + 祖先链校验 + **跳过坏槽而非整批拒绝** | **崩溃消失**，启动到达挂载决策 |

v38 实机点名（IMG_7059–7063）：

```
M:stalled post h=0xfffffff568caffe0 cnt=172 c0=0xfffffff007c1c130 c1=0xfffffff007adfcd8
M:bad[170] bad-chain p=0xfffffff007ae36b0 vt=0xfffffff0072086d0 sz=248 n=NativeUfsBlock bad=0x3135205b0a293061
M:stalled scan BAD h=0xfffffff568caffe0 cnt=1 c0=0xac c1=0x6
```

四个字段各自独立对上静态证据：`p=` 与 `__ZN14NativeUfsBlock10gMetaClassE` 逐位相同；`vt=` 等于构造器的
`adrp+add`；`sz=248` = `0xf8` = `sizeof(NativeUfsBlock)`；`c0=` 是 `sOSMetaClassMeta`（数组确实干净且语义正确）。
172 项里**只有第 170 项坏**，坏值的小端字节是 `a 0 ) \n [ 5 1` —— 含换行的控制台文本。

由此推翻两个前提（记在这里免得走回头路）：

1. 崩溃**不是**元类重名 panic —— `getObject` 返回 NULL，我们的类登记在自己的唯一名字 `NativeUfsBlock` 下。
2. 所以 v36 的 gate 是**假阳性**：家族预链就必然合法持有 `IOBlockStorageDevice` 这个名字，gate 见就拒绝
   ⇒ v36c/v37/v38 必然走到 `I60:base-dup` + 挂载失败。它保住了命，但也永久挡住了根。

剩下的两个阻塞点：① 谁把 8 字节控制台文本写进 `NativeUfsBlock::gMetaClass + 0x10`（`superClassLink`）——
驱动自己的 `native_ufs_*` 静态量已排除，它们与该对象在 `__common` 里首尾精确相接且全是 uint32；
② 根挂载要求我们的类真正登记成功。

**下一步（v39）**：改成社区实机证明过的延迟注册形态——手写 `class MetaClass : public OSMetaClass`，
超类指针**运行时**从已登记的 `IOBlockStorageDevice` 取（把 gate 从拦路石变成指针来源），显式
`preModLoad(g_kernel_kmod_info.name)` → 构造 → `postModLoad(handle)`，加**重入闩**和 `getClassSize()`
ABI 闸，并在注册前后各打一行 `M:reg` 快照。这样既绕开那 172 项批次，又会在受控时刻把被涂的字段改回正确值。

诊断手段：面板里程碑控制台（`K`/`B`/`U`/`I`/`M:` 前缀）+ 1 MiB 环形缓冲 + USB 调试口。
逐轮改动、照片判读与被证伪的假设都固化在 **[`docs/boot-rounds-v35-v38.md`](docs/boot-rounds-v35-v38.md)**；
主机侧回归 `tests/metaclass-simtest/run.sh`（场景 A/B/C + **v38 新增的 D：复现 v37 的
`postModLoad+0x2d4` abort，并证明扫描只丢掉那个中毒槽**）；
上游 descriptor oracle `drivers/ShengUfs/tests/check_legacy_descriptors.py`（14/14）。
镜像本身放在构建机的 `releases/`（不入库，`fastboot boot` 用）。

## 进度（2026-11-21，PR #1 合并 + `unixnu` 分区实机）

本轮合并 Fly116 的 PR #1（v39→v60 构建链、platform-power 归档、`install-liuqin-ufs-data.py`、
驱动指针 `26ae865`）。**README 保留本仓原版**：PR 想把 `docs/` 折进它自己的单文件 README，
所以这个提交把本文引用的 5 篇 `docs/*.md` 连同原 README 一起留下（Fly116 版 README 的独有内容已按他的原编号合并进下方 §0–§13；被删的两篇 `docs/`
仍在 `4fc7f56` 里可查）。

下面的结论全部取自**实体机**：23046RP50C / liuqin，序列号 19562650，V14.0.9.0.TMYCNXM，
活动槽 `_b`，`verifiedbootstate=orange`（已解锁）。

### 1. §5 / B1 分区改造：已落地并逐字节校验

userdata 从 105.79 GiB 缩到 94 GiB，尾部腾出一个 raw 分区 `unixnu` 放 iOS 根镜像 —— 必须另开
分区，因为 Android 的 `/data` 是 f2fs over dm-default-key，XNU 读不到明文。

| 项 | 值 |
|---|---|
| 磁盘 | 124,893,790,208 B = 30,491,648 个 4096 B LBA |
| `userdata`（条目 34） | `2758144..27399679` = 100,931,731,456 B（94 GiB；原 `2758144..30491642`） |
| `unixnu`（条目 35，新增） | `27399680..30491642` = 12,664,680,448 B（11.7949 GiB） |
| SHNGROOT manifest base | 第 27399680 个 LBA（`0x1A21600`）= 字节 `0x1A21600000` = 112,229,089,280；根镜像之后余量 3,282,022,400 B |
| 启动参数 | `native-ufs-root=1 native-ufs-root-partition=unixnu`（cmdline 961 B / 上限 1000 B） |

设备侧校验（TWRP 里的 adbd，root）：`/dev/block/by-name/unixnu -> sda36`；首块 magic
`SHNGROOT`，sha256 `bc908a6c…` == host 的 `manifest_sha256`；OS / OS-mid / SystemOS / tail
四个 64 MiB 抽检区与 host 产物逐个一致；`userdata` 首块已清零。**LBA 0（protective MBR）全程未写。**

GPT 实况和教科书不一样，记一笔免得下次照抄：条目数组是 **64 项 = 2 个 LBA**（不是 128 项 = 4
LBA），备份数组 `30491643..44` 与备份头 `30491647` 之间还有 **2 个 pad LBA**（`30491645..46`）。
按"128 项 + 紧邻"的假设写备份区会直接毁掉磁盘尾部，所以写盘窗口必须由**抓到的备份头**反推，
pad 原样保留。改分区的 `carve-unixnu-gpt.py`（含 `selftest`，模拟盘回归覆盖了这两处）与回滚集
`backup/live-20261120-2145/ROLLBACK-gpt-{primary,backup}.bin` 在构建机的项目目录里，未入库。

前置条件：§5 的 dd 需要 root，而 `fastboot boot recovery.img` 在这块板上做不到（TWRP 镜像
`kernel_size=0`），只能 `fastboot flash recovery_b`（`recovery_a` 报 `partition size: 0`）。
**副作用：`recovery_b` 现在不是 stock**，stock 的 `recovery.img` 在 V14.0.9.0 的 fastboot 包里，
刷回去即可。`/data` 是按方案擦掉的，首次进 Android 会在 94 GiB 上重建。

### 2. 全量 Fly116 树构建：通过

设备树 `ed74857` + 驱动 `26ae865`（内容 v60）。产物 `boot-liuqin-fly116-v60.img`
69,525,504 B（sha256 `2c2255c0…`），`kernel.macho` sha256 `64d79963…`，`fastboot boot`
纯内存运行。两处必要偏差：

- **D1** — builder 不会自动跑 `patches/xnu/manual/`，手工 XNU 补丁（waiting-thread dump +
  IOResources notify）必须显式执行，否则链接缺 `_liuqin_dump_waiting_threads`；
- **D2** — 那个补丁插的 `#include <kern/thread_kernel_state.h>` 要删掉：这个头的 `#ifndef`
  后面**没有配对的 `#define`**（29 / 46 行，与上游 `feeds/xnu-0b4b910d` 逐字节相同，不是补丁
  改的），而 struct 已经由 `kern/thread.h → arm/thread.h` 进来了，于是
  `redefinition of 'thread_kernel_state'`。

内核标记核对：`native-ufs-root-partition` 2、`gLatePublishGate` 2、`startDeferredMatches` 2、
`liuqin_dump_waiting_threads` 2、`liuqin_iokit_job_counts` 2、`IORegistryIterator` 104。

### 3. 卡点前移到 `bsd_init` 之后的 platform-power，根因已钉死

第一轮（v43 驱动）报 `root manifest rejected` **不是分区失败**：那个驱动把根分区名硬编码成
`"userdata"`，读的就是刚被擦掉的 userdata（构建出的内核里 `native-ufs-root-partition` 出现
0 次）。v60 才真正解析这个启动参数。

v60 面板，逐地址用 `nm -n -arch arm64` 导出的 51,273 个符号对上：

```
M:stalled post h=0xfffffff567d58600 cnt=1 c0=0xfffffff007ae37f0 = __ZN18ShengPlatformPower10gMetaClassE
Kernel instruction fetch abort at pc 0x003f0100aa1503e0, lr 0xfffffff0079eb28c
  lr  = _sheng_pmgr_run+0x364，而 lr-4 处正是 `bl _sheng_original_pmgr_nub_read`
  x25 = _sheng_pmgr_observation（精确）      x8 = __ZTV18ShengPlatformPower+0x10（vtable）
```

坏 `pc` 逐位等于 `build/sheng-platform-power/original-bindings.S` 里生成的常量：

    .set __ZN9ApplePMGR7readAonEv,  0xd63f0100aa1503e0
    .set __ZN9ApplePMGR8writeAonEy, 0xf940f508f9400008

这两个值**不是地址，是指令字**（`mov x0,x21` / `blr x8`，`ldr x8,[x0]` / `ldr x8,[x8,#0x1e8]`），
被当成 `ApplePMGR::readAon` / `writeAon` 的函数指针写进了 `__ZTV18ShengPlatformPower`，
虚调用就跳到代码页上当数据取指。来源在 `scripts/build-sheng-platform-power.py`：

- 正常 reference 是 `build/xnu-prelink/*-candidate.macho`，只有先前跑过一次原生构建才有；
  `71c6278` 加了回落：改用 `out/ipsw/<build>/kernelcache.decompressed`（本机 `21H461`）；
- AON 槽位取值是 `bindings[name] = ptr(0xfffffff005ce26d8 + slot*8)`。**这个常量在
  "cache VA + slide" 空间里**，而回落镜像是**未滑动**的原始 cache，读位置整体差了 16 MiB，
  落进 `__PLK_TEXT_EXEC`（prot=0x5）：在 21H461 里 `0xfffffff005ce30d8` 的 8 字节正是
  `e0 03 15 aa 00 01 3f d6`；
- 读位置补上板级 slide（`+0x1000000` → `0xfffffff006ce30d8`）就落在 `__PLK_DATA_CONST`，
  读出 `0xfffffff005a20fbc` / `0xfffffff005a20fc4`：两者都在 `__PLK_TEXT_EXEC`，首字分别是
  `mov w0,#0` 和 `ret`，且互相相邻 8 字节 —— 真 vtable 该有的样子。

同一个坑他在 v50 已经为 IOStorageFamily 修过：`build-liuqin-ufs-bindings.py` 会读
`board.toml` 的 `slide`，`slide == 0` 直接 `fail('refuse to bind raw cache VAs')`，再逐指针
`+slide`；`build-sheng-platform-power.py` **一处 slide 处理都没有**，而它那些 `require()` 只校验
符号名和槽位形状，从不校验取到的值像不像地址，所以坏常量一路通过 `build-report.json` 进了链接。

**下一步（本轮未做）**：给 platform-power 的绑定补 slide；并加两道 sanity 门 ——
读位置必须落在参考镜像的**数据段**（vtable 是数据，这次它落在了 `__PLK_TEXT_EXEC`），
读出来的值必须是参考镜像里**已映射且可执行**的地址（函数指针，`0xd63f0100aa1503e0` 连合法
VA 都不是）。任一道门都能在本次构建里当场拦下这个毒常量；或者干脆在缺 candidate 时拒绝构建。
改完要重跑一遍 v60 才能重新判断根挂载。
根挂载与 userland 至今仍未达成，本节记录的是"§5 分区方案本身成立 + 卡点换了位置"。

---

# 附：PR #1 的 README 合并内容（@Fly116，`4fc7f56`）—— §0 至 §13

> 他的 §12 致谢不在此重复，已照录进后面的「致谢 / Acknowledgments」一节；
> 本节内部凡是"见致谢"都指那里。§0 与 §13 分别是他原文顶部的警告块与文末声明。

**为什么是"以下"而不是"替换"**：本仓原版 README 一字未删（`git diff --numstat cbbb7b1 HEAD -- README.md`
可核对为纯追加）。PR 想把 `docs/` 折进它自己的单文件 README 并整篇替换本文件，所以这里采取
**并列合并**：他的章节标题保留原始的 `§N` / `3.x` / `9.x` 编号，因为他文中大量交叉引用（
"见 §3.5"、"见 §5"、"§7 的三个大坑"、"§9.3 的坑"）只有按原编号才不会失效。
凡本仓 2026-11-21 实测与他的结论有出入或需要补充的地方，都用 **【合并注】** 标出，正文其余是他的原文。

---

## §0 ⚠️ 请先读完这段，再决定要不要继续折腾

> **这个仓库目前无法启动 iOS 用户界面，也看不到任何 iOS 桌面。**
>
> 现状是：内核能启动、UFS 存储链路能跑通、能读取真实的 iOS 根镜像、能把镜像窗口发布成块设备；
> 但**根卷还没挂载成功**，更没有 `launchd`、没有图形界面。所有发布出去的镜像都只是
> **实验性 bring-up 镜像**，仅用于 `fastboot boot` 内存启动调试。
>
> 另外必须说明一个**硬件层面的现实**：iOS 的图形栈依赖 Apple 自己的 GPU（Metal 驱动）。
> 这台机器目前没有 GPU 驱动。
>
> 本仓库**没有**任何写入 Android 分区的自动化脚本，所有实验默认 `fastboot boot`（只进内存）。
> 除非你清楚自己在做什么，**不要**照搬本仓库历史中的分区改造步骤。

【合并注】§5 的分区改造在本仓设备上是**真的做了**（2026-11-21，见上文「进度（2026-11-21…）」），
所以这句"没有自动化脚本"要补一句：**做分区改造必须自带逐 LBA 备份与回滚集**，本仓的回滚集是
`backup/live-20261120-2145/ROLLBACK-gpt-{primary,backup}.bin` + `ROLLBACK.txt`。

---

## §1 这个项目是什么 / 不是什么

**是**：

- 一个「把 XNU 移植到非苹果 SoC」的工程实验记录：包含板级描述、内核补丁、UFS 块设备驱动、
  构建脚本、以及大量实机调试结论与踩坑档案；
- 一份**可复现的构建与调试方法**：从官方线刷包抽 UEFI、构建 XNU、打包 `boot.img`、
  在真机上用面板里程碑定位卡点。

**不是**：

- 不是能日常使用的系统，不是「刷完就能进 iOS」的一键包；
- 不是与苹果官方相关的项目；
- 不提供任何对 Android 分区的自动破坏性操作。

---

## §2 当前进展总览

| # | 里程碑 | 状态 | 说明 |
|---|--------|------|------|
| 0 | Linux 构建链（固件 / 内核 / prelink / payload / boot.img） | ✅ | 不需要 macOS |
| 1 | 固件启动 → UniXNU 菜单 → payload 卷挂载 | ✅ 实机 | Mu-Silicium UEFI |
| 2 | 内核启动 → `bsd_init` → `startRoot` | ✅ 实机 | |
| 3 | 元类（`OSMetaClass`）修复 | ✅ 实机 | 见 §3.5，后来的结论是"符号绑定少了一次 slide" |
| 4 | UFS 控制器初始化 / 链路启动 / LUN 就绪 | ✅ 实机 | v40 打通，READ(10) 可用 |
| 5 | 读 GPT / 解析 SHNGROOT 根清单 | ✅ 实机 | `root-layout-ok` / `root-manifest-ok` |
| 6 | 设备分区改造：`userdata` → 94 GiB + 新增 `unixnu` 11.79 GiB | ✅ 实机 | 见 §5，**会动分区表**，务必先备份 |
| 7 | 把 8.74 GiB iOS 根镜像写入 `unixnu` 并被内核接受 | ✅ 实机 | `root-plan-ok` / `root-iostart-ok` |
| 8 | 发布窗口为块设备（`IOBlockStorageDevice`） | 🟡 部分 | 构造函数、注册均已打通，但 **IOKit 迟迟不创建 `IOMedia`** |
| 9 | APFS 根卷挂载（T1） | ⛔ 未完成 | 卡在 #8 |
| 10 | 用户态 `launchd`（T2） | ⛔ 未开始 | |
| 11 | 图形界面 | ⛔ 未开始，且受 GPU 限制（见 §0 警告） | |

一句话概括：**存储这一层（最难、最硬件相关）基本打通；根卷挂载还差一步；用户态与界面还没开始。**

### 进度参考（按工作量粗略估计）

- 硬件/存储 bring-up：约 100%
- 块设备发布：约 85–90%
- APFS 根卷挂载：约 10%
- 用户态：约 0%
- 图形界面：0%

综合约 **45–50%**（以"能看到某种界面/用户态输出"为目标）。

【合并注】本仓设备（序列号 19562650）在 2026-11-21 的独立复核结果，与上表**部分一致、部分需要修正口径**：

- **#6 在本仓设备上成立**：分区已按 §5 落地，并且在 TWRP 侧做过逐字节校验（`unixnu` 首块
  magic `SHNGROOT`、sha256 == `manifest_sha256`、四个 64 MiB 抽检区全等）。数值见上文表格。
- **#5 / #7 的"被内核接受"这一半，本仓尚未复现**：不是分区没做好，而是 v60 内核在能读根清单
  之前就先崩在 `bsd_init` 里的 platform-power（见 §3.7）。**先修 §3.7 才能重新判断 #5/#7/#8。**
- **本轮新暴露的次序问题**：上表按"越往下越远"理解已经不成立 —— #2 与 #5 之间现在多了一个
  platform-power 崩溃点，它属于 #3 的同一 bug 类（漏 slide），所以 §3.5 那一条应当看成**未完成**。
- v43→v60 之间还有一个纯构建问题：本仓第一轮报 `root manifest rejected` 的驱动
  把根分区名**硬编码**成 `"userdata"`（`RootLayout.h` 里 `wanted[] = "userdata"`），
  根本不读 `native-ufs-root-partition`，于是去读了刚被 §5 擦掉的 userdata。
  **照 §5 改完分区后，必须确认内核二进制里真的有这个记号**：
  `strings kernel.macho | grep -c native-ufs-root-partition`（v60 = 2，v43 = 0）。

---

## §3 已经实机打通的硬骨头（技术档案）

这一节是本仓库最有价值的部分：每一条都是在这台真机上验证过的结论，能帮后来者少走几天弯路。

### 3.1 非苹果平台没人初始化 `IOMapper`

IOKit 的 DMA 路径默认依赖 `IOMapper::gSystem`，而它在苹果平台上由特定驱动设置。
非苹果平台永远保持 `kUnknown`，导致所有需要映射的分配路径不可用。
**处理**：驱动里显式 `IOMapper::gSystem = 0`，并用 `kmem_alloc_contig` + `ml_vtophys` 自建 DMA 缓冲。

### 3.2 wired（不可分页）描述符的 `prepare()` 永不返回

`IOBufferMemoryDescriptor` 走 `kIOMemoryAutoPrepare → prepare() → wireVirtual()` 时挂死。
**处理**：不使用 wired 描述符，改用可分页路径 + 自己管理物理地址。

### 3.3 早期连续页分配不可靠 / `mach_absolute_time()` 不前进

- `kmem_alloc_contig` 在驱动启动阶段约有一半概率卡死在 `vfc-locked`；
  **解决**：把连续页分配提前到 `STARTUP (EARLY_BOOT, STARTUP_RANK_FIRST, ...)` 阶段做一次，之后复用。
- 早期 `mach_absolute_time()` 不前进，所有**基于 deadline 的等待都会挂死**；
  **解决**：UFS 等待改成忙等 + 计数上限，不用时间。

### 3.4 `IOSleep()` 在本平台永不返回（重要）

这不是"睡得久"，而是**永远醒不过来**。凡是 `IOSleep` 参与的等待都会变成永久挂起，
而且因为它是忙等实现，还会饿死其它线程。
**处理**：等待逻辑一律改成「忙等 + `thread_yield_to_preemption()` 让出 CPU」，
必要时主动调用 `IOService::startDeferredMatches()` 推动 IOKit 匹配。
（本仓库 v45 镜像就是靠这条才把"卡住不动"变成"能报超时错误"。）

### 3.5 预链接 kext 的符号绑定漏了 slide（也解释了"元类被涂鸦"）

`emit_prelink.py` 把 IOStorageFamily 放在 `原始 kernelcache VA + slide`（本板 `-0x1000000`），
但 `build-liuqin-ufs-bindings.py` 早期版本按**原始 VA** 写绝对符号。
结果 `new NativeUfsBlock` 会跳到 16 MiB 之外的零填充区，执行 `UDF #0`（未定义指令）——
构造函数根本没跑，块设备发布自然无法开始。
同一个 stale 值也是历史上"元类 `superClassLink` 被涂鸦成 ASCII 字符"的真正原因。
**处理**：绑定生成时套用 board 的 slide（`scripts/build-liuqin-ufs-bindings.py`）。

### 3.6 device-tree 父节点无法发布服务

`IOService::registerService()` 在服务于 `gIOServicePlane` 中找不到通往 registry root 的路径时，
会**静默返回**（连日志都不打）：

```c
while (parent && (parent != root))
	parent = parent->getParentEntry(gIOServicePlane);
if (parent != root) { IOLog("... not registry member ..."); return; }
```

而本驱动是挂在**设备树节点**（`xiaomi,liuqin`）上的，该节点在 service plane 里没有父节点，
于是窗口设备从未真正注册（`state=0x0`），存储家族驱动自然看不到它。
**处理**：发布前把窗口 attach 到 **platform expert（`ArmDTPlatform`）**，链路可达 root，
之后能看到 `REGPUBLISH ok`。

### 3.7 （本仓 2026-11-21 新增）platform-power 的 AON 绑定也漏了 slide，而且是**静默**的

与 §3.5 同一 bug 类，换了条路径，且这次**没有任何 UDF/零填充可辨认**——毒掉的槽位里存的是
**指令字**，虚调用直接把 CPU 送到代码页去取指：

    Kernel instruction fetch abort at pc 0x003f0100aa1503e0, lr 0xfffffff0079eb28c (= _sheng_pmgr_run+0x364)
    lr-4 处是 `bl _sheng_original_pmgr_nub_read`；x25 = _sheng_pmgr_observation；
    x8 = __ZTV18ShengPlatformPower+0x10；M:stalled 的 c0 = ShengPlatformPower::gMetaClass（精确）

成因在 `scripts/build-sheng-platform-power.py`：正常 reference 是
`build/xnu-prelink/*-candidate.macho`（**只有先前跑过一次原生构建才有**，这正是 PR #1 抱怨的
"No reference candidate available"）；`71c6278` 加的回落改用了**未滑动**的
`out/ipsw/<build>/kernelcache.decompressed`，可 AON 槽位是拿**候选镜像地址空间**的常量去读它：

    bindings[name] = ptr(0xfffffff005ce26d8 + slot * 8)      # slot 320 = readAon，321 = writeAon

在 21H461 里 `0xfffffff005ce30d8` 落在 `__PLK_TEXT_EXEC`（prot=0x5），8 字节是
`e0 03 15 aa 00 01 3f d6` —— 即 `mov x0,x21` + `blr x8`，被当成 `ApplePMGR::readAon` 的函数指针
写进 `__ZTV18ShengPlatformPower`。把读位置补上板级 slide（`+0x1000000` → `0xfffffff006ce30d8`）
就落在 `__PLK_DATA_CONST`，读出 `0xfffffff005a20fbc` / `0xfffffff005a20fc4`：都在
`__PLK_TEXT_EXEC`，首字分别是 `mov w0,#0` 与 `ret`，且正好相邻 8 字节 —— 真 vtable 的样子。

**为什么它没被拦住**：脚本里那一串 `require()` 校验的是**符号名与槽位形状**（那份 vtable 来自
我们自己编的 `ShengPlatformPower.o`，与地址空间无关），从不校验**取到的值像不像地址**，
于是毒常量一路写进 `build/sheng-platform-power/build-report.json` 并进了链接。
对照 `build-liuqin-ufs-bindings.py` —— 它读 `board.toml` 的 `slide`，`slide == 0` 直接
`fail('refuse to bind raw cache VAs')`，再逐指针 `+slide`。**§3.5 修过的防线没有覆盖到这条路径。**

**建议的两道门**（任一条都能当场拦下，别只修第一条）：

1. 读位置换算到参考镜像自己的地址空间（`-slide`）；
2. 校验：读位置必须落在参考镜像的**数据段**（vtable 是数据），读出的值必须是参考镜像里
   **已映射且可执行**的地址（函数指针）。宁可拒绝构建，也不要静默发出一个毒常量。

**定位手段**（这条是本轮唯一有效的办法）：把 panic 块里的 `pc`/`lr`/`x8`/`x25`/`c0` 全部拿去
跟 `nm -n -arch arm64 kernel.macho` 导出的符号表对（本仓 51,273 个符号，存成
`build/v60-kernel.syms`），再用 `llvm-objdump -d` 看 `lr-4` 那条指令 —— `lr` 落在 `bl` 之后
就说明崩在被调函数里，而不是在带 `lr` 的那个函数里。这一步把"卡在 IOKit"直接缩到一次生成常量的误用上。

---

## §4 当前卡点与已知问题

**卡点**：`NativeUfsBlock::start()` 成功、`registerService()` 也真正发布了（`REGPUBLISH ok`），
但之后 **没有 `IOBlockStorageDriver` 认领该设备**，因此没有 `IOMedia`，
媒体等待超时 → `UFS ROOT FAILURE: SystemOS window media not found`。

**已排除**的假设（省得后来者重查）：

| 假设 | 结论 |
|------|------|
| 元类链断裂（历史 H16） | ❌ 已排除，`metaCast("IOBlockStorageDevice")` 正常 |
| 服务没注册 | ❌ 已修（改挂 platform expert） |
| `startRoot` 被调用两次 | ❌ 已排除，`PUBSTATE entries=1` |
| 发布太早、IOKit 匹配未就绪 | ❌ 已排除（加了延迟门仍无 IOMedia） |
| `IOService::getMatchingServices()` 计数为 0 | ⚠️ 该方法在此阶段不可信，不能作为证据 |

**下一步优先级**：

1. 用**注册表迭代器**（`IORegistryIterator`）遍历 platform expert 下的服务，确认到底有没有
   `IOBlockStorageDriver` / `IOMedia`；**注意把结果拼进 panic 文本**，因为面板普通输出行会丢失；
2. 若没有驱动 → 查 IOStorageFamily 的 personality 是否注册、匹配是否真的在跑；
3. 若有驱动无媒体 → 查驱动的 start 路径，以及 **`bsd_init` 之后 UFS 读是否仍可靠**
   （UFS 写在 `bsd_init` 缓冲缓存初始化之后会挂死，读共用同一套传输槽位，这是真实风险）。

【合并注】本仓 2026-11-21 的实测：**上面这个卡点这轮没机会复现** —— v60 在 `bsd_init` 内部
`sheng_pmgr_run` 就崩了（§3.7），位置比 `startRoot` 更早。所以 §4 的优先级 1–3 要整体往后挪一位，
**新的第 0 优先级是修 §3.7**。修完重跑同一份 v60 源码，才能判断 §4 是否依然存在。
（另注：`REGPUBLISH` 这个文本在 v60 已被改名，`strings` 数为 0 属正常，别当成回归。）

---

## §5 设备分区改造（`unixnu`）说明

> ⚠️ **这是本仓库唯一动过分区的部分，且不可"一键回滚"。做之前必须完整备份。**
> 本人（@Fly116）的设备已经改造完成；如果你只是来复现内核部分，**不需要**做这一步。

原因：Android 的 `/data` 位于 f2fs + metadata 加密（dm-default-key）之上，
XNU 的裸读拿不到明文，所以 iOS 根镜像必须放在一块**内核能裸读写**的独立分区里。
而 128 GB 设备原本排满，于是做了：

1. 完整备份 GPT（主/备两份，CRC 校验）与 `/sdcard`；
2. 改写 **primary GPT**：`userdata` 105.79 GiB → **94 GiB**，尾部新增 **`unixnu`** 11.79 GiB；
3. `fastboot erase userdata metadata` → Android 重新格式化为 94 GiB（**应用数据会清空，文件已备份**）；
4. 用 `unixnu userdata` 从 iPad7,1 17.7.11 (21H461) IPSW 构建 9,382,658,048 B 的根镜像；
5. sparse 化后写入 `unixnu`；
6. 内核用 `native-ufs-root-partition=unixnu` 启动。

**保数据缩容（原地缩 f2fs）试过两版，都不可行**：Android 13 的 `/data` 有大量 bind mount，
卸载时触发 `reboot,vold-failed` 保护，已放弃。

相关工具与文档：

- `scripts/install-liuqin-ufs-data.py`（镜像切分/写入，含 `--partition-offset`、自检）
- `docs/userdata-carve-plan.md`、`docs/userdata-build-plan.md`
- `docs/bringup-2026-11-19-session.md`（那一轮的全部结论）

【合并注】本仓设备已按本节 1–6 全做完，实测数值与逐字节校验在上文「进度（2026-11-21…）」。
本节步骤在实操上还需要补三条，都是踩过才知道的：

1. **写 GPT 之前必须实地抓一次真实布局**，不能按教科书假设写。本仓设备实测：条目数组是
   **64 项 = 2 个 LBA**（不是 128 项 = 4 LBA），备份数组 `30491643..44` 与备份头 `30491647`
   之间还有 **2 个 pad LBA**（`30491645..46`）。按"128 项 + 紧邻"写备份区会直接毁掉磁盘尾部。
   写盘窗口要**由抓到的备份头反推**，pad 原样保留，写完做读回比对。
   另：TWRP 的 adbd 会把 `dd` 的 stderr 并进 stdout（本仓实测尾巴多出 78 B，不是 4096 的整数倍），
   所以抓取一律在设备侧 `2>/dev/null`，并在解析端拒绝长度不是 LBA 整数倍的 blob。
2. **第 3 步的 dd 需要 root**，而 `fastboot boot recovery.img` 在这块板上做不到（TWRP 镜像
   `kernel_size=0`），只能 `fastboot flash recovery_b`（`recovery_a` 报 `partition size: 0`）。
   **副作用：`recovery_b` 从此不是 stock**；stock 的 `recovery.img` 在 V14.0.9.0 线刷包里，
   要还原刷回去即可。这一步应当写进 §5 的前置清单。
3. **`install-liuqin-ufs-data.py --transport fastboot --apply` 会被脚本自己 `parser.error` 拒掉**
   （快速开始式的想当然命令，实际不支持）。正确做法是让它**打印** fastboot 命令、由人执行。

---

## §6 快速开始（测试已有镜像）

> 只做 `fastboot boot`（加载进内存），**不要** `fastboot flash`。
> 前提：设备已完成 §5 的分区改造，否则会在解析根布局处停止。

```bash
adb reboot bootloader
fastboot boot boot-v41-unixnu.img          # 或 Releases 中最新的实验镜像
```

观察面板里程碑（U/B/K 开头的编号行）。预期会看到：

```
U86:root-bringup-ok → U87:root-layout-ok
U88:root-manifest-ok → U89:root-plan-ok → U90:root-iostart-ok
...
U139:probe-nub → U140:start-enter → U141:start-root
U142:blk-registered → U143:blk-matched → U146:root-pub-SystemOS
U147:root-media-timeout        ← 当前卡在这里
```

排障：`docs/` 下有本轮全部调试记录；`docs/bringup-2026-11-19-session.md` 是最新的一份。

【合并注】本仓**没有跑过 Releases 里的镜像**，只跑过自己从本仓源码构建的 v60，因此上面这段
预期里程碑序列在本仓设备上**尚未取到**（原因见 §3.7：崩在 `bsd_init` 之内，比 `U86` 更早）。
把它当"修好 §3.7 之后的验收序列"看，不要当现状。

---

## §7 从零构建

需要：Linux x86_64、Python 3、约 60 GB 磁盘、能下载 IPSW。

```bash
# 1) 代码
git clone https://github.com/Fly116/UniXNU-liuqin      # 设备仓库（含 board.toml / patches / drivers）
git clone https://github.com/Fly116/UniXNU-new         # 构建驱动（unixnu CLI）
git clone https://github.com/UniXNU/Mu-Silicium        # UEFI（仓库内称 uefi-liuqin）

# 2) 工具链（装到 /opt/unixnu-linux）
python3 UniXNU-new/scripts/linux/setup-toolchain.py
# 3) 从官方线刷包抽厂商 UEFI 模块（需要 V14.0.9.0 之类的线刷包）
# 4) 准备 IPSW（iPad7,1 17.7.11 / 21H461）
#    放到 F:\\liuqin_ios\\_dl\\ 或任意路径，用 --ipsw 指定
# 5) 构建
python3 UniXNU-new/unixnu build kernel  -D device-new
python3 UniXNU-new/unixnu build payload --kernel out/liuqin/kernel/kernel.macho \
        --unique-chip-id 0x84750A0B0C0D0001 -D device-new
python3 device-new/scripts/wrap-payload-mbr.py out/liuqin/payload/payload.img out/liuqin/payload/payload-mbr.img
python3 UniXNU-new/unixnu bootimg --uefi uefi-liuqin --fd .../SILICIUM_UEFI.fd \
        --dtb .../liuqin.dtb --ramdisk out/liuqin/payload/payload-mbr.img -D device-new
```

### 构建的三个大坑（必读）

1. **`patches/xnu/*.patch` 不会自动应用**。构建脚本只编译 `.cache/xnu-work` 里已有的源码，
   补丁需要**手动** `patch -p1 <` 到 `out/liuqin/tree/.cache/xnu-work/` 再构建。
   漏应用或在错误顺序下应用，会出现 `start.s: patch does not apply` 或
   `Native kernel lacks sparse-prelink index ABI` 之类的报错。
2. **`XNU_SHENG_PMGR` 默认 0**，`sheng_pmgr_run()` 根本不会被编译进去
   （`board.toml` 里 `sheng-pmgr=2` 只是 boot arg 门禁，不代表代码在）。
3. 环境变量至少要设：`UNIXNU_PATCH_LIST=manual`、
   `XNU_OWNER_ROOT_VOLUME_UUID=18AB5557-DC2D-469B-8758-855E60F5B0DF`。

【合并注】本节是**他那台机器**的路径与参数（`-D device-new`、`/opt/unixnu-linux`、Windows 盘符）。
本仓实测可用的配方在上文「构建与打包」一节，环境差异四条：

- 工具链用仓内的 `toolchain/`（真带 `CommandLineTools/usr/bin/ld`）；
  **`/opt/unixnu-linux` 这套里没有 `ld`**，直接报 `unixnu: ... /usr/bin/ld missing`；
- **必须导出 `UNIXNU_UEFI`**，否则 panic 上屏编译不过：
  `Panic screen requires Mu-Silicium FrameBufferSerialPortLib/Font.h`；
  另外 `UNIXNU_EDK2=$UNIXNU_UEFI/Mu_Basecore`；
- 上面坑 1 在 `manual` 补丁上还有第二层：**builder 不会调用 `patches/xnu/manual/` 里的脚本**
  （它不是 `patch -p1`，是个 python 生成器）。不显式跑就会链接报
  `Undefined symbols "_liuqin_dump_waiting_threads", "_liuqin_iokit_job_counts"`；
- 显式跑完那个脚本后会撞上**新坑 4**：它插入的 `#include <kern/thread_kernel_state.h>` 要删掉。
  这个头的 `#ifndef`（29 行）/ `#endif`（46 行）**没有配对的 `#define`**（与上游
  `feeds/xnu-0b4b910d` 逐字节相同，不是补丁改的），而 `kern/thread.h → arm/thread.h` 已经带进
  同一个 struct，于是 `error: redefinition of 'thread_kernel_state'`。
  注意这条坑**很会伪装**：删掉 include 之前，builder 只会报
  `Unavailable real kernel imports: ['_thread_deallocate', ...]`，因为 `kernel_names()`
  只读**已存在**的 `.o`，而 `thread.o` 正因为这个错误没编出来。
- **还有第 5 个坑，就是 §3.7**：在一台从没跑过原生构建的机器上，`build/xnu-prelink/*-candidate.macho`
  不存在 → platform-power 回落到未滑动的 `kernelcache.decompressed` → 编出来的内核能链接、能启动、
  在 `bsd_init` 内崩。也就是说**这条路径上"构建成功"完全不代表"能跑"**。

---

## §8 实机调试方法论（血泪经验）

本平台的调试手段有限，**用错通道会得出错误结论**（我们在这上面浪费了很久）：

| 通道 | 可靠性 | 说明 |
|------|--------|------|
| 面板里程碑 `liuqin_milestone(tag,msg)` | 🟡 内容可信、**顺序不可信** | 直接写 framebuffer，最早可用；但缓冲区会覆盖/回绕，照片里会出现编号倒序 |
| 持久日志扇区（`unixnu` LBA 2400000） | 🟡 **仅早期可用** | `bsd_init` 缓冲缓存初始化之后 UFS 写会挂死，日志探针会反过来阻塞被测路径 |
| `IOLog` / `printf` | 🔴 基本不可见 | 该阶段控制台未接到面板 |
| **panic 文本块** | 🟢 最可靠 | 完整显示在最显眼位置，适合放关键状态汇总（如 `PUBSTATE ...`） |
| `IOService::getMatchingServices()` | 🔴 不可信 | 该阶段对已有实例也返回 0 |

**结论**：要判断"走到哪一步"，用里程碑；要判断"状态是什么"，把状态塞进 panic 文本。

【合并注】本仓再加两行进这张表：

| 通道 | 可靠性 | 说明 |
|------|--------|------|
| **"设备从 adb / fastboot / EDL 全部消失"** | 🟢 可用 | 说明 XNU 已经在面板上跑（不是重启循环），可以放心判读照片 |
| **`strings kernel.macho \| grep -c <记号>`** | 🟢 最便宜的止损 | 刷机前一条命令就能否掉"驱动根本没编进去/参数没被解析"这类假故障，本仓靠它拦掉两次误判（v43 硬编码分区名、§3.7） |

以及一条实操：`fastboot boot` 只能看一次，**照片要一次拍全**（本仓判读用的是 IMG_7234：
`M:stalled pre/post` + panic 头 + 全部寄存器 + `pages/free`，缺一个字段就得再刷一轮）。

---

## §9 给 AI / 新手的继续开发指南

### 9.1 代码地图（按重要性排序）

| 路径 | 作用 |
|------|------|
| `drivers/ShengUfs/NativeUfs.cpp` | **主战场**：UFS 主机控制器、`startRoot`、块设备类 `NativeUfsBlock`、发布流程、绝大多数里程碑与诊断 |
| `drivers/ShengUfs/RootCommands.h` | 传输层：SCSI 命令、PRD、槽位池、store/log 写窗口（`hostWrite4k` 是唯一写入口） |
| `drivers/ShengUfs/RootLayout.h` | GPT 解析 + SHNGROOT 清单解析（含 `native-ufs-root-partition` / `-offset`） |
| `drivers/ShengUfs/RootPublication.h` | prepare → validate → schedule → start/wait 的发布状态机 |
| `drivers/ShengUfs/RootWindowRead.h` | 窗口读（bounce buffer、校验、并发） |
| `scripts/build-liuqin-ufs-bindings.py` | **prelink 符号绑定**生成（§3.5 的战场，改动风险高） |
| `package/ShengPlatformPower.cpp` | 平台电源 nub（默认不参与编译） |
| `board.toml` | 板级描述 + 内核启动参数（改行为优先从这里下手） |
| `scripts/install-liuqin-ufs-data.py` | 镜像切分/写入工具 |

【合并注】按本轮结论补两行，并把"默认不参与编译"改准：

| 路径 | 作用 |
|------|------|
| `scripts/build-sheng-platform-power.py` | **§3.7 的战场**：生成 `original-bindings.S` 与 `libShengPlatformPower.a`；一旦启用 `sheng-pmgr`（`pmgr=2` + `XNU_SHENG_PMGR`）它就真的在 `bsd_init` 路径上，见 §9.3 |
| `build/sheng-platform-power/build-report.json` | 每次生成的 `.set` 常量都会落在这里 —— **判读 §3.7 类问题先看它**，面板 pc 与其中某个值逐位相同即可定罪 |

`package/ShengPlatformPower.cpp` 的准确说法是：**由 `pmgr` boot arg + 归档开关决定**，
本仓构建出的 v60 内核里 `sheng_pmgr_run` 已编入并被调用（否则不会崩在那条路上）。

### 9.2 按顺序推进的建议

1. **先解决 IOMedia 不出现**（§4）。不要跳到 APFS 或用户态，没有 `/dev/diskNsM` 什么都做不了。
2. 拿到 IOMedia 之后，先验证**读**是否可靠：用 `doAsyncReadWrite` 读第 0 块并校验签名。
   （UFS 写在 `bsd_init` 后会挂死，读共用同一套槽位，必须实测。）
3. 再做 APFS 根卷挂载（T1）。
4. 最后才是用户态。

【合并注】本仓实测的插入项：**第 0 步是修 §3.7**（platform-power 的 AON 绑定漏 slide）。
它排在 §4 之前，且不修的话 §4 的任何观测手段（包括把状态拼进 panic）都拿不到数据。

### 9.3 一定要避开的坑

- **不要用 `IOSleep`**，也不要用任何基于 deadline 的等待（§3.4）。
- **不要在 `bsd_init` 之后写 UFS**（日志探针也不行），会挂死。
- **不要相信面板的行序**，也不要相信 `getMatchingServices` 的计数（§8）。
- **不要改 `board.toml` 里的 `sheng-pmgr`**：`native-ufs-root=1` 的 payload 门禁要求恰好一个
  `sheng-pmgr=2`，改了会直接构建失败（"AMFI otherwise panics with No PMGR"）。
- **不要打开 `liuqin_usb=1`**：USB CDC 调试口初始化会挂在 `bsd_init:usb_start`。
- **不要用 `fastboot flash`** 测试实验镜像；一律 `fastboot boot`。
- 改驱动后如出现"行为完全没变化"，先确认改动真的进了 **tree 副本**
  （`out/liuqin/tree/drivers/ShengUfs/...`），并用
  `strings kernel.macho | grep <你的记号>` 验证。

【合并注】本仓再加四条（都是这一轮真实付过学费的）：

- **改了驱动要在设备仓里更新 `drivers/ShengUfs` 的 gitlink**。`unixnu` 是 checkout 到**记录的 SHA**，
  不更新就会静默编上一个 commit（他的 v56 记录里也写过这条，这里升格为"必查项"）。
- **别信"构建成功"**：§3.7 那条毒常量能顺利链接、能出 `boot.img`、能 `fastboot boot`。
  验收至少要跑一次 `strings kernel.macho | grep -c <新记号>` + 一次 `fastboot boot` 看面板。
- **boot arg 有 1000 B 上限**（本仓实测 961 B）。`board.toml` 里
  `native-ufs-root-partition=unixnu` 目前**在 185 行和 212 行重复了两遍**，白占 32 B ——
  合并后这是本仓需要决定的清理项（未动，因为那是改他的板级描述）。
- **主机重启会杀掉后台构建**（本仓踩过一次，日志和半成品一起没了）：长构建一律
  `setsid nohup` + 日志写在项目目录里，不要写 `/tmp`。

### 9.4 验证清单（每轮改动的自检）

```bash
# 1) 构建成功且 kernel sha 变化
tail -1 out/liuqin/kernel/../liuqin/logs/../  # 见构建日志最后一行
# 2) 关键记号进了内核
strings out/liuqin/kernel/kernel.macho | grep -c "<新增的里程碑文本>"
# 3) 镜像回读校验通过（bootimg 会自己做 head/hash 检查）
# 4) 真机：fastboot boot → 面板读数 → 回 Android 读日志/看 panic
```

【合并注】本仓在第 2 步和第 3 步之间加两条，专防 §3.7 这一类"生成期中毒"：

```bash
# 2b) 生成的绝对符号绑定是否落在合法地址范围（platform-power / ufs-bindings 同理）
sed -n 's/^\.set \([^,]*\), \(0x[0-9a-f]*\)/\1 \2/p' \
  out/liuqin/tree/build/sheng-platform-power/original-bindings.S \
  | awk '{ if (and(strtonum($2), 0xfffff00000000000) != 0xfffff00000000000) print "SUSPECT:", $0 }'
# 2c) 内核链接基址做个参照，超出即值得怀疑
nm -n -arch arm64 out/liuqin/kernel/kernel.macho | awk '$3=="__ZTV18ShengPlatformPower"{print "vtable:", $1}'
```

---

## §10 路线图与可行性

- **T1 根卷挂载**：只差 IOMedia → BSD disk → APFS 挂载。这是当前唯一真正的工作面。
- **T2 用户态**：`launchd` 需要可写 `/private/var`（内存盘或数据窗口）。
- **图形界面**：受 GPU 限制，**不建议作为目标**（见 §0 警告）。

同 SoC 参考实现：[Emilelu/UniXNU-mondrian](https://github.com/Emilelu/UniXNU-mondrian)（红米 K60 / SM8475）。

【合并注】本仓对 T1 的补一条前置：`docs/b1-unixnu-partition-plan.md` 与 `docs/route-b-qprs6.md`
（PR #1 后三个纯文档提交之一）指的路线已在本仓落地（§5 分区 + `native-ufs-root-partition=unixnu`），
所以路线选择这件事**不再是阻塞项**；阻塞项是 §3.7。

---

## §11 仓库结构

```
docs/                        本轮及历次调试记录（含 bringup-2026-11-19-session.md）
patches/
  driver/                    NativeUfs 早期元类修复补丁
  xnu/                       XNU 侧补丁（**需手动应用**，见 §7）
  bringup-2026-11-18/        11-18 轮改动
  bringup-2026-11-19/        11-19 轮改动（含最终驱动 diff：shengufs-v50-publish.diff / v52）
drivers/ShengUfs/            UFS 驱动（子模块 / fork）
scripts/
  build-liuqin-ufs-bindings.py   prelink 符号绑定生成
  install-liuqin-ufs-data.py     iOS 根镜像切分/写入
  wrap-payload-mbr.py            payload 打包
tests/metaclass-simtest/     元类场景主机仿真
board.toml                   板级描述与内核启动参数
```

构建产物不入库，走 Releases（每个镜像约 66 MB）。

【合并注】本仓**没有**采纳"`docs/` 折进单文件 README 并删除"这一步：上面「进度（截至 2026-11-17）」
一节引用了 `docs/boot-rounds-v35-v38.md`、`docs/liuqin-hardware.md`、`docs/liuqin-usb-debug.md`、
`docs/porting-plan.md`、`docs/vendor-inputs.md` 五篇，照删即断链，所以合并提交把它们保留了
（`docs/liuqin-boot-progress.md`、`docs/route-plan-2026-11-15.md` 两篇未被引用，按 PR 原样删除，
内容仍在 `4fc7f56` 可查）。子模块指针 = `26ae865`（内容 v60），与实机测过的一致。

---

## 致谢 / Acknowledgments

- **qwen3.8-flash** — 本移植的自动化工程会话由驱动该模型的 DeepSeek Harness agent 完成：
  Linux 工具链搭建、板级数据核实、ShengUfs SM8475 移植（fork 分支 `liuqin-sm8475`）、
  21H461 storage-bindings 离线推导、userdata Linux 构建、实机里程碑诊断与
  panic 可见化修复等系列迭代。
- UniXNU 官方与各社区机型仓（尤其 sheng 模板与 mondrian 的 SM8475 实测数据）。
- 把 `OSMetaClass::postModLoad` 这一类崩溃讲清楚并给出实机可行形态的社区仓：
  **N1709/UniXNU-whyred**（延迟注册 + 重入闩 + ABI 闸；round 117 的重名 panic 复盘）、
  **tb128fu**（家族确实预链时的 storage 绑定形态）、**qiumochen/UniXNU-zizhan**、
  **document97**（同平板 pong 栈）、**RigoLigoRLC/unixnu-nativeufs**（SM7325 不变量检查）、
  **Emilelu/PongUfs**。
- Apple 开源 xnu（`libkern/c++/OSMetaClass.cpp` 的 stalled 批次语义）。
- **@Fly116** — PR #1 的作者，也是本文件 §0–§11 与 §13 全部内容的作者：v39→v60 构建链、§3 技术档案、
  §5 分区方案、`ShengPlatformPower` 归档、`scripts/install-liuqin-ufs-data.py`，以及他在自己设备上
  完成的分区改造与 v39–v60 实机验证。合并时保留了他的小节编号（他文中大量 `见 §3.5` / `§7` 交叉引用），
  与本仓实测结论不同的判断一律以【合并注】标出，没有替换他的原文。
- **PR #1 原文的致谢（照录，其中"上游主线"就是本仓）**：
  - 上游主线：[hhyygg2009/UniXNU-liuqin](https://github.com/hhyygg2009/UniXNU-liuqin)
  - 同 SoC 参考：Emilelu/UniXNU-mondrian（SM8475）
  - 前人记录：[1696108518/UniXNU-liuqin](https://github.com/1696108518/UniXNU-liuqin)、
    [document97/UniXNU-liuqin](https://github.com/document97/UniXNU-liuqin)
  - UEFI 层：UniXNU / Mu-Silicium；设备与实机验证：**@Fly116**
  - **DeepSeek**：本仓库 2026-11-19 之后的存储链路定位（prelink slide、`IOSleep`、
    `registerService` 链路）与大量调试脚本、文档整理，由 DeepSeek 模型驱动完成
  - UniXNU 官方与各社区机型仓
- 本轮（2026-11-21：§5 分区落地与取证、全量 Fly116 构建复现、§3.7 定位）同样由
  **qwen3.8-flash / DeepSeek Harness** agent 会话完成 —— GPT 编辑器与模拟盘自检、TWRP 取证通路、
  D1/D2 两处构建偏差、面板照片的逐地址符号化。

特别感谢：

- 感谢朋友 **@dfvx** 提供 token
- 感谢喵喵乖乖睡觉
- 感谢自己狠狠鞭策 AI
- 感谢小米平板
- 感谢女朋友大力支持

### AI 用量统计（截至 2026-11-17，含本轮会话进行中）

现在由 `scripts/token-stats.py` 复现，任何人跑一次就能对账：

    python3 scripts/token-stats.py

| 指标 | 数值 |
|---|---|
| 会话目录 | 91（13 个 subagent 派生，78 个顶层） |
| assistant 回合 | 8,929 |
| 计费 usage 记录 | 8,382 |
| 非缓存输入 | 22,734,889 tokens |
| 缓存读取 | 1,301,055,533 tokens |
| 输出 | 3,818,512 tokens |
| **合计** | **1,327,608,934 tokens（约 13.3 亿）** |
| 缓存占比 | 97% |

统计口径：本机两个工作区目录全部会话日志里，harness 记在每个 `assistant/message`
与 `compaction/summary` 上的 provider usage，按 `(会话, seq, turn, step)` 去重后求和；
`inputTokens` 已是非缓存输入，`totalTokens = 输入 + 缓存读取 + 输出`。绝大部分是本移植，
含少量同工作区的群聊问答回合。缓存占 97%，是长会话上下文复用的结果。

> 上一版（截至 2026-11-16：合计 1,688,243,120 / 10,676 回合）用的是另一套更宽的扫描口径，
> 无法用本脚本复现。从本次起以 `scripts/token-stats.py` 为准。

## §13 声明（PR #1，@Fly116 原文）

- 本项目与 Apple、小米、高通均无关联；
- 所有实验均可能使设备无法启动，**请自行承担风险**，并提前完整备份；
- 仓库内历史性的分区改造步骤**不可一键回滚**，请勿在主力设备上照做；
- 代码与文档按现状提供。

【合并注】本仓补一条实操提醒：§5 的改造在本仓设备上**确实做过**，能回头靠的是改造前抓到的
主 / 备 GPT **原始字节**（`ROLLBACK-gpt-primary.bin` 12,288 B、`ROLLBACK-gpt-backup.bin` 20,480 B，
加 `ROLLBACK.txt` 里的 dd 复原命令），不是任何自动化工具；`/data` 会被 `fastboot erase userdata metadata`
清空，`recovery_b` 还要另外刷回 stock（见 §5 合并注第 2 条）。

---

## 合并说明（provenance）

- **PR #1**：[hhyygg2009/UniXNU-liuqin#1](https://github.com/hhyygg2009/UniXNU-liuqin/pull/1)，
  head `4fc7f56`（75 commits，44 files），合并提交 `9590685`（parent1 = 本仓 `cbbb7b1`），
  2026-11-21 在 GitHub 上记为 MERGED；子模块指针 `drivers/ShengUfs = 26ae865`（内容 v60）。
- **他的 README 原文** 362 行 / 18,365 B 逐字可取：`git show 4fc7f56:README.md`。
  本文件的 §0–§13 即其内容（去掉重复的一级标题；§12 致谢并入本仓致谢一节；编号原样保留以便交叉引用），外加【合并注】。
- **本仓原版 README** 157 行 / 10,228 B 逐字可取：`git show cbbb7b1:README.md`。
  校验方式：`git diff --numstat cbbb7b1 HEAD -- README.md` 的**删除列恒为 0**（纯追加）。
