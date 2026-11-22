# NativeUfs TUR 失败分析（v114，2026-11-22）

## 1. 实际失败点（v114 屏上诊断，逐字转录）

```
PONG_UFS: UCD 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
PONG_UFS TRANSPORT: probe TUR
stage=probe TUR (shim parity) result=0x00000246
HC  cap=1587031f ver=00000300 hcs=0000010f hce=00000001 db=00000001 is=00000004
DMA=04c4a000 UTRL=04c4d000 SMMU smr=00000000 s2cr=00000000 cbar=00000000 sctlr=00000000
CMD n=1 type=01 op=00 lun=00 ocs=0f rsp=00000000 scsi=ffff power=00
PROTOCOL reject=0 residual=0 sense=000000 SMMU slot=4294967295 id1=0
QUEUE rsr=1 ahit=0 polls=0x0635 dbSeen=1 isSeen=4 isBefore=4 contiguous=0x22800
FAULT global=0->0 syn=0 cb=0->400 syn=19940a far=11f001c3066d01 id2=0 cba2r=0
UIC pa=00000000 dl=00000202 net=00000000 tr=00000000 dme=00000000
GPT/read lba=0 userdata=0..0 magic=00000000 00000000 manifest=4294967295
WINDOWS found=0 prepared=0 published=0 bytes=0 policies=0 verify=ffffffff
IO reads=0 failures=0 pubPhase=0 window=4294967295
```

判读（结合 `drivers/NativeUfs/RootCommands.h`、`RootQueue.h`）：

| 字段 | 值 | 含义 |
|---|---|---|
| `ocs` | `0x0f` | OCS 从未被完成写入（0xf 是 `hostScsiDir` 的初值）→ **TUR 的 UTP 任务完成中断没来** |
| `scsi` | `0xffff` | 同上，响应头没被读到 |
| `is` | `0x04` | 只有一次中断状态位（bit2 = UTRCS，任务完成），且只出现 1 次 |
| `dbSeen` / `db` | 1 / 1 | 门铃写下去了（UTRLDBR=1），但控制器没有清除它 |
| `rsr` | 1 | 捕获时 UTRLRS=1 |
| `polls` | 0x635=1589 | 轮询约 1589 次仍无完成 |
| `UIC ...` | 全 0 | `uic[]` 诊断数组从未被填充（Init 序列没有跑 UIC DME_GET） |
| `SMMU slot` | 4294967295 | 没有匹配到 SID e0 的 SMR/S2CR 槽位（诊断路径没命中） |

结论：**不是设备回了错误 SCSI 状态，而是命令提交后控制器没有完成它**。方向是队列/中断/门铃/映射，不是分区内容。

## 2. 两个"能工作的参照"

### 2.1 我们自己的 ShengUfs（同设备已验证能读写 UFS）

`drivers/ShengUfs/NativeUfs.cpp`（v40–v60 演进）在同一台 liuqin 上成功：
- 读取 GPT（诊断里有真实 lba/userdata 区间）；
- 注册 `IOBlockStorageDevice`（`WIN NativeUfsBlock state=0x0 matched=0 busy=1`）。

它比 pong 的 NativeUfs 多做的、值得逐项对照的事：

1. **UIC 命令通道**：`DME_GET` 前先清 IS.UCCS（`hostWrite(0x20, 1<<10)`），等 `HCS.UCRDY` 再发。pong 的 transport 没有 UIC 初始化，假设固件已把链路留在可用档位。
2. **中断方式**：以 legacy doorbell 模式只开 UTRCS（IE bit0），中断里清 UTRCS 并按 doorbell 位唤醒等待者；pong 当前是纯轮询。
3. **门铃/暂停位策略**：ShengUfs adopt 路径要求 `doorbell==0 && 0x78==0`，停队列、重写 UTRLBA 再启动；pong 的 `hostStopQueue` 要求 `hce&1`、`(hcs&0x0f)==0x0f`。
4. **DMA/UTR 内存位置**：ShengUfs 用 DT 预留的 UTRLBA 窗口（16 KiB cached），pong 用 `IOBufferMemoryDescriptor` 物理连续内存；两者物理地址是否落在 SMMU identity 窗口内需核对。
5. **SMMU**：ShengUfs 显式 takeover；pong 刻意"不重编程 SMMU，沿用固件 SID e0 映射"。

### 2.2 UEFI 的 UFS DXE（固件里能读 GPT 的路径）

`uefi-liuqin-d97/repo/Silicon/Samsung/SamsungPkg/Drivers/UfsDxe/UfsDxe.c` 是固件里真正读盘的驱动，UIC 顺序可作为完整参照：

`DME_LINK_STARTUP` → `DME_GET PA_ConnectedTx/RxDataLanes(0x1561/0x1581)` → `DME_SET PA_ActiveTx/RxLanes(0x1560/0x1580)` → `DME_SET power mode(0x1571)` → `DME_SET PA_RxGear/PA_TxGear(0x1583/0x1568)`，另有 `VS_FORCE_HCS` 处理。pong 的 kernel transport 完全没有这一步。

## 3. 明天优先实验（按性价比排序）

1. **队列提交最小对照**：把 pong `hostStartQueue`/`hostSubmit` 与 ShengUfs 成功路径逐行 diff：
   - UTRLBA/UTRLBAU 写入后是否要等 `HCS.UTRLR`；
   - `UTRLRS` 语义（UFSHCI：0=run、1=stop；两边代码方向相反，以实测为准）；
   - 门铃写之前的 `dsb sy` 与 UTRL 表 cache clean（确认 `cacheCivac` 覆盖 UTRL 表）；
   - `IS.UTRCS` 清除顺序。
2. **给 transport 加 UIC 初始化**（参照 UEFI UfsDxe 顺序），至少在 TUR 前做一次 `DME_GET 0x1587` 与 power mode 查询；`uic[]` 全 0 说明完全没读。
3. **SMMU 诊断落地**：让 `gHost.smmu`/`cba2r` 真正填上（现在 slot=-1），对照固件里的 SID e0 配置。
4. 若 1–3 仍无完成：改成中断驱动（legacy doorbell）而不是 200 ms 轮询，排除"完成被固件中断处理吃掉"。

## 4. CI 支持（已加）

`kernel-upstream.yml` 会在构建前把 `ci/patches/*.patch` 应用到 device 检出（`git apply -p1`），
驱动补丁走 CI 即可，不需要改上游仓库。命名：`ci/patches/0001-nativeufs-*.patch`。

## 5. 产物

| 文件 | 说明 |
|---|---|
| `F:\liuqin_ios\boot-v114-nocpuvalidate.img` | 到达 root device 阶段的镜像（sha256 `0213013f…`） |
| `/root/work/patched/kernel.macho` | AppleARMCPU 校验补丁后的内核（sha256 `157641da…`） |
| `/root/work/v112-44891152869/` | 官方 finalize 工作目录（payload/boot/ADT 全部中间产物） |
