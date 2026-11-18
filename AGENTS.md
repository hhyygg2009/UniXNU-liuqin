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
| 本板逐轮判读 | `docs/boot-rounds-v35-v40.md`（v35→v40 每轮改了什么、照片看到什么、哪些假设被证伪） |

## 硬性约束（踩过才知道的）

- **只允许 `fastboot boot`。** 不写任何分区。镜像约 69.5 MB，ABL 缓冲够用。
- **打包对账尺寸 69,525,504 B。** 打出 68,476,928 B 就是漏了 `scripts/wrap-payload-mbr.py`，
  PartitionDxe 认不出 ESP，面板停在 payload 打不开。
- **启动参数总长度必须小于 1000 字节。** 加参数就要打算换掉参数。
- **诊断只能走面板里程碑控制台**（前缀 `K`/`B`/`U`/`I`/`T`/`M:`）+ 1 MiB 环形缓冲 + USB 调试口。
  KDB/串口是静默的（`debug=0x14e` 下 "configuring debugging over serial"），别指望它出东西。
- **panic screen 重绘会吃掉控制台**，只留最后几行。关键结论必须**打印在最后一行**，
  否则照片里没有（v37 的 `M:stalled post` 就是这么丢的）。
- **搜控制台镜像的代码，三条规矩**（v40 由仿真逼出来，不是设计时想到的）：
  ① **先搜后印**——报告自己会把 needle 原样印进 `asc="…"`，先印再搜就是拿自己的行当证据，
  还带一个真偏移；② **命中要归类**——第二次报告看见的第一次的自己是真的在环里，所以
  命中点前 32 字节内有 `asc="`/`bad=` 要记进 `self=`，不能算生产者；③ **偏移取 `pos-got`
  不是 `started`**——`lq_copy` 会把过旧的位置钳到最老可读字节，用请求位置算出的偏移指向
  已被覆盖的字节，还会白烧轮数；绕回过就报 `lost=`，别报 `clean`。
- **内核补丁是否真的进像，用反汇编证明，不要用源码 diff 证明。** H16-F/G 这条已经有现成的
  一条命令：`python3 scripts/audit-h16f-image.py`（42 项：符号、格式串、postModLoad 的钩子载入/
  `blr`/下界比较/`str [obj+0x10]`/二次走链/两个点名分支、元类构造函数里两次装钩子的
  `adr`+`str`，以及 H16-G 的调用顺序、helper 的 `br` 派发、报告体内 `liuqin_debug_copy`/`total`/
  `puts` 的调用次数、见证函数里那条重放）。窗口按**符号边界**取，不按猜的长度——否则隔壁函数
  的 `blr` 会让检查假通过。要手工做：驱动侧编译命令在 `out/liuqin/tree/build/native-ufs/compile-command.json`
  （或直接跑 `python3 scripts/build-arm-driver.py NativeUfs`，cwd 必须是
  `build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT`，因为参数里有 `-I .`）；
  反汇编只有 `llvm-objdump --macho -d --section=__text` 会出内容，`--start-address/--stop-address`
  和 `--disassemble-symbols=` 在这个 llvm 构建上输出为空，按地址窗口自己筛。
- **改任何 `patches/xnu/` 文件会让 `.patches.sha256` 变化，下一轮重放整个序列。** 补丁互相重叠，
  第二遍既不能 apply 也不能 reverse-check。

## 记录与检查

`porting.toml` 是设备记录的权威来源（`schema_version = 1`，13 个必需部分 + `cellular` 不适用）。
改代码就要同步更新对应 `[[area]]` 的 `findings` 或 `evidence`；每个代码路径都要归属某个部分。

    python3 ../UniXNU/scripts/porting-record.py check --device ../UniXNU-liuqin
    python3 ../UniXNU/scripts/porting-record.py check --device ../UniXNU-liuqin --base origin/main

主机侧回归（不需要交叉编译，也不需要上机 —— 平板不在时照样能推进一整轮）：

    ./unixnu build kernel && python3 scripts/audit-h16f-image.py      # 生产构建 + 进像证明
    tests/metaclass-simtest/run.sh                                   # ALL SIM ASSERTIONS PASS（A–F）
    python3 drivers/ShengUfs/tests/check_legacy_descriptors.py \
        --output <dir> --compiler g++                                # PASS 14/14
    tests/run-host-test.sh                                           # USB 调试口逻辑

GitHub Actions 的 `Device documentation`（`.github/workflows/porting-record.yml`）跑的就是上面第一条。
