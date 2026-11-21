# UniXNU-liuqin（Xiaomi Pad 6 Pro 上的 XNU）

Xiaomi Pad 6 Pro / liuqin / SM8475 的 UniXNU 设备仓库，由 UniXNU-sheng 派生。

**本仓拥有**：板级描述与设备记录（`board.toml`、`porting.toml`）、板级 DTS、defconfig、
XNU 补丁序列、板级驱动与构建/打包脚本、实机判读档案（`docs/`）。
XNU 源码、UniXNU 构建驱动、Mu-Silicium 固件、工具链都不在本仓 —— 见「依赖」。
**必读的硬约束在 [`AGENTS.md`](AGENTS.md)**（踩过才知道的那些），本文只留状态与入口。

## 当前状态（2026-11-21，v67）

- 内核 / UEFI 固件 / payload / boot.img 都能在 Linux 主机构建；实机**只用 `fastboot boot`，
  不写任何分区**（AGENTS 第 1 条）。断言/panic 之后设备会自己回到 fastboot（v66 当场回来；v67 中间有
  十几分钟从主机 USB 完全消失，21:5x 又回到 `19562650 fastboot`）—— **boot 前先确认在不在**，别假设立刻回来。
- 已打通：IOKit 引导 → BSD → 根分区解析；块栈在实机注册到
  `MEDIA start … IOMedia=7 IOMediaBSDClient=7`、`V58 sync st=0x1e dev=2 drv=2 med=3 bsd=3`。
- **v66 的墙**：`copyio.c:94 Assertion failed: __builtin_arm_rsr("pan") != 0` —— 根因是
  DEVELOPMENT 构建默认开着全任务微堆栈采样，采样路径在不该开用户访问的时候开了它
  （源码判读见 `docs/bringup-2026-11-21-v66-round.md`）。
- **v67 已把那堵墙摘掉**：分支 `v67-telemetry-gate`（`e0bf4a0`）只加一个 boot-arg
  `telemetry_sample_all_tasks=0`，内核字节与 v66 完全相同 ⇒ 单变量。断言整屏消失。
- **现在的墙**：launchd 被 exec 之后收到 SIGKILL —— 面板
  `unexpected SIGKILL of launchd with reason — namespace 9 code 0x8 description none`。
  可信链 `_load_init_program → __mac_execve → _process_signature → _psignal_internal`；
  `namespace 9 / code 0x8` 的语义**还没查清，不许当结论用**。
- 根挂载与 userland 仍未达成；分区改造（`unixnu` 根分区）已落地并逐字节校验过。

逐轮细节：`docs/boot-rounds-v35-v44.md`（v35→v44）、`docs/bringup-2026-11-19-session.md`、
`docs/bringup-2026-11-21-v66-round.md`、`docs/bringup-2026-11-21-v67-telemetry-gate.md`（在
`v67-telemetry-gate` 分支）。**权威结构化记录是 [`porting.toml`](porting.toml)**（14 个区域）。

## 快速开始

```bash
cd ../UniXNU
export UNIXNU_DEVICE=$PWD/../UniXNU-liuqin UNIXNU_UEFI=$PWD/../uefi-liuqin \
       UNIXNU_EDK2=$PWD/../uefi-liuqin/Mu_Basecore UNIXNU_TOOLCHAIN=$PWD/../toolchain

./unixnu defconfig liuqin_boot_defconfig                      # 声明==选定==.config，守卫靠它
./unixnu build kernel                                         # out/liuqin/kernel/kernel.macho
./unixnu build firmware                                       # …/FV/SILICIUM_UEFI.fd
./unixnu build payload --kernel out/liuqin/kernel/kernel.macho --unique-chip-id 0x84750A0B0C0D0001
python3 ../UniXNU-liuqin/scripts/wrap-payload-mbr.py \
    out/liuqin/payload/payload.img out/liuqin/payload/payload-mbr.img
./unixnu bootimg --fd ../uefi-liuqin/Build/liuqinPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd \
    --dtb ../uefi-liuqin/Resources/DTBs/liuqin.dtb --ramdisk out/liuqin/payload/payload-mbr.img
```

上机（先起串口捕获，再 boot）：

```bash
bash scripts/capture-serial.sh ./out-round 200 140 &        # 量调试口注不注册
fastboot -s <serial> boot build/boot-liuqin-fly116-vNN.img
```

- **对账尺寸 69,525,504 B**。打出 68,476,928 B 就是漏了 `wrap-payload-mbr.py`，
  PartitionDxe 认不出 ESP，面板停在 payload 打不开。
- **启动参数必须 < 1000 字节**（v66 是 980 B，v67 是 976 B）；加参数就要打算换掉参数。
- 主机侧回归（不用上机也能推进一整轮）：

```bash
python3 scripts/check-defconfig.py                                 # 声明 / 选定 / .config 三者一致
./unixnu build kernel && python3 scripts/audit-h16f-image.py       # 进像证明（该在的在、该没的没）
python3 scripts/check-trust-cache-image.py                         # trust-cache =y 分支守卫
tests/metaclass-simtest/run.sh                                     # ALL SIM ASSERTIONS PASS（A–G）
python3 drivers/ShengUfs/tests/check_legacy_descriptors.py --output <dir> --compiler g++
python3 ../UniXNU/scripts/porting-record.py check --device <本仓> [--base origin/main]
```

## 文件地图

| 路径 | 内容 |
|---|---|
| `board.toml` | 板级描述：userdata LBA、splash、FD 窗口、archives/roots/payload/bootimg 全实测值 |
| `porting.toml` | **设备记录**（`schema_version = 1`，14 区域：输入/配置/步骤/经验/完成条件/范围/下一步/证据） |
| `AGENTS.md` | 硬性约束与自检命令（改之前先读） |
| `configs/` | `liuqin_defconfig`、`liuqin_boot_defconfig`、`liuqin_usbdebug_defconfig` |
| `dts/liuqin.dts` | 厂商 DTB 的 dtc 反编译视图（来源与"非逐字节等价"写在头注释） |
| `drivers/ShengUfs/` | UFS/SMMU/根存储驱动子模块（fork `liuqin-sm8475-v56`，gitlink `26ae865e…`） |
| `patches/xnu/` | 板级 XNU 补丁序列（`series` 按重放顺序）+ `attic/`（不入库序列的历史补丁） |
| `scripts/` | 构建/绑定/打包/判读/守卫脚本（`read-panel.py`、`capture-serial.sh`、`check-*.py`） |
| `tests/` | 主机侧仿真 `metaclass-simtest/`、USB 调试口逻辑测试 |
| `docs/` | 硬件底账、厂商输入、逐轮判读、分区方案、trust-cache 方案、Fly116 原文 |
| `proprietary-files.txt` | 专有输入的**来源与哈希**（文件本身放工作树 `.cache/firmware`，不入库） |

## 依赖

| 依赖 | 位置 / 固定版本 |
|---|---|
| UniXNU 构建驱动 | `../UniXNU`（XNU 源码 pin `d8b80295…`，见 `unixnu.lock`） |
| Mu-Silicium 固件 | `../uefi-liuqin`（FD + `Resources/DTBs/liuqin.dtb`，DTB sha256 `ce90f88b…`） |
| Linux 交叉工具链 | `../toolchain`（`scripts/linux/setup-toolchain.py` 装） |
| OS 镜像 | iPad7,1 iPadOS 17.7.11 / 21H461 IPSW（`board.toml` 的 `[ipsw]` + 成员 sha256） |
| 板级驱动 | 子模块 `drivers/ShengUfs`（`.gitmodules` 指 fork，记录的提交才公开可解析） |
| 专有输入 | 工作树 `.cache/firmware/`，**不入库**；`proprietary-files.txt` 记哈希，脚本可重生成 |

同级参考仓（非依赖）：`../UniXNU-sheng`（模板）、`../liuqin-nixos`、`../xiaomipad-6pro-mainline`、
`../liuqin-firmware`（stock UEFI 抽出的 PE32/Raw）。

## 进度时间线（一行一轮，细节看 `docs/`）

| 轮 | 做了什么 | 实机结果 |
|---|---|---|
| v35 | panic 漏斗上屏 | 崩点第一次可读：`OSMetaClass::postModLoad+0x288` |
| v36 / v36c | 驱动侧 base-class gate | 同一 pc；证明 gate 是**假阳性**（家族预链必然占那个名字） |
| v37 | XNU 侧 `M:stalled` + 脏槽护栏 | 数组本身干净；崩点后移到 `+0x2d4` |
| v38 | 逐槽命名 + 跳过坏槽 | **崩溃消失**，到达 `Failed to mount root device` |
| v39–v42 | 延迟注册 / 对象身份判决 / 绑定按段映射 | **H16 结案**，第一次过挂载：`root-*` 一路到 `B19:at_mountroot` |
| v43 | 剪掉已结案的诊断 | 面板不再被几十行扫描顶掉（留着坏诊断比没有更坏） |
| v44 | 撤 `cs_enforcement_disable`（AMFI 在自己 `start()` 里就 panic） | 当时的 bench 镜像；`amfi_get_out_of_my_way` 保留 |
| v45 | trust-cache 候选（=y 分支） | 主机侧 21 项守卫过，实机未证 |
| v60 | 全量切到 Fly116 树 + `unixnu` 根分区 | 新墙：platform-power 的 AON 绑定**漏 slide**（指令字被当函数指针） |
| v61–v62 | 修 §3.7 绑定 + trust-cache 线 | v62 是空转轮（`export` 不进 env dict 的教训） |
| v65 | 里程碑叠加层可关（`liuqin-milestone=0`） | 产品层核验过，面板不再重影 |
| v66 | 按日志证明内核确实重编 | 新墙：`copyio.c:94` PAN 断言（VM telemetry 采样路径） |
| v67 | 一个 boot-arg 关掉全任务采样 | 断言消失；新墙：launchd 被 SIGKILL（`namespace 9 code 0x8`，未解释） |

## 规范符合性（摘要）

`python3 ../UniXNU/scripts/porting-record.py check --device <本仓>` → `liuqin: 14 structured areas checked`；
路径归属自检（`git ls-files` 对全部 `[[area]] paths` 求并集）→ **0 个无归属**。
逐组细节与缺口以 `porting.toml` 的 `limits` / `next_action` 为准，这里只给状态：

| 组 | 状态 | 一句缺口 |
|---|---|---|
| `board.toml` / `porting.toml` | 🟡 | 身份三字段一致 ✓；board 仍是 schema 1（原因见「待办 2」，不是没改） |
| `feeds.toml` / `unixnu.lock` / `.gitmodules` | ✅ | lock 8 条源码固定，驱动由 gitlink 固定；`.cache` 已不入库 |
| `configs/` / `patches/xnu/series` | 🟡 | series 60 条按重放顺序 ✓；但 `out/liuqin/.config` 曾与**任何**已提交 defconfig 都不符（差一个 `TRUST_CACHE_RUNTIME`），v67 分支已把它纳管成 `configs/liuqin_boot_tc_defconfig` |
| `dts/` | 🟡 | `dts/liuqin.dts` 入库 ✓；schema-2 的 `[device_tree]` 键还没有 |
| `package/` 与构建配方 | ✅ | 12 条 archives + `scripts/build-*.py` |
| `proprietary-files.txt` / 固件配置 | ✅ | 每行哈希本机实测且与 `board.toml` 的 pin 逐字一致 |
| `porting.toml` / `porting-record.yml` | ✅ | 14 区域 + CI 启用；跨仓证据一律 HTTPS permalink 且逐条核过 blob |
| `.github/workflows/package.yml` | 🔴 | 卡在构建器上 3 个未上游化的提交（文件顶部列成 BLOCKED，不藏进设备仓） |
| `README.md` / `AGENTS.md` | ✅ | 职责、依赖、构建入口、记录链接齐；硬约束在 AGENTS |
| `.gitignore` / `local.toml.example` | ✅ | 忽略 `local.toml`、`out/`、`.cache/`；`unixnu.lock` 入库 |

### 发布与复用（`unixnu.com/zh/porting/records#publishing-and-reuse`）

| 要求 | 状态 |
|---|---|
| 公开仓 + `unixnu` topic | ✅ `repositoryTopics = unixnu, liuqin` |
| 已发布提交 `Porting record` 绿 | ✅ main 与各实验分支都绿 |
| board = schema 2、记录 = schema 1 | 🟡 记录 ✓；board 还没翻 ⇒ 目录按**格式 1** 收录，[机型页](https://unixnu.com/zh/devices/models/liuqin) 这一行只有"仓库链接" |
| README / AGENTS 必备内容 | ✅ 见上 |
| 通用方法回写公共指南 | 🟡 补丁已写好并 `git apply --check` 过，待发上游 PR |

扫描每天 UTC 00:00 / 12:00（UTC+8 08:00 / 20:00）各一次；`gh workflow run sync-devices.yml
--repo UniXNU/UniXNU` 需要组织管理员权限（非成员 403）。**上游扫描目前是红的**（第三方记录缺
`evidence` 键，`device catalog: 'evidence'`），所以收录刷新要等上游修；本仓侧已按上游同一条
`tools.validate(...)` 路径核过：记录是**通过**的，board 一翻格式 2，`record_state` 立即 `available`。

## 待办（按代价排序）

1. **解释 launchd 的 SIGKILL**：在 XNU 源码里定位 reason 的 namespace/code 枚举与
   `_psignal_internal` 调用者，判定是 AMFI/代码签名判决还是 exec 失败；然后按"翻一个开关 =
   造一对互斥证据"的规矩逐个试 `amfi_get_out_of_my_way` / `liuqin_allow_unsealed_root=1` /
   `native-ufs-unauth-root=1` / `-apfs_shared_datavolume`。
2. **翻格式 2**（唯一还没达标的规范项）：① 4 处同名不同内容且行区间真重叠的补丁重基
   （`native-4k-signature-hash-cache`、`offline-component-load-probes`、
   `zzy-native-gpu-addon-mount`、`zzy-staged-system-apps`）；② 完整 `unixnu fetch` 让 lock 记下
   SoC 载体；③ 干净检出重建 kernel/firmware/payload/bootimg 并上一轮机；④ 才改
   `schema_version = 2`。载体本身已不阻塞：`document97/UniXNU-soc-seed`（`e0030d06d78d`）解析
   rc=0 / 73 条补丁，`a77710178a/UniXNU-sm8475-common`（`cd36e7097899`）rc=0 / 75 条但多一处撞车；
   设备树等价性也已用 dtc 证过（与厂商 DTB diff 0 行）。实测账见分支 `soccarrier-trial`、`schema2`。
3. **构建器那 3 条提交上游化**（或推到 `hhyygg2009/UniXNU` 并填实际 ref），否则打包 CI 永远红。
4. **驱动私有线是否公开**：`433e7fdd…`/`8abb519…`/`91c6fcc…`/`c36c575…` 与
   `liuqinBaseClassFree`/`liuqin_block_super_fix`/`liuqinMetaclass*` 在三个公开 ShengUfs 里都不存在，
   所以 H16 那段历史目前只能引 `docs/boot-rounds-v35-v44.md`。
5. **`.cache/firmware` 的历史对象**：已从索引移除并忽略，但公开历史里还在；要不要重写由维护者决定。

## 附录与来源

- **Fly116 PR #1 的 README 原文（§0–§13）逐字保留**在 [`docs/fly116-pr1-readme.md`](docs/fly116-pr1-readme.md)，
  含他的「致谢 / Acknowledgments」与「AI 用量统计」；编号原样保留以便交叉引用。
  2026-11-21 从本文拆出：内容一字未删，只是换了位置，本文只留状态与入口。
- 原文可逐字取回：`git show 4fc7f56:README.md`（362 行 / 18,365 B）；
  本仓原版 README：`git show cbbb7b1:README.md`（157 行 / 10,228 B）。
- 通用指南入口：[移植步骤](https://unixnu.com/zh/porting/steps)、
  [设备仓库](https://unixnu.com/zh/porting/device-repository)、
  [适配记录与发布复用](https://unixnu.com/zh/porting/records)、
  [组件目录](https://unixnu.com/zh/reference/components)、
  [驱动适配顺序](https://unixnu.com/zh/porting/drivers)、
  [构建与启动](https://unixnu.com/zh/building)、
  [日志抓取](https://unixnu.com/zh/porting/log-capture)。

Released under the BSD-2-Clause-Patent License. 上游：[UniXNU](https://github.com/UniXNU/UniXNU)。
