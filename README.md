# UniXNU-liuqin（Xiaomi Pad 6 Pro 上的 XNU）

Xiaomi Pad 6 Pro / liuqin / SM8475 的 UniXNU 设备仓库，由 UniXNU-sheng 派生。

状态：**内核、UEFI 固件、payload、boot.img 都能在 Linux 主机上构建**（2026-11-13 验证）；
实机 `fastboot boot` 已反复验证（v33→v38，纯内存运行，不写分区）。
**2026-11-17（v38）：内核第一次走完 IOKit 引导进入 BSD，一路到达挂载决策**，阻塞点被钉死到
具体对象、具体字段（见「进度」一节）。**2026-11-18（v39）：修好那个字段的代码已经构建完成**；**同日（v40）：回答"谁写的"的代码
也已经构建完成**（`releases/boot-liuqin-v40-fingerprint.img`，69,525,504 B）。这两轮都
**没有上机**（平板不在手边），结论全部来自主机侧：生产构建、进像反汇编审计、仿真回归。
板级关键数值已经从 stock 固件里核实完了，board.toml 里每个 TODO 都有明确的取值方法和当前状态。

    board.toml                 板级描述（userdata LBA、splash、FD 窗口已填实测值）
    porting.toml               设备适配记录（schema_version = 1，14 个部分，逐条带证据）
    AGENTS.md                  agent 必读入口：职责、依赖、构建入口、记录链接、硬性约束
    configs/liuqin_defconfig   内核配置（从 sheng 派生，PMGR/Adreno/UUID 标了 TODO）
    docs/liuqin-hardware.md    硬件底账：已核实 / 仍未知，每条带来源
    docs/vendor-inputs.md      stock UEFI 抽出的 154 个厂商输入 + uefiplat.cfg 硬数据
    docs/porting-plan.md       四阶段计划、通过标准、当前阻塞、立刻可做的事
    docs/liuqin-usb-debug.md   内核 USB CDC-ACM 调试口（LIQDBG001）：开关、四道自检门、移植对照表
    docs/boot-rounds-v35-v40.md  逐轮判读：改了什么、照片看到什么、哪些假设被证伪
    scripts/audit-h16f-image.py  一条命令证明 H16-F/G 的代码真在 kernel.macho 里（42 项检查）
    drivers/ShengUfs/          SM8475 UFS/SMMU/根存储驱动 fork（分支 liuqin-sm8475），
                               tests/check_legacy_descriptors.py 是上游 descriptor oracle
    patches/xnu/               基础补丁 + zzz-liuqin-usb-debug*.patch（USB 调试口、bsd_init 里程碑）
                               + zzzzzzzzzz-liuqin-metaclass-stalled-diagnostics.patch（H16 系列，见「进度」）
    scripts/                   板级构建/打包/补丁脚本；token-stats.py 复现「AI 用量统计」
    tests/liuqin_usb_host_test.c  调试口逻辑的主机侧测试（gcc，不需要内核）
    tests/metaclass-simtest/   postModLoad 扫描/修复/指纹的主机侧仿真（./run.sh，场景 A–F）
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

## 进度（截至 2026-11-18，v40）

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
| v39 | **修那个字段**：libkern 侧钩子 + 驱动交出链接期超类地址；策略不再因"名字被占"而拒绝；`M:mc pre/pub` 见证 | 未上机（主机侧 12 项审计 + A–E 仿真全通过） |
| v40 | **给毒值按指纹**：第二个钩子在修复之前把毒值渲染成字符，并回搜 1 MiB 控制台镜像，报 `hits/self/lost/at/after` + 命中处上下文；证据行由 `witness("pub")` 重放到面板 | 未上机（主机侧 42 项审计 + A–F 仿真全通过；仿真逼出 4 个真缺陷并全部修掉） |

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
   （v39 起 `I60:base-dup` 这条里程碑与 `liuqinBaseClassFree()` 一起删除：照片上还能看到它，
   说明刷的是旧镜像。）

剩下的两个阻塞点：① 谁把 8 字节控制台文本写进 `NativeUfsBlock::gMetaClass + 0x10`（`superClassLink`）——
驱动自己的 `native_ufs_*` 静态量已排除，它们与该对象在 `__common` 里首尾精确相接且全是 uint32，
而 libkern 里唯一写该字段的语句是 `OSMetaClass` 构造函数（`superClassLink = inSuperClass;`）；
② 根挂载要求我们的类真正登记成功（v38 把它从批次里丢了，`isMemberOf()` 走同一字段仍会炸；
**v39 已把这条解决到"登记而不是丢弃"，只差实机确认**）。

**v39 做了什么（H16-F，已构建，等平板）**：

- libkern 侧新增钩子全局 `liuqin_metaclass_super_fix(name)`：扫描撞到坏链时先问它要正确的超类，
  **验过再写** `[obj+0x10]` 并重走一遍链，成功就照常登记这个类（`M:bad[i] fixed`）。钩子没装、
  或答案不合格 → 逐字节退回 v38 行为。
- 驱动在自己的**元类构造函数**里装钩子（`OSDefineMetaClassAndStructorsWithInit` 的 init 参数），
  填进去的是 `&IOBlockStorageDevice::gMetaClass` 的**链接期地址**——adrp/adr 直接算出的常数，
  不读任何可能被涂写的内存。这批构造函数正是往 `sStalled` 里塞东西的那批，所以钩子必然早于扫描就位。
- 策略从"名字被占就拒绝"改成"**占名字的对象是不是我们编译时那个对象**"：
  `U:base-ok`（是家族、size 也对）/ `U:base-abi`（size 不一致：打印两个数字，仍然放行）/
  `U:base-alien`（另一个对象占着 → 仍拒绝）/ `U:base-free`（闭包里没家族）。
- `M:mc pre`（`startRoot` 入口）与 `M:mc pub`（窗口全部备齐）各读一次该字段，坏则当场修并打
  `was=`/`now=`。两行都健康 = 涂写只发生在扫描前；`pub` 又坏 = **涂写者仍在活动**，
  那时才上 whyred 的延迟注册形态（手写 MetaClass + 显式 pre/post + 重入闩）。
- 扫描的最后一行固定为 `M:scan done bad=%u healed=%u cnt=%u deep=%u`（v37 的教训：结论必须在最后一行）。

**v40 做了什么（H16-G，已构建，等平板；它回答"谁写的"，不再猜）**：

- libkern 侧第二个钩子 `liuqin_metaclass_poison_report(tag, obj, bad)`：扫描撞到坏链时**在动手
  修复之前**叫它一次，整个启动最多 2 次（面板只留最后几行，这里刷量是净损失）。钩子是 NULL
  （别的板子）→ 输出与 v39 逐行相同。
- 驱动侧把毒值当文本处理：`liuqinEightText` 按内存顺序渲染 8 个字节（换行→`|`、不可打印→`.`、
  引号与反斜杠→`'`，所以它不会打断自己所在那行的引号）；`liuqinRingFind` 在 1 MiB 控制台镜像里
  搜这 8 个字节——每块 256 B 经 `liuqin_debug_copy` 拷进栈缓冲，**一次都不解引用内核给的指针**，
  跨界命中靠回退 7 字节重扫，轮数封顶。
- 面板上因此多出三行：`M:fp <相位> p=… bad=%016llx asc="…"`、
  `M:fp <相位> ring hits=… self=… lost=… at=… after=… total=…`、`M:fp <相位> ctx|…|`。
  `at`/`after` 说明这行文本是**在扫描之前多久**打印的，`ctx` 直接把它所在的那行端出来。
- 三个计数把"没找到"和"不可能找到"分开：`self=`（命中的是我们自己打印的行）、
  `lost=`（镜像已绕回、那段字节不在了）、verdict 词 `clean`/`lost`/`gaveup`（锁被占，一次就退）。
- 证据行存一份，`witness("pub")`（本次启动最后一个见证）重放它——v37 的 `M:stalled post`
  就是因为不是最后一行而从照片上消失的。

仿真在这一轮不是"回归检查"，而是**发现缺陷的地方**（`tests/metaclass-simtest`，场景 F1–F7）：
先打印后搜索会让纯可打印的 needle 命中自己那行（还带一个真偏移，比不命中更坏）；第二次报告会
把第一次的自己当成生产者；镜像绕回之后偏移必须取自 `lq_copy` 真正开始的位置，否则报出的是已被
覆盖的地址，还会白烧轮数预算；面板会滚动，所以证据要重放。四条都是先由仿真暴露、再改生产代码。

判读表（哪种照片 → 哪个下一步）与刷机命令在 `releases/v40-NOTES.md`（v39 的判读表仍然有效：
`M:bad[170] fixed` 那几格没变）；设备一出现也可以直接 `bash releases/watch-v40.sh` 让它自动
`fastboot boot`（纯内存，不写分区）。

诊断手段：面板里程碑控制台（`K`/`B`/`U`/`I`/`M:` 前缀）+ 1 MiB 环形缓冲 + USB 调试口。
逐轮改动、照片判读与被证伪的假设都固化在 **[`docs/boot-rounds-v35-v40.md`](docs/boot-rounds-v35-v40.md)**；
主机侧回归 `tests/metaclass-simtest/run.sh`（场景 A/B/C/D + E1–E4 + **v40 新增的 F1–F7：生产者命中
且 `at/after/total` 逐字节正确、无自我命中、绕回报 `lost`、绕回后偏移仍精确、锁被占即退、每次启动
2 次封顶、无钩子板子与 v39 逐行相同、面板滚动后证据仍在**；五个 `.inc` 由 `extract.py` 从驱动源码与
两个 XNU 补丁逐字节抽取，漂移即失败）；进像证明一条命令 `python3 scripts/audit-h16f-image.py`（42 项，
每个函数按符号边界取反汇编）；上游 descriptor oracle
`drivers/ShengUfs/tests/check_legacy_descriptors.py`（14/14）。
镜像本身放在构建机的 `releases/`（不入库，`fastboot boot` 用）。

## 收录与发布

按 [发布与复用](https://unixnu.com/zh/porting/records#publishing-and-reuse) 的要求：

- GitHub topic 已加 `unixnu`（目录检索范围）与 `liuqin`。
- 设备记录 `porting.toml`（`schema_version = 1`，13 个必需部分 + `cellular` 不适用）已入库，
  本地检查通过：

      python3 ../UniXNU/scripts/porting-record.py check --device ../UniXNU-liuqin
      # liuqin: 14 structured areas checked

- `.github/workflows/porting-record.yml` 调用官方可复用工作流，PR/push 上跑同一检查。
- 为了让记录的身份与组件覆盖成立，`board.toml` 补了 `[board] os_profile` 和
  `[components] include`。这两项在 **schema_version = 1 下构建根本不读**，已实测证明：
  补完之后 dry-run 重构建产出**逐字节相同**的 `kernel.macho` sha256 `49247f07…`。

还差的（需要一次真正的迁移，不是文档活）：`board.toml` 仍是 **schema 1**，而目录的 format 2
要求 `schema_version = 2` + profile/组件选择模型；官方 `devices.toml` 里 liuqin 目前登记为
`format = 1`、`legacy_page = "preserve"`、钉在旧提交 `907b8e91`，摘要还停在"UEFI next"。
迁移完成后由 `sync-devices` 工作流（UTC 00:00 / 12:00，即 UTC+8 08:00 / 20:00）重新扫描生成页面，
也可手动触发：

    gh workflow run sync-devices.yml --repo UniXNU/UniXNU

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
