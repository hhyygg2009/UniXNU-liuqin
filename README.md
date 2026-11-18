# UniXNU-liuqin（Xiaomi Pad 6 Pro 上的 XNU）

Xiaomi Pad 6 Pro / liuqin / SM8475 的 UniXNU 设备仓库，由 UniXNU-sheng 派生。

状态：**内核、UEFI 固件、payload、boot.img 都能在 Linux 主机上构建**（2026-11-13 验证）；
实机 `fastboot boot` 已反复验证（v33→v38，纯内存运行，不写分区）。
**2026-11-17（v38）：内核第一次走完 IOKit 引导进入 BSD，一路到达挂载决策**；
剩下的阻塞点已经在实机上钉死到具体对象、具体字段（见「进度」一节）。
板级关键数值已经从 stock 固件里核实完了，board.toml 里每个 TODO 都有明确的取值方法和当前状态。

    board.toml                 板级描述（userdata LBA、splash、FD 窗口已填实测值）
    porting.toml               设备适配记录（schema_version = 1，14 个部分，逐条带证据）
    AGENTS.md                  agent 必读入口：职责、依赖、构建入口、记录链接、硬性约束
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
