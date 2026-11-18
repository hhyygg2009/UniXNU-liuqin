# 逐轮判读：v35 → v40（2026-11-16 → 2026-11-18）

K49:PE_init_iokit 处的 `Kernel data abort` 从 v35 第一次可读，到 v38 被彻底消掉并点名。
这一页记录每一轮改了什么、照片看到什么、以及被证伪的假设——避免以后走回头路。
v39 与 v40 都是"平板不在手边"的轮次：改动、主机侧证据和判读表写全，照片栏留空。
v40 把"谁写的这 8 个字节"变成一条可以打印的答案，而它的全部依据来自仿真——
那面一兆控制台镜像在主机上是一比一复刻的，所以搜得到搜不到、偏移对不对，不用上机就能判。

诊断通道：面板里程碑控制台（前缀 `K` 内核 / `B` BSD / `U` 驱动 / `I` 挂载 / `M:` 元类扫描）
+ 1 MiB 环形缓冲；v35 起 panic 直接上屏（`patch-xnu-entry-probe.py --panic-screen --panic-only`）。
所有轮次都用 `fastboot boot` 纯内存运行，不写任何分区。

## 版本线

| 轮 | 内核 sha | 改动 | 实机结果 |
|---|---|---|---|
| v35 panic-screen | `ecc3508b`（源同 v34b） | 只动 payload：SCTLR/ISB 与 MOVZ/CPACR 两对锚点独立定位（liuqin 17.7.11 的 CPACR 对在 MMU 写后 +0x34，非固定 +0x20）；新增 `--panic-only`（4K 基线的 `_start` 是内联序言，不是可搬移的单条 `b`，故只装 panic 漏斗不装彩色条）；`panic_screen_only=true` | panic 第一次上屏。`K49:PE_init_iokit` → `K31/K32` 后 `Kernel data abort pc 0xfffffff00789287c` = **OSMetaClass::postModLoad+0x288** |
| v36 gate | `5687393d` | 驱动侧 `driver-H16.diff`：`startRoot` 入口 `getMetaClassWithName("IOBlockStorageDevice")`，名字被占就在任何分配前拒绝发布，输出 `U:base-dup` | 同一 pc。`U:*` 一行都没出现 ⇒ 崩在 `startRoot` 之前，gate 根本没机会跑 |
| v36b | `5687393d` | 与 v36 同一内核，重打包/回归轮，无新代码 | 同上 |
| v36c | `e53fc818` | probe 抗内联（gate 调用不被优化掉） | 仍崩同一 pc，但 far 从 typed-free 毒化变成 `'ffffff'` 型 ASCII |
| v37 stalled-diag | `6b3a208c` | 转到 **XNU 侧**：`M:stalled pre/post` 里程碑 + 第一遍循环对 `classes[i] < 0xfffffff000000000` 的脏槽位护栏 | 崩点后移到 **postModLoad+0x2d4**；面板出现 `M:stalled pre … cnt=0 c0=0 c1=0`，且没有 `M:stalled dirty!` |
| v38 chain-dump | `49247f07` | 逐槽命名 + 祖先链校验 + **跳过坏槽而非整批拒绝**；结论行放最后 | **崩溃消失**，第一次走完 IOKit 引导到挂载决策 |
| v39 heal | `b261c1b6` | 扫描撞到坏链时向驱动要链接期超类，验过再写回；策略不再因"名字被占"拒绝；`M:mc pre/pub` 见证 | **未上机**（平板不在手边），主机侧 12 项审计 + A–E 仿真 |
| v40 fingerprint | `0f6b131d` | 第二个钩子：把毒值按字符打出来，并回搜 1 MiB 控制台镜像；`hits/self/lost/at/after` + 证据行重放 | **未上机**，主机侧 42 项审计 + A–F 仿真 |

## v37 判读：脏数组假设被否证

反汇编 v37 镜像（`postModLoad` 基址 `0xfffffff0078926a0`）+ 符号表 + 照片 IMG_7051–7054：

- pc `0xfffffff007892974` = **+0x2d4** = `ldr x24,[x24,#0x10]`，是 `while ((me = me->superClassLink)) depth++;`
  的**第一次跳转**，从 +0x2ac 的 `cbz x0` 进入 ⇒ `getObject` 返回 NULL，**根本没进重名分支**；
- lr `0xfffffff00789294c` 正是那次 `blr getObject` 的返回地址 ⇒ `classes[i]` 是合法元类、`className` 可读；
- x24 = `0x6131373030666666`（小端字节 `f f f f 0 0 7 a`），far = x24 + 0x10 ✓ 自洽；
- x22 = `0xfffffff2c33c3140` = 面板 `M:stalled pre` 的那个 handle ✓ 同一次调用。

⇒ stalled 数组本身是干净的（零填充有效），**H16-D 的"脏数组"假设被否证**；被涂的是某个合法元类对象的
`superClassLink` 字段（偏移 +0x10）。H16-D 的护栏只查 `classes[i]` 本身，所以拦不住，崩溃只往后挪了 0x4c。

链接期绑定也排除掉了：驱动 `NativeUfsBlock::MetaClass` 构造器是 `ldr x2,[0xfffffff007a0c008]`（GOT 槽），
该槽在镜像里的内容 = `0xfffffff007add1d0` = `__ZN20IOBlockStorageDevice10gMetaClassE` ✓ 正确。
所以是**运行时涂写**，必须先知道涂的是哪个类。

## v38 判读：点名成功，两个前提被推翻

启动线（v35–v37 从未越过 K49）：

```
K49:PE_init_iokit → K50 early_boot … K57:PE_lockdown_iokit → U58:probe-expert_init
→ B01:bsd_init … B101:at_parse_rd  B102:at_from_chosen  B103:at_di_ramfile
→ U59:start-root_init → NativeUfs: IOBlockStorageDevice is already registered as
   IOBlockStorageDevice; root publication refused → I60:base-dup
→ Failed to mount root device @IOKitBSDInit.cpp:989      ← 可读 panic，不再是 data abort
```

关键三行（IMG_7061，`vt` 已放大复核）：

```
M:stalled post h=0xfffffff568caffe0 cnt=172 c0=0xfffffff007c1c130 c1=0xfffffff007adfcd8
M:bad[170] bad-chain p=0xfffffff007ae36b0 vt=0xfffffff0072086d0 sz=248 n=NativeUfsBlock bad=0x3135205b0a293061
M:stalled scan BAD h=0xfffffff568caffe0 cnt=1 c0=0xac c1=0x6
```

| 面板 | 独立验证 |
|---|---|
| `p=0xfffffff007ae36b0` | nm 里 `__ZN14NativeUfsBlock10gMetaClassE` **逐位相同** |
| `vt=0xfffffff0072086d0` | 构造器 `adrp 0xfffffff007208000 + add #0x6d0` ✓ |
| `sz=248` | 构造器 `mov w3, #0xf8` = `sizeof(NativeUfsBlock)` ✓ |
| `c0=0xfffffff007c1c130`（classes[0]） | nm 里 `sOSMetaClassMeta` ✓ 数组确实干净且语义正确 |
| `cnt=1` / `c0=0xac`(=172) / `c1=0x6` | 172 项里只有第 170 项坏；depth 统计未被破坏 |

`bad=0x3135205b0a293061` 的小端字节 = `a 0 ) \n [ 5 1` —— **含换行的控制台文本**。
驱动自己的静态量已排除：`native_ufs_extra_atos[8]` / `dpu_smmu[24]` / `dpu_state` / `dpu_check[8]`
与 gMetaClass 在 `__common` 里首尾精确相接（`0x7ae360c→0x7ae362c→0x7ae368c→0x7ae3690→0x7ae36b0→0x7ae36d8`），
且全是 uint32 数值，不是文本源。

**被推翻的两个前提**

1. 崩溃**不是**元类重名 panic。`getObject` 返回 NULL，我们的类登记在自己的唯一名字 `NativeUfsBlock` 下
   （v35 时把它读成"重名 → panic → 解引用已释放 `orig->reserved`"，是因为当时还没有逐槽命名）。
2. 因此 v36 的 gate 是**假阳性**：家族预链就必然合法持有 `IOBlockStorageDevice` 这个名字，
   gate 见就拒绝 ⇒ v36c/v37/v38 必然走到 `base-dup` + 挂载失败。它保住了命，但也永久挡住了根。

## v39 计划（社区实机证明过的形态）

来自 **N1709/UniXNU-whyred** 的 round 117–145 复盘（它踩的是同一个函数）：

1. 手写 `class MetaClass : public OSMetaClass`，超类指针用 `static const OSMetaClass *superClass`
   **运行时填**——直接取 gate 已经查到的家族 `IOBlockStorageDevice` gMetaClass，
   把 gate 从拦路石变成指针来源；
2. 显式 `preModLoad(g_kernel_kmod_info.name)` → 构造 → `postModLoad(handle)`，
   我们的类**不再进那 172 项批次**；
3. **重入闩**：`gIOResources` 的 `doServiceMatch` 每次 `publishResource()` 都会重新触发通知且
   **忽略返回值**，`return false` 不退订 ⇒ 第二次跑就进重名 panic 分支；
4. ABI 闸 `base->getClassSize() == sizeof(NativeUfsBlock)`（面板已给 248，正好对表）；
5. 注册前后各打一行 `M:reg`（对象地址 + `superClassLink` 值）——涂写若再现，就能区分
   是**注册前就有**还是**注册期间被写**。

副作用正好治本：运行时填 `superClass` 会在受控时刻把被涂的字段改回正确值。
H16-E 的扫描护栏保留（它是这次救命的东西）。

## 主机侧回归

- `simtest`（ASan/UBSan）：A 无 gate→复现面板崩溃形状；B 有 gate→拒绝且 `U:base-dup`；
  C 闭世界（无家族）→放行；**D（v38 新增）→复现 v37 的 `postModLoad+0x2d4` abort，
  并证明 v38 的扫描只丢掉那个中毒槽、其余照常登记、depth 语义不变**。
- 上游 descriptor oracle `drivers/ShengUfs/tests/check_legacy_descriptors.py`：**PASS 14/14**。

## 交付物

- 内核 `49247f07`；`boot-liuqin-v38-chain-dump.img` 69,525,504 B
  sha256 `08a01ee150dd0979f99905b278d9b5c66434c1352d7e5b9b23f6e3de0efd2068`
- 提交：`57b2d3c`（v35 payload）→ `6e006a3`（H16 gate）→ `c9065af`（probe 抗内联）→
  `cd0f12b`（H16-D）→ `1db66fc`（H16-E）
- 打包对账：v36c/v37/v38 均 69,525,504 B；漏掉 `wrap-payload-mbr.py` 会打成 68,476,928 B

## v39（H16-F heal，2026-11-18 构建；**未上机**，平板不在手边）

没有照片的一轮，所以只写"改了什么"和"照片该长什么样"，判读表全文在
`../releases/v39-NOTES.md`（镜像 `boot-liuqin-v39-heal.img` 69,525,504 B，
sha256 `59b2c52a…`；内核 `b261c1b6…`）。

| 改动 | 位置 | 反汇编证据（`scripts/audit-h16f-image.py`，12 项 PASS） |
|---|---|---|
| libkern 侧钩子全局 `liuqin_metaclass_super_fix` | `patches/xnu/zzzzzzzzzz-…-stalled-diagnostics.patch` | 位于 `0xfffffff007adfcb8` |
| 扫描发现坏链 → 问驱动要链接期超类 → 验 → 写 `[obj+0x10]` → 重走链 | 同上，postModLoad 第一轮 | `+0x36c` 载入钩子、`+0x380 blr`、`+0x384` 镜像下界比较、`+0x394 str x0,[x28,#0x10]`、`+0x3a0` 第二次 `liuqin_chain_depth`、`+0x3c4/+0x3f0` 两个 `liuqin_slot_dump`（`" fixed"` / `" bad-chain"`） |
| 结论行 `M:scan done bad=%u healed=%u cnt=%u deep=%u` | 同上 | 字符串在镜像里，打印在扫描最后一行 |
| 元类构造函数里装钩子（填 `sStalled` 的那批构造函数，必然早于扫描） | 驱动 `OSDefineMetaClassAndStructorsWithInit` | `MetaClass::MetaClass @0xfffffff0079da1d8`：`mov w3,#0xf8`、`bl OSMetaClassC2`、`adr x9 → 0xfffffff0079d5750`、`str x9,[x8,#0xcb8]` |
| 策略从"名字被占就拒绝"改成"占名字的对象是不是我们编译的家族对象" | 驱动 `liuqinBaseClassCheck()` | 新字符串两条 + `U:base-ok/abi/alien/free` 里程碑 |
| `M:mc pre` / `M:mc pub` 见证并原地修复 | 驱动 `liuqinMetaclassWitness()` | 两条 `M:mc` 格式串在镜像里 |

被本轮**取代**的旧结论：`I60:base-dup` 这条里程碑已随 `liuqinBaseClassFree()` 一起删除，
以后照片上看到 `base-dup` 说明刷的是 v36c–v38 的旧镜像。

主机侧（不需要平板）：生产 `./unixnu build kernel` 通过且驱动 `compile.log` 为 0 字节；
补丁三连验证（pristine `--check` / apply 后与工作树逐字节一致 / 工作树 `--reverse --check`）；
`tests/metaclass-simtest/run.sh`（A–E，`.inc` 由 `extract.py` 从生产源码逐字节抽取、漂移即失败）；
descriptor oracle 14/14；USB 调试口主机测试通过。

## v40（H16-G fingerprint，2026-11-18 构建；**仍未上机**，平板不在手边）

v39 让字段重新可用，v40 问的是**谁写的那 8 个字节**。毒值本身就是控制台文本，而内核留着
一面 1 MiB 的控制台镜像（`bsd/dev/liuqin_debug.c`，每次 `liuqin_debug_puts` 都进环），
于是把它当成可查的东西：按内存顺序渲染成字符（换行→`|`、不可打印→`.`、`"` 与反斜杠→`'`），
再在镜像里搜这 8 个字节，命中就把附近 64 字节一起打出来。

| 改动 | 位置 | 进像证据（`scripts/audit-h16f-image.py`，42 项） |
|---|---|---|
| 第二个钩子槽 + 每次启动最多 2 次的预算 | `patches/xnu/zzzzzzzzzz-…-stalled-diagnostics.patch` | `liuqin_metaclass_poison_report @0xfffffff007adfcc0`；helper `@0xfffffff007892d6c`：`ldr [x8,#0xcc0]`、`cbz/b.ls`+`ret`（没钩子就一句不打）、`add w9,w9,#0x1`+`str`（计数）、`br x3`（跳转） |
| 扫描**先指纹后修复** | 同上，postModLoad 第一轮 | `+0x374 bl __ZL18liuqin_fingerprint` 在 `ldr x25,[x8,#0xcb8]`（修复钩子）**之前** |
| 镜像搜索：只用内核交回来的字节 | 驱动 `liuqinRingFind` | 报告函数体内 4 次 `bl _liuqin_debug_copy`（分块 / 回退重读 / 上下文 / 自家字段探测）+ 1 次 `bl _liuqin_debug_total` |
| 装钩子 | 驱动 `liuqinInstallMetaclassFix`（元类构造函数） | ctor 里 `adr x9 → 0xfffffff0079d58c8`、`str x9,[x8,#0xcc0]` |
| 三行新输出 | 驱动 `liuqinMetaclassPoisonReport` | 三条 `M:fp` 格式串各 1 份；报告体内 4 次 `bl _liuqin_debug_puts` |
| 证据重放（面板只留最后几行） | 驱动 `liuqinMetaclassWitness` | 见证体内 3 次 `bl _liuqin_debug_puts`：两条 `M:mc` + 一条重放 |

### 仿真逼出来的四个真缺陷（都不是"等等看照片"能发现的）

1. **自我命中**。原来是先打印 `asc="…"` 再搜索：毒值纯可打印时，那 8 个字节就在刚打出去
   的那行里，搜索必然命中自己——而且带一个真偏移，比不命中更坏。改成搜索早于任何打印；
   F1 断言"指纹行在 `fixed` 行之前"，F2 用一个只可能来自我们自己的 needle 断言 `clean`。
2. **第二次调用会把第一次的自己当成生产者**。`pre`/`pub`/scan 多次报告之间，前一行的
   `asc="…"`/`bad=…` 确实留在镜像里。加 `self=` 计数：命中点前 32 字节内出现 `asc="` 或
   `bad=` 就归到 self，不当生产者。F2 的第二次调用断言 `self=1` 且没有 `hits`。
3. **环绕之后的偏移是错的**。`lq_copy` 会把过旧的位置钳到最老可读字节，而旧代码把
   "请求的位置"当成命中位置报出来：报的地址落在已被覆盖的区域，还会把轮数预算白烧掉。
   现在偏移取自 `pos - got`（真正开始的位置），新增 `lost=` 说明丢了多少。F4 构造"绕回但
   生产者还在"，断言 `at/after/lost/total` 四个数字与算出来的一字不差；F3 构造"绕回且证据
   没了"，断言报 `lost` 而不是 `clean`（**把"找不到"和"没得找"分开**是这一格的全部意义）。
4. **面板会滚动**。证据行在扫描期打印，等拍照时早就被吃掉——v37 的 `M:stalled post` 就是这么
   丢的。报告把最后一行存下来，`witness("pub")` 重放；F7 先把面板刷掉（断言 `ctx|` 不在），
   `pre` 不放（还是不在），`pub` 放（必须在）。

另外 F5 断言锁被占时一次拷贝即放弃（`gaveup`，且新行确实没进环），F6 断言第 3 个坏槽不再
指纹、以及没装钩子的板子输出与 v39 逐行相同（`M:bad[1] bad-chain`、丢 1 槽、登记 1 类）。

### 主机侧证据（八项，全部不需要平板）

| # | 手段 | 结果 |
|---|---|---|
| 1 | `./unixnu build kernel` | 通过；`build/native-ufs/compile.log` **0 字节**（新代码在生产参数下零告警） |
| 2 | `python3 scripts/audit-h16f-image.py` | **42 项全 PASS**（H16-F 的 12 项一条没退）|
| 3 | 补丁三连（reverse-check → forward-check → 逐字节回环） | 全部通过，工作树与重放结果逐字节一致 |
| 4 | `tests/metaclass-simtest/run.sh` | `ALL SIM ASSERTIONS PASS`，A/B/C/D/E + **F1–F7** |
| 5 | `check_legacy_descriptors.py` | `PASS 14/14` |
| 6 | `tests/run-host-test.sh` | `all liuqin usb debug host checks passed` |
| 7 | `porting-record.py check --device ../UniXNU-liuqin` | `liuqin: 14 structured areas checked` |
| 8 | 打包对账 | 69,525,504 B；启动参数 917 B < 1000（与 v39 一字未改） |

交付物：`releases/boot-liuqin-v40-fingerprint.img`（sha256 `5cee607e…`，内核 `0f6b131d…`）；
提交：驱动 fork `8abb519`（branch liuqin-sm8475）、设备仓 `8c39186`（XNU 半侧 + 仿真 + 审计）。
判读表见 `releases/v40-NOTES.md`；平板一出现也可以 `bash releases/watch-v40.sh` 自动 `fastboot boot`。

被本轮**取代**的旧写法：v39 判读表里"`M:mc pub` 又坏 = 涂写者仍在活动，下一轮才上 whyred
延迟注册"——现在不必猜了：涂写者若仍在活动，`M:fp pub` 会带着它自己的 `hits/self/lost`
出现在面板上。**为什么先不做延迟注册**：v39 之后字段在受控时刻一定是好的，剩下的唯一未知
就是"谁写的"，而延迟注册回答不了这个问题，只会把现场搬走。
