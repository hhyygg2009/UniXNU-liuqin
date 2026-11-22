# UniXNU 菜单默认项：为什么看起来默认停在 "2: Restart"

## 现象

payload 里的 `unixnu.toml` 已经是：

```toml
[boot]
timeout = 15
default = "xnu"
```

按 `Config.c:1478-1493`，`default = "xnu"` 会匹配到第 1 个 entry，`DefaultIndex = 0`，
倒计时结束也确实启动第 1 项。但屏幕上的高亮却在动，看起来"默认是 2: Restart"。

## 根因

`UniXNUPkg/Library/UniXNULib/Menu.c:122-125`：

```c
  RotateMs = Config->RotateSeconds * 1000;
  if (!HasInput && (RotateMs == 0)) {
    RotateMs = DEFAULT_ROTATE_SEC * 1000;   /* 2 s */
  }
```

平板没有 `SimpleTextInput`（`HasInput == FALSE`），于是菜单每 2 秒执行
`Selected = (Selected + 1) % Count`（`Menu.c:197-203`）轮换高亮。功能上超时仍启动
`DefaultIndex`（第 1 项），但视觉上像默认第 2 项。

## 修复（构建沙箱，不改上游仓库）

删掉强制轮换的 3 行（`docs/patch-menu-no-rotate.patch`），重编固件：

```bash
bash docs/build-firmware-from-upstream.sh
```

- 修改文件：`Platforms/Nothing/UniXNUPkg/Library/UniXNULib/Menu.c`
- 新 FD sha256：`44485ef0ca1e61d714115957cab3e6d5b53746bffae1e1f05090a077d7ab47d5`（3,145,728 B）
- 验证镜像：`F:\liuqin_ios\boot-v111-menu.img`（旧 payload + Cape DTB，仅用于看菜单）

## 备注

- `rotate` 配置项关不掉这个行为：`Config.c:1389` 把 `rotate` 钳到 >=0，
  `Menu.c:123` 在无输入设备时把 0 又强制成 2 秒；只能改代码。
