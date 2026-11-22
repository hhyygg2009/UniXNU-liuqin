# NativeUfs 传输层调试记录（2026-11-22，v112–v124）

## 1. 结论先说

**UFS 传输层尚未打通**，但阻塞点已经从"不知道为什么"收敛到**一条明确的因果链**：

1. 固件（XBL/ABL 或交接流程）在把控制权交给 XNU 前**关闭了 UFS**：
   `GDSC` 掉电、所有 UFS 时钟门控 `CLK_OFF=1`、QMP PHY `pwrdn=1`、`serdes/tx/rx` 读回全 0；
2. UIC 命令通道（host 侧）仍然可用，所以 `DME_GET/DME_SET/DME_RESET` 都返回 0——
   **但这不代表链路是通的**：`HCS.DP` 是残留位，`DME_LINKSTARTUP` 一律返回 1，
   `PA_LINK_STATUS` 的 `dl` 在 0/1 之间跳，链路实际是断的；
3. 上游驱动检测到 latched UIC 错误后会清错误 + 重发 LINK_STARTUP，
   在已经断开的链路上再次失败，并且把 `dl` 从 1 打到 0 —— **v119–v121 的"自杀"路径**；
4. 旁路证据：同 SoC（SM8475）的 MIX Fold 2（`qiumochen/UniXNU-zizhan`）能启动 iPadOS，
   它的 `NativeUfs` **完全不碰 UIC/PHY**，只读 GCC 确认"固件已上电"就直接接管队列——
   说明那台机器的固件/ABL 交接时 UFS 是活的。liuqin 的固件版本不同，交接时是关的。

## 2. 变体与证据（全部实机 `fastboot boot`，不写分区）

| 变体 | 改动 | 结果 | 关键证据 |
|---|---|---|---|
| v112/v114 | 上游最新 prelink + AppleARMCPU 补丁 | ✅ 到 root device | `Failed to mount root device @IOKitBSDInit.cpp:996` |
| v115 (A) | SID e0 SMMU bypass | ❌ TUR 无完成 | `ocs=0f`、`db=1`、`polls=1589`；`SID e0 bypass ... slot=2 bank=74 s2cr=00000004`（读回未变） |
| v116 (C) | 用固件 UTRL 页（`kIOMapInhibitCache`） | ❌ panic | `Unaligned kernel data abort`（缓存维护/宽存储打在设备内存上） |
| v119 (F) | C + 失败即 panic | 信息可读 | `PHY serdes=0 tx=0 rx=0` |
| v120 (G) | + QMP PHY v6 表（0x1d80000） | ❌ panic | `PHY bring-up ready=1 ... serdes=00000000`（写了不生效） |
| v121 (H) | + 双 PHY 基址探测（0x1d80000 / 0x1d87000） | ❌ panic | `PHY probe primary=1(answers=0) alternate=1(answers=0)` → **两个窗口都写不进去**；`DME_LINKSTARTUP result=1 dl=1->0 ue=4` |
| v122 (L) | DP 置位时**不重发 LINK_STARTUP**；NOP→唤醒→TUR；SMMU takeover | ❌ NOP 无完成 | `DME_LINKSTARTUP skipped, device present hcs=101f dl=1 ue=0`；NOP `db=1 is=4 polls=1957`；SMMU 读回 `smr=0 s2cr=4` 仍不变 |
| v123 (N) | L + **固件 UTRL 页当设备内存**（不动 SMMU） | ❌ panic | `using the firmware request list at fdcc000 (16384 bytes, device mapped)` 后 `Unaligned kernel data abort ... far=...c004`（第一条 64 位存储） |
| v124 (O) | N + UTRD/PRDT/清零改 **volatile 32 位写** | ❌ NOP 无完成 | 描述符不再崩溃；`dl=00000000`、NOP `db=1 is=4 polls=6060`；UTRD 内容读回与写入不一致（`words[4]` 期望 `fdccd000` 实读 `70000000`） |

## 3. 已确认的硬件事实

| 项 | 值/结论 |
|---|---|
| UFS host | `0x01d84000`（0x3000），`CAP=0x1507031f`、`VER=0x00000300` |
| QMP PHY | 两个候选：`0x1d80000`（上游 pong 注释）与 `0x1d87000`（Linux SM8450 PHY 节点，v5 布局）；两者在本机都**不响应写测试** |
| GCC | UFS 时钟页 `0x187000`；GDSCR `+0x004`；`BCR +0x000`（读回 0，已释放） |
| GDSC/时钟 | 进内核时 `CLK_OFF` 全置位、GDSC 掉电 → 驱动上电后仍无法让 PHY 响应 |
| SMMU | `idr0=0x4c6877f1`、`idr1=0x6000004b`（113 groups / 128 pages）；**SID 0xe0 → type=0（翻译）bank=2**；本机对 SMR/S2CR 的写入读回不生效 |
| 链路 | `DME_LINKSTARTUP` 恒 1；`dl` 0/1；DP 为残留位不可信 |
| 参考机 | zizhan（同 SM8475）UFS 在固件交接时是活的，内核完全不碰 UIC/PHY |

## 4. 下一步（按优先级）

1. **给 QMP PHY 找到并打开电源**（当前唯一硬阻塞）：
   - 查 SM8450/SM8475 的 RPMh 稳压器投票：PHY 需要 `vdda-phy`/`vdda-pll` 两条轨，
     固件关机时可能一并关掉；PongPower 里已有 RPMh/QcomRpmh 写入路径，可加一次投票再试 PHY 表；
   - 同时确认 PHY 的 GDSC 是否与 UFS 控制器同一个（`0x187004`），还是另有 mem-phy GDSC；
   - 判据：PHY `+0x000` 写入 0x5a5a5a5a 能读回。
2. 若 PHY 写通了：按 Linux qmp-ufs v5/v6 序列初始化 → `DME_LINKSTARTUP` → 链路 `dl=0xf`
   → 再回到 v124 的 NOP/TUR 路径（设备映射缓冲已可用）。
3. 若 PHY 仍不可写：考虑**从固件侧解决**——比较 liuqin 与 zizhan 的 ABL/XBL（固件包版本不同），
   或者让 payload 在 ExitBootServices 前做一次 UFS 初始化（XBL 交接后立即重新上电）。
4. 传输层打通后：`pong_shngroot_4k` 支持（参考 zizhan）→ 写根镜像 → APFS 挂载。

## 5. 复现资源

- 补丁：`ci/patches-variants/{a..o}/`（GitHub：`Fly116/liuqin-ipados18-ci`）
- 镜像：`boot-v112…v124`（本地 `F:\liuqin_ios\`），v114 已上传 Release `v114-2026-11-22`
- 构建：`.github/workflows/kernel-upstream.yml`（`patch_dir` 选择变体；已加 ccache）
- 参考树：`qiumochen/UniXNU-zizhan`（同 SoC 已跑通，UFS 不碰 UIC/PHY）、
  `Emilelu/UniXNU-mondrian`（K60 同 SoC，UFS 同样未通）
