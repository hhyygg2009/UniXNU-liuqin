# 2026-11-21 实机第四轮：v66（只 `fastboot boot`，未写任何分区）

设备 `19562650`，`product: liuqin`，`variant: SMP UFS`。
镜像 `build/boot-liuqin-fly116-v66.img`，69,525,504 B，sha256
`978bb3ab7b937e522528b20141a78dfe297102ceb4eae605f036f1b02fdcfb77`
（与同目录 `.sha256` 逐字一致；尺寸正好是对账基准 69,525,504，MBR 包装没漏）。

```
20:13:00  fastboot -s 19562650 boot build/boot-liuqin-fly116-v66.img
          Sending 'boot.img' (67896 KB)   OKAY [ 1.640s]
          Booting                          OKAY [ 0.287s]
20:13:02  Finished. Total time: 2.010s
```

主机侧串口监听 `capture-serial.sh`（210 s 窗口，`out-v66-round/`）：**没有出现新的 CDC-ACM**。
boot 之后主机侧 USB 完全看不到设备（`18d1:d00d` 不再枚举），与"内核接管 DWC3、调试口尚未注册"
一致 —— 也就是说这一轮的判读只能靠面板。

**panic 之后设备自己回到 fastboot**：20:24 复查 `fastboot devices` 仍是 `19562650 fastboot`，
没碰按键、没拔线。一轮的代价只有几十秒。

## 面板判读

照片：`IMG_7249`（滚动正文，sha256 `2e3b78df231f6a054006d2b52cfc10315b2831993fb2afafc5302206ac92d423`）、
`IMG_7250`（终点屏，sha256 `6520c1f2483243ac6c9ac3b701392d2dc30bf60c454350f066f26d76316c8c1e`）。

1. **开机的就是这份内核。** 屏上版本串
   `Darwin Kernel Version 23.6.0: Sat Nov 21 09:22:28 CST 2026; hhyygg:ios-obj/DEVELOPMENT_ARM64_BCM2837`
   与 `out/liuqin/kernel/kernel.macho`（51,200,000 B，sha256 `b91c756729611065…`）里的 `@(#)VERSION` 逐字相同。
   stamp 是 09:22 而产物 mtime 是 17:10，一开始像"只重打包"；`out/liuqin/logs` 否掉了这个猜测 ——
   `archive-01..12`（17:09–17:10）、`kernel.log` 17:10:26、`prelink.log` 17:10:31、`payload-adt.log`
   17:10:33 都在，**内核确实重编了**，只是 `@(#)VERSION` 串不随每次构建重新生成。
   所以"面板串 == 内核串"只证明镜像里是这份内核，不能用来推构建次数；构建次数看 logs 目录。
2. **H17 没有复现。** 整屏没有 `Taking non-sleepable RW lock … @lock_rw.c:1400`。
   上一堵墙（launchd exec 时的 trust-cache 锁）在这轮没有再挡路。
3. **H16 绊线健康。** `NativeUfs: M:reg pre p=0xfffffff007ac36b0 link=0xfffffff006add1d0` 出现 3 次，
   `link=` 与 audit 里编译期家族元类 `__ZN20IOBlockStorageDevice10gMetaClassE = 0xfffffff006add1d0` 相同
   —— 运行时绑定和编译期绑定是同一个对象，这正是绊线要证明的事。
   屏上没有任何 `M:stalled` / `M:bad[` / `M:fp` / `M:alien`（v43 的退役仍然有效）。
4. **存储走到了新地方。** 依次出现
   `MEDIA start IOBlockStorageDevice=1 IOBlockStorageDriver=1 IOMedia=3 IOMediaBSDClient=3`、
   `MEDIA start IOBlockStorageDevice=2 IOBlockStorageDriver=2 IOMedia=7 IOMediaBSDClient=7`，
   `V58 sync st=0x1e busy=0 dev=2 drv=2 med=3 bsd=3 mc=111 n=55 dt=Generic`，
   以及 `REGPROBE class=NativeUfsBlock castsDevice=1 castsService=1 state=0x1e busy=0`、
   `CHAINPROBE devParent=ArmDTPlatform provParent=xiaomi,liuqin provPath=1 devAttached=1`。
   两个 block device 都注册到了 BSD 客户端 —— 之前任何一轮都没到这里。
5. **`liuqin-milestone=0` 生效。** 里程碑叠加层不再和文字控制台互相压字。屏上残留的重影来自**固件菜单**
   文字（`liuqin boot: 512M window (test)`、`3000 ms, input yes`、`Payloads\kernel.macho' Success`、
   `kernel.macho.adt' Success`、`kits 0`、`WAKE LUCK, Module: 42bb-f0a-13f2-4B21-8A23-53D3`），
   不是内核叠加层 —— 这两件事要分开记。
6. **新墙（`IMG_7250`）：**

   ```
   copyio.c:94 Assertion failed: __builtin_arm_rsr("pan") != 0
   frames fffffff0079ead00 fffffff00740f5c4 fffffff0073ed410 fffffff0073ec1c4
          fffffff0073ec4e8 fffffff0072767bc fffffff00741a9fc fffffff00741a5d8
   pages=0x000000000001b6bb
   free=0x00000000000019c3c
   ```

## 帧符号化（`llvm-nm` 对 v66 的 `kernel.macho`，51,271 个符号，取 ≤ 帧地址的最近符号）

| 帧 | 符号 | 偏移 |
|---|---|---|
| `fffffff0079ead00` | `_panic_with_data` | +0x0 |
| `fffffff00740f5c4` | `_copy_validate` | +0x0 |
| `fffffff0073ed410` | `_telemetry_process_sample` | +0x11c |
| `fffffff0073ec1c4` | `_telemetry_take_sample` | +0x16c |
| `fffffff0073ec4e8` | `_telemetry_ast` | +0x164 |
| `fffffff0072767bc` | `_ast_taken_user` | +0x22c |
| `fffffff00741a9fc` | `user_take_ast` | +0xc |
| `fffffff00741a5d8` | `_fleh_synchronous` | +0x28 |

读法：**同步异常 → 返回用户态的 AST → VM telemetry 采样 → `copy_validate` 在不该做 copy 的上下文里做 copy
（PAN 没置位）→ 断言**。这条路径完全在 XNU 自己的 telemetry 里，和 NativeUfs 无关（绊线在第 3 条里已经证明绑定是对的）。

配置侧证据：`configs/liuqin_boot_defconfig:15` 与 `out/liuqin/.config:15` 都是
`CONFIG_XNU_NATIVE_PAN=y`，经 `patches/xnu/native-arm-pan.patch` 打开
`__ARM_PAN_AVAILABLE__`，于是 `copyio.c` 的 PAN 断言在真机上生效。镜像里能看到这条路径的注册点与旋钮：
`com.apple.xnu.vmtc_telemetry`、`_act_set_io_telemetry_ast`、`_act_set_macf_telemetry_ast`、
`_compute_telemetry`、`telemetry_sample_rate`、`io_telemetry_limit`、`telemetry_init`。

## 主机侧守卫与镜像不一致（这轮顺手量出来的）

- `scripts/audit-h16f-image.py` → `AUDIT FAILED (6)`：`_liuqin_base_class_probe` 在镜像里没有地址，
  三条 `NativeUfs: …` 串（metaclass chain broken / base class live size / is held by）计数为 0，
  连带 K/Q/P/C 的窗口检查整组无法评估。
- `scripts/check-trust-cache-image.py` → `FAILED (1/21)`，唯一失败项是 `T payload.json reports that
  same bundle = None`；而它的 P 组说 `amfi interface not available` **在镜像里 1 次**。
  两个守卫对同一条串的判据不一致 ⇒ audit 的期望已经落后于 v66 的补丁集。
  按 AGENTS 的规矩（动过代码要同时改守卫），这是守卫要修，不是镜像要背。
- `build/boot.json` 还是 11-19 那次 v1 的记录（`image_sha256 e8589009…`），跟 v66 无关；
  `build/NOTES.md` 也只写到 v1。v60–v66 六张镜像**都没有 provenance 记录**，所以设备证据没法引用
  "验证过的完整源码提交"，只能引用镜像 sha256 + 面板串。

## 下一步（按代价排序）

1. 在构建机上找 telemetry AST 的**注册条件**（`act_set_io_telemetry_ast` /
   `telemetry_process_sample` 的调用前提与 boot-arg/sysctl 门），做一轮"关掉采样"的互斥证明：
   只翻一个开关 → `check-defconfig.py` → 构建 → 两个守卫应只挂在断言旧分支的项上。
2. 把 audit 的 6 项期望与 v66 补丁集对齐（该删的删、串名改对），并让 `check-trust-cache-image.py`
   的 `T` 项与 audit 的 `T+` 用同一判据。
3. 修 `payload.json` 的 bundle 字段（`fetch-liuqin-trust-caches.py` 产出的 bundle 要在打包后回报）。
4. 给 `build/*.img` 补 provenance：设备仓 commit、`kernel.macho` sha256、FD/DTB sha256、打包时间与
   构建机；同时解释 17:10 重打包为什么带着 09:22 的 `VERSION` stamp（是只重打包，还是 `VERSION` 没再生成）。
5. 之后才轮到 schema 2：4 处同名不同内容补丁重基 + 完整 `unixnu fetch` + 干净检出重建一轮。
