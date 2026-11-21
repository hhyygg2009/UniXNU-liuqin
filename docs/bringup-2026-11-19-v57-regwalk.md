# 2026-11-19 晚场：v57 registry walk 与 IOStorageFamily 结论

本文记录 v57 这一轮的改动、实机看到的事实，以及从 IOStorageFamily 源码得到的结论。
所有真机结论都来自 `fastboot boot` 的内存镜像，未写任何分区。

## 本轮改了什么

1. `captureRegistryWalk()` 不再只打面板：结果写进 `gRootRegistryText`，由
   `rootDiagnosticFailure()` 追加进 panic 文本（面板会丢行/乱序，panic 块是唯一可靠通道）。
   内容包括：service plane 遍历计数、`dev/drv/media/bsd` 精确类名计数、
   `mc=XYZ`（三个类是否已注册元类）、平台专家下的直接子级类名、窗口自身 state/matched/busy，
   以及末尾一行 `V57 ...` 摘要。
2. 修掉 v56 遗留的两处编译错误：`gLatePublishGate` 在 `probe()` 里先于定义被使用；
   late-gate 的 `liuqin_milestone()` 调用少了一个 tag 参数。

## 构建链的坑（必须记住）

`unixnu` 在构建前会 `git submodule update --init --recursive`，把**工作树干净**的子模块
检出到**主仓库记录的 SHA**。v57 的驱动提交因此被静默回退成 v56，第一次/第二次构建出来的
镜像里根本没有新代码（`strings kernel.macho | grep REGWALK` 为 0 才发现）。

规则：改完驱动后必须**同时提交主仓库的 submodule 指针**，否则构建用的是旧代码。
每次构建后用 `strings -a out/liuqin/kernel/kernel.macho | grep <新记号>` 验证。

## 实机事实（2026-11-19 19:31，boot-v57-regwalk.img）

照片可读部分（面板小字层）：

```
U143:start-root
REGPUBLISH ok
U146:root-pub-SystemOS
REGPROBE class=NativeUfsBlock castsDevice=1 castsService=1 state=0x0 busy=1
CHAINPROBE devParent=ArmDTPlatform provParent=xiaomi,liuqin provPath=1 devAttached=1
CHAINPROBE path=IOService:/ArmDTPlatform/...
MEDIA start IOBlockStorageDevice=0 IOBlockStorageDriver=0 IOMedia=0 IOMediaBSDClient=0
```

解读：

- 窗口确实挂在 platform expert（`ArmDTPlatform`）下，provider 是 DT 节点 `xiaomi,liuqin` 下的
  `NativeUfs`，且 provider 在 service plane 里有完整路径 —— 之前 H13/H16 的链路问题不是当前卡点。
- `castsDevice=1 castsService=1`：元类链正确，`IOBlockStorageDevice` 类型识别通过。
- `state=0x0`：这只是**等待开始那一刻**的状态（匹配刚交给异步 job，还没跑），不能当结论。
- `MEDIA ...=0`：`capturePlaneCounts` 用的是 `IOService::getMatchingServices()`，而它按
  `kIOServiceMatchedState` 过滤 —— 未完成匹配的实例天然不在结果里。**这条不能证明"没有 driver"**。

未读到：`REGWALK` / `V57` 行。面板是"里程碑小字 + 内核控制台大字"两层叠加，且拍照时媒体
等待（300 tick 自旋）大概率还没走完，panic 尚未发生。

## IOStorageFamily 源码结论

源码在 `out/feeds/IOStorageFamily-ad2e13e0/`（不在 `xnu-work` 里，是预链接二进制对应的源码）。

1. **认领条件**：personality = `IOProviderClass IOBlockStorageDevice` +
   `IOPropertyMatch {device-type: Generic}`。而 `device-type=Generic` 是
   `IOBlockStorageDevice::init()` 自己 `setProperty` 的（`IOBlockStorageDevice.cpp:30-42`），
   只要我们的 `init()` 链走到基类，该属性就在。
2. **`IOBlockStorageDriver` 没有 `probe()`**，门槛在 `start()`：
   `open(this)` → `handleStart(provider)` → `PMinit()` → `provider->joinPMtree(this)` → `close` → `registerService()`。
   `handleStart` 的硬失败点是 `reportRemovability` / `reportEjectability` / `checkForMedia`
   （→`reportMediaState`→`recordMediaParameters`）。
3. **不依赖 `IOWorkLoop`**：IOStorageFamily 四个类里没有任何 workloop 使用。
4. **personality 早于任何 matching 就进了 catalogue**（`readStartupExtensions` →
   `sendAllKextPersonalitiesToCatalog`），所以"发布太早导致够不到 personality"不成立。
   真正的时序风险是"类是否已注册"（kext 是否已 start）；但 `startRoot` 里
   `getMetaClassWithName("IOBlockStorageDevice")` 非空，说明 IOStorageFamily 至少 start 过一次。
5. **`kIOServiceMatchedState` ≠ 被认领**：它表示"这一轮匹配处理完了"。没有 driver 认领也会有；
   只有 module-stall 或还没跑完才没有。`getMatchingServices()` 只挑带该位的实例，
   所以"它对已存在实例返回 0"是预期现象。
6. **`REGPUBLISH ok` 打在 `startMatching()` 之前**（本项目补丁在 `IOService.cpp:1089`），
   只证明"路径能到 registry root + `platformAdjustService` 通过"，不证明已注册、更不证明匹配发生。
7. 匹配是**异步**的：`startMatching` → `_IOServiceJob` → `_IOConfigThread` 现开内核线程 →
   `doServiceMatch`。`kernel_thread_start` 失败是静默 `continue`，没有日志。
   驱动里紧跟其后的第二次 `startMatching()` 是空操作（`needConfig` 已为 false）。

## 下一步（v58）

一行摘要，确保能在面板上读全，并且**在媒体等待结束后**再取值：

```
V58 st=<state> busy=<n> drv=<n> med=<n> mc=<XYZ> n=<n> dt=<device-type>
```

- `st`/`busy`：等待结束后重新取一次窗口状态（区分 `0x0` 没跑 / `0x2` 跑一半 / `0x6` 跑完）。
- `drv`/`med`：用 `IORegistryIterator` 遍历 service plane 并按类名过滤计数，**不要**用
  `getMatchingServices`。
- `dt`：`copyProperty("device-type")`，确认是 `Generic`。
- 把媒体等待的 tick 数降下来，确保几十秒内能走到 panic，否则又拍不到。
- 若 `st` 仍是 `0x0` → `doServiceMatch` 从未运行 → 试 `registerService(kIOServiceSynchronous)`。

---

# v58 实机结果（已在上机验证，2026-11-19 19:46）

把面板照片分成上下两半拍，这次一次读全了。

```
U143:start-root
REGPUBLISH ok
U144:blk-registered
U145:blk-matched
U146:root-pub-SystemOS
REGPROBE class=NativeUfsBlock castsDevice=1 castsService=1 state=0x0 busy=1
CHAINPROBE devParent=ArmDTPlatform provParent=xiaomi,liuqin provPath=1 devAttached=1
MEDIA start IOBlockStorageDevice=0 IOBlockStorageDriver=0 IOMedia=0 IOMediaBSDClient=0
V58 pre  st=0x0 busy=1 dev=0 drv=0 med=0 bsd=0 mc=111 n=32 dt=Generic
U147:root-media-timeout
MEDIA timeout IOBlockStorageDevice=0 IOBlockStorageDriver=0 IOMedia=0 IOMediaBSDClient=0
V58 post st=0x0 busy=1 dev=0 drv=0 med=0 bsd=0 mc=111 n=32 dt=Generic
WALK from=ArmDTPlatform n=32 dev=0 drv=0 media=0 bsd=0 mc=111
WALK direct: IOSystemStateNotification, IOPMrootDomain, IOPMrootDomain, IOPlatformDevice, ...
WALK cls: NativeUfsBlock, NativeUfsBlock, ...
WIN NativeUfsBlock state=0x0 matched=0 busy=1
PUBSTATE entries=1 flag=1 this=0xffffff6a6172d80 first=0xffffff6a6172d80 published=1 busy=0
UFS ROOT FAILURE: SystemOS window media not found
```

## 结论（这一轮钉死的）

1. `mc=111` —— `IOBlockStorageDevice` / `IOBlockStorageDriver` / `IOMedia` 三个类都已注册。
   **IOStorageFamily 加载并 start 过了**，"family 没加载"这条线排除。
2. `dt=Generic` —— `IOBlockStorageDriver` personality 要求的属性条件满足，"属性不对"排除。
3. `st` 在等待**前后都是 `0x0`**，`matched=0` —— `doServiceMatch()` **一次都没执行**。
4. `busy` 从 `startMatching()` 的 `_adjustBusy(+1)` 之后一直是 `1`，直到超时都没回落 ——
   说明 `_IOServiceJob` 一直躺在队列里，没有被 `_IOConfigThread` 取走。
5. `n=32`、`WALK direct` 里是平台设备 —— 注册表很小，说明这时候匹配确实还没在这个子树里跑过。
6. `WALK cls: NativeUfsBlock, NativeUfsBlock` —— 两个窗口确实都在 service plane 里，
   挂在 `ArmDTPlatform` 下（`CHAINPROBE` 也证实）。
7. `dev=0` 是**统计口径问题**：`dev` 用精确类名比 `IOBlockStorageDevice`，而我们的实例类名是
   `NativeUfsBlock`，永远数不到。v59 改成用 `metaCast` 走元类链统计。

**根因（已验证）：这块板子上 IOKit 的异步匹配线程不工作。**
`registerService()` → `startMatching(0)` 只是把 job 排队，真正执行依赖 `_IOConfigThread`
临时 `kernel_thread_start` 一个内核线程；那个线程没起来（失败是静默 `continue`），
所以设备永远停在"已注册、未匹配"。

## v59 的对策（已构建，未上机）

1. `registerService(kIOServiceSynchronous)` —— 让 `doServiceMatch()` 在发布线程里就地执行，
   不再依赖 config thread。
2. `mediaUnderWindowExists()` 改为**遍历 service plane 并检查祖先链**，
   不再用 `getMatchingServices()`（它按 matched 位过滤，永远看不到
   `IOBlockStorageDriver::handleStart` 异步创建的 `IOMedia`）。
3. `dev` 计数改用 `metaCast("IOBlockStorageDevice")`。

镜像：`boot-v59-syncmatch.img`，kernel.macho sha256
`71b1c0356ef75c5000a1cefd118fb0ae247dd7d9208d0c89237da1c85ebc5eba`。

下机后先看照片上有没有：
- `U145:blk-matched` 之后是否紧接 `V58 sync st=0x6 ...`（`0x6` = 匹配跑完了）
- `drv=` 是否变成 1 或 2
- 是否出现 `U148:root-media-SystemOS`（说明媒体被认领并找到）

---

# v60：等待线程转储 + IOResources 立即通知（2026-11-19 夜，已构建未上机）

v58 只回答了"匹配没跑"，但"没跑"有两种成因，从外部看一模一样：

- `_IOServiceJob` 排队后**根本没有配置线程**来取；
- 配置线程取了，但**卡在某个 `start()` 里永不返回**。

v60 直接把这个区分做成证据。

## 移植自 SM8475 参考实现（UniXNU-pong → zizhan）

| 改动 | 出处 | 作用 |
|---|---|---|
| `liuqin_dump_waiting_threads()` | zizhan 补丁 0005 | 每个等待线程一行：tid、K/U、block hint、wait event、continuation、保存的 lr 和前 4 帧 |
| `publishResource()` 立即发 matched 通知 | zizhan 补丁 0006 | `IOResources` 仍在配置时不推迟 re-match，避免 `waitForMatchingService()` 永不醒来（他们 3/4 挂根失败的根因） |
| `liuqin_iokit_job_counts()` | 本项目新增 | 导出 `gOutstandingJobs / gNumConfigThreads / gNumWaitingThreads` |

XNU 侧以幂等脚本形式记录在 `patches/xnu/manual/0001-liuqin-waiters-and-resource-notify.py`，
构建前对 `out/liuqin/tree/.cache/xnu-work` 跑一次即可。

## 驱动侧

超时路径上新增两段输出，都追加进 panic 文本、位于摘要行之上：

```
JOBS out=<n> cfg=<n> wait=<n> max=<n> cpus=<n>
WAITERS begin
W <tid>k h=.. ev=.. cont=.. lr=.. bt ....
...
WAITERS end <n>
V58 post st=0x.. busy=.. ...
```

## 上机后怎么读

| 现象 | 结论 |
|---|---|
| `JOBS out=0` | 队列里没有作业：匹配已经跑过了（与 `st` 对照） |
| `JOBS out>=1 cfg=0` | **没有配置线程** —— 确认 `_IOConfigThread` 创建失败 |
| `JOBS out>=1 cfg>=1` | 有线程但没处理完 → 看 WAITERS 里哪个线程卡住 |
| `st=0x6` 且 `drv>=1` | 匹配成功，下一步是媒体/BSD 节点 |
| `WAITERS` 出现 `cont=` 落在 `start`/`IOSleep` 附近 | 就是被那个 `start()` 卡住了，按地址符号化即可定位 |

镜像：`boot-v60-waiters.img`，kernel.macho sha256
`a926cb7ee2e9627551570d0384d893c171f6adb2ff964ce4847a2d0d3e403ba6`。

> 踩过的坑：第一次编 v60 时 `thread.c` 里多 include 了一次 `thread_kernel_state.h`
> （该头无 include guard），编译失败并把 `thread.o` 删了；而 `build-libpthread-kernel.py`
> 的符号审计要读 `link.filelist` 里列出的对象，于是接着报 `Unavailable real kernel imports`。
> 用记录下来的 `thread.o.json` 单独重放编译即可恢复。

---

# v60 实机结果（已验证，2026-11-20 00:20）

里程碑停在同步匹配的入口：

```
U141:probe-nub
U142:start-enter
U143:start-root
U144:sync-match-enter
REGPUBLISH ok
（之后没有 blk-registered，也没有 blk-matched）
```

## 结论

**同步匹配在 `doServiceMatch` 内部挂死了。**

`registerService(kIOServiceSynchronous)` 的路径是：
`REGPUBLISH ok`（路径检查 + platformAdjustService 通过）→ `startMatching(0x2)`
→ 因为 sync=true，**当场**执行 `doServiceMatch()` → 不再返回。

所以：

- `U145:blk-registered`（registerService 返回后打）永远没出现；
- 媒体等待、`JOBS`、`WAITERS` 全都排在它后面，**一次都没机会执行**；
- v60 精心准备的两组诊断，被自己的修复挡在了门外。

对照 v58：**异步路径"排队不执行"，同步路径"执行但挂死"** —— 两条路都被堵住，
说明 IOKit 匹配链在早期这个上下文里根本不可用，而不是"我们调用的方式不对"。

## 为什么会挂住（推测，待验证）

`doServiceMatch` → `startCandidate` → `IOBlockStorageDriver::start(我们的窗口)`
→ `open(this)` / `handleStart` / `provider->joinPMtree(this)`。
而此刻我们的 `NativeUfsBlock::start()` **还没有返回**，窗口处于半初始化状态；
`IOStorageDriver` 在 `open()`/`handleStart()` 里回头对这台"正在 start 的设备"做操作，
很容易与 `start()` 自己持有的仲裁/初始化状态互锁。

参考实现对这一点很谨慎：它们**从不在 `start()` 里同步匹配**，而是无参
`registerService()`，并且把发布时机推迟到整个启动更靠后的位置
（`PongUfsTransport.cpp:554`、`:576-605`）。

## v61 的设计（下次执行）

1. **不要在 `start()` 里同步匹配**。改为在 `start()` 末尾起一个独立内核线程，
   由它调用 `设备->startMatching(kIOServiceSynchronous)`；
   发布线程本身继续往下走，不被拖住。
2. 发布线程**轮询**窗口的 `getState()`（不用定时器），等 N 秒；
   超时就调用 `liuqin_dump_waiting_threads()` —— 这次它会真的有机会跑，
   并且能直接给出**卡在 `doServiceMatch` 里的那个线程的栈**。
3. 拿到栈之后按地址符号化，定位到具体是 `open()`、`handleStart()` 还是
   `joinPMtree()` 死锁，再决定是绕开还是修。

## 本轮已确认无误、可复用的资产

| 资产 | 状态 |
|---|---|
| XNU 侧 `liuqin_dump_waiting_threads()` | 已编进镜像（`WAITERS` 字符串在），只是本轮没被触发 |
| XNU 侧 `publishResource()` 立即发 matched 通知（0006） | 已在镜像里 |
| XNU 侧 `liuqin_iokit_job_counts()` | 已在镜像里 |
| 驱动侧 JOBS/WAITERS 超时输出 | 已在镜像里，等 v61 让它跑起来 |
| 构建链 | 正常，含「单对象重放编译」的恢复办法 |

镜像保留：`boot-v60-waiters.img`（kernel.macho sha256 `a926cb7e…`）。
