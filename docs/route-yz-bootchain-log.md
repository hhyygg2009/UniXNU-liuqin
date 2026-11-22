# 路线 Y/Z 引导链试验记录（2026-11-21 更新）

目标：把 pong 栈（QPRS-6，iPadOS 18.7.10 / Darwin 24）的内核跑到我们这台 liuqin 上。
全部为真机 `fastboot boot` 实测。

## 已确认（真机证据）

| 结论 | 证据 |
|---|---|
| 他们的内核能在本机执行 | 两次进内核：`pmap_map_bd` panic、`zone_init: kmem_suballoc` panic |
| 窗口分配是第一个坎 | 诊断屏 `NO WINDOW 10000000 BIG RUN CDC0000`（220 MiB < 需要的 256 MiB） |
| 我们的固件把显示保留区当成 System RAM | 内核把启动数据放进去 → `pmap_map_bd` |
| 他们的固件依赖 5 个 EDK2 补丁 | `AsmMacroLib/ Auth-Service/ Boot-Manager/ Timer/ Usb-Bus` |
| 补丁打不上是行尾问题 | EDK2 源码 CRLF、补丁 LF；LF 归一化 + GNU `patch -l` 后 **5/5 全部成功** |
| **他们的同源固件能在本机完整启动** | UEFI 起来、UniXNU 菜单显示正常（v90 实测） |
| 同源固件的 PEI 不再崩 | 之前 v81 在 `0xA7104A04` `Synchronous Exception`，同源后消失 |

## 同源固件的构建方法（可复现）

见 `docs/build-firmware-from-upstream.sh`。要点：

1. 克隆 `document97/uefi-liuqin`
2. 子模块指向我们已展开的 Mu 树；`Mu_Basecore` 用他们锁定的 commit `cfb93724`
3. 把 EDK2 树统一成 LF（`sed -i 's/\r$//'`），否则 `git apply` 拒绝
4. 用 GNU `patch -p1 -l` 打 5 个补丁（`git apply` 因行尾会失败）
5. 拉齐 Mu_Basecore 子模块（brotli/mipisyst/libfdt/spdm 等）
6. `unixnu build firmware --uefi <他们的树> --edk2 <他们的 Mu_Basecore>`

## 产物

| 文件 | 说明 |
|---|---|
| `boot-v90-realfw.img` | 他们的固件 + PONGFAT1 payload + 192 MiB 窗口 ADT（菜单正常，交接一闪而过） |
| `boot-v91-haltdiag.img` | 同上 + DarwinBoot 失败冻结补丁，用于把诊断钉在屏幕上 |
| `userdata.simg` | 11,331,461,604 B sparse，32 GiB 虚拟，3 卷，有效数据 19.39 GiB |
| `gpt-new-head.bin` / `gpt-new-tail.bin` | 新分区表（userdata 84.79 GiB / unixnu 21.00 GiB），双表 CRC 校验过 |
