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
| 本板逐轮判读 | `docs/boot-rounds-v35-v44.md`（v35→v40 每轮改了什么、照片看到什么、哪些假设被证伪） |

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
  ④ **里程碑叠加层本身也要能关**：`liuqin_milestone` 用 `liuqin_debug_puts` 绕过控制台层直接写
  framebuffer（它自己的注释写着 "the panel scrolls whole pages"）。文本控制台一旦可用
  （`K17:console_init` 之后，`debug=0x14e`），两套写入落在同一片区域，照片就是一屏重影。
  v65 起 `startup.c` 的门读 `liuqin-milestone`（默认 1，别的板子不受影响），liuqin 传 `=0`。
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
- **给 codeless 组件加 boot arg 之前，先在镜像里数它的致命串。** AMFI / AppleImage4 / APFS 这些
  组件是**原厂二进制**（`kernel.json` 的 `codeless_components`），我们没有它们的源码，它们对
  参数的态度只有"炸"和"不炸"两种。v43 加了 `cs_enforcement_disable=1`，AMFI 在自己 `start()`
  里就 `panic("can't has cs_enforcement_disable")`（`AppleMobileFileIntegrity.cpp:5261`，
  在 `mac_policy_initmach → KLDBootstrap::loadSecurityExtensions()`，K51），比它要绕开的那堵墙
  早 16 个启动阶段。一条命令就看清：`strings -a kernel.macho | grep "can't has"`——全镜像只有
  这一条，而 `amfi_get_out_of_my_way` 没有任何 fatal 检查，所以它留、`cs_enforcement_disable` 撤。
  audit 的 `Y-` 段把这件事变成了静态守卫（参数不在 payload 里 + 那条串恰好 1 次 + 无辜那条 0 次）。
  还有个坑：XNU 把站点**烤进字面量**，镜像里存的是 `"message @%s:%d"`，所以按串找要搜**前缀**、
  别带 `\0`（`check-trust-cache-image.py` 的 `P` 组起初五项全 0，就是栽在这儿）。
- **驱动子模块的 `origin` 必须是官方仓 `https://github.com/UniXNU/ShengUfs.git`。** `unixnu tree` 只有在
  `components.official_alias()` 认这个身份时才创建 legacy 别名 `drivers/NativeUfs`（和
  `build/native-ufs/libNativeUfs.a` 兼容链），而 `board.toml` 的 `[kernel].archives` 写的正是
  `build-arm-driver.py NativeUfs`。把 origin 指到 fork（`Fly116/ShengUfs` 或 `hhyygg2009/ShengUfs`）之后，
  报错在整整一层抽象之外：`clang-24: error: no such file or directory:
  .../out/liuqin/tree/drivers/NativeUfs/NativeUfs.cpp`（2026-11-19 22:44 试 `test-pr1` 就是这么把构建弄死的）。
  fork 留成 remote `mine`、维护者留成 `fly116`，`.gitmodules` 继续指 fork（记录的 commit 才公开可解析）。
  一句话自检：`./unixnu tree && ls -l out/liuqin/tree/drivers/NativeUfs`。
- **证据引用不要指进子模块，CI 看不见那里。** UniXNU 的复用工作流
  （`.github/workflows/porting-record.yml` → `UniXNU/UniXNU/.github/workflows/porting-record.yml`）
  只用 `actions/checkout@v4` 检出设备仓，**不初始化子模块**，而检查器要求非 HTTPS 的
  `reference` 必须是仓里真实存在的文件（`evidence file missing`），所以
  `reference = "drivers/ShengUfs/NativeUfs.cpp"` 这类条目会把 `Device documentation` 直接打红，
  本地却永远复现不出来（本地子模块是满的）。规矩：跨仓的代码证据一律用 HTTPS permalink，
  并且**逐条核 blob** —— `tests/check_legacy_descriptors.py` 在公开 `26ae865ea314` 与当初跑测试的
  私有 gitlink `433e7fdd5735` 上是同一个 `654934e7f0f6`，这条链接才算数。
  自检：`git worktree add /tmp/x <分支> && python3 ../UniXNU/scripts/porting-record.py check --device /tmp/x`
  （干净检出 = CI 条件）。
- **驱动 H16 的中间态没有公开可解析的 commit。** `Fly116/ShengUfs` 上只有 v39 重写的
  `adc855a5a05a`（`liuqinBlockSuperClass`/`liuqinBlockAdoptSuperClass`/`liuqinBlockChainOk`）和
  退役后的 `26ae865ea314`；`433e7fdd5735`、`8abb519365d7`、`91c6fcc35d1c`、`c36c575fe0a0` 和
  `liuqinBaseClassFree`/`liuqin_block_super_fix`/`liuqinMetaclass*` 都在维护者的私有克隆里，
  三个公开 ShengUfs 都查不到。所以那段历史引 `docs/boot-rounds-v35-v44.md`，不要写成能点开的源码；
  要恢复可复核性就把这几条推到 fly116 或 mine 的分支上（storage 的 `next_action`）。
- **schema 2 是"没选型 + 没复验"，不是缺输入，也不是没改。** 2026-11-21 在分支 `schema2`（`00a131d26ec1`）实测：
  `unixnu status --json` 拒绝 —— `schema_version = 2 requires [board] soc_source and os_profile`，
  UniXNU 组织里没有 SM8475 共享仓（只有 `UniXNU-sm8550-common`），**但社区有三个可以直接钉**：
  `a77710178a/UniXNU-sm8475-common`（`cd36e7097899`，`soc.toml` + 两条 soc series + `dts/sm8475.dtsi`）、
  `Emilelu/UniXNU-sm8475-common`（`c5b50c3927a5`）、`document97/UniXNU-soc-seed`（`e0030d06d78d`，
  `migration/format2/soc-seed`；站点上另一个 liuqin 格式 2 条目钉的就是它）。所以这不是缺输入，
  是**选型 + 复验没做**：要拿本板实测（`docs/liuqin-hardware.md`）比寄存器/中断/CPU/timer，
  并把载体 dtsi 编出的 DTB 与厂商 `liuqin.dtb` 对比。
  **2026-11-21 第三轮把这些做完了（分支 `soccarrier-trial`，`f1f6002`）**：钉
  `document97/UniXNU-soc-seed`（`e0030d06d78d`，`feeds` 里 `path = migration/format2/soc-seed`）后
  `unixnu status --json` **rc=0 / schema_version = 2**，`patch_order` 73（arch/os 57 + kit 2 +
  device 14 + soc 0）；钉 `a77710178a/UniXNU-sm8475-common`（`cd36e7097899`）是 75，多一条
  `native-smp-secondary.patch` 与板级同名不同内容 → 撞车 5 处。**载体不再是阻塞项。**
  `dtc 1.7.2` 编译 `dts/liuqin.dts` 再反编译，与 `../uefi-liuqin/Resources/DTBs/liuqin.dtb`
  （sha256 `ce90f88b…`）**diff 0 行**：1717/1717 节点、827 compatible、157 interrupts 全等，
  blob 只差 22 字节（dtc 串表打包）—— 设备树等价性不用等上机。
  **现在真正挡着的是两件事**：① 4 处同名不同内容且行区间真重叠的补丁要重基
  （`native-4k-signature-hash-cache`→`ubc_subr.c`+`ubc_internal.h`；
  `offline-component-load-probes`→`bsd_init.c`+`IOUserClient.cpp`；
  `zzy-native-gpu-addon-mount`→`kern_exec.c`+3 个 bindfs 文件；
  `zzy-staged-system-apps`→`kern_exec.c`），两边 hunk 数相同，是同一改动的分叉副本；
  ② schema 2 多带 6 条上游补丁（`native-lookup-trace`、`native-union-lower-directory`、
  `native-work-interval-instance`、`zzwb-native-sandbox-grant-set-properties`、
  `zzyb-native-cryptex-os-view`、`zzyc-owner-skip-launch-constraints`），内核会变，
  必须干净检出重建 + 上一轮机，之后才翻 main。
  补丁账也在这轮量化清楚：60 条顶层补丁里 46 条与上游 arch/os profile 逐字节相同（schema 1 解析不了
  profile 根才复制进来的），真正的板级 series 是 14 条。**内核会变，所以必须整轮端到端复验，
  不能顺手把 main 翻成 2**；board 停在 1，`sync-devices` 就按 `format = 1` 收录，
  站点只给仓库链接、不生成设备页（`format = 2 if board.schema_version == 2 else 1`）。
- **上机前看 `out/liuqin/payload/payload.json` 的 `kernel_args`，别看镜像文件名。** `out/` 会被上一轮试验
  留在别的配置上（2026-11-19 那一晚它就停在 `test-pr1` 的 payload，参数里连 `amfi_get_out_of_my_way` 都没有），
  而 `releases/*.img` 才是发出去的那个。抓它的正是 `audit-h16f-image.py` 的 `Y` 段。
- **翻一个 `CONFIG_XNU_*` 开关 = 造一个"该在的在、该没的没"互斥证明。** 做法：复制实际生效的那份 defconfig
  （现在是 `liuqin_boot_defconfig`）→ 只翻一个开关 → 改 `board.toml` 的 `defconfig` 声明 →
  `./unixnu defconfig <新名>`（`check-defconfig.py` 会守"声明==选定==.config"）→ 构建 →
  `audit-h16f-image.py` **应该只挂在断言旧分支的那几项上**，其余全过。两项互斥的通过集合就是"开关真的进了镜像"
  的证据；否则就是 H17 第二回。
- **内核补丁是否真的进像，用反汇编证明，不要用源码 diff 证明。** 现成的一条命令：
  `python3 scripts/audit-h16f-image.py`（v44 起 **64 项**，文件名保留是因为记录里到处引用它；
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

- **`UNIXNU_PATCH_LIST` 只要非空，补丁改动就全部失效。** `build-xnu-4k-baseline.sh:52` 把
  戳计算与重放整段包在 `if [ -z "${UNIXNU_PATCH_LIST:-}" ]` 里；戳 = `xnu-work` 的 HEAD +
  每个 `patches/xnu/*.patch` 与 `kits/XnuPrelink/patches/*.patch` 的 sha256。我们的配方曾
  `export UNIXNU_PATCH_LIST=manual`，结果**连续两轮构建产出与上一轮逐字节相同的
  `kernel.macho`**（删掉的补丁还在、新加的门没进去），而日志一切正常。两条规矩：
  ① 动过补丁前先零风险算一遍戳对比 `xnu-work/.patches.sha256`，别靠构建去试；
  ② 重放会 `git reset --hard` + `clean -fdq`，**把 `patches/xnu/manual/` 生成器的产物一起冲掉**
  （D1 的 `liuqin_dump_waiting_threads` / `liuqin_iokit_job_counts` 不在补丁序列里），
  所以重放后必须补做 manual 步骤再链接，否则报 `Undefined symbols`——
  这不是绑定坏了，是树被重置了。回滚手段：`build/xnu-work.v63-state.patch` 是整棵树
  补丁态的快照（HEAD `d8b8029` + 193,912 B diff），`reset --hard` 后 `git apply` 即可复原。
- **`unixnu` 会记住你给过的 `-D` 设备仓。** `config.py:120` 的解析顺序是
  `args.device → $UNIXNU_DEVICE → state['device']`，注释写着 "it is remembered afterwards"。
  于是跑一次 `unixnu fetch -D <另一个克隆>`（比如为了跑记录检查）之后，**所有不带 `-D` 的构建
  都换到了那个克隆**——日志里 `== submodules …` 那行换了路径，而它的 `.cache/firmware` 是空的。
  规矩：构建脚本必须显式 `export UNIXNU_DEVICE=<路径>`，不依赖被记住的状态。
- **`pkill -f "<命令名>"` 会杀掉执行它的这个 shell 自己**（命令行里含同样的字符串）。
  本项目长构建多，症状是重试分支被截成 `rc=143`（SIGTERM），看起来像构建失败。
  要么用 `pgrep` 先确认 PID 再按 PID 杀，要么写成正则不匹配自身的形式（`[u]nixnu build`）。

## 记录与检查

- **每个代码路径都要归属某个 `[[area]]`；漏了平时看不出来，一改就红。** `paths` 里少了
  `feeds.toml`、`unixnu.lock`、`local.toml.example`、`proprietary-files.txt`、`dts/**`、
  `stubs/**`、`tests/run-host-test.sh` 时结构检查照过；等这轮真去改 `feeds.toml`，
  `check --base` 立刻报 `feeds.toml: add its owner area and paths to porting.toml`。
  自检（要 0 无归属才算干净）：

      python3 -c 'import tomllib,fnmatch,subprocess as sp;a=[x["paths"] for x in tomllib.load(open("porting.toml","rb"))["area"] if x["status"]!="not-applicable"];f=[p for p in sp.run(["git","ls-files"],capture_output=True,text=True).stdout.split() if not p.endswith((".md",".rst")) and p!="porting.toml"];print(len(f),"files;",[p for p in f if not any(fnmatch.fnmatchcase(p,x) for ps in a for x in ps)])'

- **`.cache/firmware/` 不进仓库。** 规范把专有输入放在工作树的 `.cache/firmware`，靠
  `proprietary-files.txt` 记来源与哈希。本仓曾提交过三样：一个指向
  `/home/hhyygg/.dsh-qqbot/workspace/unixnu-port/UniXNU/out/ipsw/21H461/kernelcache.decompressed`
  的**符号链接**（别人克隆过来就是死链，还把本机路径公开出去），以及 `original-static-system.bundle`
  与它的 json（Apple 派生二进制，`sha256 7416ca4f…` 已在 `proprietary-files.txt` 和
  `[payload] trust_cache_sha256` 里钉住，`scripts/fetch-liuqin-trust-caches.py` 能从本地 21H461
  重新生成）。现在 `.gitignore` 加 `.cache/`、从索引移除，本机文件没动所以构建照旧。
  自检：`git ls-files .cache` 必须为空。历史里那几个对象还在，要不要重写提交由维护者单独决定。

`porting.toml` 是设备记录的权威来源（`schema_version = 1`，13 个必需部分 + `cellular` 不适用）。
改代码就要同步更新对应 `[[area]]` 的 `findings` 或 `evidence`；每个代码路径都要归属某个部分。

    python3 ../UniXNU/scripts/porting-record.py check --device ../UniXNU-liuqin
    python3 ../UniXNU/scripts/porting-record.py check --device ../UniXNU-liuqin --base origin/main

主机侧回归（不需要交叉编译，也不需要上机 —— 平板不在时照样能推进一整轮）：

    python3 scripts/check-defconfig.py                                # 声明/选定/.config 三者一致
    ./unixnu build kernel && python3 scripts/audit-h16f-image.py      # 生产构建 + 进像证明（64 项）
    python3 scripts/fetch-liuqin-trust-caches.py                     # trust cache：从本地 IPSW 抽 bundle，必须命中 pin
    python3 scripts/check-trust-cache-image.py                       # TC 线 =y 分支的 21 项守卫（K/B/L/Q/P/T/C/A）
    tests/metaclass-simtest/run.sh                                   # ALL SIM ASSERTIONS PASS（A–G）
    python3 drivers/ShengUfs/tests/check_legacy_descriptors.py \
        --output <dir> --compiler g++                                # PASS 14/14
    tests/run-host-test.sh                                           # USB 调试口逻辑

GitHub Actions 的 `Device documentation`（`.github/workflows/porting-record.yml`）跑的就是上面第一条。
