# 元类哨兵与 base-class gate：主机侧仿真

v43 形状。v42 给 H16 结了案——UFS 绑定脚本把原厂 21H461 kernelcache 的 VA 当成我们镜像的 VA
用了（每个 kext 段低 16 MiB），所以"谁涂写了 `superClassLink`"这个问题从一开始就问错了：
没有涂写，是**读错了地方**。扫描、修复钩子、指纹搜索全部随诊断一起删除，于是这个 harness
也不再镜像 `postModLoad` 的扫描分支——**给已经不在像里的代码做仿真等于证明空气**。

现在跑的是三件仍然在承重的事：

1. **走链**：报告一个未验证的指针，但**永远不解引用它**（v35–v37 那句
   `ldr x24,[x24,#0x10]` 的 abort 就是这么来的），而且撞到环要能封顶；
2. **gate 的判决表**：家族自己占名 / size 不一致 / 别的对象占名 / 闭包里没有家族 / boot-arg 跳过；
3. **哨兵只报告、不修**：修好绑定的唯一正确方式是让链接期地址对，而不是运行时写回去。

    sh run.sh                     # 期望最后一行 ALL SIM ASSERTIONS PASS

`run.sh` 第一步是 `python3 extract.py`：它从生产源码逐字节重抽四个 `.inc`（驱动里三个，
XNU 的 USB 调试补丁里一个——`ring.inc` 是内核那一兆控制台镜像 `lq_ring/lq_append/lq_copy`），
**只要漂移就非零退出**（要更新就 `python3 extract.py --write`）。抽取只做一处替换，并且会打印出来：

    liuqinPtrOk: (uintptr_t)p >= LIUQIN_KERNEL_TEXT_LO   ->   liuqinSimPtrOk(p)

主机没有内核地址空间，所以"落在镜像内"改成"在合成镜像的对象表里"；ASCII 毒值
`0x6131373030666666` 照样不在表里，判定语义与设备上一致。

| 文件 | 来源 |
|---|---|
| `helper.inc` | 驱动的 `liuqin_meta_shim` / `liuqinChainDepth` / `liuqinBaseClassCheck` / `liuqin_base_class_probe`（连续一段，原样） |
| `witness.inc` | 驱动的 `liuqinMetaclassWitness()`：走链、坏则打印、`mc-ok`/`mc-bad`（原样） |
| `gate.inc` | `NativeUfs::startRoot` 的序言：先见证走链 → 读 boot-arg → 走策略（原样） |
| `ring.inc` | XNU 补丁 `zzz-liuqin-usb-debug.patch` 里的 `lq_ring` / `lq_append` / `lq_copy`（原样） |
| `sim.cpp` | IOKit 桩件 + 合成镜像 + 面板（只保留最后 512 B）+ 场景 A–G |

| 场景 | 建模对象 | 期望 |
|---|---|---|
| **A 健康链** | 绑定正确（v42 之后应有的样子） | 走链 depth=3；`U:base-ok`；面板上**一句多余的话都没有**——沉默就是目标状态 |
| **B gate 判决表** | B1 size 倾斜 / B2 别的对象占名 / B3 闭包里没有家族 / B4 `liuqin-native-ufs-skip-gate=1` | `base-abi` 仍放行；`base-alien` 拒绝并把原因打在面板上；`base-free` 放行；带 skip 参数时 alien 也放行 |
| **C 哨兵只报告** | 把 `superClassLink` 换成 ASCII 毒值（= v42 那种错绑定的现场形态） | `U:mc-bad` + 那行带 `bad=`/`link=`/`depth=` 的报告；**字段一个字节都没被改**（不修）；绑定修好后同一个见证必须沉默 |
| D 走链的安全性 | D1 三元环 / D2 一个外来（未对齐、不在像里）地址 | D1 撞到 `LIUQIN_CHAIN_MAX` 就报 `depth=-65`，**不自旋**；D2 返回 `depth=0` 且 `bad=` 指向那个地址，**一次都没解引用**（主机上解引用就是 SIGSEGV，所以断言的是返回值形状） |
| E 控制台镜像 | E1 绕回 / E2 可读窗口 / E3 `log_lock` 被占 | E1 最老的字节读不到，且偏移必须取自 `pos-got`（AGENTS 规矩③）；E2 顺序与 `total` 精确；E3 镜像丢字并计数、面板照样画、一次拷贝就退，**绝不自旋** |
| **F 面板会滚** | 只在 `pre` 打印的结论 | 刷屏之后照片上就没了（`!panelHas`），镜像里还在（`mirrorHas`）；`pub` 那一枪是最后一张票，断言它带着同一行 |
| G 完整序言 | 健康状态下的 `startRoot` 入口 | `mc-ok` → `base-ok`，面板上留下那行 ABI 证据（`live size 248, compiled size 248`） |

桩件里 `IOBlockStorageDevice` 的 `sizeof` 是 248，与实机符号表一致，因此 B1 的
`base-ok`/`base-abi` 两条分支是真的在比同一对数字（v42 实机那行是 `144/144`）。

E/F 两组断言的是**镜像里有什么**（`mirrorHas` 走的也是生产的 `lq_copy`）以及**面板上还留着
什么**（`simPanel` 只保留最后 512 字节，模拟 panic screen 的重绘）；一个只在 stdout 上正确的
实现过不了这一关。AGENTS 里"搜镜像三条规矩"中的第③条（偏移取 `pos-got`）现在只有这里守着——
搜索本身已经随 H16 一起删掉了，但 `lq_copy` 的钳位行为还在生产代码里。
