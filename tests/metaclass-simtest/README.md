# OSMetaClass::postModLoad 主机侧仿真

不交叉编译、不上机就能跑：桩件复刻 XNU 的元类登记批次与 `postModLoad` 第一轮扫描的四条
分支，把实机照片上的崩溃形状在主机上重现，并验证**驱动侧策略**（`drivers/ShengUfs`）与
**XNU 侧扫描护栏/修复**（`patches/xnu/zzzzzzzzzz-liuqin-metaclass-stalled-diagnostics.patch`）。

    sh run.sh                     # 期望最后一行 ALL SIM ASSERTIONS PASS

`run.sh` 第一步是 `python3 extract.py`：它从生产源码逐字节重抽三个 `.inc`，**只要漂移就
非零退出**（要更新就 `python3 extract.py --write`）。抽取只做一处替换，并且会打印出来：

    liuqinPtrOk: (uintptr_t)p >= LIUQIN_KERNEL_TEXT_LO   ->   liuqinSimPtrOk(p)

主机没有内核地址空间，所以"落在镜像内"改成"在合成镜像的对象表里"；ASCII 毒值
`0x6131373030666666` 照样不在表里，判定语义与设备上一致。

| 文件 | 来源 |
|---|---|
| `helper.inc` | 驱动的 `liuqin_meta_shim` / `liuqinChainDepth` / `liuqin_block_super_fix` / `liuqinInstallMetaclassFix` / `liuqinBaseClassCheck` / `liuqin_base_class_probe`（连续一段，原样） |
| `witness.inc` | 驱动的 `liuqinMetaclassWitness()`（原样） |
| `gate.inc` | `NativeUfs::startRoot` 的 H16-F 序言：装钩子 → 见证字段 → 读 boot-arg → 走策略（原样） |
| `sim.cpp` | IOKit 桩件 + `postModLoad` 第一轮扫描的**逐分支镜像**（不是抽取：那段代码嵌在大函数里） |

| 场景 | 建模对象 | 期望 |
|---|---|---|
| A 重名理论（已死，留作证据） | 批次里出现第二个 `IOBlockStorageDevice` 名字 | 扫描进入重名分支（`-2`），这正是 v36 gate 想挡的东西 |
| **B（v39 策略）** | 名字被**家族本身**占用（iOS 17 闭包的常态） | 放行并打 `U:base-ok`；size 不一致时 `U:base-abi` 仍放行；被**另一个对象**占用时 `U:base-alien` 拒绝 |
| C 闭世界 | 家族没进预链闭包 | `U:base-free` 放行 |
| D（v37/v38） | 批次干净，但某项 `superClassLink` 是 ASCII 毒值 | D1 裸走链复现 `postModLoad+0x2d4` abort；D2 关闭修复时**只丢那一项**，其余照常登记，`sDeepestClass` 语义不变 |
| **E（H16-F）** | 同一毒值 + 驱动装了修复钩子 | E1 扫描点名 `M:bad[1] fixed`、`healed=1 dropped=0`、两项都登记、depth=3；E2 `startRoot` 的见证把字段改回家族对象；E3 家族自身链坏时钩子**拒绝交指针**，退回丢弃；E4 只有名字精确等于 `NativeUfsBlock` 才给超类 |

桩件里 `IOBlockStorageDevice` 的 `sizeof` 是 248，与面板 `sz=248` 对齐，因此 B 的
`base-ok`/`base-abi` 两条分支是真的在比同一对数字。
