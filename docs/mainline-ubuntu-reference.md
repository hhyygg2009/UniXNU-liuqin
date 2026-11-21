# 同板对照：mainline Ubuntu（yzddmr6/xiaomipad-6pro-mainline）

同一台机器（Xiaomi Pad 6 Pro / liuqin / SM8475）上的**主线 Linux + Ubuntu 26.04 GNOME** 移植。
2026-11-19 拉下来做逐值对照。本文只写**我们核过的**内容，每条给本地路径与行号。

- 上游：https://github.com/yzddmr6/xiaomipad-6pro-mainline ，`main` @ 2026-11-16，`VERSION` = **0.8.0**
- 内核仓：https://github.com/yzddmr6/linux-sm8450-liuqin 分支 `liuqin-6.17`，
  commit `c25f056e0c20…`，upstream base `sm8450-mainline/linux` `a19585977c11…`
- 落盘位置（不进 `/tmp`，AGENTS 硬规矩）：`../upstream/`，来源与校验值见 `../upstream/README.md`
- 许可：项目自研部分 **MIT**（`LICENSE`），第三方与二进制输入按 `NOTICE` 与文件头；
  内核 DTS 是 **GPL-2.0-only** —— **只引用、不复制进我们的树**

## 1. 它不用 UEFI，我们为什么必须用（一句话版）

它把 `Image.gz + DTB + initramfs` 用 AOSP `mkbootimg.py` 打成 Android boot image（我们解包的
`../upstream/ubuntu-boot-v0.8.0.img`：`header_version = 2`，kernel 18,143,450 B，ramdisk
43,694,718 B，DTB 226,922 B 附在末尾），**ABL 认 ARM64 Image 头，直接跳进内核**；
rootfs 在重新划出来的 UFS 分区 `linux_root`。整仓 781 个文件里 `uefi/edk/BootShim` 命中 0。

我们的负载是 Mach-O，ABL 不认，所以必须有 `BootShim + UEFI FD + UniXNU + DarwinBoot`。
**结论：它不构成"我们也可以不用 UEFI"的证据；但它是"UEFI 里不需要高通厂商驱动栈"的第二份证据**
（隔壁 mondrian 的 `DXE.inc` 已经把厂商驱动全删了）。

## 2. ABL 侧的硬事实（我们将来要持久化或改 DT 时一定会撞上）

在它们**实际发布并被这台 ABL 接受**的 boot DTB 里（`../upstream/ubuntu-boot.dts:3-11`）：

```
xiaomi,miboard-id = <0x10 0x00>;
qcom,board-id     = <0x10008 0x00>;
qcom,msm-id       = <0x213 0x10000 0x21c 0x10000 0x212 0x10000>;   /* 531 cape, 540, 530 waipio */
model             = "Xiaomi Pad 6 Pro";
compatible        = "xiaomi,liuqin", "qcom,sm8475";
chassis-type      = "tablet";
```

- 与 `docs/liuqin-hardware.md` A 节里从 `liuqin-abl-boot-overlay.dts` 抄来的那行**逐字节相同** ✓。
- `device/liuqin-abl-boot-overlay.dts:11-16` 记录：stock DTBO 有 8 条（entry.2/15/18/19/23/28/34/43）
  `qcom,msm-id`+`qcom,board-id` **完全一样**，只靠小米私有的 `xiaomi,miboard-id` 区分；
  ABL 二进制里有 `MiBoardid = %x` / `xiaomi,miboard-id does not match`，也就是**真有拒绝路径**。
- 它的"实验 D"（`tools/lib/abl-dtb-ids.py` 文件头）：只差 DTB，mainline DTB 被 ABL 在 4.3 s
  打回 fastboot，stock `vendor_boot` 的 `dtb-02.dtb` 被放行；公开的高通参考代码两者都接受
  → **拒绝逻辑在小米私有 ABL 里，离线读不到**。它的绕法是把"被接受的那颗 DTB"根节点身份属性
  逐字节镜像过来（`MIRROR_PROPS`，`tools/lib/abl-dtb-ids.py:33-41`），并保留
  `interrupt-parent/#address-cells/#size-cells/phandle` 不被镜像。
- ABL 还会 **往 `/chosen/bootargs` 原地追加自己那 ~800 字符**（`tools/lib/build-bootimg.sh:33,163-164`），
  并会对 DTB 套 stock/runtime DTBO overlay —— 它的 `tools/lib/abl-symbols.py` 把 overlay 需要的
  label 全导出到一个不在任何总线下的 sink 节点 `liuqin-abl-overlay-sink`，免得 overlay 写坏硬件节点。
- **对照我们**：`uefi-liuqin/Resources/DTBs/liuqin.dtb` 的根是
  `model = "Qualcomm Technologies, Inc. Cape SoC"`、`compatible = "qcom,cape"`、
  `qcom,msm-id = <0x212 0x10000>`、`qcom,board-id = <0 0>`，**没有** `xiaomi,miboard-id`。
  它今天能过 ABL —— **未核实**原因（我们的判断：`fastboot boot` 走的是 `LinuxLoader` 的临时路径，
  不跑 DT 选择那套；不要在这上面建假设，要持久化时先按上面的身份属性补齐再测）。

## 3. 钉值对照：我们写死的常量 vs 它跑在真机上的值

| 项 | 我们的值（来源） | mainline 值 | 结论 |
| --- | --- | --- | --- |
| GICD | `0x17100000 +0x10000`（`scripts/make-liuqin-adt.py:36-37`） | 同（`kernel-dts/sm8450.dtsi:5148-5156`） | ✓ |
| GICR | `0x17180000 +0x200000`，stride `0x40000` | 同 | ✓ |
| CPU timebase | `19200000` | `clock-frequency = <19200000>`（`sm8450.dtsi:6399`） | ✓ |
| UFS HC | `0x1d84000 +0x3000`（`drivers/ShengUfs/GccUfs.h:46`） | `ufshc@1d84000` reg `0x3000`，**IRQ = GIC_SPI 265**，`lanes-per-direction = <2>`（`sm8450.dtsi:5361-5368`） | ✓ 窗口一致；中断号与 lane 数是我们以后转中断时才用得上的新信息 |
| UFS PHY | ADT 里写 `0x1d80000 +0x2000`（`make-liuqin-adt.py:41-42`，注释"节点只用于类匹配"） | mainline `phy@1d87000 +0x1000`；硬件文档 A 节记的是 cape 用 `0x1d80000` | **两处不一致，别当事实**：`NativeUfs` 实际不吃 ADT 里这个值，真要动 PHY 前先按 `docs/liuqin-hardware.md` 的 cape 注记复核 |
| ABL splash | `0xb8000000 +0x2b00000`，1800x2880，stride 7200，`a8r8g8b8`（`board.toml` `[[bootimg.dtb_node]]`） | 完全相同（`ubuntu-boot.dts:15-22`、`sm8475-xiaomi-liuqin.dts:73`） | ✓ 第三方独立确认 |
| SMEM | `0x80900000 +0x200000`（`MemoryMapLib.c:12`） | 同，外加 `compatible = "qcom,smem"` 和 **`hwlocks = <&tcsr_mutex 3>`**（`sm8450.dtsi:643-648`） | ✓；`tcsr_mutex` 是 SMEM 访问的硬件锁，我们以后真读 SMEM 要按它排队 |
| ramoops | 只在注释里提过 | `0xa7000000 +0x400000`，console 2 MiB + pmsg 2 MiB（`ubuntu-boot.dts:897-904`） | 见第 5 节 |

## 4. 新发现的真实冲突：**rmtfs 落在我们的 DXE Heap 里**

它（和 stock vendor DT）在 liuqin 上把 rmtfs 钉在
`0xf8700000 +0x280000`，`compatible = "qcom,rmtfs-mem"`、`qcom,client-id = <1>`、
`qcom,vmid = <0x0f /* MSS_MSA */>`、`no-map`（`ubuntu-boot.dts:906-912`；
`sm8475-xiaomi-liuqin.dts:331-338`）。

我们的 UEFI 内存表里 **没有这一条**：`{"DXE Heap", 0xF8000000, 0x07C00000, AddMem, SYS_MEM,
SYS_MEM_CAP, Conv, WRITE_BACK_XN}`（`uefi-liuqin/Platforms/Xiaomi/liuqinPkg/Library/MemoryMapLib.c:58`）
把 `0xF8000000–0xFFC00000` 整段当成可分配 RAM，`0xf8700000` 正在其中；
我们的固件 DTB 里也 grep 不到 `f8700000`。

- 后果（当前）：**尚未观察到**。我们还没跑 ADSP/SLPI/modem，也没走厂商 GPU 路径，所以没人用这块。
- 后果（将来）：任何走 PIL / rmtfs 的东西都会和 DXE 分配器互相踩。
- 处理：在 `MemoryMapLib.c` 里把 `0xF8700000 +0x280000` 从 DXE Heap 里挖出来记成 `MEM_RES`
  （与同文件其它 `Reserv` 行同风格），**留到下一轮改内存表的构建里一起做**，别和 v43 的
  H17 判读混在一轮。记录：见 `porting.toml` memory area。

## 5. 面板之外可能还有一条日志通道（提案，未验证）

它的板 DTS 里有一条设计说明值得抄思路：ramoops 的 zone 尺寸**故意和厂商一致**，
"这样这边启动失败后可以从 stock 内核的 `/sys/fs/pstore` 里读回来 —— 这块板子上没有别的后验通道，
零售机的调试 UART 根本没引出来"（`sm8475-xiaomi-liuqin.dts:313-329`）。

我们的处境一模一样（AGENTS：诊断只能走面板 + 1 MiB 环形缓冲 + USB 调试口）。
差别在于：**这台机器上现在装着一个能进 root shell 的另一个 OS**（`docs/liuqin-hardware.md` E 节：
HyperOS + KernelSU，另有 Ubuntu 双系统）。于是可以把 `liuqin_debug` 环的尾巴**额外镜像**到一个
"两边都当成 no-map 保留、且另一个 OS 不会写"的物理窗口，重启进 Android/Ubuntu 后用 root 读回来：

| 候选窗口 | 大小 | 谁会写它 | 备注 |
| --- | --- | --- | --- |
| `xbl_sc_region@0xa6e00000` | 256 KiB | 正常启动路径不写 | 我们的表里落在 `DBI Dump 0xA6000000+0xF00000`（`MMAP_IO`，不当 RAM 用）✓ |
| `xbl_ramdump_region@0xa6b80000` | 2.5 MiB | 只有 XBL 崩溃转储会写 | 空间够放 1 MiB 环 |
| `ramoops@0xa7000000` | 4 MiB（console 2M + pmsg 2M） | **Linux 一起来就写** | 除非我们按 mainline 的 prz 头格式伪装，否则读回来的是 Ubuntu 自己的日志 |

先要验的三件事（都要上机，都是只读操作）：
① 那个 root shell 里 `/dev/mem` 开不开（vendor kernel 常 `CONFIG_DEVMEM=n`；Ubuntu 侧
`no-map` 区在 `CONFIG_STRICT_DEVMEM` 下通常可读，`CONFIG_IO_STRICT_DEVMEM` 就不行）；
② 热重启后 DRAM 内容是否保留（冷断电一定不保留）；
③ XBL 会不会清 `xbl_sc`。
**都不许写分区**，AGENTS 的"只允许 `fastboot boot`"在这条路上不变。

## 6. 可直接复用的厂商输入（省掉我们自己的抓取）

`../upstream/xiaomipad-6pro-mainline/` 里：

- `device/firmware/` —— **199 个文件、已经装配好的 SM8475/liuqin 固件池**：
  `qcom/sm8475/liuqin/{adsp,cdsp,slpi}.b*`、`a730_zap.mbn`、`ath11k/WCN6855/*`、`qca/hpnv*.bin`、
  `novatek/liuqin/novatek_nt36532_m81_fw_{csot,tm}.bin`、`qcom/vpu/vpu20_4v.mbn`，
  外加 `firmware.manifest`。以后做 Wi-Fi（`&pcie0` + `qcom,wcn6855-pmu`，
  enable 线是 TLMM `gpio 80/81/82/204`，`sm8475-xiaomi-liuqin.dts:396-417`）、
  GPU（`&gpu` `zap-shader` 固件名，同文件 `:746-751`）时**不必再去拆 ROM**。
- `tools/lib/liuqin-rom-images.json` —— 钉死在 **`OS2.0.6.0.VMYCNXM`**（包名
  `liuqin_images_OS2.0.6.0.VMYCNXM_20250318.0000.00_15.0_cn`）的逐分区 sha256/字节数表
  （`boot_a` 201,326,592 B、`vendor_boot_a` 100,663,296 B、`modem_a` 114,151,424 B…）。
  我们手上是 OS3.0.7.0 与 MIUI V14 包；**要核对原厂分区时这是第二份独立 hash 源**。
- `tools/lib/build-liuqin-audio-topology.sh` —— AudioReach 拓扑用
  `linux-msm/audioreach-topology` commit `2af1f1ebb8d4…` 生成 `Xiaomi-Pad-6-Pro-tplg.bin`；
  以后做 CS35L41 四扬声器时按这条路径。
- `device/fingerprint/oem/`（QSEE supplicant + QCBOR）—— FPC1264 走 QSEE，**要 TEE**，
  我们短期不碰，但它证明了这条链在 SM8475 上可用。
- `sm8475-xiaomi-liuqin.dts` 本身 —— 我们后面做显示/触摸/传感器/电源时最省事的"接线答案"：
  面板 `xiaomi,pipa-nt36532`（`:1261`）、触控 `novatek,NVT-ts-spi` on `&spi4`，
  IRQ = TLMM 21（`:1511-1539`）、背光 `kinetic,ktz8866` on `&i2c1`（`:784`；注意它注释里
  写了这个 fork 的驱动按 compatible 做**双芯片互锁**，`ktz8866a` 见不到 `ktz8866b` 就一直
  `-EPROBE_DEFER`，我们只有一颗，所以写的是 `kinetic,ktz8866`）、音频 `qcom,wcd9380-codec`（`:164`）。

## 7. 它踩过的坑，我们已经踩过的对答案

| 它的教训 | 我们的对应记录 |
| --- | --- |
| 零售机没有调试 UART，`earlycon=simplefb` + `console=drm_log` + `initcall_blacklist=simplefb_driver_init` 才看得见早期失败 | AGENTS"诊断只能走面板里程碑控制台"；我们的 K/B/U 里程碑 + panic 屏是同一类补偿 |
| `keep_bootcon` 让 simplefb 一直占着 bootloader 帧缓冲，桌面合成器盖不住 | 我们的 `panic_screen_only=true` / 控制台与 panic 屏抢屏问题（`docs/panel-rules.toml`） |
| `clk_ignore_unused`/`pd_ignore_unused` 是"插电还掉电"的原因，正式版删掉了 | 我们还没走到电源域；等做 PMGR/RPMh 时直接按删掉的版本抄 |
| 分区按 `PARTNAME` 解析，不认编号 | 我们 `board.toml` `first_lba/last_lba` 记的是**设备自己报的 GPT**，同一条教训 |
| 原厂 `fastboot --set-active` 会同时交换每对 `_a/_b` 的类型 GUID **和 UFS 启动 LUN**，用别的方式切槽会让所有镜像加载失败 | 我们从不切槽（只 `fastboot boot`），继续维持 |

## 8. 待办（按优先级）

1. **[主机侧]** `MemoryMapLib.c` 挖出 rmtfs 行（第 4 节），和下一次内存改动同轮上机。
2. **[需上机]** 第 5 节三件验证：root shell 能不能 `/dev/mem`、热重启保不保内容、XBL 清不清 `xbl_sc`。
   通过就把"重启到另一个 OS 读环"写进 `docs/`，替掉一部分拍照判读。
3. **[主机侧]** `docs/liuqin-hardware.md` A 节的 `compatible` 由 `"xiaomi,liuqin","qcom,sm8450"`
   改成实测的 `"xiaomi,liuqin","qcom,sm8475"`（第 2 节已给出两处证据）。已在本轮改。
4. **[低优先]** UFS 中断号 265 / lane=2 记进 storage area 备用；`make-liuqin-adt.py` 的
   `UFS_PHY` 与文档不一致，下一轮改 ADT 时一并澄清（**别和 v43 判读混轮**）。
