# B 路线修正：改用 QPRS-6 的 liuqin 端到端实现

> 结论先说：之前说的"B = document97 的 pong 栈"**应该替换成** [QPRS-6/iPadOS18-liuqin](https://github.com/QPRS-6/iPadOS18-liuqin)。
> document97 那份自述"没有在真机上验证过任何一项"；而 QPRS-6 是**同一台机器**上已经进到桌面的一份完整实现。

## 1. QPRS-6 是什么（核实结果）

| 项 | 值 | 出处 |
|---|---|---|
| 目标系统 | iPadOS **18.7.10 / 22H374**，iPad7,11 (j171ap) | `README.md`、`board.toml` |
| 内核 | XNU **11417.140.69**（Darwin 24.6.0），`MACHINE_CONFIGS=PONG` | `README.md`、`board.toml` |
| 机型 | liuqin，SM8475，8 核，Adreno 730，NT36532 触摸，KTZ8866 背光 | `README.md` |
| 结构 | `schema_version = 2`，`soc_source = "sm8475"`，`kit = "liuqin"`，`components = ["PongPlatform24"]` | `board.toml` |
| 规模 | 6089 个文件，36.6 MB，6★ | GitHub API |
| 自述已验证 | **APFS 挂根 + SpringBoard 桌面**、触摸可用、Adreno 730 Metal 渲染、关机滑条能真正断电 | `README.md` "Device status at the time of this drop (2026-11-17)" |
| 自述未完成 | KTZ8866 背光设置滑条无效、电池/音频/Wi-Fi/传感器仍在做 | 同上 |

**注意**：以上是**仓库自述**。我没有在真机上独立验证任何一条 —— 但它是同机型、有版本矩阵、有分层文档（BUILD/LAYOUT/BACKLIGHT-AUDIT）和独立 CI 仓库，可信度远高于 document97。

## 2. 它和我们现在这套的根本差别

| | 我们（sheng 派生，format 1） | QPRS-6（pong 栈，format 2） |
|---|---|---|
| SoC 层 | ShengUfs 改 SM8475 | `soc_source = "sm8475"` + `PongPlatform24` |
| 系统版本 | iPadOS 17.7.11 / 21H461 | iPadOS **18.7.10 / 22H374** |
| 内核 | xnu 17.7.11 树 | XNU 11417.140.69 |
| 根镜像 | 8.74 GiB，写在 `unixnu` 分区 | ~11 GiB，OS/SystemOS/Data 三窗口，**写进 `userdata`**（脚本硬要求 `/dev/block/sda13` 与特定几何） |
| 构建 | 纯 Linux（我们自建） | macOS：Xcode + iPhoneOS18.6 SDK + MIG + dtc；**另有 GitHub Actions CI 仓库** |

## 3. 关键的可行性与约束

### 3.1 好消息：构建不一定需要本地 Mac

[Kalospacer/liuqin-ipados18-ci](https://github.com/Kalospacer/liuqin-ipados18-ci) 有三个 workflow：

- `.github/workflows/kernel.yml` — 编内核（含 liuqin 的 OSMetaClass 锁序修复、icache phystokv、触控 IRQ 等补丁）
- `.github/workflows/metal.yml` — 编 Mesa parity167 + 22H374 Metal 插件
- `.github/workflows/userdata.yml` — 编用户态/根镜像

产物用 secret `LIUQIN_ARTIFACT_PASSWORD` 加密，保留三天。**这意味着可以用我们自己的 fork + 自己的 secret 在 Actions 上跑通，不需要你那台 Ventura。**

### 3.2 硬约束：分区

他们的安装脚本 `scripts/install-ipados18-ufs.py` 明确要求：

- 目标 `/dev/block/by-name/userdata`，且 `readlink -f` 必须等于 `/dev/block/sda13`
- `geometry['first_512'] == layout['partition_first_512']`，逻辑块必须 4096

我们的设备是：`userdata = sda35`（94 GiB）、`unixnu = sda36`（11.79 GiB）。**几何完全对不上。**

三条出路：

| 方案 | 是否动 GPT | 说明 |
|---|---|---|
| **B1**（推荐）改他们的脚本 + 驱动里的分区名/几何，把三窗口写进我们现成的 `unixnu`（11.79 GiB，装 ~11 GiB 刚好） | **不动** | 改动集中在 `RootLayout.h` 的分区名与几何校验、`install-ipados18-ufs.py` 的目标与断言 |
| B2 照他们的样子建一个 `hfs` 分区 | **要动** | 与你"不随意改 GPT"的规则冲突，需要你明确同意 + 先备份 |
| B3 把 `userdata` 缩回原状（撤销我们的 carve-out） | **要动且风险高** | 不建议，之前的 R1 方案就是这样失败的 |

### 3.3 其他必须重做的输入

- 新的 IPSW（18.7.10 / 22H374），CI 会从 Apple CDN 拉
- Adreno 730 固件（ZAP/GMU）
- Mesa 26.2.6 + `mesa-delta` 补丁 + Metal bridge（cargo 交叉编到 aarch64-apple-ios）
- 根镜像构建约 19 GB 中间产物

## 4. 我应该做什么、需要你决定什么

### 我可以直接做（不需要设备和你的时间）

1. fork `QPRS-6/iPadOS18-liuqin` 与 `Kalospacer/liuqin-ipados18-ci` 到 Fly116 名下（只读源 + CI）
2. 在自己的 fork 里设 `LIUQIN_ARTIFACT_PASSWORD`，跑一次 `kernel.yml`，确认**内核能编出来**（这是整条链的第一道门）
3. 把 `RootLayout.h` / `install-ipados18-ufs.py` 里对 `sda13`/`userdata` 的硬编码改成可配置，并指到 `unixnu`
4. 输出一份"从零到能刷"的操作清单

### 需要你决定

| 问题 | 选项 |
|---|---|
| **A. 允许我在 GitHub 上 fork 这两个仓库吗？** | 是 / 否（否则只能在本地手动复现 CI） |
| **B. 根镜像写哪里？** | B1 写进现有 `unixnu`（不动 GPT，推荐）/ B2 新建 `hfs` 分区（要改 GPT）/ 其他 |
| **C. 是否接受切换到 iPadOS 18.7.10？** | 我们现在写的是 17.7.11 的 8.74 GiB 镜像；换 18.7.10 要重新下载 IPSW 并重建 ~11 GiB 根镜像，旧的 `unixnu` 内容会被覆盖 |

## 5. 风险与诚实评估

- **最大风险是"能编不等于能跑"**：他们的 CI README 自己写着"构建成功不代表设备已启动"。我们设备的分区几何和他们的不同，B1 需要改驱动里的几何校验，改错了会在 UFS 初始化阶段就 fail。
- **第二风险是 Metal 链**：Metal 依赖 Mesa 26.2.6 + Turnip 补丁 + Rust bridge，这一整套在我们环境里从未验证过。哪怕内核跑通、根挂上，桌面出不来也可能卡在这里。
- **第三是磁盘**：8 GB 版 liuqin 的 userdata 只有 94 GiB，我们已划走 11.79 GiB；他们用 32G 机型的布局。若走 B1，要先确认三窗口总大小 ≤ 11.79 GiB。
- **时间**：即便一切顺利，从 fork 到第一次上机也是**数天**量级（CI 编内核 + 用户态各一轮，加上改几何、装镜像、调 touch/backlight）。

## 6. 建议的执行顺序

1. **先只做一件事**：fork CI 仓库，跑 `kernel.yml`，确认内核能在 Actions 上编出来。这一步不需要你参与，也几乎零风险。
2. 编出来了，再决定 B1/B2 和是否换 18.7.10。
3. 根镜像最后做（最贵、最占磁盘）。
