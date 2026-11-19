# 逐轮判读：v35 → v43（2026-11-16 → 2026-11-19）

K49:PE_init_iokit 处的 `Kernel data abort` 从 v35 第一次可读，到 v38 被彻底消掉并点名。
这一页记录每一轮改了什么、照片看到什么、以及被证伪的假设——避免以后走回头路。
v39 与 v40 都是"平板不在手边"的轮次：改动、主机侧证据和判读表写全，照片栏留空。
v40 把"谁写的这 8 个字节"变成一条可以打印的答案，而它的全部依据来自仿真——
那面一兆控制台镜像在主机上是一比一复刻的，所以搜得到搜不到、偏移对不对，不用上机就能判。
v41/v42 把这条线追到底并结案（**绑定用错了地址空间**），v42 实机第一次挂上根卷；
v43 剪掉整套 H16 诊断、修掉新墙 H17（trust cache 的非睡眠锁）。

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
| v40 实机 | 同上 | —（IMG_7119–7123，2026-11-18 20:40） | `M:fp … ring hits=2`；gate 报 `base-alien`，墙从崩溃变成一条可读的拒绝 |
| v41 alien-arithmetic | `0f6b131d`→`9…` | `M:alien held/want/delta/slide/cmp/off`：把"两个地址差多少"变成可拍的算术 | `held=` 与 `want=` 差 **恰好 −0x1000000**、`slide=0x0`、`want=` 那行自己是垃圾 → **根因浮出**（见 v41 节） |
| v42 relocated-bindings | `f958d67f` | **只改绑定脚本**：按 `kernel.json` 的 `coalesced_segments` 逐段映射，落在原厂内核本体段里的对象交回链接期解析 | **H16 结案**：`M:bad/M:fp/M:alien` 全部消失、`U61:base-ok`（`live size 144, compiled size 144`）、**第一次越过 `Failed to mount root device`**；新墙 = `lock_rw.c:1400` panic |
| v43 trust-cache-lock | `e8233d2e` | H17：`startup.c` 的 `#else` 分支初始化 `trust_cache_rt_lock`、`kern_trustcache.c` 判空 `amfi`；启动参数加 `amfi_get_out_of_my_way=1`+`cs_enforcement_disable=1`；**删掉 H16 诊断**（扫描补丁 + 驱动 369 行）；菜单 timeout 15→3 | 未上机。主机侧 62 项审计 + A–G 仿真全过 |

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

---

## v40 实机判读（IMG_7119–7123，2026-11-18 20:40 上机）

上机方式：`releases/watch-v40.sh` 在 20:40:39 抓到 serial `19562650`，
`fastboot boot` → Sending `OKAY 2.384s` / Booting `OKAY 0.287s`，纯内存、没写任何分区。

### 面板读到的关键行（视觉判读，存疑位见注）

```
M:fp scan p=0xfffffff007ae36c0 w1=… w2=0xfffffff007add1d0 w3=0xfffffff0071373ae
        bad=6331323766393730 asc="079f721c"
M:fp scan ring clean self=0 lost=0 total=3985
M:bad[170] bad-chain p=0xfffffff007ae36c0 vt=0xfffffff0072006d0 sz=248
           n=NativeUfsBlock bad=0x6331323766393730
M:scan done bad=1 healed=0 cnt=172 deep=6
M:fp pre  … bad=6331323766393730 asc="079f721c"
M:fp pre  ring clean self=1 lost=0 total=7592
M:mc pre  was=0x6331323766393730 now=0xfffffff007add1d0 depth=-2
M:mc pre  unfixable p=0xfffffff007ae36c0 link=0xfffffff007add1d0 vt=0xfffffff0072006d0
          sz=240 depth=-2
U60:mc-bad
NativeUfs: IOBlockStorageDevice is held by 0xfffffff006add1d0, not by the compiled
           family object 0xfffffff007add1d0; root publication refused
U61:base-alien
Failed to mount root device @IOKitBSDInit.cpp:989
```

存疑（摄像头/角度吃掉的位，不影响下面四条结论）：`w1=` 读成 17 位（必有一位多余）、
`sz=240` 还是 248、`depth=-2`。`scripts/read-panel.py` 可以给这几位分级复核。

### 四条结论，其中三条是这一轮新拿到的

1. **崩溃这条线过去了**：`M:mc pre was=… now=0xfffffff007add1d0` 就是 v39 的修复在现场生效的
   证据；v40 复现 v38 的"可读挂载失败"，没有 data abort。扫描阶段 `bad=1 healed=0` 说明
   扫描只报告、修复发生在登记路径——这个分工记下来。
2. **毒值被符号表点名**：`bad=0x6331323766393730` 的内存字节是 `079f721c`，而
   `0xfffffff0079f721c` 在 **v40 自己的**符号表里正好是 `_sysctl_register_oid_early` 的起始
   （`llvm-nm -arch arm64 out/liuqin/kernel/kernel.macho`，EXACT；51,246 个符号铺在约
   2^24 的地址空间里，随机串撞上某个符号起始的概率约 0.3%）。所以写进指针字段的是
   **一个内核文本地址的低 8 个十六进制字符**，不是垃圾、也不是巧合级的数字。
3. **`ring clean` 证明不了"不是控制台文本"——v40-NOTES 那一格写过头了**：ring 的写入者只有
   `liuqin_debug_putc/puts`（`zzz-liuqin-usb-debug.patch` 里 `liuqin_debug_append` 的全部调用点
   就这两处），普通 `kprintf/printf` 不进镜像；而补丁里恰恰有
   `printf("LIUQIN USB: … idr0=%08x …")` 这类 **8 位十六进制**打印。`hits=0 clean` 目前只排除
   "我们自己里程碑流的复制"。这一条已经进 AGENTS 的硬性约束。
4. **墙换了，而且它自己报了出来**：挡住根的不再是 H16，而是 `base-alien`——名字表里
   `IOBlockStorageDevice` 的持有者在 `0xfffffff006add1d0`，驱动链接的是
   `0xfffffff007add1d0`（v40 nm 确认 = `__ZN20IOBlockStorageDevice10gMetaClassE`，落在
   `__common`(BSS) 0x07a50000–0x07ae3828）。两个地址**差值正好 0x1000000 = 16 MiB（slide
   粒度），低 24 位完全相同**；且 `0xfffffff006add1d0` 不在内核镜像的任何段里（最低段
   VMA 是 `0xfffffff007008360`），也就是说持有者是 **zone 里的对象**（面板上 `M:stalled`
   的 `c0/c1` 也全在 0x06 段）。两种解释都还活着：
   (a) 家族 metaclass 被复制/搬进了 zone；
   (b) 一个 −16 MiB 的 slide 被**单边**应用（运行时算出的指针减了，编译期立即数没减）。

### v41（H16-H）要打印什么——一次照片就能把 (a)/(b) 分开

- `M:alien slide=%p`：直接读 `vm_kernel_slide`（`osfmk/mach/vm_param.h` 导出，arm64 定义在
  `arm_vm_init.c:105` 的 `SECURITY_READ_ONLY_LATE`）。slide 若非零，(b) 立刻成立。
- 持有者自己的身份：`M:alien held p= vt= sz= n=`，再加与静态对象前 64 字节的比对结果
  （首个差异偏移）——"同一份内容、另一个地方" vs "另一个类"。
- ring 覆盖面：把 `kprintf/printf` 的字节也 append 进镜像，并打印 `mirror=` 实际覆盖字节数；
  之后 `clean` 才有"不是任何控制台文本"的分量。

---

## v41 判读 + 根因：H16 从来不是"涂写"，是绑定用错了地址空间

v41 第二轮（IMG_7130–7137）终于拍到 `K49 → U59` 那一页，三条新行把整条线翻盘：

```
M:alien held p=0xfffffff006add1d0 link=0xfffffff007ae0c00 name=… sz=144
M:alien want p=0xfffffff007add1d0 link=0x3066666666666666 name=0x3061303861313730 sz=542037209
M:alien delta=-16777216 slide=0x0 cmp=1 off=0x0
M:fp scan ring hits=2 self=0 lost=0 at=3927 after=31 total=3966
M:fp scan ctx|0xfffffff007ae36c0 cnt=172 c0=0xfffffff007c1c130 c1=0xfffffff0071…
```

`slide=0x0` 判死"单边 slide"；`cmp=1 off=0x0`（第一个字节就不同）判死"同一对象的副本"。
而 `want=` 那行才是钥匙：**我们链接期引用的那个"家族对象"自己就是垃圾**——它的
`link/name/sz` 是 ASCII 与荒谬数字，因为 `0xfffffff007add1d0` 在我们的镜像里**不是家族
元类**，而是 `__common`(BSS, 0x07a50000–0x07ae3828) 中间别人的存储。

静态一查到底（`scripts/build-liuqin-ufs-bindings.py` 生成的 `original-bindings.S`）：

```
.globl __ZN20IOBlockStorageDevice10gMetaClassE
.set __ZN20IOBlockStorageDevice10gMetaClassE, 0xfffffff007add1d0     ← 原厂 21H461 kernelcache 的 VA
```

而 `out/liuqin/kernel/kernel.json` 的 `coalesced_segments` 说我们自己的布局是：

| 段 | 原厂 cache | 我们的镜像 | 位移 |
|---|---|---|---|
| `__PRELINK_TEXT` | `0xfffffff00527c000` | `0xfffffff00427c000` | **−0x1000000** |
| `__PLK_TEXT_EXEC` | `0xfffffff005920000` | `0xfffffff004920000` | **−0x1000000** |
| `__PLK_DATA_CONST` | `0xfffffff006ca0000` | `0xfffffff005ca0000` | **−0x1000000** |
| `__PRELINK_DATA` | `0xfffffff007adc000` | `0xfffffff006adc000` | **−0x1000000** |
| `__PRELINK_INFO` | `0xfffffff007c30000` | `0xfffffff0083a4000` | **+0x774000** |

**四个 kext 段全部低 16 MiB，而 `__PRELINK_INFO` 是另一个方向**——所以既不能用原厂 VA，
也不能用一个统一 slide，必须**按段映射**。脚本里那句 `slide = 0` + 注释"raw original-cache
VAs，不像 mondrian 那样重排"是错的假设，它让**每一条**绑定都偏 16 MiB：

| 绑定 | 现在写进镜像 | 按段映射应为 |
|---|---|---|
| `IOBlockStorageDevice::gMetaClass` | `0xfffffff007add1d0` | `0xfffffff006add1d0` |
| `__ZTV20IOBlockStorageDevice` | `0xfffffff007ae0c00` | `0xfffffff006ae0c00` |
| `requestIdleEv` 等方法槽 | `0xfffffff00596f050` | `0xfffffff00496f050` |

于是整条 H16 链条一次性解释干净：

1. 驱动构造时把 `superClassLink` 设成幻影地址（原厂 VA）；
2. 扫描走链时把幻影地址当 OSMetaClass 解引用，读到的是**邻居静态变量的字节**——那些字节
   含文本缓冲，所以"毒值"看起来像 ASCII（`079f721c`、`ffffffff0`、`0718a0a0`），而且
   **随启动时序变**（v41 第一次拍到 `bad=0`，第二次 `bad=1`），这正好解释了此前所有"偶发"；
3. `M:fp … ring hits=2` 的"生产者"就是我们自己的 `M:stalled/M:fp` 输出被邻居缓冲携带——
   不是谁涂写，是**读错了地方**；
4. v39 的"修复"把幻影地址写回去（`now=0x07add1d0`、`depth=-2`）——它一直在往错的目标修；
5. gate 拿注册表里的真对象（`0x06add1d0`）与幻影（`0x07add1d0`）比 → `base-alien` →
   拒绝发布 → `Failed to mount root device`。

### 修法（v42）

`build-liuqin-ufs-bindings.py` 的 placement 段改成：kext 段按 `coalesced_segments` 逐段位移；
落在原厂 **kernel 本体**段里的（继承自内核的 vtable/方法）**不绑绝对地址**，交回链接期按
符号名解析。报告里新增 `segment_deltas` / `emitted_metaclass` / `emitted_vtable` /
`inherited_kernel_bindings`，并打印每条数据绑定的 `original -> emitted`。

可证伪的预测：v42 之后 `M:bad[170]` 与 `M:alien` 都应消失，出现
`NativeUfs: base class IOBlockStorageDevice live size …, compiled size …` + `U:base-ok`，
然后**第一次**看到 APFS 挂载的真实结果。

---

## v42 实机判读（IMG_7140–7153，2026-11-19 00:02 上机）：H16 结案

上机方式：`releases/watch-v42-loop.sh` 在 23:53:09 抓到 serial `19562650`，
`fastboot boot` → Sending `OKAY 2.300s` / Booting `OKAY 0.287s`，纯内存、没写任何分区。

### 三条可证伪预测全部命中（v42-NOTES 里那是"预测"，这里是结果）

| v42 的预测 | 面板 | 判定 |
|---|---|---|
| `M:bad[170]`、`M:fp`、`M:alien` 全部消失 | 一行都没有 | ✓ 幻影地址没了 |
| 出现 `NativeUfs: base class IOBlockStorageDevice live size …, compiled size …` + `U:base-ok` | `live size 144, compiled size 144` + `U60:mc-ok` → `U61:base-ok` | ✓ gate 第一次放行 |
| 越过 `Failed to mount root device @IOKitBSDInit.cpp:989` | 越过：`root-bringup-ok → root-layout-ok → root-manifest-ok → root-plan-ok → root-iostart-ok → root-unauth-open → ac-ok → root-prepared → root-media-SystemOS → B17:ofindroot_done → B19:at_mountroot` | ✓ 根窗口读到、计划建起、根卷挂上 |
| **反证条件**：pc 落在 `0xfffffff00496…`（方法槽不该位移） | 没有发生 | ✓ 按段映射是对的，不需要回滚 |

**H16 到此结案。** 追了五天的"谁涂写了 `superClassLink`"，答案是：**没有人**——是我们把
原厂 21H461 kernelcache 的 VA 当成我们镜像的 VA 用，读到了自己 `__common` 里邻居静态变量的字节。

### 新墙（H17）：挂上根卷之后，死在 exec init

```
panic: Taking non-sleepable RW lock with preemption enabled   @lock_rw.c:1400
frames fffffff00729d414 fffffff00775f028 fffffff004aad120 fffffff004ab3e34
       fffffff004ab5bf0 fffffff0079bc9f8 fffffff00777cbfc fffffff0077ace40
       fffffff0077aa1f0 fffffff0077a9860 fffffff0077164f8 fffffff00771a1c4
       fffffff00771b744 fffffff00771bddc fffffff00771bc00 fffffff0076ef3f8
       fffffff00773c960 fffffff007276688 fffffff00741b9fc fffffff00741b838
       fffffff007420664
pages=0x000000000000babd  free=0x000000000000a9f2
```

21 帧全部用 `llvm-nm -m` 对着 `out/liuqin/kernel/kernel.macho` 符号化（`_query_trust_cache+0x70`
这个偏移与面板读数逐位吻合，说明这些内核函数在 v42/v43 之间没位移）：

| # | 地址 | 符号 |
|---|---|---|
| 0 | `0xfffffff00729d414` | `_lck_rw_lock_shared+0x58` ← **panic 点** |
| 1 | `0xfffffff00775f028` | `_query_trust_cache+0x70` |
| 2–4 | `0xfffffff004aad120 / …ab3e34 / …ab5bf0` | `__PLK_TEXT_EXEC` 内（AMFI 的 kext 帧，本体符号不在主符号表里） |
| 5 | `0xfffffff00779bc9f8` | MACF `_mac_vnode_check_*+0x48` |
| 6–7 | `0xfffffff00777cbfc / 0xfffffff0077ace40` | `_ubc_cs_blob_add+0xf0`、`_load_code_signature+0x340` |
| 8–14 | … | `_parse_machfile → _load_machfile → _exec_mach_imgact → _exec_activate_image → ___mac_execve → _load_init_program_at_path → _load_init_program` |
| 15 | `0xfffffff0076ef3f8` | **`_bsdinit_task+0x74`** ← 就是 exec init |
| 16–20 | … | `_bsd_ast → _ast_taken_user → user_take_ast → _thread_bootstrap_return → _Call_continuation` |

也就是说：**根挂上了，内核去 exec `/sbin/launchd`，代码签名漏斗问 AMFI"这个 cdhash 可信吗"，
AMFI 走 `query_trust_cache`，那把锁炸了。** 每一次启动都会炸在这里。

### 根因（全在主机侧查到）

`lock_rw.c` 里炸的那一句：

```c
if (lock->lck_rw_can_sleep) { … }
else if (get_preemption_level() == 0) {
        panic("Taking non-sleepable RW lock with preemption enabled");
}
```

`trust_cache_rt_lock` 是 `decl_lck_rw_data(, trust_cache_rt_lock)`——一个躺在 `__common`
里零初始化的对象，`lck_rw_can_sleep = false`。全镜像唯一把它变成可睡眠锁的地方是
`lck_rw_init(&trust_cache_rt_lock, &trust_cache_lck_grp, 0)`，而它在
`trust_cache_runtime_init()` 里；那个函数只在

```c
#if !defined(BCM2837) || (defined(XNU_NATIVE_TRUST_CACHE) && XNU_NATIVE_TRUST_CACHE)
```

这一块里被调用。我们的基线**就是**按 BCM2837 编的，而 `XNU_NATIVE_TRUST_CACHE` 没开
⇒ 整块被编译掉。主机侧的三条独立证据：

1. `llvm-nm`：`_trust_cache_runtime_init` 在镜像里**存在**（`0xfffffff00775e858`），
   但全镜像**没有任何一条 `bl` 指向它**；
2. `grep -c trust_cache_init kernel.macho` = **0**：连 `"trust_cache_init"` 这个 bootstrap
   字符串都不在像里，所以面板上永远不可能出现它（v42 那一页里也确实没有这一行）；
3. 配置来源：`out/liuqin/.config` 与 `configs/liuqin_boot_defconfig` **逐字节相同**，
   里面 `CONFIG_XNU_TRUST_CACHE_RUNTIME=n`。而 `board.toml` 写的是
   `defconfig = "liuqin_defconfig"`（那一版是 `=y`）——**活跃配置和板级声明不一致**，
   这是本轮顺手挖出来的一个真问题，单独立项处理（见 v43 节的"下一件事"）。

顺带：`out/liuqin/payload/payload.json` 的 `trust_cache_sha256` 是 **null**——board.toml 里
`[ipsw]` 明明抓了 `StaticTrustCache` 与 `Cryptex1,SystemTrustCache`，但没打进 payload。
所以就算锁修好，静态 trust cache 也是空的，没有任何 cdhash 会被判可信。

### v43 怎么修（两条腿，各答一个问题）

- **答 panic**：新补丁 `patches/xnu/zzzz-liuqin-trust-cache-lock-init.patch`
  在 `startup.c` 的 `#else` 分支里补上 `lck_rw_init(&trust_cache_rt_lock, &trust_cache_lck_grp, 0)`，
  并 `kernel_bootstrap_log("trust_cache_lock_init")`（面板上那一行就是"这个分支跑了"的证明）；
  `kern_trustcache.c` 里给 `amfi == NULL` 一个 `KERN_NOT_FOUND` 的回答——"空 trust cache"本来
  就该是这个语义，解引用空接口不是。
- **答判决**：启动参数加 `amfi_get_out_of_my_way=1` + `cs_enforcement_disable=1`。这是
  bring-up 开关，和 TC 那条线一起撤（`CONFIG_XNU_TRUST_CACHE_RUNTIME=y` + 把两份 IPSW trust
  cache 打进 payload）。参数总长 **967 B < 1000 B** ✓。

---

## v43（H17 修复 + 剪掉 H16 诊断，2026-11-19 构建；**未上机**）

用户这一轮的要求是："diag 没用的话，缩短时间或者不要了"。H16 已经结案，那一整套
"为了回答'谁写的'"而生的代码就变成纯成本：每次启动都跑、每轮扫描都往面板上刷几十行，
把真正的里程碑顶出屏幕（v37 丢 `M:stalled post` 就是这么丢的）。

### 剪掉什么、留下什么

| | 内容 | 原因 |
|---|---|---|
| 删 | `patches/xnu/zzzzzzzzzz-liuqin-metaclass-stalled-diagnostics.patch`（整个 H16-E/F/G 扫描） | `M:stalled` / `M:scan done` / `M:bad[` / `M:dirty[` 的源头；扫描要找的那个"涂写者"不存在 |
| 删 | 驱动里 `liuqinEightText` / `liuqinRingFind` / `liuqinHasOwnFieldBefore` / `liuqinMetaclassPoisonReport` / `liuqin_block_super_fix` / `liuqinInstallMetaclassFix` / `liuqinAlienReport`（NativeUfs.cpp **−369 / +25** 行） | 指纹、修复钩子、base-alien 算术报告，全都只为诊断服务 |
| 改 | `OSDefineMetaClassAndStructorsWithInit (NativeUfsBlock, …)` → 普通 `OSDefineMetaClassAndStructors` | `WithInit` 存在的唯一理由是在元类构造函数里装钩子 |
| **留** | `liuqinChainDepth` + `liuqinMetaclassWitness`（只报告、不修、不搜）+ `liuqinBaseClassCheck` gate | **绊线**：`superClassLink` 全 XNU 只有 OSMetaClass 构造函数会写；绑定哪天再指错，它是第一个开口的地方，而且比 `isMemberOf()` fault 更早 |
| 留 | 1 MiB 控制台镜像本身（`ring.inc` 那套 `lq_ring/lq_append/lq_copy`） | 它是 USB 调试口和所有 `M:`/`U:` 行的底座，不是诊断脚手架 |
| 改 | `[payload] timeout 15 → 3` | 拍照靠看门狗自动 boot，不需要人守菜单 |

### 主机侧证据（七项，全都不需要平板）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `./unixnu build kernel` | `EXIT=0`；补丁序列因 stamp 变化（`d5fb9560…`）从 pristine 重放整个序列成功 |
| 2 | `build/sheng-ufs/compile.log` | **0 字节**（驱动零警告） |
| 3 | `python3 scripts/audit-h16f-image.py` | **IMAGE AUDIT PASS 62 项** |
| 4 | `grep -c` | `trust_cache_lock_init` 1、`trust_cache_init` 0、`M:fp`/`M:alien`/`M:stalled`/`M:bad[` 全 0、`chain broken` 1、`base class … live size` 1 |
| 5 | `tests/metaclass-simtest/run.sh` | `ALL SIM ASSERTIONS PASS`（场景重写成 A–G） |
| 6 | `drivers/ShengUfs/tests/check_legacy_descriptors.py` | `PASS 14` |
| 7 | `tests/run-host-test.sh` | `all liuqin usb debug host checks passed` |

产物：内核 `e8233d2e047e…`、payload 重打（启动参数变了）、`boot.img` **69,525,504 B** ✓、
`releases/boot-liuqin-v43-trust-cache-lock.img` sha256 `a358b648bbca…`。

### audit 脚本被改写成本轮的形状

`scripts/audit-h16f-image.py` 原来只证明"H16-F/G/H 进了像"。清理轮次的风险不是"新代码没进去"，
而是"**旧代码还在里面**"，所以它现在四段并列：该在的在（`S+`/`T+`/`K`/`Q`/`C`/`G`/`B`）、
该没的没（`S-`/`T-`/`P`）。本轮新增、以后长期有用的三条：

- **`K` 段看"分支"**：`kernel_bootstrap_thread` 里必须出现 `lck_rw_init(_trust_cache_rt_lock,
  _trust_cache_lck_grp, 0)`，而且**不得**出现 `bl _trust_cache_runtime_init` /
  `bl _load_static_trust_cache`——两条合起来才证明编译的是 `#else`，也就是我们的补丁真的生效；
- **`B` 段守 v42 的根因**：家族元类/vtable 的绑定地址必须落在我们自己的
  `__PRELINK_DATA` / `__PLK_DATA_CONST` 里，且没有一条指回 `__common`。以后 fileset 布局再动，
  这一条会先响；
- **`G` 段读 GOT**：gate 的比较对象不是 `adrp/add` 立即数，而是 `__got` 里的一格
  （`ldr x8, #0x36c78` → `0xfffffff007a0c008`）。脚本因此**把那 8 个字节从镜像里读出来**
  再和 `nm` 比；只看 `adrp/add` 会漏掉这种形态，也漏掉"链接期就绑错"这种失败。

仿真（`tests/metaclass-simtest/`）也跟着换了对象：它原来镜像 `postModLoad` 的扫描分支，
而那段代码已经不在像里——镜像删掉的代码等于证明空气。现在它跑 A–G 七组：健康链、gate 四种判决
（`base-ok` / `base-abi` / `base-alien` / `base-free` + skip-gate 参数）、哨兵**只读不修**、
走链的环上限（`depth=-65`，不自旋）、镜像的环绕/钳位/锁被占三种形态、面板滚掉之后 `pub`
那一枪还在、以及健康路径的完整序言。

### v43 的判读表（同样是可证伪的预测）

| 面板 | 结论 | 下一件事 |
|---|---|---|
| `K5x:trust_cache_lock_init`（插在 `dtrace_early_init` 与 `OSKext…KextBootstrap` 之间，后面的 K 编号因此整体 +1） | 补丁那条分支跑了 | 看第 2、3 行 |
| **没有** `lock_rw.c:1400`，`B19:at_mountroot` 之后出现新的 `U`/`B` 里程碑 | init 起来了，H17 结案 | 战线移到用户态（launchd / 签名判决） |
| 仍然 `lock_rw.c:1400`，但**已有** `trust_cache_lock_init` | **反证**：炸的不是这把锁，是另一把非睡眠锁 | 21 帧重新符号化，按新地址找锁的持有者 |
| 完全没有 `trust_cache_lock_init`，K 编号也没位移 | 补丁没进像 | 先跑 audit 的 `K` 段，再决定重打 |
| 出现 `img4`/`AppleImage4`/`amfi` 字样的新 panic，或 `unable to query trust caches: amfi interface not available` 之后停住 | `img4if/amfi` 未注册那一类：TC 进 payload 从"下一步"变成硬前置 | 做 `CONFIG_XNU_TRUST_CACHE_RUNTIME=y` + `kernel.macho.trustcache` |
| 画面变成 `PreBoard`/`SpringBoard` 相关行 | 存储 + 根卷 + exec 全线打通 | 换战线：显示 / 输入 / launchd 约束 |

拍照要什么：① `K49 → K60` 那一页（确认 `trust_cache_lock_init` 插进去了、`M:stalled` 成片刷屏
真的没了）；② `U10x → 最后一屏`（`at_mountroot` 之后走到哪）；③ 若还有 `frames=…`，把它连
`pages=/free=` 拍全，地址要逐位可读。

### 顺带挖出来的独立问题（记进 porting.toml，别丢）

`board.toml` 声明 `defconfig = "liuqin_defconfig"`，而**实际构建用的** `out/liuqin/.config`
与 `configs/liuqin_boot_defconfig` 逐字节相同（`CONFIG_XNU_TRUST_CACHE_RUNTIME=n`、
`CONFIG_XNU_BOOT_PROGRESS=n`）。这两个文件从 11-16 起就没被板级声明重新生成过。
v43 不去动它（换 defconfig 会一次性翻掉几十个 `CONFIG_XNU_*` 人格开关，属于另一轮），
但要么让构建在 defconfig 变化时强制重生成 `.config`，要么把 `board.toml` 改成如实声明——
**不能继续留着一个会误导下一轮的假声明。**
