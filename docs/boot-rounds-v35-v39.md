# 实机逐轮判读：v35 → v38（2026-11-16 → 2026-11-17）

K49:PE_init_iokit 处的 `Kernel data abort` 从 v35 第一次可读，到 v38 被彻底消掉并点名。
这一页记录每一轮改了什么、照片看到什么、以及被证伪的假设——避免以后走回头路。

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
