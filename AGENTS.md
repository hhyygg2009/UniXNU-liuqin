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
| 本板逐轮判读 | `docs/boot-rounds-v35-v43.md`（v35→v40 每轮改了什么、照片看到什么、哪些假设被证伪） |

## 硬性约束（踩过才知道的）

- **只允许 `fastboot boot`。** 不写任何分区。镜像约 69.5 MB，ABL 缓冲够用。
- **打包对账尺寸 69,525,504 B。** 打出 68,476,928 B 就是漏了 `scripts/wrap-payload-mbr.py`，
  PartitionDxe 认不出 ESP，面板停在 payload 打不开。
- **启动参数总长度必须小于 1000 字节。** 加参数就要打算换掉参数。
- **诊断只能走面板里程碑控制台**（前缀 `K`/`B`/`U`/`I`/`T`/`M:`）+ 1 MiB 环形缓冲 + USB 调试口。
  KDB/串口是静默的（`debug=0x14e` 下 "configuring debugging over serial"），别指望它出东西。
- **panic screen 重绘会吃掉控制台**，只留最后几行。关键结论必须**打印在最后一行**，
  否则照片里没有（v37 的 `M:stalled post` 就是这么丢的）。
- **搜控制台镜像的代码，三条规矩**（v40 由仿真逼出来。v43 把搜索删了，但 `lq_copy` 的钳位行为
  还在生产里，`tests/metaclass-simtest` 的 E 组仍守着第③条）：
  ① **先搜后印**——报告自己会把 needle 原样印进 `asc="…"`，先印再搜就是拿自己的行当证据，
  还带一个真偏移；② **命中要归类**——第二次报告看见的第一次的自己是真的在环里，所以
  命中点前 32 字节内有 `asc="`/`bad=` 要记进 `self=`，不能算生产者；③ **偏移取 `pos-got`
  不是 `started`**——`lq_copy` 会把过旧的位置钳到最老可读字节，用请求位置算出的偏移指向
  已被覆盖的字节，还会白烧轮数；绕回过就报 `lost=`，别报 `clean`。
- **结案之后要把诊断剪掉。** H16 的诊断在 v43 之前每次启动都跑、每次扫描刷几十行，
  把真正的里程碑顶出面板（面板只留尾巴）——**留着坏诊断比没有诊断更坏**。剪的时候三条一起：
  ① XNU 侧补丁删掉（不是注释掉）；② 驱动里装钩子的那条 `OSDefineMetaClassAndStructorsWithInit`
  退回普通形式；③ **留下的必须是绊线**：`liuqinChainDepth` + `liuqinMetaclassWitness`（只报告、
  不修、不搜）+ base-class gate，因为 `superClassLink` 全 XNU 只有 `OSMetaClass` 构造函数会写，
  绑定哪天再错它第一个开口。**给已删除的代码做仿真等于证明空气**——simtest 的场景随之换对象。
- **1 MiB 控制台镜像只包含 `liuqin_debug_puts/putc` 写的字节**（`liuqin_debug_append` 的全部
  调用点就这两处）。普通 `kprintf/printf` **不进镜像**，而补丁里有 `printf("LIUQIN USB: …=%08x…")`
  这类 8 位十六进制打印。所以 `M:fp … ring clean` 只能排除"我们自己里程碑流的复制"，
  **不能证明"不是控制台文本"**（v40 实机就是这么写过头一格的，见 boot-rounds 的 v40 实机判读）。
- **原厂 kernelcache 的 VA 不能直接当绑定用。** `scripts/build-liuqin-ufs-bindings.py` 从
  21H461 的 cache 里解出家族元类/vtable/方法地址，但我们的 fileset 布局把**四个 kext 段都
  搬低 16 MiB**，而 `__PRELINK_INFO` 搬 +0x774000 —— 既不能用原厂 VA，也不能用一个统一
  slide。唯一正确的做法是按 `out/liuqin/kernel/kernel.json` 的 `coalesced_segments` **逐段
  映射**；落在原厂 kernel 本体段里的对象（继承自内核的 vtable/方法）**不要绑绝对地址**，
  交回链接期按符号名解析。H16 追了五天"谁涂写了 superClassLink"，真相是绑定指向了
  `__common` 里别人的存储（见 boot-rounds 的 v41 根因节）。
- **内核补丁是否真的进像，用反汇编证明，不要用源码 diff 证明。** 现成的一条命令：
  `python3 scripts/audit-h16f-image.py`（v43 起 **62 项**，文件名保留是因为记录里到处引用它；
  它现在同时证明**"该在的在"和"该没的没"**——清理轮次的风险不是新代码没进去，是旧代码还在里面）：
  `S+/S-` 符号在/不在、`T+/T-` 格式串在/不在、`B` 绑定落在我们自己的段里（`__PRELINK_DATA` /
  `__PLK_DATA_CONST`，且没有一条指回 `__common`）、`K` `lck_rw_init(trust_cache_rt_lock, grp, 0)`
  出现在 `kernel_bootstrap_thread` 且**没有** `bl _trust_cache_runtime_init`（两条合起来才证明
  编译的是打了补丁那条 `#else`）、`Q` `amfi` 判空**早于**取锁且返回 `KERN_NOT_FOUND`、
  `P` postModLoad 既不写 `[obj+0x10]` 也不调用任何我们的符号、`C` 元类构造函数只剩一条 store、
  `G` gate 的比较对象要从 `__got` 那 8 个字节里读出来（编译器不放 `adrp/add` 立即数，只查
  adrp/add 会漏掉"链接期就绑错"）。窗口按**符号边界**取，不按猜的长度——否则隔壁函数
  的 `blr` 会让检查假通过。要手工做：驱动侧编译命令在 `out/liuqin/tree/build/native-ufs/compile-command.json`
  （或直接跑 `python3 scripts/build-arm-driver.py NativeUfs`，cwd 必须是
  `build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT`，因为参数里有 `-I .`）；
  反汇编只有 `llvm-objdump --macho -d --section=__text` 会出内容，`--start-address/--stop-address`
  和 `--disassemble-symbols=` 在这个 llvm 构建上输出为空，按地址窗口自己筛。
- **改任何 `patches/xnu/` 文件会让 `.patches.sha256` 变化，下一轮重放整个序列。** 补丁互相重叠，
  第二遍既不能 apply 也不能 reverse-check。**新补丁的文件名要排在所有改同一个文件的既有补丁之后**
  （`zzzz-…` 就是为此），否则从工作树导出的 diff 会把别人的 hunk 烙进来，重放时必炸。
- **`board.toml` 里的 `defconfig` 不是事实，`out/liuqin/.config` 才是。** H17 就是这么来的：
  `board.toml` 写 `liuqin_defconfig`（`CONFIG_XNU_TRUST_CACHE_RUNTIME=y`），而构建实际用的
  `.config` 与 `configs/liuqin_boot_defconfig` 逐字节相同（`=n`），于是 `trust_cache_runtime_init()`
  整块被编译掉，锁从没初始化，`exec init` 每次必炸（`lock_rw.c:1400`）。**想知道某个
  `CONFIG_XNU_*` 有没有生效，去镜像里找证据**：`grep -c <bootstrap字符串> kernel.macho`、
  `llvm-nm` 里那个函数存在但没有任何 `bl` 指向它，都比重复读 defconfig 可靠。
  **已收口（2026-11-19）**：`board.toml` 现在声明实际生效的 `liuqin_boot_defconfig`，`scripts/check-defconfig.py`
  证明"声明 = `out/state.json` 选定 = `.config` 字节"三者一致；换 defconfig 必须同时跑 `unixnu defconfig`，
  否则这条守卫就是红的。

## 记录与检查

`porting.toml` 是设备记录的权威来源（`schema_version = 1`，13 个必需部分 + `cellular` 不适用）。
改代码就要同步更新对应 `[[area]]` 的 `findings` 或 `evidence`；每个代码路径都要归属某个部分。

    python3 ../UniXNU/scripts/porting-record.py check --device ../UniXNU-liuqin
    python3 ../UniXNU/scripts/porting-record.py check --device ../UniXNU-liuqin --base origin/main

主机侧回归（不需要交叉编译，也不需要上机 —— 平板不在时照样能推进一整轮）：

    python3 scripts/check-defconfig.py                                # 声明/选定/.config 三者一致
    ./unixnu build kernel && python3 scripts/audit-h16f-image.py      # 生产构建 + 进像证明（62 项）
    tests/metaclass-simtest/run.sh                                   # ALL SIM ASSERTIONS PASS（A–G）
    python3 drivers/ShengUfs/tests/check_legacy_descriptors.py \
        --output <dir> --compiler g++                                # PASS 14/14
    tests/run-host-test.sh                                           # USB 调试口逻辑

GitHub Actions 的 `Device documentation`（`.github/workflows/porting-record.yml`）跑的就是上面第一条。
