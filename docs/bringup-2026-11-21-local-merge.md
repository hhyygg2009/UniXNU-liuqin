# 2026-11-21：本仓未推送的本地线合并进 main（决策与否决）

从 README 的「进度（2026-11-21…）」一节拆出来逐字保留：那 30 个提交（v39–v44）从没推到远端，
今天并进来时的取舍规则、带进来的工具、以及**两条否决**都记在这里，免得有人再走一遍。

## 4. 本仓未推送的本地分支合并进来（同日）

这台构建机上还有一条**从没推到远端**的本地线（`main@248ac99`，16 个提交，v39–v44：H16-F/G/H
扫描修复、H17 trust-cache、面板判读工具、`porting.toml`/`AGENTS.md` 记录流程）。今天它被合并进
`main`。取舍规则一句话：**与实机测过的 v60 冲突的代码和配置一律取 v60 一侧**
（`board.toml`、`scripts/build-liuqin-ufs-bindings.py`、`drivers/ShengUfs` 指针、README 本身），
只把本地独有的内容并进来，净效果 30 文件 / +4,922 / −238（删的都是别机带来的 `__pycache__/*.pyc`）。

带进来的东西：`scripts/read-panel.py` 与 `docs/panel-rules.toml`（面板照片判读，并把可读物限制在
"这一版镜像真能打出的字符串"）、`scripts/check-defconfig.py`（声明构建真正读的是哪个 defconfig
并守卫它）、`scripts/fetch-liuqin-trust-caches.py` 与 `scripts/check-trust-cache-image.py` +
`docs/trust-cache-plan.md`（不靠平板、在主机侧证明 trust-cache 那条线）、`docs/boot-rounds-v35-v44.md`
（v39–v44 逐轮记录，接上本文引用的 v35-v38）、`docs/mainline-ubuntu-reference.md`（同机型主线内核
对板）、`porting.toml` + `AGENTS.md` + `.github/workflows/porting-record.yml`、
`tests/metaclass-simtest/` 的 `extract.py` / `ring.inc` / `witness.inc` 与更新过的 `sim.cpp`。

两条**否决**，理由写在这里免得有人再走一遍：

- **H17 的 `startup.c` 锁初始化已作废**：它的前提是"trust-cache 运行时被编到外面，锁从没变成
  可睡眠锁"。v60 这条线走的是 `CONFIG_XNU_TRUST_CACHE_RUNTIME=y` + 本仓已有的
  `patches/xnu/native-trust-cache-runtime.patch`（门改成 `#if !defined(BCM2837) ||
  XNU_NATIVE_TRUST_CACHE`）。实测证据：刷进实机的那颗 `kernel.macho` 里
  `_trust_cache_runtime_init` 与 `_load_static_trust_cache` 都是已定义符号。补丁因此放进
  `patches/xnu/attic/`（顶层 `*.patch` 才进补丁序列，放子目录即不会触发整套 XNU 重编）。
  **但同一提交里的 `kern_trustcache.c` 的 `amfi == NULL` 护栏仍未被任何一条线覆盖**
  （`kern_trustcache.c:630` 依旧是 `lck_rw_lock_shared()` 之后直接 `amfi->TrustCache.query(`），
  要启用请只取那一半。
- **H16 的 `M:stalled` 诊断补丁不退役**：v60 就是带着它编的，本轮 §3.7 的定位全靠它打的那两行。

本地线里今天仍然最值钱的一条是 `ee2b5a8`：**原始 cache 的 VA 要按段映射，单一 slide 形状不对**
（kext 段一律 `-0x1000000`，而 `__PRELINK_INFO` 是 `+0x774000`）。§3.7 正是"只补了一个全局
slide"留下的洞，修它就用这个模型。

> 三条只在这里记着、别处没有的事实：① `kern_trustcache.c:630` 的 `amfi == NULL` 护栏
> 至今没被任何一条线覆盖（`lck_rw_lock_shared()` 之后直接 `amfi->TrustCache.query(`），要启用就只取那一半；
> ② H16 的 `M:stalled` 诊断补丁**不退役**（v60 就是带着它编的，platform-power 的定位全靠它打的两行）；
> ③ 改分区的 `carve-unixnu-gpt.py` 与回滚集 `backup/live-20261120-2145/ROLLBACK-gpt-{primary,backup}.bin`
> 在构建机的项目目录里，**未入库**。
