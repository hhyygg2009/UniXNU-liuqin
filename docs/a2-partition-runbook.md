# 根镜像写入手册（2026-11-22 修订：优先"直接刷 userdata"）

用户已于 2026-11-21 授权 A2（备份 + 重建 `userdata`）。除此之外不碰 Android 系统分区。

## 1. 两条路线（先读）

pong 的 `NativeUfs` 固定查找名为 **`userdata`** 的分区（`drivers/NativeUfs/RootLayout.h:106`），
上游 liuqin 安装脚本也写 `/dev/block/by-name/userdata`。所以有两条路线：

| 路线 | 操作 | 代价 | 推荐 |
|---|---|---|---|
| **B. 直接刷 userdata** | `fastboot flash userdata userdata.simg`（分区 105.79 GiB，镜像 32 GiB 虚拟 / 11.33 GB 实际数据） | Android 应用数据被覆盖（/sdcard 已备份），**不改 GPT** | ✅ 优先 |
| A. 扩 `unixnu` 分区 | 改 GPT：`userdata` 缩到 84.79 GiB + 新增 `unixnu` 21 GiB，再刷镜像 | 需要 root/TWRP 写 GPT；而且 pong 驱动还要改成找 `unixnu` 分区（当前不会） | 仅当必须保留 Android userdata 时 |

**结论：先走 B。** A2 的 GPT 文件仍然备着（已双表校验），但按当前驱动它不会带来收益。

## 2. 路线 B 执行

前置检查：

```bash
# 1) 镜像与校验
ls -l userdata.simg            # 11,331,461,604 B（Android sparse，虚拟 32 GiB）
sha256sum userdata.simg        # 与 CI meta 里记录一致
# 2) 确认设备在 fastboot、电量 > 50%
fastboot devices
# 3) /sdcard 备份在 F:\liuqin_ios\_dl\backup\sdcard-20261121\files（5,875 文件）✓
```

执行：

```bash
fastboot flash userdata userdata.simg
# fastboot 会按 sparse chunk 写入：只写有效块、跳过 DONT_CARE，约 11.33 GB，20–40 分钟
fastboot reboot bootloader
```

写入后布局（由 `shngroot-ipados18.bin` 声明，内核按此挂载）：

| 窗口 | 偏移 | 大小 | 卷 UUID |
|---|---|---|---|
| OS | 1 MiB | 6,845,104,128 B | `B07DF049-7AD9-47B5-9F8F-9EE728056398` |
| SystemOS | 6,846,152,704 B | 5,368,709,120 B | `5D08420A-43CD-43AD-8B1B-1E8F5B86AB0A` |
| Data | 12,231,639,040 B | 8,589,934,592 B | `5DF4DC0A-9451-4994-A495-1554A8AC42A2` |

这三个 UUID 就是内核 `ci/volume-layout.json` 编译时写入的 `XNU_OWNER_*_VOLUME_UUID`（已核对一致）。

## 3. 恢复 Android

```bash
# 需要时：刷回官方 userdata（会重新格式化）
fastboot -w            # 或刷官方 fastboot 包里的 userdata.img
# 完整恢复参考 F:\liuqin_ios\liuqin_images_V14.0.3.0.*\（官方线刷包）
```

## 4. 路线 A（GPT）备查：已校验的数值

| # | 分区 | first LBA | last LBA | 大小 |
|---|---|---|---|---|
| 35 | userdata | 2,758,144 | 24,986,618 | 84.79 GiB |
| 36 | unixnu | 24,986,619 | 30,491,642 | 21.00 GiB |

- 主 GPT：`scratch/gpt-new-head.bin`（135,168 B，sha256 `a85268dd380389d4b5c67edc82552dbc7474f85e181b9d11ceb155f37cb702ff`）
- 备份 GPT：`scratch/gpt-new-tail.bin`（20,480 B，sha256 `9b0d164e8f549197a26ce4238390683a6fe303dacdaefc331af5894a722485af`）
- 改动前备份：`scratch/gpt_now.bin`（17,408 B）
- 写 GPT 需要 root/TWRP：`dd if=gpt-new-head.bin of=/dev/block/sda bs=4096 seek=1 conv=fsync`，
  备份表写到 LBA 30,491,643；随后 `sgdisk --verify /dev/block/sda`。
- 若走 A，还需要把 `RootLayout.h` 的 `wanted[] = "userdata"` 改成 `"unixnu"`（需要额外内核补丁）。

## 5. 顺序

1. 先修 UFS 传输层（TUR 能完成）——这是唯一硬阻塞；
2. 再按路线 B 刷 userdata；
3. 重启进 v115+ 镜像，观察 `PONG_UFS: SHNGROOT ... publish 512-byte disk` 与 APFS 挂载。
