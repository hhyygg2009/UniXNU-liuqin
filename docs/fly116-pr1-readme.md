# PR #1 的 README 合并内容（@Fly116，`4fc7f56`）—— §0 至 §13

> 从 `README.md` 拆出来单独保存：本仓 README 只留状态、入口、依赖、进度时间线与规范摘要，
> Fly116 的原文逐字保留在这里，编号不动，交叉引用照旧。
> 原文可逐字取回：`git show 4fc7f56:README.md`（362 行 / 18,365 B）；
> 本仓原版 README：`git show cbbb7b1:README.md`（157 行 / 10,228 B）。

> 他的 §12 致谢不在此重复，已照录进后面的「致谢 / Acknowledgments」一节；
> 本节内部凡是“见致谢”都指那里。§0 与 §13 分别是他原文顶部的警告块与文末声明。
---

**为什么原文整块保留在这里而不是"替换"本仓 README**：本仓原版 README 一字未删（拆前置
`git diff --numstat cbbb7b1 HEAD -- README.md`，删除列恒为 0，纯追加）。2026-11-21 把这块 600 行
的合并内容移到本文件，README 只留状态、入口、依赖、时间线与规范摘要 —— **内容没有删，只是换了位置**；
交叉引用（"见 §3.7"、"见致谢"）在本文件内部照旧成立，从 README 引用时写成
`docs/fly116-pr1-readme.md` 的对应小节。

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
跟符号表对（工具链里的 cctools `nm -n -arch arm64`；Linux 上 `llvm-nm` 同样能读 Mach-O，
binutils 的 `nm` 不认 `-arch`）（本仓 51,273 个符号，存成
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
# 2b) 生成的绝对符号绑定是不是合法 VA（platform-power / ufs-bindings 同理）。
#     内核地址高 16 位恒为 0xffff；被当成指针的指令字一定不是（§3.7 的实测输出如下）。
#     注意别用 awk 的 and()/strtonum()：Debian 默认 mawk 没有这两个函数。
python3 - out/liuqin/tree/build/sheng-platform-power/original-bindings.S <<'PY'
import re, sys
for line in open(sys.argv[1]):
    m = re.match(r'\.set (\S+), (0x[0-9a-f]+)', line)
    if m and int(m.group(2), 16) >> 48 != 0xFFFF:
        print('SUSPECT:', m.group(1), m.group(2))
PY
#   SUSPECT: __ZN9ApplePMGR7readAonEv 0xd63f0100aa1503e0
#   SUSPECT: __ZN9ApplePMGR8writeAonEy 0xf940f508f9400008

# 2c) 内核链接基址 / 具体对象地址做参照（Linux 上用 llvm-nm，binutils 的 nm 不认 -arch）
llvm-nm out/liuqin/kernel/kernel.macho | grep __ZTV18ShengPlatformPower
#   fffffff0072091d0 S __ZTV18ShengPlatformPower   ← 本仓 v60 实测
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
