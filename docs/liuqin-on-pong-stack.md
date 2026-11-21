# liuqin 改走 pong/zizhan 栈：要做什么、值不值

> 目的：把「要不要换地基」这件事变成一张可判断的清单。
> 凡标 **已验证** 的，都在本机代码里读到了出处；标 **未验证** 的是推断，不能当结论。

## 0. 一句话结论

**可行、路线清晰，但要换地基**：从我们现在的 format 1（sheng 派生、板级全内联）换成
format 2（`soc_source = "sm8475"` + `PongPlatform24` 组件 + 设备 DTS）。
最大阻碍不是代码，是**构建环境**：zizhan 的内核侧脚本是 macOS 专用的。

但也有一条**成本极低、可能立刻解开当前卡点**的中间路线（见第 5 节）。

## 1. 参考实现的真实结构（已验证）

zizhan 的 `board.toml` 只有 92 行，几乎是纯声明：

| 字段 | 值 | 出处 |
|---|---|---|
| `schema_version` | 2 | `board.toml:1` |
| `soc` / `soc_source` | SM8475 / `sm8475` | `board.toml:5-6` |
| `os_profile` | `ipados-18.7.10-22H374` | `board.toml:7` |
| `kit` | `pong` | `board.toml:9` |
| `[components] include` | `["PongPlatform24"]` | `board.toml:22-24` |
| `device_tree.source` | `device:dts/zizhan.dts` | `board.toml:27` |
| `[kernel] archives` | `["component:PongPlatform24"]` | `board.toml:34` |

依赖来源（`feeds.toml`）：

| 源 | 引用 |
|---|---|
| xnu | `apple-oss-distributions/xnu` @ `xnu-11417.140.69`（Darwin 24.6.0） |
| IOStorageFamily | `IOStorageFamily-323` |
| sm8475 SoC 支持 | `Chengdong-Yu/UniXNU-pong` @ `f62243e`，路径 `migration/format2/soc-seed` |
| PongPlatform24 组件 | 同一 commit |

补充（已验证）：**`kits/PongPlatform24/` 这个目录并不存在**。仓库用
`scripts/migrate-pong-format2.py:64` 把 kit 项 override 成本仓库根目录（`CLAUDE.md:78`），
所以 "PongPlatform24" 实际就是 zizhan 自己的 `drivers/`。这条对我们是好消息：
要复用的东西都在一个仓库里。

**重要风险（已验证）**：`api.github.com/repos/Chengdong-Yu/UniXNU-pong` 现在返回 **404**。
好在该 repo 已经把 `migration/format2/soc-seed` 和 `drivers/*` 都 vendor 进了 zizhan 本体，
`kits/` 里也有 `NativePrelink`，所以 **fork zizhan 是自洽的**，不依赖那个已消失的 repo。
Chengdong-Yu 名下 11-11 之后拆出了一批单组件仓库（`pong-ufs`、`pong-power`、`pong-adreno`、
`pong-metal`、`pong-arm-cpu`、`pong-arm-dt-platform`、`pong-darwin-boot24`、`pong-wifi`、
`pong-usb`、`pong-baseband`、`pong-ram-block`、`pong-software-aes`、`pong-apple-keystore-state`），
也可以作为替代来源。

## 2. 他们的存储链路（这一段对我们当前卡点最有用）

已验证的事实，来自 `drivers/NativeUfs/PongUfsTransport.cpp` 与 `porting.toml` 的 storage 段：

1. **根镜像不是分区，是 Android `/data` 里一个物理连续的 `root.img`（19.4 GiB）**。
   `tools/zizhan/phone/pinalloc.c` 负责分配到连续区间，`tools/zizhan/mac/install_pinned.py` 写入，
   内核侧用 boot-arg `pong_shngroot_4k=<4K 块号>` 定位这个区间。
2. **`RootLayout` 把 OS / SystemOS / Data 各发布成一个独立 IOMedia**。
3. **发布顺序是刻意的**（`PongUfsTransport.cpp:576-605`）：
   OS 窗口立即发布 → 等 `rootvnode`（根挂上）→ 再每隔 1.5 s 依次发布 SystemOS、Data。
   原因写在注释里：三次失败的根挂载（g9/g14/g22）都是 OS/SystemOS/Data 三个容器**并发** `nx_mount`，
   全部停在 checkpoint 搜索之后、传输通道空闲。用 `pong_ufs_defer=0` 可退回"一次全发"。
   → **我们现在是反过来的：先发 SystemOS 并等它的媒体。这可能本身就是问题。**
4. **`pong_ufs_async` 默认 0**（`PongUfsTransport.cpp:624-626`）：它控制的是**传输完成线程**
   （4 个 worker 在 `doAsyncReadWrite` 之外完成请求），不是 IOKit 匹配。
   注释写明：在发起线程里就地完成会间歇性挂住并发的 APFS 容器探测（g14），开了 worker 又在 g16 挂。
   → 我们用忙等同步完成读，属于同类风险，迟早要面对。
5. **XNU 补丁 0006 修的是根挂载死锁**：`publishResource` 发现 `IOResources` 仍在配置时，
   **立即发 matched 通知**。修之前约 3/4 的启动挂不上根：APFS 探测持锁等 `IOBSD`，
   与 `IOResources` 配置形成环（g28 的等待线程 dump 定位）。修之后 g36–g39 **4/4** 挂上。
   → 这条几乎肯定也适用于我们（我们在 `bsd_init` 之后才发布，`IOBSD` 正是那时发布的）。
6. **userdata 几何是硬编码的**：`PongUfsTransport.cpp:48-54` 里
   `kPongUserdataFirst4k=0x2dfa00` / `kPongUserdataLast4k=0x76753fa`，并与 GPT 实测强比对，
   不符就直接 `transportFailure("Pong userdata geometry changed")`。
   → 换到 liuqin 必须换成我们自己的值（我们的 userdata 起止 LBA 是已知的）。
7. **他们认为"异步匹配彻底不执行"的机理是：某个 kext 的 `start()` 在配置线程里永远不返回。**
   `porting.toml` 与 `docs/progress/2026-10-30-root-and-autoloop.md:173-181` 记录：
   AppleImage4 在 `/defaults` 上等 `IOAESAccelerator`，拿不到就等/panic；配置线程只要有一个
   `start()` 卡住，后面所有排队 job 都不会再被处理。
   → **这可能就是我们的情况**：v58 观测到"job 排队、`st` 从不变化"，与"配置线程被某个
   `start()` 卡死"完全吻合。我们有 `IOSleep()` 永不返回的已知平台问题，任何用定时等待的
   `start()` 都会永久占住配置线程。
   他们为此准备了现成工具：补丁 0005 的 `pong_dump_waiting_threads()`
   （`PongUfsTransport.cpp:222,239-243`），根卷 25 s/45 s 未挂就打印所有等待线程的事件与栈。
   **这个可以直接搬到我们的树上用。**

## 3. liuqin 必须自己做的部分

| 项 | zizhan 的做法（文件） | liuqin 要做什么 | 难度 |
|---|---|---|---|
| 设备树 | `dts/zizhan.dts`，由真机 DT + `tools/gen_reserved.py` 生成 | 我们已有 `uefi-liuqin/Resources/DTBs/liuqin.dtb` 和 `make-liuqin-adt.py`，可复用 | 中 |
| 面板 | 2160×1916 命令模式 DSC，60/120 Hz | **2880×1800**，时序/背光/DSI 参数全部要重取 | 中高 |
| 触摸 | ST FTS（`fts-pri`，SPI0 + GPIO 模拟） | liuqin 的触摸控制器与 I2C/SPI 路径未确认（**未验证**） | 中 |
| DRAM 第二段 | `0xf4600000+0xba00000` | liuqin 的值要从真机 DT 取 | 低 |
| ADSP carveout | `0x9fd00000+0x3100000` | 同上 | 低 |
| reserved-memory | `configs/pong-memory-sm8475.json` + dts | 同上 | 低 |
| defconfig | `zizhan_ipados18_gpu_defconfig` | 新建 `liuqin_ipados18_*_defconfig` | 低 |
| 固件 | `uefi-zizhan` = `uefi-pong` + `Platforms/Xiaomi/zizhanPkg` | **我们已有 `uefi-liuqin`（Mu-Silicium + liuqinPkg）**，天然对应 | 低 |
| 电池/按键/Wi-Fi | `PongPower`、`PongWifi` | 我们明确不做外设，可先跳过 | — |

## 4. 两条路的取舍

| | 继续自研（sheng 派生） | 换 pong/zizhan 栈 |
|---|---|---|
| 已有投入 | 分区改造 + 8.74 GiB 镜像 + Linux 构建链全部保值 | 分区/镜像可保留，构建链要换 |
| 存储驱动 | 我们自己 fork 的 ShengUfs，**正在为匹配问题填坑** | `PongPlatform24` 在 SM8475 上已跑到主屏、可写 Data |
| GPU | `Adreno740` 组件，**与 A730 错配** | `PongAdreno`/`PongMetal` + Turnip，A730 实测 95.3 fps |
| 构建环境 | **纯 Linux**，已验证可用 | 内核侧脚本是 `*-macos.py`，需要 Xcode + Homebrew llvm@18 |
| 风险 | 未知数多，但每一步都可控 | 依赖别人的代码，但那条路已被验证到主屏 |

## 5. 建议：先做 A/B，不要现在就整体搬迁

**A. 先验 v59**（已构建未刷）。它用 `kIOServiceSynchronous` 绕开异步匹配，
如果 `st=0x6`、`drv≥1`、媒体出现，我们这条路还活着，继续走最省。

**B. 无论 A 成不成，先移植他们两条最便宜的洞察**（不换栈，改动很小）：

1. **发布顺序**：改成先发 OS 窗口 → 挂根 → 再发 SystemOS/Data，而不是反过来。
2. **0006 补丁**：把"`IOResources` 仍在配置时立即发 matched 通知"打到我们的 XNU 树上。
   这条对我们在 `bsd_init` 之后发布的时序几乎必然相关。

这两条加起来可能就解开当前卡点，成本是一天以内。

**C. 只有当 A+B 都不行，才整体迁到 pong 栈。**

## 6. 构建环境：你的 MacBook Pro 2017 能干什么（关键约束）

已验证：

- zizhan 的 CI 用 **`macos-15-intel`** runner（Intel！不是 Apple Silicon），Xcode 16.4，
  工具链是 Homebrew 的 `llvm@18` + `dtc` + `lzfse` + `nasm` + `acpica` + `mtools` + Python 3.12
  （`.github/workflows/macos-build.yml:49,171,200`）。
  → **"必须 Apple Silicon" 不成立**，Intel 是官方支持路径。
- 但他们很多脚本名字里带 `-macos`：`build-ipados18-pong-macos.py`、`build-pong-armcpu-macos.py`、
  `build-pong-platform-macos.py`、`build-darwinboot24-macos.py`、`build-pong-ufs-ipados18-macos.py`、
  `build-pong-ram-block-macos.py`。→ **内核侧确实绑 macOS**。

你的机器：MacBook Pro 2017 / **macOS 13 Ventura** / 已装 Xcode。

**先说好消息（已验证）**：**Intel Mac 是这条流水线的原生目标，不是二等公民。**
`scripts/build-darwinboot24-macos.py:2` 的 docstring 原文就是 "Compile the Darwin 24 loader **on the Intel Mac**"；
多个 kext 脚本显式接受 `x86_64`，报错文案还写着 "uses the Intel macOS toolchain"；
他们的云端内核 CI 也跑在 Intel 上（`runs-on: macos-15-intel`）。

**会挡住你的是版本和路径，不是架构**：

| 障碍 | 具体情况 | 判断 |
|---|---|---|
| **Xcode 的 SDK** | 构建目标写死 `arm64-apple-ios18.0`、`-miphoneos-version-min=18.0`（`build-xnu-ipados18.sh:1120-1121`、`build-ipados18.py:930`），实测用 `iPhoneOS26.5.sdk`；Ventura 官方上限 Xcode 15.2 只有 iOS 17.2 SDK | **这是最高风险项**：Xcode 15 大概率编不动 18.0 目标（未验证） |
| Homebrew 前缀 | 全部脚本硬编码 `/opt/homebrew`（Apple Silicon 路径），Intel 是 `/usr/local`（`ci-env.sh:9-12` 等十余处） | 可改，但要让每一处都对 |
| `llvm@18` 瓶装 | 他们自己的 CI 注释写着 Intel runner 上 cmake/llvm **没有 bottle**，只能吃预装 keg | Ventura x86_64 是否有 bottle **未验证**；没有就要源码编 LLVM（数小时） |
| `aea`（解密 IPSW 组件用） | 只在 macOS 15 上验证过 | Ventura 上是否可用 **未验证** |

**结论**：你的 MBP2017 拿来跑**固件 + DarwinBoot24 + pinalloc + 根镜像写入**没问题；
跑**内核和 userland 预链接**大概率会被 Xcode 15 的 iOS 17 SDK 挡住。

**三条可选路**：
① 先用仓库自带的 GitHub Actions（`macos-15-intel`）把 zizhan 原版跑通，证明可复现；
② 用 OpenCore Legacy Patcher 把 MBP2017 升到 Sonoma/Sequoia + Xcode 16.4；
③ 内核/userland 放云端或别的机器，Mac 只做固件和上机 —— 你现有的 Linux 流水线仍是最快的迭代工具。

## 7. 下一步（不需要设备）

1. 我先把 `PongUfsTransport.cpp` 的发布时序和 `patches/xnu-ipados18/0006` 读透，
   产出「移植到我们驱动」的具体 diff 方案。
2. 你决定 A/B/C 哪条路；B 不需要换构建环境，我可以直接做。
3. 若要评估 C，我再去拉 `Y3d4/UniXNU-pong-clean`（pong 原版 format 2，仍在）做一次对照。
