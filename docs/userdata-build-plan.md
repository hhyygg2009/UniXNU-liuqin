# userdata-root.img 构建计划（liuqin / iPadOS 17.7.11 21H461）

最后更新：2026-11-19（Asia/Shanghai）
状态：**只读分析完成**。本次没有下载 OS/SystemOS 两个大 dmg、没有生成镜像、没有操作设备。
分析对象：`/root/work/UniXNU-new`（构建器）与 `/root/work/device-new`（设备仓库，`[ipsw]` / `[ipsw.sha256]` / `[userdata]` 段已在 d21e714 提交）。

---

## 0. 结论摘要

1. **`unixnu userdata --dry-run` 实测通过**（exit 0，约 0.9~1.0 s，无需代理），输出见 §2.4。它只做“IPSW 组件是否存在 + 分区容量”检查：不写盘、不校验 sha256 pin、不下载大文件（实测只发 2 次 HTTP Range 请求、共 14.5 MiB）。
2. **产物精确大小：9,382,658,048 B = 8,948 MiB = 8.738 GiB**。
   推导：1 MiB manifest 头 + OS 窗口 5,797 MiB + SystemOS 窗口 3,150 MiB（每个窗口按 1 MiB 对齐，见 §2.6）。
3. **分区容量：113,596,411,904 B = 105.795 GiB**，与设备当前 `fastboot getvar all` 的
   `partition-size:userdata: 0x1A72DFB000`（= 113,596,411,904 B，27,733,499 个 4096 B LBA）完全一致。
   产物占分区 8.3%，余量 104,213,753,856 B（97.06 GiB）。
4. **缺失输入只有两个 IPSW 成员**（其它都已在缓存或不需要）：
   - `044-76040-038.dmg`（OS）：成员 5,157,406,069 B，zip 内 **stored**，网络传输 5,157,406,069 B；
   - `044-76034-038.dmg`（Cryptex1,SystemOS）：成员 3,303,014,400 B，zip 内 **deflate** 压缩为 1,321,525,120 B；
   - 网络合计 **6,478,931,189 B（6.034 GiB）**，解出后落盘 **8,460,420,469 B（7.879 GiB）**。
5. **不需要 AEA 解密**。21H461 的两个成员都是普通 `.dmg`；`ipsw.py` 只在成员后缀为 `.aea` 时才走 AEA（iPadOS 26 才常见）。本 IPSW 无 `.aea` 成员。
6. **Linux（WSL2）可以完整构建**：`udif.py` + `/opt/unixnu-linux/bin/lzfse`（已安装，`util.lzfse()` 会自动找到该前缀）。本次已验证 `lzfse` 的 stdin/stdout 解码路径可用，`scripts/unixnu/test_udif.py` 6/6 通过。
7. **时间**（实测本机 CDN 单流 25.6 MB/s、lzfse 解码 374 MB/s、WSL 盘顺序写 1.1 GB/s）：
   整包下载约 4.5 分钟 + 解压/写盘约 1~3 分钟；首次全流程约 **6~9 分钟**。
8. **建议**：直接整包下载 IPSW（6.366 GiB，可断点续传），只比“按需 Range 取两个成员”多 0.33 GiB，换来离线复建、进度可见、失败可续传。

产物路径（schema_version=1，输出根 `out/`）：

```
/root/work/UniXNU-new/out/liuqin/userdata/userdata-root.img
/root/work/UniXNU-new/out/liuqin/userdata/userdata-root.json
```

---

## 1. 依赖输入清单

### 1.1 目标 IPSW（`board.toml` 第 14~26 行）

| 项 | 值 |
| --- | --- |
| URL | `https://updates.cdn-apple.com/2025SpringFCS/fullrestores/122-77166/DCA6CFE8-90E9-4B92-880D-3DE171798E40/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw` |
| 整包大小 | **6,835,124,840 B（6.366 GiB）**（HTTP HEAD 实测） |
| 版本 / build | 17.7.11 / 21H461 |
| product | iPad7,1（BuildManifest 同时支持 iPad7,1/7,2/7,3/7,4） |
| device_class / chip / board | j120ap / 0x8011 / 12 |
| Range 支持 | 支持（`Range: bytes=0-0` 返回 206；HEAD 头未 advertise，但请求有效） |
| 本地副本 | 当前没有（在 `/root/work`、`/mnt/f`、`/mnt/e` 浅层搜索只找到无关的 `/mnt/e/ota_iPhone4,1.ipsw`） |

`Ipsw.__init__` 会校验 `ProductVersion`/`ProductBuildVersion`/`SupportedProductTypes`，并挑出
`DeviceClass=j120ap`、`RestoreBehavior=Erase`、chip/board 匹配的 build identity；任何不符都会在
dry-run 阶段直接报错（见 §5）。

### 1.2 userdata 真正需要的两个 IPSW 成员（`[userdata].windows`）

| 项 | OS | Cryptex1,SystemOS |
| --- | --- | --- |
| `[userdata]` 窗口名 | `OS` | `SystemOS` |
| IPSW 成员路径 | `044-76040-038.dmg` | `044-76034-038.dmg` |
| zip 压缩方法 | 0（stored，不压缩） | 8（deflate） |
| 成员（解压后）大小 | 5,157,406,069 B | 3,303,014,400 B（正好 3,150 MiB） |
| zip 内压缩大小 | 5,157,406,069 B | 1,321,525,120 B |
| 落盘缓存 | 5,157,406,069 B | 3,303,014,400 B |
| 内容格式 | UDIF（`koly` 尾）磁盘镜像，内含 GPT+APFS | **裸 APFS 容器**（无 UDIF 尾） |
| 原始磁盘大小 | **6,077,580,800 B**（11,870,275 × 512） | **3,303,014,400 B**（806,400 × 4096） |
| Linux 解码 | `udif.py` 解 UDIF 块；LZFSE 块调用 `lzfse -decode` | 无 UDIF 尾 → 按裸文件逐字节复制，无需解码 |
| macOS 解码 | `hdiutil attach -nomount -readonly` + `/dev/rdiskN` | 同左（裸镜像同样由 `hdiutil` 暴露） |
| AEA 加密 | 否 | 否 |
| `[ipsw.sha256]` pin | `3f99b859c9728379110b66657c7b15285bcb1925ad8b5726542e2cdcf8b1028d` | `b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a18b1600c96a3fd70414f` |

实测到的 OS UDIF 内部结构（只 range 读了尾部 `koly` 和 234,240 B 的 plist，没有下载整文件）：

- `koly` 中 `data_fork=0`、plist 偏移 5,157,171,317 / 长 234,240、`sectors=11,870,275` → raw 6,077,580,800 B。
- 6 张 `blkx`：`MBR`、`Primary GPT Header`、`Primary GPT Table`、`Apple_APFS`（3809 块、6,077,546,496 B、
  最大单块 203,313,152 B）、`Backup GPT Table`、`Backup GPT Header`。
- 3814 个有效块：**LZFSE 3,415 个（类型 0x80000007）**、原样 396 个（0x1）、忽略/清零 3 个（0x2）；
  无 zlib/bzip2 块。→ Linux 构建会 spawn 3,415 次 `lzfse -decode`（每次一个 UDIF 块）。

实测到的 SystemOS 内容：解压出的前 64 KiB 里 offset 0x20 是 APFS 魔数 `NXSB`，offset 0x24 块大小
`0x1000`，offset 0x28 `nx_block_count=0xC4E00=806,400`，806,400 × 4096 = 3,303,014,400 B = 成员大小 →
**裸 APFS 容器**，不是 UDIF/AEA/GPT 包装，`udif.Image()` 会把文件长度当 raw size，直接复制。

### 1.3 其它 IPSW 组件（userdata 不需要，但说明“完整体”）

`python3 ./unixnu ipsw info -D /root/work/device-new`（同一套远程 Range 读取，网络量与 dry-run 同量级，
约 15 MiB）列出 34 个组件。与本项目相关的大件：

| 组件 | 成员 | 成员大小（B） | 用途 | 现状 |
| --- | --- | --- | --- | --- |
| `KernelCache` | `kernelcache.release.ipad7` | 16,238,580 | 内核 prelink 输入 | 已缓存（解压后 46,415,872） |
| `DeviceTree` | `Firmware/all_flash/DeviceTree.j120ap.im4p` | 35,260 | ADT/DTB 输入 | 已缓存 |
| `StaticTrustCache` | `Firmware/044-76040-038.dmg.trustcache` | 59,519 | trust cache | 已缓存 |
| `Cryptex1,SystemTrustCache` | `Firmware/044-76034-038.dmg.trustcache` | 1,871 | trust cache | 已缓存 |
| `OS` | `044-76040-038.dmg` | 5,157,406,069 | **userdata 窗口** | **缺** |
| `Cryptex1,SystemOS` | `044-76034-038.dmg` | 3,303,014,400 | **userdata 窗口** | **缺** |
| `RestoreRamDisk` | `044-75545-038.dmg` | 184,549,403 | 恢复模式，不用 | 不需要 |
| `Cryptex1,AppOS` | `044-75188-038.dmg` | 10,485,760 | App cryptex，不用 | 不需要 |
| `Ap,SystemVolumeCanonicalMetadata` | `Firmware/044-76040-038.dmg.mtree` | 24,102,932 | 系统卷元数据，不用 | 不需要 |

`board.toml [ipsw].fetch` 只列了 4 个小件，所以 `unixnu ipsw fetch`（默认参数）不会取两个 dmg；
userdata 的两个窗口要么由 `unixnu userdata` 自动取，要么显式
`unixnu ipsw fetch ... OS Cryptex1,SystemOS`（见 §2.3）。

### 1.4 工具与运行时

| 工具 | 需求 | 本机现状 |
| --- | --- | --- |
| Python | ≥ 3.11（`cli.py` 强制检查） | 3.12.3 |
| `lzfse` | Linux 解 LZFSE UDIF 块 | `/opt/unixnu-linux/bin/lzfse` 存在且可执行（`util.lzfse()` 硬编码默认前缀 `/opt/unixnu-linux`，无需进 PATH） |
| `hdiutil` | 仅 macOS 路径 | 不适用（WSL 走 `udif.py`） |
| `aea` + `ipsw` | 仅 AEA 解密（本 IPSW 不需要） | 未安装，也不需要 |
| 磁盘 | 峰值约 23 GiB（保留 IPSW 时） | `/root/work` 余 948 GiB |
| CPU/RAM | — | 20 vCPU / 13 GiB |

userdata 步骤**不需要** kernel toolchain、`unixnu fetch`、defconfig、UEFI 或 `--unique-chip-id`；
`cmd_build()` 在 `userdata.build()` 之后才调用 `need_config()/prepare()`。启动时打印的
“`unixnu: on Linux the kernel step uses the toolchain ...`”只是全局提示，可忽略。

---

## 2. 从零到 `userdata-root.img` 的完整命令序列

以下命令都在 WSL 里执行（Windows 侧入口：`wsl -d Ubuntu24 -u root`）。
仓库路径固定为 `/root/work/UniXNU-new`（构建器）与 `/root/work/device-new`（设备仓库）。

### 2.0 环境自检

```bash
cd /root/work/UniXNU-new
python3 --version                          # 期望 3.12.3（>=3.11）
ls -la /opt/unixnu-linux/bin/lzfse         # 期望存在
df -h /root/work                           # 期望 >= 25 GiB 可用
python3 ./unixnu userdata --help | tail -8 # 只看参数，不联网
```

### 2.1 代理（可选）

```bash
export http_proxy=http://172.21.64.1:7897
export https_proxy=$http_proxy
```

说明：

- **本次实测不设代理也能访问 Apple CDN**：同一台机器上不带 proxy 的 dry-run 0.916 s 成功，
  `HEAD`/`Range` 都正常。所以代理只在直连失败（超时、403）时设置。
- Python `urllib` 自动读取 `http_proxy`/`https_proxy` 环境变量，`ipsw.py` 的 `RangeFile` 不需要额外参数。
- 若走 GitHub 镜像/Release 下载 IPSW 或工具（本流程不需要），token 在
  `/mnt/f/liuqin_ios/_gh/token.txt`（40 B）；**不要**把 token 写进 board.toml、文档或日志。

### 2.2 获取 IPSW

**方案 A（推荐）：整包下载，可断点续传**

```bash
mkdir -p /root/work/ipsw
time curl -L -C - --retry 5 --retry-delay 3 \
  -o /root/work/ipsw/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw \
  "https://updates.cdn-apple.com/2025SpringFCS/fullrestores/122-77166/DCA6CFE8-90E9-4B92-880D-3DE171798E40/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw"
stat -c%s /root/work/ipsw/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw   # 期望 6835124840
```

- 预计 **6.366 GiB**；按本机实测 25.6 MB/s 约 **4.5 分钟**（代理/线路不同会有波动）。
- 校验（身份，而非整包哈希）：用同一套代码打开本地 zip，确认 build identity：

```bash
cd /root/work/UniXNU-new
python3 ./unixnu ipsw info -D /root/work/device-new \
  --ipsw /root/work/ipsw/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw | head -3
# 期望第一行：17.7.11 (21H461) j120ap  local
```

- 如需额外自证，记录自己的 `sha256sum`（约 20 s）。Apple 不提供整包官方哈希，工程内的信任锚是
  **BuildManifest 身份（版本/build/product/device_class/chip/board）+ `[ipsw.sha256]` 两个成员 pin**。

**方案 B：不整包下载，直接远程按需读取（板载 URL）**

省略 `--ipsw` 即可，`Ipsw` 用 HTTP Range 只读 zip 目录、BuildManifest 和两个成员：

```bash
cd /root/work/UniXNU-new
# 需要时先 export 代理
python3 ./unixnu userdata -D /root/work/device-new     # 首次会远程下载 6.03 GiB 成员
```

- 两成员网络量 6,478,931,189 B（6.034 GiB），只比整包少 0.33 GiB；**但不支持断点续传**
  （每 8 MiB 一个 Range 请求，失败重试 4 次后整体报错），线路差时更慢、更易失败。
- 只适合“一次性临时验证”；重复构建或需要审计时请用方案 A。

### 2.3 预填解压缓存（可选，把下载与构建分开）

```bash
cd /root/work/UniXNU-new
# 远程 URL 方式：
python3 ./unixnu ipsw fetch -D /root/work/device-new OS Cryptex1,SystemOS
# 若已整包下载，加 --ipsw 则完全不联网：
python3 ./unixnu ipsw fetch -D /root/work/device-new \
  --ipsw /root/work/ipsw/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw OS Cryptex1,SystemOS
```

- 会打印 `== ipsw: extracting OS (044-76040-038.dmg, 4918 MiB)` 等；先写 `.partial`，完成后改名为
  `out/ipsw/21H461/044-76040-038.dmg` / `044-76034-038.dmg`，并各写一个 `.json` 记录
  （component/member/size/sha256/source）。解压时按 `[ipsw.sha256]` 校验 pin。
- 期间没有逐块进度输出，几分钟无日志属正常；中断后 `.partial` 会被下次重写（从 0 开始）。

### 2.4 dry-run（本次实测输出）

```bash
cd /root/work/UniXNU-new && python3 ./unixnu userdata --dry-run -D /root/work/device-new
```

实际输出（2026-11-19，exit 0，0.916 s；加代理时 1.022 s，输出相同）：

```
unixnu: on Linux the kernel step uses the toolchain of scripts/linux/setup-toolchain.py; --skip-setup and seed-data-grow need macOS hdiutil
== userdata payload
== ipsw: remote https://updates.cdn-apple.com/2025SpringFCS/fullrestores/122-77166/DCA6CFE8-90E9-4B92-880D-3DE171798E40/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw
== userdata: OS would be extracted from 044-76040-038.dmg (4918 MiB)
== userdata: SystemOS would be extracted from 044-76034-038.dmg (3150 MiB)
== userdata: dry run, nothing extracted or written
```

参数与前提（本次 dry-run 实际使用的组合）：

- **必需**：设备仓库 `-D /root/work/device-new`（`out/state.json` 已记住时可省，换仓库必须给）；
  IPSW 源：board `[ipsw].url`（默认远程）或 `--ipsw PATH_OR_URL` / `UNIXNU_IPSW`（本地整包或 URL 均可）。
- **`--dry-run`**：只检查组件存在性与容量，不提取、不写盘、不校验 pin（见下）。
- **不需要**：`--unique-chip-id`（只用于 device tree/ADT）、`--data-ramdisk`（payload 用）、
  `--os`（本仓库 schema_version=1，无 profiles）、`--object`（本板没有 kernel 自读的数据对象；
  将来若有，窗口最多 4 个、对象最多 4 个，且 `NAME=FILE[:OFFSET]` 的 FILE 必须存在）。
- 本地缓存现状：`[ipsw].fetch` 的 4 个小件已在 `out/ipsw/21H461/`（§6.1）；两个 dmg 缺失（§6.2），
  所以本 dry-run 只做了 14.5 MiB 的元数据 Range 读取，没有触发大文件下载。

要点：

- 两个 MiB 数是 **zip 成员的解压后大小**（4918 = floor(5,157,406,069/2^20)，3150 = 3,303,014,400/2^20），
  **不是产物大小**，也不是 OS 的原始磁盘大小；产物大小见 §2.6。
- dry-run **不校验** `[ipsw.sha256]` pin（代码在 `dry_run` 分支提前返回）；pin 只在真正提取时校验。
- dry-run **不写任何文件**：`out/liuqin/userdata/` 不存在（已确认）。
- 本次 dry-run 的网络量：2 次 Range 请求、15,186,536 B（14.5 MiB）。

### 2.5 正式构建

```bash
cd /root/work/UniXNU-new
time python3 ./unixnu userdata -D /root/work/device-new \
  --ipsw /root/work/ipsw/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw
```

（若采用方案 B，去掉 `--ipsw` 一行即可。）

首次构建的日志顺序（代码固定顺序）：

```
== userdata payload
== ipsw: local /root/work/ipsw/iPad_Pro_HFR_17.7.11_21H461_Restore.ipsw
== ipsw: extracting OS (044-76040-038.dmg, 4918 MiB)          # 仅当缓存无此文件
== ipsw: extracting Cryptex1,SystemOS (044-76034-038.dmg, 3150 MiB)
== userdata: image 9382658048 bytes (8.74 GiB) in a partition of 113596411904 bytes
== userdata: OS from 044-76040-038.dmg
== userdata: SystemOS from 044-76034-038.dmg
== userdata: OS 6077580800 bytes at 0x100000 sha256 <...>
== userdata: SystemOS 3303014400 bytes at 0x16a600000 sha256 <...>
== userdata: out/liuqin/userdata/userdata-root.img
== flash it yourself with: fastboot flash userdata out/liuqin/userdata/userdata-root.img
```

- 产物：`out/liuqin/userdata/userdata-root.img`（9,382,658,048 B）+ `userdata-root.json`。
- **不自动刷机**：最后一行只是打印命令。
- 构建中会把 9.38 GiB 逐块写入镜像；完成后对整镜像算 sha256 写进 json（约 9 s）。
- 预期耗时（缓存已就绪时）：读 dmg 6.08 GB 并 LZFSE 解码（约 20~60 s，3,415 次 `lzfse` 子进程）
  + 写 9.38 GiB（本机顺序写 1.1 GB/s ≈ 9 s）+ 计算哈希，合计约 **1~3 分钟**。

### 2.6 产物大小推导，以及与 `[userdata]` 分区几何的关系

代码（`userdata.py` 112~134 行）：

```text
capacity = (last_lba - first_lba + 1) * lba_bytes
total    = 1 MiB + align_1MiB(OS_raw) + align_1MiB(SystemOS_raw)
若 total > capacity → 报错 "the image is larger than the userdata partition"
```

本板（`board.toml` 37~40 行）：`first_lba=0x2A1600=2,758,144`，`last_lba=0x1D143FA=30,491,642`，
`lba_bytes=4096`：

| 项 | 字节 | 换算 |
| --- | --- | --- |
| 分区 sectors | 27,733,499 | — |
| **分区容量** | **113,596,411,904** | 105.795 GiB / 113.596 GB |
| manifest 头 | 1,048,576 | 1 MiB |
| OS 窗口（原始 UDIF 磁盘） | 6,077,580,800 | 5,796.03 MiB → 对齐 5,797 MiB |
| OS 窗口（对齐后） | 6,078,595,072 | offset 0x100000 |
| SystemOS 窗口（裸 APFS） | 3,303,014,400 | 3,150.00 MiB（已对齐） |
| SystemOS offset | 6,079,643,648 | = 1 MiB + 5,797 MiB = 0x16A600000 |
| **镜像总大小** | **9,382,658,048** | **8,948 MiB = 8.738 GiB** |
| 余量（分区-镜像） | 104,213,753,856 | 97.057 GiB |

其它两种 GPT 布局（board.toml 注释里的历史值，仅核对用）：

| 布局 | last_lba | 容量 |
| --- | --- | --- |
| 当前 live（本板） | 0x1D143FA | 113,596,411,904 B（105.795 GiB） |
| 旧记录（userdata 缩到 96 GiB） | 0x1AA17FF | 103,081,312,256 B（96.002 GiB） |
| 恢复 stock GPT | 0xEDA2BFA | 1,009,340,362,752 B（940.021 GiB） |

三种布局都能放下 8,738 MiB 的镜像；**但 flash 前必须用设备实际 GPT 核对 `last_lba`**
（当前设备 `fastboot getvar partition-size:userdata` = `0x1A72DFB000` = 113,596,411,904 B，与 live 一致）。

### 2.7 时间与空间预算（本机实测 + 估算）

| 项 | 数值 | 依据 |
| --- | --- | --- |
| CDN 单流吞吐 | **25.6 MB/s**（32 MiB Range 测试，1.31 s） | 实测 |
| 整包下载 | 6,835,124,840 B ≈ **267 s（4.5 min）** | 实测吞吐推算 |
| 两成员按需下载 | 6,478,931,189 B ≈ **253 s（4.2 min）** | 实测吞吐推算 |
| dry-run 网络 | 15,186,536 B（14.5 MiB），2 次 Range | 实测 |
| lzfse 解码吞吐 | 374 MB/s（128 MiB 随机数据上限）；OS 6.08 GB ≈ 16~60 s | 实测 + 估算 |
| WSL 盘顺序写 | 1.1 GB/s（1 GiB dd） | 实测 |
| 解压后 dmg 缓存 | 8,460,420,469 B（7.879 GiB） | 计算 |
| 产物镜像 | 9,382,658,048 B（8.738 GiB） | 计算 |
| 峰值磁盘（保留 IPSW） | 24,678,203,357 B（22.983 GiB） | 计算 |
| 首次全流程 | **约 6~9 分钟**（下载 4.5 + 构建 1~3） | 估算 |

本机 `/root/work` 可用 948 GiB，空间不是问题。构建后若想回收缓存，可删除
`out/ipsw/21H461/044-*.dmg`（重建时会重新解压）；保留整包 IPSW 时不必留 dmg 缓存。

---

## 3. 产物验证

### 3.1 `userdata-root.json` 期望值

```bash
python3 -m json.tool /root/work/UniXNU-new/out/liuqin/userdata/userdata-root.json
```

| 字段 | 期望值 / 检查 |
| --- | --- |
| `image_bytes` | `9382658048` |
| `image_sha256` | 与 `sha256sum userdata-root.img` 一致 |
| `manifest_sha256` | 与 header 前 4096 B 的 sha256 一致 |
| `windows[0]` | `OS`, offset `1048576`, size `6077580800`, block 512 |
| `windows[1]` | `SystemOS`, offset `6079643648`, size `3303014400`, block 512 |
| `windows[].sha256` | 各窗口内容 sha256（= 源 dmg 原始磁盘字节的 sha256） |
| `objects` | `[]`（本设备没有 `--object` 输入，manifest version=1） |
| `userdata_bytes` | `113596411904` |
| `flash_command` | `fastboot flash userdata .../userdata-root.img` |
| `source` | `local`（用整包）或 `remote` |
| `hardware_tested` | `false`（代码固定写 false，未上机验证） |

### 3.2 独立解析 SHNGROOT manifest

不要只信构建器自己的 json，用下面脚本从镜像文件独立解析（字段布局见
`docs/concepts/userdata.md`）：

```python
import hashlib, struct
p = '/root/work/UniXNU-new/out/liuqin/userdata/userdata-root.img'
with open(p, 'rb') as f:
    h = f.read(4096)
assert h[:8] == b'SHNGROOT', h[:8]
version, count, image_bytes = struct.unpack_from('<IIQ', h, 8)
print('version', version, 'count', count, 'image_bytes', image_bytes)
for i in range(count):
    name, off, size, block, flags, digest = struct.unpack_from('<32sQQII32s', h, 24 + 128 * i)
    print(name.rstrip(b'\0').decode(), hex(off), size, 'block', block, 'flags', flags, 'sha256', digest.hex())
assert hashlib.sha256(h[:4064]).digest() == h[4064:4096], 'manifest self hash mismatch'
print('manifest self-hash OK')
```

期望：`version=1, count=2, image_bytes=9382658048`；`OS` offset `0x100000`；
`SystemOS` offset `0x16a600000`；两窗口 `block=512, flags=1`。

### 3.3 窗口大小 / sha256 复核

```python
import hashlib, json
base = '/root/work/UniXNU-new/out/liuqin/userdata/'
rep = json.load(open(base + 'userdata-root.json'))
with open(base + 'userdata-root.img', 'rb') as f:
    for w in rep['windows']:
        f.seek(w['offset'])
        left, d = w['size'], hashlib.sha256()
        while left:
            b = f.read(min(8 << 20, left))
            assert b, 'short read'
            d.update(b)
            left -= len(b)
        print(w['name'], 'size', w['size'], 'sha256 OK' if d.hexdigest() == w['sha256'] else 'MISMATCH')
assert rep['image_bytes'] == 1048576 + 6078595072 + 3303014400
```

### 3.4 与源 dmg 对照（缓存里应已有两个文件）

```bash
cd /root/work/UniXNU-new
python3 -c "import sys; sys.path.insert(0,'scripts'); from unixnu import udif; \
print(udif.Image('out/ipsw/21H461/044-76040-038.dmg').size); \
print(udif.Image('out/ipsw/21H461/044-76034-038.dmg').size)"
# 期望 6077580800 / 3303014400

sha256sum out/ipsw/21H461/044-76040-038.dmg    # 3f99b859...b1028d
sha256sum out/ipsw/21H461/044-76034-038.dmg    # b4908cde...70414f
```

同时 `out/ipsw/21H461/*.json` 应记录 `component/member/size/sha256/source`；pin 不符时提取阶段就会失败
（见 §5），所以“能产出镜像”本身已隐含两 pin 校验通过。

### 3.5 分区容量核对

```bash
python3 -c "print((0x1D143FA - 0x2A1600 + 1) * 4096)"     # 113596411904
# 设备端（Windows 侧，不刷写）：
#   fastboot getvar partition-size:userdata                 # 期望 0x1A72DFB000
```

本机 2026-11-19 的 `fastboot getvar all` 采集（`/mnt/f/liuqin_ios/_dl/fastboot-getvar-all.txt`）
显示 `partition-size:userdata: 0x1A72DFB000` = 113,596,411,904 B，与 board 计算完全一致，且
`partition-type:userdata:f2fs`。若恢复 stock GPT 或重新分区，先改 `board.toml [userdata].last_lba`。

### 3.6 root UUID（下游步骤；不属于 userdata 产物）

`userdata-root.json` 里**没有**卷 UUID。内核用 `/chosen/root-matching` 按 UUID 选根卷，UUID 来自
OS 窗口里 APFS 根卷（macOS 可用 `diskutil apfs list` 读；Linux 需要第三方 APFS 工具或从同 IPSW 的
已知值抄）。当前板级值：

- `board.toml:257 [payload.adt].root_match_volume_uuid = "04769D26-2CA0-4825-B443-E2E5F88300F2"`
  （注释：来自同一 IPSW 的 OS 卷）；
- `board.toml:280 [root].os_volume_uuid = "18AB5557-..."` 仍是 bring-up 占位注释里的
  “Replace with the UUID that unixnu build userdata reports”——但 userdata 报告实际不含 UUID，
  这句注释已过期；**不要**为了“让注释成立”去改 userdata 报告。刷写前确认这两个值来自同一
  OS 镜像即可。

### 3.7 刷写（手工执行，本文不做）

```bash
# WSL 内：把镜像复制到 Windows 可见目录（8.7 GiB，注意 NTFS 空间与耗时）
cp /root/work/UniXNU-new/out/liuqin/userdata/userdata-root.img /mnt/f/liuqin_ios/
```

```powershell
# Windows PowerShell（fastboot 需要直接看到 USB；WSL 默认看不到设备）
fastboot devices
fastboot getvar partition-size:userdata     # 再核对一次 0x1A72DFB000
fastboot flash userdata F:\liuqin_ios\userdata-root.img
```

- 报告打印的 `fastboot flash userdata /root/work/...` 是 WSL 路径，直接贴到 Windows 无效，需换成
  `F:\...`（或 `\\wsl.localhost\Ubuntu24\root\...`，不建议）。
- 刷写会覆盖整个 userdata 分区（含已有数据），先备份；本文只读分析，未执行。
- 若用 `usbipd-win` 把设备转发进 WSL，也可在 WSL 内跑 fastboot，但本项目现有流程是 Windows 侧 fastboot。

---

## 4. Linux 与 macOS 的差异

`userdata.py` 用 `platform.system() == 'Darwin'` 分叉，两条路径应产生**逐字节相同**的镜像。

| 项 | Linux（本机 WSL2 Ubuntu 24.04） | macOS |
| --- | --- | --- |
| 磁盘镜像大小 | `udif.Image(dmg).size`：有 `koly` 用 trailer sectors×512，否则文件长度 | `hdiutil imageinfo -plist` → `Size Information.Total Bytes` |
| 读取原始磁盘 | `udif.py` 用户态解 `blkx`/`mish` 块：raw/zero/ignore/zlib/bzip2/LZFSE | `hdiutil attach -nomount -readonly -noverify -noautofsck`，按 8 MiB 读 `/dev/rdiskN`，最后 `hdiutil detach` |
| LZFSE | 需要 `lzfse` 可执行文件；`util.lzfse()` 先找 `$UNIXNU_TOOLCHAIN/bin/lzfse`（默认 `/opt/unixnu-linux`），否则 PATH | 不需要；`hdiutil` 内建解码 |
| AEA 加密镜像 | **不支持**：`ipsw.py` 直接 `raise Error('... is AEA encrypted; decrypting it needs macOS /usr/bin/aea')` | 支持：`ipsw fw aea --key` 取 key + `/usr/bin/aea decrypt` |
| 挂载/权限 | 不需要 root 设备权限，纯用户态 | 需要 `hdiutil`（无需 sudo 通常可 attach） |
| 依赖 | Python + `lzfse` | Xcode/Command Line Tools 自带工具 |
| 本 IPSW | **可用**（两成员非 AEA；OS 为 LZFSE UDIF，SystemOS 为裸 APFS） | 可用 |
| 验证 | `python3 scripts/unixnu/test_udif.py`（6 个离线用例） | 无同等自测，靠 `hdiutil imageinfo` |
| 速度 | 受 lzfse 子进程数与磁盘影响；本机解码 374 MB/s 上限 | 通常更快（内核态读） |

补充：

- `udif.py` 对块类型严格：只接受 `ZERO_FILL(0)/RAW(1)/IGNORE(2)/ZLIB(0x80000005)/BZIP2(0x80000006)/LZFSE(0x80000007)`；
  任何未覆盖的空洞或不支持的块类型会直接报错（见 §5），不会静默产出坏镜像。
- 若在某台机器上 `UNIXNU_TOOLCHAIN` 指向别的前缀，确保该前缀下有 `bin/lzfse`，否则报
  “lzfse not found on PATH (run sudo scripts/linux/setup-toolchain.py)”。
- **一致性验证建议**：同一 IPSW 在 macOS 与 Linux 各构建一次，比较 `userdata-root.json` 的
  `image_sha256`/`windows[].sha256`；文档（`docs/concepts/userdata.md:36`）声称两者相同，但本设备尚未
  macOS 实测。

---

## 5. 常见错误与处理

| 现象（原样报错） | 原因 | 处理 |
| --- | --- | --- |
| `unixnu: IPSW not found: /tmp/nope.ipsw` | `--ipsw` 本地路径不存在/写错 | 用 WSL 路径（`/mnt/f/...`），别用 `F:\...`；或先 §2.2 下载 |
| `unixnu: no IPSW: pass --ipsw PATH_OR_URL (or UNIXNU_IPSW), or set url in board.toml [ipsw]` | board 无 `[ipsw].url` 且未给参数 | 传 `--ipsw`/`UNIXNU_IPSW`，或恢复 board `[ipsw]` |
| `unixnu: this IPSW is 26.x (...) for ...; the board expects 17.7.11 (21H461) for iPad7,1` | IPSW 版本/build/机型不符 | 换 21H461 iPad7,1 的 IPSW；不要改 board 去迁就 |
| `unixnu: no Erase build identity for j120ap in this IPSW` | IPSW 不是 iPad7,x，或 chip/board 不匹配 | 用 iPad7,1/2/3/4 的 restore IPSW |
| `unixnu: component OS is not in this IPSW` | 机型不对导致 identity 里没有该组件 | 同上 |
| `unixnu: ... is AEA encrypted; decrypting it needs macOS /usr/bin/aea` | 该 OS 版本（如 iPadOS 26）成员是 `.aea` | 换 macOS 构建，或自行在 Linux 预解密（本仓库当前不支持）；21H461 不会遇到 |
| `unixnu: lzfse not found on PATH (run sudo scripts/linux/setup-toolchain.py)` | `/opt/unixnu-linux/bin/lzfse` 不存在且 PATH 无 lzfse | 跑 `sudo scripts/linux/setup-toolchain.py`，或装 lzfse 并设 `UNIXNU_TOOLCHAIN` |
| `unixnu: 044-xxxx.dmg differs from the pinned sha256 of board.toml [ipsw.sha256]: ...` | IPSW 不是 board pin 对应的那一版/文件损坏 | 核对 URL/版本；用正确 IPSW 重新提取；确认 pin 来源后再改 board（本次不要改） |
| `unixnu: ...: no chunk covers bytes N..M` / `...: the chunks cover X bytes, the trailer Y` | dmg 截断/损坏（中断的提取、坏 IPSW） | 删除 `out/ipsw/21H461/044-*.dmg*` 后重提取；校验整包大小 |
| `unixnu: ...: unsupported chunk type 0x... at byte N` | dmg 用了 `udif.py` 不支持的压缩块 | 用 macOS `hdiutil` 路径构建（或给 `udif.py` 加解码） |
| `unixnu: incomplete extraction of ...` / `short read` | 网络中断、磁盘满、zip 损坏 | 重跑；改用整包 IPSW；`df -h` 确认 ≥25 GiB |
| `unixnu: the image is larger than the userdata partition` | `last_lba/first_lba` 与真实 GPT 不符 | 用设备 `fastboot getvar partition-size:userdata`（或设备内 `sgdisk -p`）重算，改 board（按实际刷写布局） |
| `unixnu: ... does not support range requests` | 远程源不支持 HTTP Range（代理/CDN 变化） | 改为整包下载后 `--ipsw` 本地文件 |
| dry-run 通过但正式构建报 pin 错 | dry-run 不校验 pin（§2.4） | 正常现象；按上面 pin 行处理 |
| 构建几分钟无输出、out 里只有 `*.partial` | 提取 5.16 GB 成员中，代码只在开始时打一行 | 等；看 `ls -la out/ipsw/21H461/`、网络流量；不要 Ctrl-C（会留 partial） |
| `urllib.error.URLError`/超时 | 直连 Apple CDN 被限/需要代理 | `export http_proxy=... https_proxy=...`；或整包 curl 下载 |
| `python3: can't open file '/root/work/.../unixnu'` | 当前目录不对（`./unixnu` 是相对路径） | 先 `cd /root/work/UniXNU-new` |
| `No space left on device` | 峰值约 23 GiB，`/root/work` 或 `/tmp` 所在盘满 | 清旧产物；确认 `df -h /root/work` |

---

## 6. `out/ipsw/21H461/` 现状、`feeds.toml` 与 pin 的关系

### 6.1 现有缓存（`du -sh` = 45 MiB，7 个文件）

| 文件 | 大小（B） | 来源 | 说明 |
| --- | --- | --- | --- |
| `kernelcache.decompressed` | 46,415,872 | `KernelCache` | sha256 = `f18b4df865...69de7`，**与 `[kernel.prelink].kernelcache_sha256` 一致（已核对）** |
| `DeviceTree.j120ap.im4p` | 35,260 | `DeviceTree` | 与远程 manifest 大小一致 |
| `DeviceTree.j120ap.im4p.json` | 208 | 记录 | component/member/size/sha256/source |
| `044-76040-038.dmg.trustcache` | 59,519 | `StaticTrustCache` | 与远程 manifest 大小一致 |
| `044-76040-038.dmg.trustcache.json` | 218 | 记录 | — |
| `044-76034-038.dmg.trustcache` | 1,871 | `Cryptex1,SystemTrustCache` | 与远程 manifest 大小一致 |
| `044-76034-038.dmg.trustcache.json` | 210 | 记录 | — |

结论：**小件（`[ipsw].fetch` 的 4 个组件）已齐**，且 `KernelCache` 的解压结果与 board pin 一致；
`DeviceTree`/两个 trustcache 没有 pin（只按大小/记录校验），大小与远程 manifest 相符。

### 6.2 缺什么、多大

| 缺的文件 | 成员大小 | zip 传输大小 | 用途 |
| --- | --- | --- | --- |
| `044-76040-038.dmg` | 5,157,406,069 B | 5,157,406,069 B（stored） | OS 窗口 |
| `044-76034-038.dmg` | 3,303,014,400 B | 1,321,525,120 B（deflate） | SystemOS 窗口 |
| 合计 | 8,460,420,469 B（7.879 GiB 落盘） | **6,478,931,189 B（6.034 GiB 网络）** | — |

两个文件各会带一个 `*.dmg.json` 记录（提取时生成）。它们正是 `[ipsw.sha256]` pin 的两个对象：

```toml
[ipsw.sha256]
"044-76040-038.dmg" = "3f99b859c9728379110b66657c7b15285bcb1925ad8b5726542e2cdcf8b1028d"
"044-76034-038.dmg" = "b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a18b1600c96a3fd70414f"
```

### 6.3 与 `feeds.toml` / `unixnu.lock` 的关系

- `feeds.toml` 只描述 **git/外部源码**（xnu、libplatform、libdispatch、AvailabilityVersions、edk2、
  libpthread、corecrypto-2023、IOStorageFamily），**没有任何 IPSW 条目**；`unixnu.lock` 是这些 git
  source 的 commit pin。二者与 userdata 构建无关（userdata 不调用 `feeds`/`prepare`）。
- IPSW 的 URL/身份/完整性 pin 全部在 `device-new/board.toml`：
  - `[ipsw]` 第 14~22 行：url、product、version、build、device_class、chip_id、board_id、`fetch` 小件列表；
  - `[ipsw.sha256]` 第 24~26 行：两个 dmg 成员 pin；
  - `[userdata]` 第 30~44 行：`first_lba/last_lba/lba_bytes/windows`（几何 + 窗口选择）。
- 解压缓存目录由 `ctx.out_root / 'ipsw' / board[ipsw].build` 决定，即 `out/ipsw/21H461/`；
  **只按 build 字符串分目录**，不按 IPSW 哈希/机型分。若以后同一 build 换 IPSW，靠 pin 发现不一致。
- `board.toml [ipsw].fetch` 决定 `unixnu ipsw fetch`（无参数）取哪些小件；`[userdata].windows`
  不属于 fetch 列表，所以改 board 的 windows 不会自动让 fetch 预取它们。

---

## 7. 未验证项与风险

1. **本次没有下载两个 dmg、没有真正构建**：因此两个 `[ipsw.sha256]` pin 尚未实机校验；产物大小
   9,382,658,048 B 来自对远程 UDIF trailer / APFS 头的只读探测（§2.6、附录 A），不是本地产出。
2. **SystemOS 的 `koly` 缺失**无法只靠 Range 证明（zip 内是 deflate，取尾部要全解压），但解压出的
   前 64 KiB 是 APFS 容器头（`NXSB`、block size 4096、`nx_block_count` 正好等于文件大小/4096），
   足以判定为裸 APFS 镜像；即便如此，正式构建后仍建议按 §3.4 复核 `udif.Image().size`。
3. **耗时/吞吐**来自本机微基准（32 MiB Range 测速、128 MiB lzfse、1 GiB dd），代理、CDN 节点、
   磁盘状态都会影响；首次全流程 6~9 分钟是估算。
4. **AEA**：本 IPSW 无 AEA；如果以后换 iPadOS 26 的 IPSW，Linux 路径会直接报错，必须 macOS。
5. **未上机**：`userdata-root.json` 固定写 `hardware_tested: false`；刷写后根卷 UUID 能否匹配、
   分区 scheme 是否发布窗口，仍未验证（属 boot 阶段）。
6. **GPT 值**：`[userdata]` 来自 d21e714 提交，当前设备 fastboot 读数与之相符；但 board.toml 工作树
   还有其它未提交改动（与 userdata 无关）。恢复 stock GPT 或重新分区后必须同步 `last_lba`。
7. `out/ipsw/21H461/` 缓存是“可再生成”的（`.gitignore` 忽略 `out/`、`*.dmg`、`*.ipsw`），
   不入库；跨机器复现请保留整包 IPSW 或重新下载。

---

## 8. 参考（代码/文档位置）

| 内容 | 位置 |
| --- | --- |
| userdata 步骤主体、dry-run、布局、写镜像、报告 | `UniXNU-new/scripts/unixnu/userdata.py:85-185`（capacity 93；windows 95-111；dry-run 106-111；布局 112-117；`--object` 118-133；上限 131-133；写入 142-176；报告 177-182） |
| IPSW 本地/远程打开、identity 校验、提取+pin、AEA、kernelcache | `UniXNU-new/scripts/unixnu/ipsw.py:89-278`（spec_of 89；Ipsw 97-118；identity 119-128；extract 141-168；disk_image 173-178；decrypt_aea 181-214） |
| Linux UDIF 解码（blkx/mish、块类型） | `UniXNU-new/scripts/unixnu/udif.py:26-95` |
| Context/路径/state（`-D` 记忆、out 布局） | `UniXNU-new/scripts/unixnu/config.py:110-209` |
| 命令行参数（`--ipsw/--dry-run/--object/-D`） | `UniXNU-new/scripts/unixnu/cli.py:16-46`；userdata 快捷子命令 61-63；cmd_build 146-173 |
| 板级参数 | `device-new/board.toml:14-44`（`[ipsw]`、`[ipsw.sha256]`、`[userdata]`）；kernelcache pin 71；root UUID 257/280 |
| 设计说明 | `UniXNU-new/docs/concepts/userdata.md`、`UniXNU-new/docs/guide/userdata.md`、`UniXNU-new/docs/building.md:300-304` |
| 离线自测 | `UniXNU-new/scripts/unixnu/test_udif.py`（`python3 scripts/unixnu/test_udif.py`） |
| Linux 工具链 | `UniXNU-new/docs/guide/requirements.md`（Linux 节）、`scripts/linux/setup-toolchain.py` |

---

## 附录 A：本次只读分析的实测证据

时间：2026-11-19 12:35~12:46（Asia/Shanghai）；全部命令只读（仅 `/tmp` 临时脚本 + `out/state.json` 由
Context 自动记忆设备路径，未改仓库代码/配置）。

1. **环境**：WSL2 Ubuntu 24.04.5，Python 3.12.3，20 vCPU，13 GiB RAM，`/root/work` 948 GiB 空闲；
   `/opt/unixnu-linux/bin/lzfse` 存在；`curl`/`wget` 存在；`command -v lzfse` 为空（正常，靠固定前缀找到）。
2. **CDN 探测**：`HEAD` 200、`Content-Length: 6835124840`；`Range: bytes=0-0` 返回 206；32 MiB Range 用
   1.31 s（25.6 MB/s）。
3. **dry-run**（带/不带 proxy）：见 §2.4；exit 0；无 proxy 0.916 s，带 proxy 1.022 s；`out/liuqin/userdata/`
   不存在（未写盘）。
4. **dry-run 等价网络计数**：2 次 Range 请求、15,186,536 B。
5. **IPSW 组件**：`python3 ./unixnu ipsw info -D /root/work/device-new` 输出 34 个组件；OS=5,157,406,069 B、
   Cryptex1,SystemOS=3,303,014,400 B（zip 压缩 1,321,525,120 B）、KernelCache=16,238,580 B 等（§1.3）。
6. **OS UDIF 探测**：zip local header method=0、data_offset=1,591,988,020；尾部 `koly` 的 sectors=11,870,275
   → raw 6,077,580,800；plist（234,240 B）解析出 6 张 blkx，块类型统计 LZFSE 3,415 / RAW 396 / IGNORE 3。
7. **SystemOS 探测**：解压前 64 KiB：offset 0x20 `NXSB`、0x24 `0x1000`、0x28 `0x000C4E00`=806,400 →
   806,400×4096 = 3,303,014,400 = 文件大小；非 GPT/AEA/UDIF 头。
8. **lzfse**：stdin→stdout 解码 OK；128 MiB 随机数据 encode 31 MB/s、decode 374 MB/s（`cmp` 一致）。
9. **盘/CPU**：1 GiB `dd conv=fdatasync` = 0.934 s（1.1 GB/s）。
10. **udif 自测**：`python3 scripts/unixnu/test_udif.py` → `Ran 6 tests ... OK`（0.009 s）。
11. **缓存核对**：`sha256sum kernelcache.decompressed` = `f18b4df865...69de7` = board kernelcache pin；
    DeviceTree/trustcache 大小与远程 manifest 一致；`du -sh out/ipsw/21H461` = 45 MiB。
12. **GPT 交叉核对**：`/mnt/f/liuqin_ios/_dl/fastboot-getvar-all.txt`（2026-11-19 12:41）中
    `partition-size:userdata: 0x1A72DFB000`（=113,596,411,904 B，27,733,499×4096）与 board 计算一致；
    `partition-type:userdata:f2fs`。
