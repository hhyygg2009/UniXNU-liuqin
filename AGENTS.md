# liuqin agent guide

Xiaomi Pad 6 Pro / liuqin / SM8475 设备仓库。本仓库拥有板级 DTS、外设接线、组件选择、
固件输入、分区布局和打包；XNU 侧改动以补丁序列的形式拥有在 `patches/xnu/`。

## 必读

改动之前先读这些（与 README 的入口一致）：

| 任务 | 必读 |
| --- | --- |
| 任何移植 | [Porting](https://unixnu.com/zh/porting/)、[设备仓库](https://unixnu.com/zh/porting/device-repository)、[组件目录](https://unixnu.com/zh/reference/components)、UniXNU `components.toml` |
| 适配记录 | [设备适配记录](https://unixnu.com/zh/porting/records)，尤其是[发布与复用](https://unixnu.com/zh/porting/records#publishing-and-reuse)；本仓库的记录是 `porting.toml` |
| 硬件 | [设备树](https://unixnu.com/zh/porting/device-tree)、`docs/liuqin-hardware.md`、`docs/vendor-inputs.md` |
| 存储与根卷 | [UFS 存储](https://unixnu.com/zh/porting/ufs)、`drivers/ShengUfs/`（分支 `liuqin-sm8475`） |
| 内核 | [修改 XNU](https://unixnu.com/zh/xnu/source-and-patches)、`patches/xnu/` 序列与 `.patches.sha256` 戳 |
| 镜像与启动 | [构建与启动](https://unixnu.com/zh/building)、[启动验证顺序](https://unixnu.com/zh/porting/bring-up)、[日志抓取](https://unixnu.com/zh/porting/log-capture) |
| 本板逐轮判读 | `docs/boot-rounds-v35-v38.md`（v35→v38 每轮改了什么、照片看到什么、哪些假设被证伪） |

## 硬性约束（踩过才知道的）

- **只允许 `fastboot boot`。** 不写任何分区。镜像约 69.5 MB，ABL 缓冲够用。
- **打包对账尺寸 69,525,504 B。** 打出 68,476,928 B 就是漏了 `scripts/wrap-payload-mbr.py`，
  PartitionDxe 认不出 ESP，面板停在 payload 打不开。
- **启动参数总长度必须小于 1000 字节。** 加参数就要打算换掉参数。
- **诊断只能走面板里程碑控制台**（前缀 `K`/`B`/`U`/`I`/`T`/`M:`）+ 1 MiB 环形缓冲 + USB 调试口。
  KDB/串口是静默的（`debug=0x14e` 下 "configuring debugging over serial"），别指望它出东西。
- **panic screen 重绘会吃掉控制台**，只留最后几行。关键结论必须**打印在最后一行**，
  否则照片里没有（v37 的 `M:stalled post` 就是这么丢的）。
- **内核补丁是否真的进像，用反汇编证明，不要用源码 diff 证明。** 取 `compile_commands.json`
  里该文件的原始编译命令、换掉源文件路径即可在生产参数下验证编译；再用 `llvm-nm` +
  `llvm-objdump --macho -d --section=__text --start-address=… --stop-address=…` 看调用点。
- **改任何 `patches/xnu/` 文件会让 `.patches.sha256` 变化，下一轮重放整个序列。** 补丁互相重叠，
  第二遍既不能 apply 也不能 reverse-check。

## 记录与检查

`porting.toml` 是设备记录的权威来源（`schema_version = 1`，13 个必需部分 + `cellular` 不适用）。
改代码就要同步更新对应 `[[area]]` 的 `findings` 或 `evidence`；每个代码路径都要归属某个部分。

    python3 ../UniXNU/scripts/porting-record.py check --device ../UniXNU-liuqin
    python3 ../UniXNU/scripts/porting-record.py check --device ../UniXNU-liuqin --base origin/main

主机侧回归（不需要交叉编译，也不需要上机）：

    tests/metaclass-simtest/run.sh                                   # ALL SIM ASSERTIONS PASS
    python3 drivers/ShengUfs/tests/check_legacy_descriptors.py \
        --output <dir> --compiler g++                                # PASS 14/14
    tests/run-host-test.sh                                           # USB 调试口逻辑

GitHub Actions 的 `Device documentation`（`.github/workflows/porting-record.yml`）跑的就是上面第一条。
