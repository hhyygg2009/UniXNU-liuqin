# 2026-11-22：prelinked 内核已到 "Failed to mount root device"，卡在 UFS TUR 探测

## 1. 时间线（全部实机验证）

| 版本 | 内容 | 结果 |
|---|---|---|
| v112 | 上游 `document97/UniXNU-liuqin@fe0e8d7` 重编的 prelinked 内核（slide=auto 0x5ed0000）+ 官方 finalize ADT/容器 + 原厂 Cape DTB + 菜单修复 FD | 越过 VM bootstrap；跑到 IOKit 后期：UFS nub 发布、framebuffer 1800×2880 亮起、APFS kext 加载、ArmCpu 首启完成，随后 panic `"CPU configuration in device tree is invalid" @AppleARMNCPU.cpp:111` |
| v113 | 同上但 native（未 prelink）内核 | ~60 s 内 panic 回 fastboot（native 缺 corecrypto/pthread/apfs 函数表，符合上游注释） |
| v114 | v112 + 二进制补丁：`AppleARMCPU::_validateConfiguration` 入口改为 `mov w0,#1; ret`（`0xfffffff00b80dad4`） | **越过 AppleARMCPU panic**，CPU 调频驱动起来，一路跑到 `Still waiting for root device` → `panic: Failed to mount root device @IOKitBSDInit.cpp:996` |

## 2. v114 屏上日志（关键片段，已从照片转正抄录）

```
PongUFS: clock tx CLK_OFF cleared 00000001 -> 00000000
PongUFS: clocks active, read host state
PongUFS: HCS 0x00000001
PongUFS: UIC DME_RESET(peer) result=0x00000000 hcs=0000010f dl=00000000 -> 0000000f
PongUFS: GEAR ONLY
PongUFS: storage write path enabled by pong_ufs_readwrite boot-arg
PongUFS: SMMU bypass with 4k pages, IOVA window 4KiB
PongUFS: SD slot2 ...
ArmCpu: boot CPU running with GICv3/GICv4 ...
ArmDTPlatform: registered platform cpu=1 arm-io=1 device=1 nvram-proxy=1
registered CoreAnalyticsHub functions ...
PONG_UFS TRANSPORT: probe TUR
stage=probe TUR (shim parity) result=0x00000246
... PROTOCOL: reject=0 residual=0 sense=0000 SCSI slot=0x92 LUN=...
PONG_UFS: no media published; continue boot
rootmode is not set        (重复)
Pong CPU: EFSS domain2 CPU-3 LUT-16 cap=1094000 kHz
Pong CPU: CPU-3 fixed request 1494000 kHz (LUT13), hardware limits retained
Still waiting for root device
panic(cpu 0 caller 0xfffffff008b7e0ac): Failed to mount root device @IOKitBSDInit.cpp:996
```

## 3. 结论

1. **引导链已完整打通到 BSD/根卷阶段**：固件 → UEFI → 菜单 → DarwinBoot → prelinked XNU → IOKit → APFS kext → 等待根设备。
2. 当前唯一阻塞：`NativeUfs` 的传输层 TUR 探测失败（`stage=probe TUR (shim parity)`），因此没有发布任何 IOMedia；BSD 等待超时后 panic。
3. 这与「还没写根镜像」不是同一层问题：即使写了 SHNGROOT 根镜像，TUR 不通也读不到分区表。先修传输层，再写根镜像。
4. `AppleARMCPU::_validateConfiguration` 对我们这种"非 Apple DT"必然失败（需要 Apple 私有 CPU 属性 `max_cpus`/`cpu-id`/`interrupts`/`function-ipi_*`），上游没有解决；用二进制补丁绕过是有效的 bring-up 手段，后续若要恢复 CPU 电源管理可再按反汇编结论补 ADT 属性。

## 4. 下一步候选（按优先级）

1. **对比"working shim"**：上游 2026-11-21 提交 `UFS: probe with TUR like the working shim` 说明存在一个能过 TUR 的实现。ShengUfs（我们旧栈）在 v57–v60 修过 UIC 错误清除、doorbell 屏障对齐；NativeUfs 的 TUR 失败点（`shim parity`、`result=0x246`）应逐项对照。
2. **抓完整日志**：屏幕只有约 50 行缓冲。可考虑（a）给 NativeUfs 加 probe 记录到固定内存并在 panic 时打印摘要；（b）用 `pong_boot_manifest`/ramoops 落盘（需要把 ramoops 地址改成 liuqin 的 `0xA7000000`，旧 CI 的 `ci/patch-device.py` 有现成补丁）。
3. **先写根镜像再试**：A2 分区 + `userdata.simg`（19.39 GiB）写入 `unixnu`；若 NativeUfs 的 TUR 只是"设备状态未就绪"而非硬故障，也可能在正确分区内容下恢复。
4. 备选：检查 SMMU stream id 0xe0 / 4K 页 bypass 与 liuqin 的 SMMU 配置是否一致。

## 5. 产物

| 文件 | 说明 | sha256 |
|---|---|---|
| `F:\liuqin_ios\boot-v112-upstream-prelinked.img` | 上游 prelinked，撞 AppleARMCPU | `5f6d1bcc…` |
| `F:\liuqin_ios\boot-v113-upstream-native.img` | 同源 native | `ac1a2428…` |
| `F:\liuqin_ios\boot-v114-nocpuvalidate.img` | 绕过 AppleARMCPU 校验，到 root device | `0213013f678733b8c059ef132bb55217c0ca29659eaeeea0858dd3b36352437b` |
| 补丁后内核 | `kernel.macho` 的 `0x1943AD4` 起 8 字节 | sha256 `157641daf9a0479b633d0bfc47f1046340d6dfac333fba594bc7839d15447039` |

## 6. 复现

```bash
# prelink=true 的内核产物（run 44891152869）：
bash docs/ci-fetch-kernel.sh 44891152869 kernel-upstream-encrypted
# 打补丁（AppleARMCPU 校验 -> true）：
python3 - <<PY
d=bytearray(open(".../kernel.macho","rb").read())
fo=0x18dd000+(0xfffffff00b80dad4-0xfffffff00b7a8000)
d[fo:fo+8]=bytes.fromhex("20008052c0035fd6")
open("patched-kernel.macho","wb").write(d)
PY
# 组镜像（官方 finalize + 菜单修复 FD + Cape DTB）：
bash docs/make-v112.sh 44891152869 boot-v114.img patched-kernel.macho
```
