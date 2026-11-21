# B1 落地细节：把根窗口放到 `unixnu` 分区

> 结论：**内核侧只需要改一行**。驱动把分区名当启动参数读，几何只做下界检查。

## 1. 驱动怎么找根分区（已核实）

`drivers/NativeUfs/PongUfsTransport.cpp`：

```c
char rootPartition[32] = "userdata";
PE_parse_boot_argn("ufsroot", rootPartition, sizeof(rootPartition));      // :785-786
if (!findRootLayout(&layout, &why, rootPartition, true)) return transportFailure(why);
```

- `findRootLayout()` 在 LUN0 的主 GPT 里**按名字**找分区（`RootLayout.h:57-61`），
  窗口偏移来自镜像内部的 SHNGROOT 清单，不是硬编码 LBA。
- 唯一的几何检查（`:798`）是：
  ```c
  layout.userdataLast < layout.userdataFirst || layout.userdataLast >= gHost.blocks
  ```
  → 只要分区在 LUN 范围内、且镜像不大于分区即可。源码注释明确写着
  "Do not require one exact partition size here so a 32 GiB image also boots on a
  device whose GPT reserves more space."
- `kPongUserdataFirst4k / Last4k`（`:54/:58`）只用于诊断记录，不参与校验。

## 2. 因此改动只有两处

| 位置 | 改动 | 状态 |
|---|---|---|
| `UniXNU-liuqin/board.toml`，`kernel_args` 里的 `"ufsroot=hfs"` | 改成 `"ufsroot=unixnu"` | **已做**，分支 `liuqin-unixnu-partition`（`5c8a6e5`）已推到 [Fly116/iPadOS18-liuqin](https://github.com/Fly116/iPadOS18-liuqin) |
| 主机侧安装脚本 `scripts/install-ipados18-ufs.py` | 它写死 `/dev/block/by-name/userdata` 且要求 `readlink -f` 等于 `/dev/block/sda13` | 待改：目标换成 `unixnu`（`sda36`），去掉 `sda13` 断言，保留"无挂载/无 holder"检查 |

## 3. 空间

| 项 | 值 |
|---|---|
| 我们的 `unixnu`（`sda36`） | 12,367,852 KiB = 12,664,680,448 B ≈ **11.79 GiB** |
| 参考实现的 SHNGROOT 全量镜像 | LAYOUT 记录为 **11 GiB** 级 |
| 判断 | 装得下，但余量不多，`Data` 窗口若要扩需谨慎 |

## 4. 执行顺序（当前在等第一步）

1. **基线构建**（正在进行，未改任何东西）：确认他们的流水线在我们 fork 上能跑通
2. 基线通过后，把 CI 的 device checkout 从 `QPRS-6/iPadOS18-liuqin@38ca105d`
   换成我们自己的 `Fly116/iPadOS18-liuqin@liuqin-unixnu-partition`
3. 用 product 里的内核 + 根镜像，按 `fastboot boot` 上机，启动参数带 `ufsroot=unixnu`
4. 只有出现 SHNGROOT 解析失败/窗口不匹配时，才回头改驱动

## 5. 仍然未知的

- 基线构建能否成功（他们自己的流程也是迭代出来的）
- SHNGROOT 镜像的实际字节数（要等 CI 产物）
- 他们的内核是否依赖 `hfs` 分区存在（例如 GPT 里必须有该名字的条目）—— 目前从代码看只依赖 `ufsroot` 指定的那个
- Metal 链路（Mesa/Turnip/Rust bridge）在我们这边的可用性
