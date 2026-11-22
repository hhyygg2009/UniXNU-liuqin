# 2026-11-22 主机侧：`namespace 9 code 0x8` 语义结案（待实机复核）

v67 的终点屏 `unexpected SIGKILL of launchd with reason — namespace 9 code 0x8 description none`
在官方 XNU 源码里逐环对上了。**本文是主机侧结论，枚举映射已证死；具体是哪个调用者还没上机证明。**

## 1. 消息出处（逐字吻合）

`bsd/kern/kern_sig.c` `psignal_internal()`：SIGKILL 发给 `initproc` 时提前 panic——

```c
panic_plain("unexpected SIGKILL of %s %s with reason -- namespace %d code 0x%llx description %." ...
    (p->p_name[0] != '\0' ? p->p_name : "initproc"),
    ((proc_getcsflags(p) & CS_KILLED) ? "(CS_KILLED)" : ""),
    signal_reason->osr_namespace, signal_reason->osr_code, ...);
```

我们的屏上没有 `(CS_KILLED)` ⇒ **`unexpected_failure == TRUE`**（见 §3）——
即内核把这枚 SIGKILL 记为"非预期失败"，没设 `CS_KILLED`。这本身就是一条判别信息。

## 2. namespace/code 解码（`bsd/sys/reason.h`，官方枚举）

| 字段 | 值 | 枚举 |
|---|---|---|
| namespace | 9 | `OS_REASON_EXEC`（**不是** 3=`OS_REASON_CODESIGNING`） |
| code | 0x8 | `EXEC_EXIT_REASON_SECURITY_POLICY` |

⇒ 排除 codesigning namespace 的全部 code（含 launch constraints——那条走 `OS_REASON_SECURITY`/26）。
也排除 taskgated（9/9、3/1）。**exec 路径以"安全策略"为由处死，reason 在 `process_signature()` 里造。**

## 3. `process_signature()` 内三个 9/8 构造点 + CS_KILLED 对照

| 构造点 | 条件 | unexpected_failure | 会打 (CS_KILLED)? |
|---|---|---|---|
| A `imgp->ip_mac_return != 0` | **MACF（AMFI）execve 检查返回非 0** | TRUE | 否 ✅ |
| B subsystem-root 护栏 | restricted 镜像 + 无 LV + 设了 subsystem root | FALSE | 是 ❌ |
| C `imgp->ip_cs_error` | ubc/AMFI 早先塞的 cs 错误（namespace 随构造方） | FALSE | 是 ❌ |

屏上没有 `(CS_KILLED)` ⇒ **只有 A 与观测一致**：`error = imgp->ip_mac_return; unexpected_failure = TRUE`。

## 4. `ip_mac_return` 的上游（`__mac_execve`）

```c
kauth_proc_label_update_execve(..., &label_update_return);   // mac_transition 时
imgp->ip_mac_return = label_update_return;
```

`kauth_proc_label_update_execve`（`security/security.c`）遍历注册的 MACF policy 的
`mpo_proc_label_update_execve`，**第一个非 0 返回者赢**。本内核里注册该 hook 的就是 AMFI。
⇒ **墙 = AMFI 对 launchd 的 execve 标签更新说了"不"**，与 H17 时代"AMFI panic 在自己
start() 里"不同：这次 AMFI 活着跑完了，返回了拒绝。

## 5. 为什么 description 是 none

9/8 这个 reason 构造后没人往里塞字符串（`exit_reason_get_string_desc` 返回 NULL → "none"）。
AMFI 是 codeless 组件（Apple 二进制），它的拒绝没有字符串通道到屏上——所以别再等这条屏变出细节。

## 6. 实机复核方案（每轮一个变量，cmdline < 1000 B）

**主机侧新事实（本轮核实）**：v67 `board.toml` 的 `kernel_args`（46 条 / 976 B）里
**没有 `amfi_get_out_of_my_way=1`**。porting.toml 记它"stays"是 v43–v45 时代的状态；
现在它在 payload 里不存在——也就是说 **AMFI 从未被这个参数放行过**，v67 的拒绝发生在
AMFI 全功能模式下。这把最大嫌疑变量直接摆到了台面上，且加它只需 +28 B（976+28=1004 > 1000，
要按惯例先换掉一条惰性的）。

判别实验：**翻 A 的开关，看 (CS_KILLED) 与 reason 的变化**。

| 轮 | 改动 | 预测（若 §3-A 成立） |
|---|---|---|
| v68a | **加** `amfi_get_out_of_my_way=1`（先腾字节：候选是重复的 `boot-ui-focus*` 之一或惰性项） | AMFI hook 直接放行 → SIGKILL 消失，前进到 launchd 用户态或被下一堵墙接住 |
| v68b | 若 a 无效：撤 `liuqin_allow_unsealed_root=1`（板级补丁线）反向验证——它改的是根卷认证，不是 exec 检查；预期**无变化**，用来排除"根策略影响 AMFI 输入"这条支线 | SIGKILL 原样复现 |
| v68c | 若 b 也无变化：`-apfs_shared_datavolume` 撤除 | 同上，改挂载语义的支线排除 |

辅助证据（主机侧可做，上机前）：反汇编 `b91c7567…` 内核的 `_process_signature+0x1dc`，
确认 panic 帧停在 A 分支的 goto done 路径（而非 B/C 的构造点），把 §3 的排除从"屏上推断"升级成
"二进制证明"。

## 8. 二进制证明（本轮完成，内核 `b91c7567…` = v67 实机那份）

全部用 `llvm-nm` / `llvm-objdump --macho -d --section=__text` + 按符号边界取窗口
（AGENTS 的规矩：`--start-address` 在这份 llvm 构建上输出为空）。

**帧核对**：v67 文档的屏上帧与这份内核逐一对上——
`_process_signature+0x1dc`、`___mac_execve+0xf4`、`_load_init_program_at_path+0x124`、
`_load_init_program+0x180`（nm 符号起点 + 文档偏移 = 屏上地址，四个全中）。

**panic 消息侧（`_psignal_internal`）**：
- `fffffff00773a840` = `panic("psignal: bad signal number")`；
- `fffffff00773a88c` = **无 reason 版** panic，格式串 `"unexpected SIGKILL of %s %s (no reason provided)"`（屏上是 with-reason 版 ⇒ 排除）；
- `fffffff00773a900` = **with-reason 版**，格式串 VA `0xfffffff0070cb7ee`
  （`"… with reason -- namespace %d code 0x%llx description %.800s"`）；屏上帧 `+0x990` = `773a904` = 这条 `bl _panic` 的返回地址 ✓。
- 两个分支都有 `(CS_KILLED)` csel（`tst x0, #0x1000000`）⇒ **屏上没有 (CS_KILLED) ⇒ 发信号那一刻 CS_KILLED 没置位**，实锤。

**reason 构造侧（`_process_signature`）**：
- A 分支 `7719a84`: `mov w0,#9; mov w1,#8; bl _os_reason_create`；
- B 分支 `7719aec`: 同样 `#9/#8`，**但后面紧跟 strlen + kcdata buffer 写入**（往 reason 里塞 subsystem-root 字符串）——若走到过 B，屏上 description 不会是 none ⇒ **B 双重排除**；
- C 路径 = `_mac_proc_check_launch_constraints` 的 reason：hook 体内两处 `os_reason_create` 是 `(3,4)` 与 `(3,1)`（codesigning ns）⇒ **C 排除**。

**done 标签侧（最硬的一条）**：
- `7719f8c`: `mov w1,#0x1000000; bl _proc_csflags_set` = `CS_KILLED` 置位块；进它的边只有三条：
  `7719b38`/`7719b5c`（launch-constraints，C）与 `7719b90`（`cbz w25`——w25 即 `ip_mac_return`）。
- **A 分支的出口是 `7719a98: cbnz w22, 7719b94`，直接跳过 CS_KILLED 块落到 psignal 分发**；
  `7719b94` 再按 `IMGPF_SPAWN`（bit4 of ip_flags）分 `_psignal_with_reason(pid, 9=SIGKILL, reason)`（`7719ba8`，屏上帧 +0x1dc = 其返回地址 ✓）或 vfork 版。
- assert 冷块 `771a3a8/771a390` = done 处两条 `assert(signature_failure_reason != OS_REASON_NULL)`（行号 0x2228/0x222e），与源码对位。

**结论升级**：屏上帧 + 无 (CS_KILLED) + description none 三条观测，在二进制里只有
**A 分支（`imgp->ip_mac_return != 0` ⇒ MACF `kauth_proc_label_update_execve` 返回非 0）**
一条路径同时满足。B、C、taskgated、machine-thread(9/18)、adhoc-ignore 块全部排除。
**"AMFI 的 execve 标签更新检查拒绝了 launchd"从推断升级为已证明（主机侧）。**

两个顺带发现：
1. **我们的 `process_signature` 没有 `proc_process_signature()` 调用**——官方 main 里 done 处
   error==0 时会调它；AMFI 若把拒绝留到那一刻（比如 launch constraints 的 taskgated upcall 路径），
   reason 会带 CS_KILLED、屏相不同。我们的树停在引入前的版本，这是已知偏差，记档。
2. adhoc-ignore 块（`ubc_cs_blob_get(-1,-1)` → generation/revalidate → `CS_ALLOWED_MACHO==CS_ADHOC`
   → CMS blob 查找 `0xfade0b01` → cdhash printf）在镜像里完整存在，与官方 main 一致；
   launchd 走不到它（那是 error==0 之后的路径）。

## 9. v68a 的字节账

现 cmdline 46 条 / 976 B；加 `amfi_get_out_of_my_way=1`（+28 B 含空格）= 1004 > 1000。
候选腾退项（v67 文档已认定惰性的重复项 `native-ufs-root-partition=unixnu` 已在 v67 撤过），
剩余候选：`boot-ui-focus2/3/4`（SpringBoard/Setup/backboardd 三个焦点参数，launchd 之前无人读）——
撤 `boot-ui-focus3=backboardd`（−25 B）+ `boot-ui-focus4=Setup`（−21 B）后 976−46+28 = **958 B** ✓。
撤除本身也是单变量记账：这两个参数只在 launchd 之后的 UI 焦点逻辑里消费，本轮目标到不了那里。

## 7. 与 Fly116 v114 的交叉

pong 栈（22H374 + ADT 容器 + 他们的 trust cache 配置）在同设备上 exec **没有**死在这一步
（到了 root device）。若他们没动 AMFI，差异就在镜像/信任输入：launchd 二进制的签名来源、
trust cache 装载状态。我们的 `payload.json` 至今 `trust_cache_sha256 = null`（H17 记录），
这是 v68 系列最值钱的对照组——他们的 CI 配方里 trust cache 是怎么进去的，值得抄。

## 证据出处

- `bsd/sys/reason.h`（apple-oss-distributions/xnu main @ fetch 2026-11-22）：namespace/code 枚举
- `bsd/kern/kern_exec.c`：`process_signature()` 三个 9/8 构造点、`unexpected_failure` 与 CS_KILLED 语义、
  `kauth_proc_label_update_execve → ip_mac_return` 赋值
- `bsd/kern/kern_sig.c:2082-2091`：panic 消息模板（含 `(CS_KILLED)` 后缀逻辑）
- 屏上帧符号化沿用 v67 文档（`_psignal_internal+0x990 ← _process_signature+0x1dc ← ___mac_execve+0xf4`）
