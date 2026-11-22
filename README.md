# UniXNU-liuqin（Xiaomi Pad 6 Pro 上的 XNU）

Xiaomi Pad 6 Pro / liuqin / SM8475 的 UniXNU 设备仓库，由 UniXNU-sheng 派生。
本仓拥有板级描述、设备记录、DTS、defconfig、XNU 补丁序列、板级驱动与打包脚本；
XNU 源码、构建驱动、固件、工具链在外部 —— 见「依赖」。
**硬约束（踩过才知道的那些）在 [`AGENTS.md`](AGENTS.md)，逐轮事实与缺口在 [`porting.toml`](porting.toml)。**

## 两条技术栈（先分清）

| | 旧栈：ShengUfs 路线（本仓主线） | pong 栈（Fly116 线，2026-11-22 并入档案） |
|---|---|---|
| iOS | iPadOS 17.7.11 / 21H461 | iPadOS 18.7.10 / 22H374（Darwin 24.6.0） |
| 存储驱动 | ShengUfs（本机已验证可读写 UFS） | NativeUfs（TUR 无完成中断，当前卡点） |
| 构建 | Linux 主机 | GitHub Actions `macos-15-intel` |
| 最远 | launchd 被 SIGKILL（见下） | `Failed to mount root device @IOKitBSDInit.cpp:996` |

两条线在同一台设备上并行；旧栈的 ShengUfs 能完成 UFS 命令，是 pong 栈 UFS 问题最有价值的对照物。

## 当前状态（2026-11-21，v67）

- 内核 / 固件 / payload / boot.img 全部可在 Linux 主机构建；实机**只用 `fastboot boot`，不写任何分区**。
- 已打通：IOKit 引导 → BSD → 根分区解析；块栈实机注册到 `IOMedia=7 IOMediaBSDClient=7`。
- v66 的墙 `copyio.c:94 Assertion failed: __builtin_arm_rsr("pan") != 0` —— DEVELOPMENT 构建默认开着
  全任务微堆栈采样，采样路径在不该开用户访问时开了它。**v67 用一个 boot-arg 摘掉了**
  （分支 `v67-telemetry-gate`，`telemetry_sample_all_tasks=0`，内核字节与 v66 相同 ⇒ 单变量）。
- **现在的墙**：`unexpected SIGKILL of launchd … namespace 9 code 0x8`；可信链
  `_load_init_program → __mac_execve → _process_signature → _psignal_internal`。
  `namespace 9 / code 0x8` **语义未查清，不许当结论用**。根挂载与 userland 仍未达成。
- 分区改造（`unixnu` 根分区）已落地并逐字节校验。boot 前先确认设备在不在：v66 断言后当场回
  fastboot，v67 中间十几分钟从主机 USB 完全消失，21:5x 才回到 `19562650 fastboot`。

**pong 栈现状（2026-11-22，v114）**：引导链已打通到「等待根设备」——固件 → 菜单 → DarwinBoot →
prelinked XNU → IOKit → APFS kext → framebuffer 全部起来；唯一阻塞是 NativeUfs 的 TUR 探测拿不到
完成中断（`ocs=0f`、`db=1`、`polls=1589`）。v112–v124 的诊断把根因收敛到**固件交接时 UFS 已掉电、
QMP PHY 不响应写**：[`docs/ufs-investigation-2026-11-22.md`](docs/ufs-investigation-2026-11-22.md)、
[`docs/ufs-tur-analysis-2026-11-22.md`](docs/ufs-tur-analysis-2026-11-22.md)。
现成镜像与 Release 见 [Fly116/UniXNU-liuqin](https://github.com/Fly116/UniXNU-liuqin)（tag `v114-2026-11-22`）。

逐轮细节：[`docs/boot-rounds-v35-v44.md`](docs/boot-rounds-v35-v44.md)、
[`docs/bringup-2026-11-21-v66-round.md`](docs/bringup-2026-11-21-v66-round.md)、
[`docs/bringup-2026-11-21-local-merge.md`](docs/bringup-2026-11-21-local-merge.md)、
`docs/bringup-2026-11-21-v67-telemetry-gate.md`（在 `v67-telemetry-gate` 分支）。

## 快速开始

```bash
cd ../UniXNU
export UNIXNU_DEVICE=$PWD/../UniXNU-liuqin UNIXNU_UEFI=$PWD/../uefi-liuqin \
       UNIXNU_EDK2=$PWD/../uefi-liuqin/Mu_Basecore UNIXNU_TOOLCHAIN=$PWD/../toolchain
./unixnu defconfig liuqin_boot_defconfig          # 声明==选定==.config，守卫靠它
./unixnu build kernel && ./unixnu build firmware
./unixnu build payload --kernel out/liuqin/kernel/kernel.macho --unique-chip-id 0x84750A0B0C0D0001
python3 ../UniXNU-liuqin/scripts/wrap-payload-mbr.py \
    out/liuqin/payload/payload.img out/liuqin/payload/payload-mbr.img
./unixnu bootimg --fd ../uefi-liuqin/Build/liuqinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd \
    --dtb ../uefi-liuqin/Resources/DTBs/liuqin.dtb --ramdisk out/liuqin/payload/payload-mbr.img

bash ../UniXNU-liuqin/scripts/capture-serial.sh ./out-round 200 140 &   # 量调试口注不注册
fastboot -s <serial> boot build/boot-liuqin-fly116-vNN.img
```

- **对账尺寸 69,525,504 B**；打出 68,476,928 B 就是漏了 `wrap-payload-mbr.py`。
- **cmdline 必须 < 1000 B**（v66 980、v67 976）；加参数就要打算换掉参数。

主机侧回归（不上机也能推进一整轮）：

```bash
python3 scripts/check-defconfig.py                                # 声明 / 选定 / .config 一致
./unixnu build kernel && python3 scripts/audit-h16f-image.py      # 进像证明：该在的在、该没的没
python3 scripts/check-trust-cache-image.py                        # trust-cache =y 分支守卫
tests/metaclass-simtest/run.sh                                    # ALL SIM ASSERTIONS PASS（A–G）
python3 ../UniXNU/scripts/porting-record.py check --device <本仓> [--base origin/main]
```

pong 栈的组装与 CI 脚本在 `docs/`（`make-v112.sh`、`ci-fetch-kernel.sh` 等）——**依赖 Fly116 的私有
CI 仓与构建沙箱路径，本仓只存档案，不可直接复现**。

## 目录与依赖

| 路径 | 内容 |
|---|---|
| `board.toml` / `porting.toml` / `AGENTS.md` | 板级描述 / **设备记录**（14 区域）/ 硬约束与自检 |
| `configs/` `dts/` `patches/xnu/` `package/` `scripts/` `tests/` `docs/` | defconfig、厂商 DTB 的 dtc 视图、补丁序列（`attic/` 不入序列）、板级 package、构建与守卫脚本、主机侧仿真与测试、判读档案 |
| `drivers/ShengUfs/` | UFS/SMMU/根存储驱动子模块（fork 分支 `liuqin-sm8475-v56`，gitlink `26ae865e…`） |
| `proprietary-files.txt` | 专有输入的**来源与哈希**；文件本身在工作树 `.cache/firmware/`，**不入库** |
| `../UniXNU` | 构建驱动（XNU 源码 pin `d8b80295…`，见 `unixnu.lock`） |
| `../uefi-liuqin` | Mu-Silicium 固件 + FD + `Resources/DTBs/liuqin.dtb`（sha256 `ce90f88b…`） |
| `../toolchain` | Linux 交叉工具链（`scripts/linux/setup-toolchain.py` 装） |
| iPad7,1 21H461 IPSW | `[ipsw]` url + 成员 sha256 固定 |

同级参考（非依赖）：`../UniXNU-sheng`（模板）、`../liuqin-firmware`、`../liuqin-nixos`、`../xiaomipad-6pro-mainline`。

## 进度时间线（一行一轮）

| 轮 | 改动 → 结果 |
|---|---|
| v35–v38 | panic 上屏 → gate 假阳性 → 数组干净 → 逐槽命名**跳过坏槽**：崩溃消失，到 `Failed to mount root device` |
| v39–v42 | 延迟注册 / 对象身份判决 / 绑定按段映射：**H16 结案**，第一次过挂载到 `B19:at_mountroot` |
| v43–v44 | 剪掉已结案诊断；撤 `cs_enforcement_disable`（AMFI 在自己 `start()` 里就 panic） |
| v45–v60 | trust-cache 候选；全量切 Fly116 树 + `unixnu` 根分区 → 新墙：platform-power AON 绑定**漏 slide** |
| v61–v65 | 修绑定；v62 是空转轮（`export` 不进 env dict）；里程碑叠加层可关，面板不再重影 |
| v66 | 内核确实重编（看 `out/liuqin/logs`，别看 VERSION 串）→ 新墙：`copyio.c:94` PAN 断言 |
| v67 | 关掉全任务采样 → 断言消失；新墙：launchd 被 SIGKILL（`namespace 9 code 0x8`，未解释） |
| pong v106–v124 | （Fly116 线）prelink slide / ADT 容器逐项排除 → 上游源码重编过 VM bootstrap → AppleARMCPU 校验二进制绕过 → 到 root device；UFS PHY 上电未解，档案在 `docs/` |

## 规范符合性与发布

`porting-record.py check` → `liuqin: 14 structured areas checked`；路径归属自检 → **0 个无归属**；
`Device documentation` 在 main 与各分支**全绿**；`repositoryTopics = unixnu, liuqin`，仓库公开。

| 项 | 状态 |
|---|---|
| 记录（14 区域、证据可公开解析） | ✅ |
| 布局文件、lock、proprietary-files、README/AGENTS | ✅ |
| `board.toml` 翻 `schema_version = 2` | 🟡 未翻 ⇒ 目录按**格式 1** 收录，[机型页](https://unixnu.com/zh/devices/models/liuqin) 只有"仓库链接" |
| `package.yml` 打包 CI | 🔴 卡在构建器上 3 个未上游化的提交（文件顶部列成 BLOCKED） |
| 通用方法回写公共指南 | 🟡 补丁已写好并 `git apply --check` 过，待发上游 PR |

上游 `Sync device documentation` 目前自己也是红的（第三方记录缺 `evidence` 键），所以收录刷新要等上游修；
本仓侧已按上游同一条 `tools.validate(...)` 路径核过：记录**通过**，board 一翻格式 2 就立即 `available`。

## 待办（按代价排序）

1. **解释 launchd 的 SIGKILL**：在 XNU 源码定位 reason 的 namespace/code 枚举与 `_psignal_internal`
   调用者，再按"翻一个开关 = 造一对互斥证据"逐个试 `amfi_get_out_of_my_way` /
   `liuqin_allow_unsealed_root=1` / `native-ufs-unauth-root=1` / `-apfs_shared_datavolume`。
2. **翻格式 2**：① 4 处同名不同内容且行区间真重叠的补丁重基；② 完整 `unixnu fetch` 让 lock 记下 SoC
   载体；③ 干净检出重建 + 上一轮机；④ 才改 `schema_version = 2`。载体已不阻塞
   （`document97/UniXNU-soc-seed` rc=0 / 73 条，`a77710178a/UniXNU-sm8475-common` rc=0 / 75 条但多一处
   撞车），设备树等价性也已用 dtc 证过（与厂商 DTB diff 0 行）。账见 `soccarrier-trial`、`schema2`。
3. **构建器 3 条提交上游化**（或推到 `hhyygg2009/UniXNU` 填实际 ref），否则打包 CI 永远红。
4. **驱动私有线是否公开**：`433e7fdd…`/`8abb519…`/`91c6fcc…`/`c36c575…` 与
   `liuqinBaseClassFree`/`liuqin_block_super_fix`/`liuqinMetaclass*` 三个公开 ShengUfs 都没有，
   H16 那段历史目前只能引 `docs/boot-rounds-v35-v44.md`。
5. **`.cache/firmware` 的历史对象**：已移出索引并忽略，公开历史里还在；是否重写由维护者决定。

## 来源与指南

Fly116 PR #1 的 README 原文（§0–§13，含致谢与 AI 用量统计）**逐字保留**在
[`docs/fly116-pr1-readme.md`](docs/fly116-pr1-readme.md)；原文可取 `git show 4fc7f56:README.md`，
本仓原版可取 `git show cbbb7b1:README.md`。pong 栈的 bring-up 档案（v106–v124、A2 runbook、
菜单修复）在 `docs/`；其中依赖私有 CI 的脚本已标注，复现以 [Fly116/UniXNU-liuqin](https://github.com/Fly116/UniXNU-liuqin) 为准。
通用指南：[移植步骤](https://unixnu.com/zh/porting/steps)、
[设备仓库](https://unixnu.com/zh/porting/device-repository)、
[适配记录与发布复用](https://unixnu.com/zh/porting/records)、
[组件目录](https://unixnu.com/zh/reference/components)、
[驱动适配](https://unixnu.com/zh/porting/drivers)、
[构建与启动](https://unixnu.com/zh/building)、
[日志抓取](https://unixnu.com/zh/porting/log-capture)。

Released under the BSD-2-Clause-Patent License. 上游：[UniXNU](https://github.com/UniXNU/UniXNU)。
