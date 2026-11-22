# 2026-11-21：改用上游最新内核源码，VM bootstrap panic 消失

## 1. 为什么换源码

先前 CI 用的是 `QPRS-6/iPadOS18-liuqin@38ca105d`（2026-11-10 drop），而上游
`document97/UniXNU-liuqin` 已经到 `fe0e8d790e24`（2026-11-18/21 drop）。上游当前
`board.toml` 的差异直接相关：

- `[kernel.prelink] slide = "auto"`：prelink 时从 22H374 kernelcache 的 `__PRELINK*`
  段派生 slide（目标基址 `FIXED_PRELINK_BASE = 0xfffffff00b000000`），不再用 21H461 的
  `-0x1000000`；
- 新增 `zzzzzzzzzzzzzzz-liuqin-prng-fallback.patch`、
  `zzzzzzzzzzzzzzzz-liuqin-crypto-random-fallback.patch`（native 内核的 PRNG 兜底）；
- 2026-11-21 的 UFS/SMMU 修复。

## 2. CI 工作流 `kernel-upstream.yml`

新增于 `Fly116/liuqin-ipados18-ci`，与旧 `kernel.yml` 的差别：

| 项 | 旧 `kernel.yml` | 新 `kernel-upstream.yml` |
|---|---|---|
| device 源码 | `QPRS-6/iPadOS18-liuqin@38ca105d` | `document97/UniXNU-liuqin@fe0e8d790e24` |
| UniXNU | c96b2997 | c96b2997 |
| prelink | `--slide=-0x1000000` | `--slide=auto` |
| 本地补丁 | `ci/patch-device.py`（ramoops/NativeUfs/wave1） | 不套用，保持上游原样 |
| WiFi / 背光 | 未启用 | **defconfig 里关掉**（上游需要加密 stock release 的 QCA6490 固件和 Linux v7.0.14 源） |
| UniXNU-sheng | 不需要 | 需要（PongPower 编译 `QcomRpmh.c`），用上游 `fetch-pong-native-sources.py` 拉公开仓 |

关键踩坑（按出现顺序）：

1. 组件配方要求 `device/refer/ipados18/*` 的源码检出（`build-ipados18-pong-macos.py:29-34`
   逐个 `git rev-parse` 校验），直接 `unixnu build kernel` 不会 populate —— 加
   `unixnu fetch -D ../device` 与 `fetch-pong-native-sources.py --only sheng_source`；
2. `XNU_PONG_WIFI=1` 会让构建在编译前直接拒绝（缺 Linux ath11k 源），关掉 WiFi/背光；
3. `find -printf` 是 GNU 扩展，macOS BSD find 不支持，`pipefail` 下会让 collect 步骤失败 ——
   改用 `find | sort`；
4. XNU 构建日志在 `$GITHUB_WORKSPACE/build-<stamp>.log`，不在 `out/` 下，collect 要单独收。

## 3. 产物

| 项 | 值 |
|---|---|
| prelinked `kernel.macho` | 51,982,336 B，sha256 `ba5aad5aebca365f3c717d9da83f1acdda224865fb4ef3a331d78f2d512ffc0b` |
| native `mach.development.pong` | 18,038,896 B，sha256 `94a2e33cfca268ad3473443764d247abf26fe37f018df822149f1f4d13d2b960` |
| prelink 日志 | `slide: auto 0x5ed0000 (0xfffffff005130000 -> 0xfffffff00b000000)` |
| 段布局 | `__TEXT=0xfffffff007004000`、`__BOOTDATA=0xfffffff007d38000`、`__PRELINK_TEXT=0xfffffff00b000000` |
| native `kernel.macho`（prelink=false 运行） | 18,038,896 B，sha256 `0be638156074cc8592d680788c63ce38f0457c0ddaebee41ac341fa56d7a45a4` |

由此组装的镜像（官方 `finalize-pong-adt.py` + 原厂 Cape DTB + 菜单修复 FD）：

| 镜像 | 内容 | sha256 |
|---|---|---|
| `F:\liuqin_ios\boot-v112-upstream-prelinked.img` | 上游最新 prelinked 内核 | `5f6d1bcca522d5439900938792754128e927a3397112424eefc09462903e90b6` |
| `F:\liuqin_ios\boot-v113-upstream-native.img` | 同源 native 内核 | `ac1a2428c98bfa15d6b2ffb7b84d09545d5462c62ff34cda6260718601b8ed2c` |

## 4. 实机结果（已验证）

`fastboot boot boot-v112-upstream-prelinked.img` 后：

- 旧内核（v106/v108/v109/v110）：panic 后 **约 75 秒内自动回 fastboot**；
- v112：**280 秒以上没有回 fastboot，也没有出现 adb** —— 说明内核没有复位，
  已越过之前 100% 复现的 `Invalid kernel stack pointer` panic（VM bootstrap 之后那处），
  现在大概率停留在 UFS/根卷或后续初始化阶段等待。

下一步：拍屏确认停在哪一步；再比较 v113（native + 新 crypto/prng 兜底补丁）能否走得更远。

## 5. 复现命令

```bash
# CI（两条线并行）
fly-workflow kernel-upstream.yml  device_ref=fe0e8d790e24 unique_chip_id=84750a0b prelink=true
fly-workflow kernel-upstream.yml  device_ref=fe0e8d790e24 unique_chip_id=84750a0b prelink=false

# 本地取产物 + 组镜像
bash docs/ci-fetch-kernel.sh <run-id> kernel-upstream-encrypted
bash docs/make-v112.sh <run-id> F:\liuqin_ios\boot-v112.img <path/to/kernel.macho>
```
