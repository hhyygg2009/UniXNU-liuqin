# liuqin userdata 落地计划（固化 A/B 两方案，T1 前必读）

适用设备：小米平板 6 Pro（liuqin / SM8475，1 TB UFS，4096 字节 LBA）。
相关代码：`drivers/ShengUfs/RootLayout.h`（SHNGROOT 目录解析）、
`drivers/ShengUfs/NativeUfs.cpp`（probe 15 GPT 转储）、
`scripts/install-liuqin-ufs-data.py`（安装/校验/布局工具）、
`patches/bringup-2026-11-19/ufs-partition-arg.diff`（本次驱动改动）。

> 本文所有“写设备”的命令都标了 **[T1]**。T1 之前只能做备份、只读探测、
> `fastboot boot` 内存启动和本地镜像构建。
>
> **2026-11-19 实测更新（覆盖下面第 2 节的历史记录）**：设备是 128 GB，`userdata`
> 实测 113,596,411,904 B（105.79 GiB，与 `board.toml last_lba=0x1D143FA` 一致）；
> `linux_root` / `linux_home` 已不存在，磁盘尾部没有空闲空间；`/data` 是 FBE 加密的
> f2fs（69 GiB 已用 / 37 GiB 可用）；iOS 根镜像 9,382,658,048 B（8.738 GiB）。
> **当前采用方案 C（第 9 节）：不改 GPT，把 f2fs 缩到 94 GiB，把镜像写进 userdata
> 分区内 94 GiB 处，驱动用 `native-ufs-root-offset=100931731456` 读取。**
> 方案 A/B（第 7/8 节）只在以后重新分区时才有意义，当前不可用。

## 0. 铁律

1. **T1 之前零写入。** T1 定义为：第一次对设备分区/分区表的写操作
   （`fastboot flash`、`adb shell dd of=...`、`sgdisk` 改 GPT、`fastboot -w` 都算）。
   在本文件第 7 节（方案 A）/第 8 节（方案 B）/第 9 节（方案 C）的 go/no-go 之前，
   不允许出现任何一条。
2. **先做 Android 整机备份，再谈 T1。** 没有备份就不存在回滚，方案 A/B 都一样。
3. **只信设备实测的 GPT。** `board.toml` 和 `docs/liuqin-hardware.md` 里的 LBA 是历史
   记录，且两者互相不一致（见第 2 节）。T1 前必须用 `native-ufs-probe=15`
   （只读）重新读一遍 GPT，并把它存成主机的 `sgdisk-p.txt` / `sda-head.bin`。
4. **先 `fastboot boot` 只读验证，绝不先刷 boot 分区。** 本次 bring-up 一贯用
   `fastboot boot boot.img` 内存运行；写分区是另一件事。
5. 安装脚本默认只打印计划；**只有同时给 `--apply --ack-backup` 才会写设备**。

## 1. 名词

| 名词 | 含义 |
| --- | --- |
| SHNGROOT 镜像 | `unixnu build userdata` 生成、可直接写进目标分区的镜像：偏移 0 是 4 KiB 目录块（magic `SHNGROOT`），窗口（OS / SystemOS…）各按 1 MiB 对齐放在后面。 |
| root 分区 | 驱动去读 SHNGROOT 目录的 GPT 分区。默认 `userdata`，可用内核启动参数 `native-ufs-root-partition=<名字>` 改成别的（本次新增）。 |
| 方案 A | 直接复用设备上已有的 `linux_root`（2026-10 记录：LBA 27924480..36313087，32 GiB）当 root 分区。**2026-11-19 实测该分区已不存在，当前不可用。** |
| 方案 B | 从空闲空间新建一个分区（建议命名 `linux_root` / `unixnu`），再把它当 root 分区。**磁盘尾部已无空闲空间，当前不可用。** |
| 分区内偏移（方案 C） | 不改 GPT，把镜像写到 root 分区内部某个 1 MiB 对齐的偏移处（当前：userdata 内 94 GiB 处，f2fs 缩容后的尾部）。驱动用 `native-ufs-root-offset` 找清单。 |
| T1 | 第一次写设备分区的动作，见第 0 节。 |
| guard | 追加安装时，已有目录末尾到新窗口之间的 16 MiB 保护带（脚本强制）。 |

## 2. 设备现状（2026-11-19 实测，覆盖旧记录）

| 项 | 实测值 |
| --- | --- |
| 设备 | 小米平板 6 Pro / liuqin / SM8475，**128 GB** UFS |
| `userdata` 大小 | `fastboot getvar all` 的 `partition-size:userdata: 0x1A72DFB000` = **113,596,411,904 B = 105.7949 GiB**（4096 字节 LBA，LBA 2758144..30491642，与 `board.toml last_lba=0x1D143FA` 一致） |
| `linux_root` / `linux_home` | **已不存在**（`getvar` 报 Variable Not found）；磁盘总分区和 ≈118.55 GiB |
| 磁盘尾部空闲 | **没有**（分区和 ≈ 磁盘容量，尾部只有 GPT 备份） |
| Android `/data` | **106G 总 / 69G 已用 / 37G 可用**，FBE 加密的 f2fs（Android 显示的 G ≈ GiB） |
| iOS 根镜像（SHNGROOT） | **9,382,658,048 B = 8.738 GiB**（`unixnu build userdata` 产物实测） |

旧记录的处理：`board.toml` 第 28-40 行注释与 `docs/liuqin-hardware.md` E 节里的
“userdata 缩到 96 GiB + linux_root/linux_home”是 2026-10 备份时的布局，**已被实测推翻**；
`board.toml` 的 `first_lba/last_lba` 数值与实测一致（注释过时，数值正确）。
因磁盘既没有空闲分区、也没有可复用的独立分区，唯一能放 8.738 GiB 镜像的地方是
**userdata 分区内部、Android f2fs 之外的尾部**——即方案 C。

## 3. T1 前置：Android 整机备份（只读）

目标：任何一步写坏（GPT、userdata、boot），都能把设备恢复成现在这样。至少保存：

1. **GPT 头尾**（主 GPT 第 0..33 个 4096 字节 LBA，备份 GPT 在磁盘末尾，LBA 数看
   `sgdisk -p` 的 "last usable sector"；通常再取最后 34 个 LBA）。
2. **boot_a / boot_b / vendor_boot / dtbo / vbmeta / persist**（persist 里面是
   传感器/指纹校准，丢了很麻烦）。
3. **userdata 里不想丢的数据**（照片/文档/微信等；105 GiB 全量镜像可选）。
4. **`sgdisk -p /dev/block/sda` 文本**和上面各分区的 sha256。

推荐在 **recovery/root adb** 下做（只读命令，不算 T1；但 `dd` 的 `of=` 必须是主机文件，
不要写成设备分区）：

```sh
# 主机（WSL）侧，把设备内容拉回本地；exec-out 是二进制安全的
adb -s <serial> get-state                       # recovery 或 device
adb -s <serial> exec-out dd if=/dev/block/sda bs=4096 count=34 > sda-head.bin
adb -s <serial> shell 'sgdisk -p /dev/block/sda' > sgdisk-p.txt 2>&1 || true
# boot/vendor_boot/persist 等（用 by-name 找实际路径）
adb -s <serial> exec-out dd if=/dev/block/by-name/boot_a bs=1M > boot_a.img
adb -s <serial> exec-out dd if=/dev/block/by-name/vendor_boot bs=1M > vendor_boot.img
adb -s <serial> exec-out dd if=/dev/block/by-name/persist bs=1M > persist.img
sha256sum sda-head.bin sgdisk-p.txt boot_a.img vendor_boot.img persist.img
```

如果 recovery 里没有 `sgdisk`，用 **probe=15** 的 GPT 转储当文本记录（见第 5 节），
再用 `--gpt-dump` 喂给安装脚本；`sda-head.bin` 可用上面的 exec-out 直接拿。

> 备份文件放三份（本机 + 另一台电脑/移动硬盘 + 云）。T1 前在群里贴出
> `sda-head.bin` / `sgdisk-p.txt` 的 sha256。

## 4. 构建 SHNGROOT 镜像（不碰设备）

1. 先用第 5 节的 probe=15 拿到**实测** `userdata` 区间；如果和 `board.toml [userdata]`
   不一致，改 `board.toml` 的 `first_lba/last_lba`（只改这两个值）并提交。
2. 在 UniXNU-new 构建树跑：

   ```sh
   ./unixnu build userdata --dry-run     # 只打印窗口来源和大小
   ./unixnu build userdata               # 产出 out/.../userdata/userdata-root.img + .json
   ```

   `userdata.json` 里要核对：`image_bytes`（本机实测应为 **9,382,658,048**）、每个
   window 的 `sha256`、`userdata_bytes`（应等于 (last_lba-first_lba+1)*4096 =
   113,596,411,904）。方案 C 只把镜像写到分区尾部，不改变镜像的构建方式/容量。
3. 镜像必须能通过安装脚本的校验：

   ```sh
   ./scripts/install-liuqin-ufs-data.py \
       --image out/liuqin/.../userdata/userdata-root.img \
       --partition userdata \
       --gpt-dump ~/liuqin-gpt/sgdisk-p.txt        # 纯离线计划
   ```

   脚本会逐个窗口算 sha256；任何不符直接拒绝。

## 5. 驱动准备（本仓库已完成，需要重新出 boot.img）

本次已改：

* `drivers/ShengUfs/RootLayout.h`：root 分区名优先读启动参数
  `native-ufs-root-partition`，缺省仍是 `userdata`；名字按 GPT 的 UTF-16LE 全名精确匹配，
  非法/超长名字回退并打 IOLog。
* `drivers/ShengUfs/NativeUfs.cpp`：`native-ufs-probe=15` 只读 GPT 转储，遍历 LUN0 主
  GPT，把最多 40 个非空条目的 `name` 和 `first-last` LBA 打到控制台；不写任何东西。
* `RootLayout.h`：`native-ufs-root-offset=<bytes>`（十进制或 `0x` 十六进制，必须 4096
  对齐；非法值忽略并回退 0 + IOLog）。root 清单基准 LBA = `userdataFirst +
  offset/4096`，清单块和所有 window/object 都从该基准读；`userdataFirst/userdataLast`
  仍是 GPT 分区边界。可用字节 = `(userdataLast + 1 - rootBaseLba) * 4096`。
* `NativeUfs.cpp`：probe=15 的 GPT 转储末尾多打一行
  `root base lba=<decimal> offset=<decimal>`；probe 10/11 的报告也带 root base/offset。
* 补丁（按顺序 `git apply` 于 device-new 根目录）：
  1. `patches/bringup-2026-11-19/ufs-partition-arg.diff`（root 分区名可配置 + probe 15）
  2. `patches/bringup-2026-11-19/ufs-root-offset.diff`（本次新增：分区内偏移；依赖 1）
  两个补丁均已验证可应用到 `drivers/ShengUfs` 当前 HEAD / v40 WIP 之上。

使用方式：

1. 方案 C 在 `board.toml [payload] kernel_args` 加
   `"native-ufs-root-offset=100931731456"`（94 GiB；分区名保持默认 userdata，
   不需要 `native-ufs-root-partition`）。注意 cmdline 有 1000 字节上限，加之前先看
   当前长度；想回滚就删掉这行。
2. 重建 payload / boot.img（沿用现有流程，`fastboot boot` 只读）：
   `./unixnu build payload ...` → `wrap-payload-mbr.py` → `unixnu bootimg ...`。
3. 先启动 probe 镜像（只读）：

   `fastboot boot` 换成 `native-ufs-probe=15` 的 boot.img（改 `native-ufs-probe=15`，
   临时保留 `native-ufs-root=1`/`native-ufs-diagnostics=1` 也行）。

   面板上应看到（`lqPanic` 口径）：

   ```
   NATIVE_UFS gpt dump LUN0
   hdr=1 backup=... usable=... elba=2 cnt=... crc ok/ok
   35:userdata 2a1600-... 105794M  36:linux_root ...
   ...
   listed=N/...
   ```

   > 注意：`lqPanic()` 本身只格式化 512 字节，所以完整列表由 `liuqin_debug_puts()` 先打到
   > 同一个控制台镜像（含 USB ring），最后一行才是 panic 摘要。拍照/抓串口都应包含
   > `listed=` 那一行。

4. 用这张实测表确认方案 C 的偏移参数（第 9 节），不再需要判断 A/B。

## 6. 安装脚本行为（先读，再决定跑哪条）

`scripts/install-liuqin-ufs-data.py` 的两种布局：

* **fresh**：目标分区没有可用的 SHNGROOT 目录（新分区/已擦除）→ 镜像**原样**写到分区
  偏移 0；manifest 在 0，第一个窗口在镜像自己的 1 MiB 偏移。等价于
  `fastboot flash <partition> <image>`，脚本会把这条 fastboot 命令打印出来（只打印，
  永远不执行 fastboot）。
* **append**：目标分区已经有 SHNGROOT 目录（同一分区里升级 iOS 根）→ 新窗口整体放到
  `已有数据末尾 + 16 MiB`、向上 1 MiB 对齐（即 `ceil(occupied+16MiB,1MiB)`），
  同名窗口替换、别的条目保留；**最后才写 4 KiB 目录（commit）**，旧的窗口数据还在盘上，
  回滚只需把 `manifest-before.bin` 写回块 0。fastboot 无法表达偏移，append 模式脚本会
  拒绝打印 fastboot 命令。

分区内偏移（方案 C）：`--partition-offset <bytes>`（默认 0，必须 1 MiB 对齐）把镜像写到
“分区起始 + 偏移”处；计划里会打印
`dd if=<image> of=/dev/block/by-name/<partition> bs=1M seek=<偏移 MiB> conv=fsync`
以及写入后驱动需要的 `native-ufs-root-offset=<bytes>`。偏移 + 镜像超过分区时脚本在
规划阶段就拒绝；fastboot 无法表达偏移，脚本会明确拒绝打印 fastboot 命令
（偏移为 0 的 fresh 布局仍打印等价 fastboot 命令）。

安全门：

* 默认只打印计划；`--apply` 才写，且必须同时给 `--ack-backup`；
* `--apply` 前会核对：live 分区大小必须等于计划几何（防止拿旧 GPT 备份写错地方）、
  分区不能挂载、不能有 dm holders；
* 设备上若已有 SHNGROOT 目录而计划是 fresh，脚本直接拒绝（要先用 `--adb` 重新计划成
  append，或明确 `--force-in-place`）；
* `--apply` 结束会回读块 0 并比对 SHA-256；窗口数据不整读回（写前已整镜像校验 sha256）。

常用命令：

```sh
# 纯离线计划（读 sgdisk -p 文本，不碰设备）——方案 C 的真实参数
./scripts/install-liuqin-ufs-data.py --image userdata-root.img \
    --partition userdata --gpt-dump sgdisk-p.txt \
    --partition-offset 100931731456        # = 94 GiB，f2fs 缩容后的尾部起点

# 读设备当前块 0 做 append 计划（只读 adb；需要 recovery/root adb）
./scripts/install-liuqin-ufs-data.py --image userdata-root.img \
    --partition linux_root --gpt-dump sgdisk-p.txt \
    --adb 'D:\PhoneTools\tool\adb.exe' --serial <serial>

# 自测（临时目录伪造镜像/GPT，不碰设备）
./scripts/install-liuqin-ufs-data.py --self-test
```

T1 执行（示例子设备名/路径自己换；`--method push` 按 1 GiB 分块 push 到设备
`/tmp` 再 dd，只需设备 tmpfs 放得下 1 GiB；`--method stream` 走 adb stdin 不落临时文件，
要求 adb ≥ 1.0.41）：

```sh
./scripts/install-liuqin-ufs-data.py --image userdata-root.img \
    --partition linux_root --gpt-dump sgdisk-p.txt \
    --adb 'D:\PhoneTools\tool\adb.exe' --serial <serial> \
    --method push --chunk-mib 1024 \
    --apply --ack-backup --output-dir ./install-logs/liuqin-$(date +%Y%m%d-%H%M)
```

产物目录里有 `plan.json`、`manifest-before.bin`（append 时）、`manifest-new.bin`。
**把产物目录和备份 sha256 一起存档。**

## 7. 方案 A：复用已有 linux_root（当前不可用：分区已不存在）

前提（全部满足才走）：

1. probe=15 实测 GPT 里确实有 `linux_root`，大小 ≥ 镜像 `image_bytes`（建议 ≥ 1.2 倍，
   给人留余量）；
2. 该分区不挂载、无 dm holders、里面没有你还要的数据（**它会被覆盖**，见风险）；
3. 已按第 3 节完成整机备份。

步骤：

1. probe=15 只读启动，记录 GPT 表；必要时更新 `board.toml [userdata]` 并提交。
2. 构建镜像（第 4 节）。
3. 离线计划：`--partition linux_root --gpt-dump sgdisk-p.txt`；确认模式是 fresh、
   无 WARNING、`image_bytes` 放得下。
4. 加 `native-ufs-root-partition=linux_root`，重建 boot.img（第 5 节）。
5. **go/no-go**：备份 sha256、plan.json、当前 GPT 文本都存档后，才允许 T1。
6. T1：跑第 6 节 `--apply --ack-backup` 命令。
7. `fastboot boot` 新的 boot.img（仍然只读内存运行）→ probe=10/11 验证（第 10 节）。
8. 验证通过后，是否把 boot.img 写到 boot 分区是另一个决定（属于 T1 之后，单独评审）。

风险与回滚：

* 风险：`linux_root` 里原来的 Linux root（Ubuntu）被覆盖；如果它不是 root 分区而是数据
  分区，数据丢失。**先确认内容可丢/已备份**。
* 风险：live GPT 若与备份不一致，脚本会在写入前用 live 分区大小对比计划几何并报错；
  报错就回第 4 步重出计划，不要 `--force` 绕过。
* 风险：`--empty`/`--force-in-place` 会跳过 append 保护带，不是崩溃安全的；本项目不需要。
* 回滚（fresh）：盘内没有旧副本，恢复 = 按第 3 节备份把分区内容写回（或重装 Ubuntu）。
  因为没有分区级旧副本，方案 A 的“回滚”实际是“重灌”，这也是优先方案 B 的原因之一。
* 回滚（append）：`adb shell 'dd of=/dev/block/by-name/<分区> bs=4096 count=1 conv=notrunc,fsync' < manifest-before.bin`，
  旧窗口数据仍在盘上。

## 8. 方案 B：从空闲空间新建分区（当前不可用：磁盘尾部无空闲空间）

前提：第 3 节备份完成；probe=15/`sgdisk -p` 已拿到实测 GPT。

1. **找空闲空间。** 比较相邻分区：`空闲 = 前一个分区 last_lba+1 .. 下一个分区 first_lba-1`；
   要求长度 ≥ `ceil(image_bytes,1MiB) + 余量`（建议 ≥ 8 GiB 余量给以后 grow），
   起点/长度按 1 MiB 对齐。把候选区间写进 `install-logs/`，含 `sgdisk -p` 文本。
   如果没有足够空闲：先考虑方案 A；或者先缩 `linux_home`（ext4 在线 shrink，风险高，
   单独评审），**不要**在没有备份时动 userdata 大小。
2. **备份 GPT**（第 3 节）→ 再做任何 GPT 写操作。
3. 在 recovery/TWRP 的 root shell 里用 sgdisk 新建（**[T1]**，这是第一次写 GPT）：

   ```sh
   sgdisk --verify /dev/block/sda                     # 先验证现有 GPT 干净
   sgdisk --new=38:<start>:<end> \
          --change-name=38:linux_root \
          --typecode=38:8300 /dev/block/sda
   sgdisk --verify /dev/block/sda
   sgdisk -p /dev/block/sda                            # 存档
   ```

   如果 recovery 没有 sgdisk：把 `sda-head.bin` 拷到主机，用主机的 `sgdisk` 对**一个
   文件副本**操作，再 dd 回主 GPT；注意**备份 GPT（磁盘末尾）也要同步**，否则 GPT 会被
   判冲突。这一步风险高，建议直接找带 sgdisk 的 TWRP/root 环境做。
4. 让内核重读 GPT：`blockdev --rereadpt /dev/block/sda`（userdata 被占用时可能失败）→
   失败就重启到 recovery，再 `ls /dev/block/by-name/` 确认新分区出现。
   不要 format，不要 mkfs——SHNGROOT 要的是裸分区。
5. 计划并 T1：同第 6 节，`--partition linux_root`（或你起的名字）。
6. 加 `native-ufs-root-partition=<新分区名>`，重建 boot.img，`fastboot boot` + probe=10/11
   验证（第 10 节）。
7. 验证通过后是否写 boot 分区，单独决定。

风险与回滚：

* 风险：GPT 改坏 → 设备可能无法进 Android/recovery。有第 3 节 GPT 头尾备份 = 可恢复
  （写回主 GPT + 备份 GPT，重启）。**没有 GPT 备份不要写 GPT。**
* 风险：Android 恢复出厂/OTA/重新分区会重写 GPT，可能把新分区“吃掉”；
  userdata 目前是 stock 大小（或历史布局），任何 Android 侧的 resize 都会碰 GPT。
* 风险：空闲区间实际被别的分区/元数据占用（例如备份 GPT 在磁盘末尾、`by-name` 之外的
  保护分区）；用 `sgdisk --verify` + `sgdisk -p` 全表核对，别只信旧记录。
* 回滚：写回 GPT 头尾备份并重启；新分区里没有需要保留的数据。

## 9. 方案 C：分区内尾部偏移（当前采用）

不改 GPT，把 `userdata` 的 f2fs 缩到 94 GiB，把 SHNGROOT 镜像写在分区内 94 GiB 处；
驱动加 `native-ufs-root-offset=100931731456` 从该处读清单。Go 的前提是第 3 节备份完成。

### 9.1 目标大小：推荐 94 GiB

分区 113,596,411,904 B（105.7949 GiB），/data 已用 69 GiB，镜像 9,382,658,048 B
（8.738 GiB）。f2fs 目标大小与尾部空间的关系（tail = 105.7949 − target）：

| f2fs 缩到 | 目标字节数 | target sectors（512 B） | 尾部空间 | 镜像后余量 | /data 空闲 |
| --- | --- | --- | --- | --- | --- |
| 93 GiB | 99,858,151,680 | 195,035,136 | 12.80 GiB | 4.06 GiB | 24 GiB |
| **94 GiB（推荐）** | **100,931,731,456** | **197,132,288** | **11.79 GiB** | **3.06 GiB** | **25 GiB** |
| 95 GiB | 102,005,473,280 | 199,229,440 | 10.80 GiB | 2.06 GiB | 26 GiB |
| 96 GiB | 103,079,215,104 | 201,326,592 | 9.80 GiB | 1.06 GiB | 27 GiB |

理由：94 GiB 给 /data 留 25 GiB 空闲（69 GiB 数据 + f2fs GC/overprovision 足够），
给镜像留 3.06 GiB（35% 余量，够镜像下次重建/对齐误差），尾部还剩空间不会被 f2fs 碰到。
93 GiB 起也能放下，但 /data 只剩 22-24 GiB，日常使用和 GC 会紧张；95/96 GiB 的镜像余量
只剩 1-2 GiB，镜像一变大就得再缩一次，不建议。

### 9.2 缩容前检查清单

- [ ] 第 3 节备份已完成（GPT 头尾、boot_a/boot_b/vendor_boot/persist、/data 里要保留的数据）；
      69 GiB 的 /data 建议至少把照片/文档/聊天记录导出，另存 `/data` 应用清单；
- [ ] 电量 > 50%，USB 线稳定，**缩容期间不能断电/拔线/锁屏重启**；
- [ ] TWRP/recovery 的 f2fs-tools ≥ 1.16 且支持缩容：`resize.f2fs -V` 看版本；
      不带 `-s` 试跑若输出 `Nothing to resize, now only supports resizing with safe resize flag`，
      说明该版本支持 safe shrink（本机 f2fs-tools 1.16.0 实测通过）；
- [ ] recovery 里 userdata 未挂载：`mount | grep -E 'userdata|/data'` 无输出；
- [ ] 记录缩容前证据：`blockdev --getsize64 /dev/block/by-name/userdata`、
      `fsck.f2fs -f` 干净、Android 侧 `df -h /data`、`resize.f2fs -V`；
- [ ] 主机侧已拿到 `sgdisk-p.txt` 与 `sda-head.bin`（第 3 节）。

### 9.3 缩容（T1 第一步：写 userdata 的 f2fs）

```sh
adb -s <serial> shell 'umount /data || true'
# 94 GiB = 94*2^30 B = 197132288 个 512 B sector；-s = safe resize（缩容必须带）
adb -s <serial> shell 'resize.f2fs -s -t 197132288 /dev/block/by-name/userdata'
adb -s <serial> shell 'fsck.f2fs -f /dev/block/by-name/userdata'
```

要点：

* `-t` 的单位是 **512 字节 sector**：197132288 × 512 = 100,931,731,456 B = 94 GiB；
* `-s`（safe resize）是缩容路径的开关；不带 `-s` 的 resize.f2fs 不缩容（本项目
  f2fs-tools 1.16.0 实测：`-s -t` 缩小后 `fsck.f2fs -f` 干净；不带 `-s` 拒绝）；
* resize.f2fs 会把 69 GiB 数据搬出尾部（输出 `Try to do defragement: Done`），可能几十分钟；
  **中途断电 = /data 损坏**，这是整个方案风险最高的一步；
* 完成后分区大小仍是 113,596,411,904 B（`blockdev --getsize64` 不变，GPT 没动），
  只是 f2fs superblock 记录的总 sector 变小了。

验证缩容成功：

```sh
adb -s <serial> shell 'fsck.f2fs -f /dev/block/by-name/userdata' | grep 'total FS sectors'
# 期望: Info: total FS sectors = 197132288 (94 GB)
```

（或在 Android 里 mount 后 `df -h /data` 总容量 ≈ 94 GiB；首次挂载 Android 可能自动
`resize.f2fs` 把 fs 扩回分区大小——如果发生，说明 Android 不尊重缩容，需在写入镜像前
再确认一次 fs 大小，见 9.8 风险表。）

### 9.4 偏移与参数

* `--partition-offset` = 100931731456（94 GiB，1 MiB 对齐；十六进制 `0x1780000000`）；
* 驱动参数 `native-ufs-root-offset=100931731456`（同一个十进制字节数）；
* 根清单基准 LBA = userdata 起点 0x2A1600 + 100931731456/4096 = **0x1A21600
  (27399680)**；镜像结束于分区字节 110,314,389,504，尾部还剩 3,282,022,400 B（3.06 GiB）；
* 偏移必须 ≥ 新 f2fs 大小；94 GiB 方案偏移等于 f2fs 末尾（相邻、无 gap，不浪费空间）。

### 9.5 写入镜像（T1 第二步）

1. 构建镜像（第 4 节），核对 `image_bytes = 9,382,658,048`、窗口 sha256 全过。
2. 计划（离线，不碰设备）：

```sh
./scripts/install-liuqin-ufs-data.py     --image out/liuqin/.../userdata/userdata-root.img     --partition userdata --gpt-dump sgdisk-p.txt     --partition-offset 100931731456
```

   计划里应看到：`offset 94.00 GiB (96256 MiB, 1 MiB aligned)`、
   `root manifest base LBA 27399680`、`native-ufs-root-offset=100931731456`、
   `dd ... bs=1M seek=96256 conv=fsync`、`slack 3.06 GiB`，并且 fastboot 行被换成
   “fastboot cannot express --partition-offset”。
3. T1 写入，二选一：

```sh
# (a) 脚本 --apply（推荐：写前校验设备块、写完回读目录块 SHA-256）
./scripts/install-liuqin-ufs-data.py ... --partition-offset 100931731456     --apply --ack-backup --adb 'D:\PhoneTools\tool\adb.exe' --serial <serial>     --output-dir ./install-logs/liuqin-offset-$(date +%Y%m%d-%H%M)

# (b) TWRP/root 手工 dd（与计划打印的命令一致；写入前务必再核对 seek）
adb push userdata-root.img /tmp/userdata-root.img
adb shell 'dd if=/tmp/userdata-root.img of=/dev/block/by-name/userdata bs=1M seek=96256 conv=fsync'
adb shell sync
adb shell 'rm -f /tmp/userdata-root.img'
```

   (b) 不做写后校验；无论哪种方式，写完记录 `dd` 输出/脚本产物（plan.json、sha256）。

### 9.6 驱动参数与验证

`board.toml [payload] kernel_args` 加 `"native-ufs-root-offset=100931731456"`，
重建 payload/boot.img 后 `fastboot boot` 只读验证：

| probe | 期望 |
| --- | --- |
| probe=15 | GPT 转储末行 `root base lba=27399680 offset=100931731456` |
| probe=10 | `root base=1a21600 offset=100931731456`、`manifest status=0 windows=2` |
| probe=11 | `w0/w1 ... gpt=46454620 50415254`（EFI PART） |
| 正式启动 | IOLog `NativeUfs: root offset is 100931731456 bytes (24641536 blocks into userdata)` |

### 9.7 回滚

| 场景 | 操作 |
| --- | --- |
| 只撤 iOS 镜像 | f2fs 不读尾部，镜像留着也不影响 Android；需要空间就 `dd` 0 覆盖尾部 |
| 把容量还给 Android /data | `resize.f2fs -s /dev/block/by-name/userdata`（扩展到分区全大小；之后 f2fs 会覆盖尾部镜像，镜像作废）。或用 `-t $(blockdev --getsize64/512)` 精确指定 |
| 撤掉驱动偏移 | 从 kernel_args 删除 `native-ufs-root-offset`（或设 0），重建 boot.img，分区数据不受影响 |
| /data 本身损坏 | `fsck.f2fs -f`；不行就从第 3 节备份恢复（缩容不可逆，只能靠备份） |

### 9.8 风险

| 风险 | 后果 | 规避 |
| --- | --- | --- |
| 缩容中断电/断线 | /data 损坏，严重时只能 format（69 GiB 丢） | 先导出重要数据；电量/线材；缩容时不要锁屏/重启 |
| recovery 的 resize.f2fs 太老 | 缩容被拒绝（安全，不损坏） | 用 ≥ 1.16 的 TWRP；先 `resize.f2fs -V`，在不带 -s 时看提示；必要时从主机推一个静态/匹配 ABI 的 `resize.f2fs`/`fsck.f2fs` 到 `/tmp` 在设备上运行 |
| 目标选得太贴近 69 GiB | 缩容失败或 f2fs 后续写满 | 用 94 GiB（25 GiB 空闲） |
| Android 首次挂载把 f2fs 自动扩回分区大小 | 尾部镜像被 f2fs 覆盖，iOS 根丢失（Android 正常） | 缩容后先在 Android 里挂载一次并确认 `df -h /data` 仍是 94 GiB；若被扩回，先禁止自动 resize（或写镜像后再挂载） |
| Android 恢复出厂/OTA 重写 GPT 或 format /data | 尾部镜像丢失 | 记录方案；重刷镜像；T1 后避免恢复出厂 |
| `--partition-offset` 写错（未对齐/写进 f2fs 数据区） | 覆盖 /data | 脚本强制 1 MiB 对齐 + `offset+image ≤ 分区`；写前人工核对 plan 的 seek/偏移 |
| 镜像比尾部大 | 越界写入 | 脚本 `does not fit` 拒绝；94 GiB 方案余量 3.06 GiB |

### 9.9 与方案 A/B 的关系

方案 C 不新建分区、不动 GPT、不依赖 `linux_root`，是当前设备上唯一可行的方案。
如果以后重新分区（例如恢复 stock GPT 腾出空间），A/B 仍可作为独立分区方案；那时
`native-ufs-root-offset` 保持 0（或删除），并改用 `native-ufs-root-partition`。

## 10. 验证清单（每步期望）

| 步骤 | 命令/现象 | 通过标准 |
| --- | --- | --- |
| GPT 实测 | probe=15（只读启动） | 面板/USB ring 有 `NATIVE_UFS gpt dump LUN0`、`crc ok/ok`、`listed=`，能读到目标分区名和 `first-last` |
| 分区几何交叉验证 | `--gpt-dump` 计划 | `partition ... = <bytes>` 与 live `blockdev --getsize64` 完全一致，否则脚本拒绝 |
| 分区内偏移 | `--partition-offset` 计划 | `offset ... MiB, 1 MiB aligned`、`root manifest base LBA 27399680`、`seek=96256`、驱动参数 `native-ufs-root-offset=100931731456` |
| 镜像写入前 | 脚本输出 | 每个窗口 `verify ... sha256 ok`；`image_bytes` 放得下；fresh/append 模式正确 |
| 写入后 | `--apply` 输出 | `read-back: block 0 matches the written directory` |
| 驱动找到目录 | probe=10 | `userdata first=2a1600 last=... root base=1a21600 offset=100931731456`、`block0 magic` 为 `SHNGROOT` 的 4 字节小端、`manifest status=0 windows=2` |
| 根窗口可读 | probe=11 | `manifest status=0 windows=2`，`w0/w1 ... gpt=46454620 50415254`（"EFI PART" 小端） |
| 根启动 | `native-ufs-root=1` 启动 | 驱动日志出现 `NativeUfs: root partition is <name>`（来自新启动参数）且不再报 `no valid root manifest in userdata` |
| 回滚演练 | append 后写回 `manifest-before.bin` | 块 0 SHA-256 回到旧值（可在另一台/旧镜像上演练） |

## 11. 风险总表

| 风险 | 后果 | 规避 |
| --- | --- | --- |
| 未备份 GPT 就写 GPT | 设备不引导，只能 EDL | 第 3 节 GPT 头尾 + `sgdisk --verify` |
| 用旧 GPT 备份/旧 `board.toml` LBA 计划 | 写到错误分区/大小不符 | 必须 probe=15 实测；脚本写前校验 live 大小 |
| 把 `linux_root` 当 root 分区时发现里面还有要用的数据 | Ubuntu 数据丢失 | 先确认/迁移；方案 B 更安全 |
| userdata 里有 Android 数据仍直接 fresh 覆盖 | Android 数据全丢 | 只选空闲分区/linux_root，不选 userdata，除非明确要放弃 Android |
| Android OTA/恢复出厂重写 GPT | 新分区消失 | 记录 GPT；T1 后避免 OTA；恢复时重做方案 B |
| 镜像来自错误 IPSW/构建 | 根卷不匹配，启动失败但设备不坏 | 校验 `userdata.json` 的 sha256/版本/窗口名；保留旧 boot.img |
| `--apply` 中途断电（append） | 块 0 未 commit → 旧 manifest 仍在（安全） | 写入顺序固定为数据→目录；脚本 readback |
| `--apply` 中途断电（fresh） | 分区半新半旧 | 重跑一次 `--apply`（fresh 是幂等覆盖）；或按备份恢复 |

## 12. 回滚速查

```sh
# append：把旧目录写回块 0（旧窗口数据没被动过）
adb -s <serial> shell 'dd of=/dev/block/by-name/<分区> bs=4096 count=1 conv=notrunc,fsync' \
    < install-logs/<本次>/manifest-before.bin
adb -s <serial> shell sync

# 方案 B 的 GPT 回滚：用第 3 节的 sda-head.bin 写回主 GPT 区，并写回备份 GPT
adb -s <serial> shell 'dd of=/dev/block/sda bs=4096 count=34 conv=notrunc,fsync' < sda-head.bin
adb -s <serial> shell 'dd of=/dev/block/sda bs=4096 seek=<disk_last_lba-33> conv=notrunc,fsync' < gpt-tail.bin
adb -s <serial> reboot recovery

# 方案 C：把容量还给 Android /data（f2fs 扩回分区全大小；会覆盖尾部镜像）
adb -s <serial> shell 'resize.f2fs -s /dev/block/by-name/userdata'
adb -s <serial> shell 'fsck.f2fs -f /dev/block/by-name/userdata'

# fresh 覆盖过的分区：没有盘内旧副本，按备份恢复
```

## 13. 附录：只读探测命令

```sh
# 块 0（驱动读的就是它）：4 KiB
adb -s <serial> exec-out dd if=/dev/block/by-name/userdata bs=4096 count=1 > userdata-block0.bin
xxd -l 8 userdata-block0.bin            # 53 48 4e 47 52 4f 4f 54 = "SHNGROOT"
# GPT 文本/原始头
adb -s <serial> shell 'sgdisk -p /dev/block/sda' > sgdisk-p.txt
adb -s <serial> exec-out dd if=/dev/block/sda bs=4096 count=34 > sda-head.bin
# 分区名列表
adb -s <serial> shell 'ls -l /dev/block/by-name/'
```

排查顺序：先 probe=15 看 GPT 名和 LBA → 再 `--gpt-dump` 计划 → 再 probe=10 看块 0
magic/manifest → 再 probe=11 看窗口自身 GPT。任何一步与预期不符都停下来回退到只读。
