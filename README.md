# FlClash Ubuntu 20.04 compatibility repair

这是用于保存、复现 Ubuntu 20.04 上 FlClash 启动修复的独立工具仓库，**不是 FlClash 官方补丁或官方兼容版本**。

本次修复最初只应用在本机，未提交到 FlClash 官方仓库。这里保留脚本和说明，方便重新安装系统或换机器后找到。

## FlClash 下载链接

- [官方最新发行版及所有平台下载](https://github.com/chen08209/FlClash/releases/latest)
- [官方 v0.8.99 发行页](https://github.com/chen08209/FlClash/releases/tag/v0.8.99)
- [v0.8.99 Linux amd64 DEB 安装包](https://github.com/chen08209/FlClash/releases/download/v0.8.99/FlClash-0.8.99-linux-amd64.deb)
- [v0.8.99 Linux amd64 AppImage](https://github.com/chen08209/FlClash/releases/download/v0.8.99/FlClash-0.8.99-linux-amd64.AppImage)

上述 v0.8.99 链接及摘要已于 2026-11-23 通过官方 GitHub Releases API 核对：

| 文件 | 官方 SHA256 |
| --- | --- |
| `FlClash-0.8.99-linux-amd64.deb` | `dd5b60d68aafb0f762692d497050fd2edcbdd772f4c53822a06eb2837cbf885f` |
| `FlClash-0.8.99-linux-amd64.AppImage` | `1c41d0e32c8d1fab4d6d10319574c0f38230a9dcbec125c0b5efa962de28e84a` |

本机实际安装包的版本字段是 `0.8.99+2026100301`；不能把它与官方 v0.8.99 安装包视为字节完全相同的构建。链接提供软件下载入口，不代表所有最新版本都已验证兼容。

仓库仅记录软件下载链接，不保存安装包，也不包含魔戒订阅地址、节点、账号或其他个人代理配置。

## 已确认的问题

测试环境：Ubuntu 20.04.6 LTS、x86_64、系统 GLIBC 2.31，已安装 FlClash `0.8.99+2026100301`。

安装的程序在创建窗口前退出，存在以下兼容问题：

- 主程序、SQLite、JNI 及辅助服务需要 GLIBC 2.34；Rust 库需要 GLIBC 2.35。
- 缺少 `libayatana-ido3-0.4.so.0`。
- Wi-Fi 插件引用 `g_spawn_check_wait_status()`，而 Ubuntu 20.04 的 GLib 提供旧名称 `g_spawn_check_exit_status()`。
- 随包共享库包含构建机器的 RUNPATH，可能阻止独立运行库的依赖搜索。

## 修复方式

脚本复制已安装的 FlClash 到当前用户目录，下载并校验 Ubuntu 官方仓库的 GLIBC 2.35 安全更新包，只解压、不进行系统安装。托盘依赖与 `patchelf` 通过本机 APT 下载，也不安装到系统。

修复仅应用于用户目录中的副本：

1. 修改主程序及辅助服务的 ELF interpreter 和 RPATH，使用私有 GLIBC。
2. 移除共享库中原有的构建路径。
3. 将 Wi-Fi 插件中新版 GLib 函数名替换为签名和行为等价的旧名称。
4. 创建用户级命令和应用菜单入口，并清理这个进程继承的 `LD_LIBRARY_PATH` / `LD_PRELOAD`。

不替换系统 GLIBC，不修改 `/opt/FlClash`，不修改全局环境变量，也不导入、上传或更改代理订阅。

## 使用

先正常安装 FlClash，并退出正在运行的 FlClash。默认源目录是 `/opt/FlClash`。

依赖：Python 3.8+、`apt-get`、`dpkg-deb`、`readelf`；本机 APT 软件源中需要能找到 `patchelf` 和 `libayatana-ido3-0.4-0`。

```bash
python3 repair.py --diagnose
python3 repair.py --install
python3 repair.py --check
```

安装完成后从应用菜单点击 **FlClash**，或者运行：

```bash
~/.local/bin/FlClash
```

不需要 `sudo`。脚本不启动代理、不启用 TUN，也不自动启动窗口。

如果安装目录不同：

```bash
python3 repair.py --install --source /path/to/FlClash
```

默认生成的位置：

| 内容 | 位置 |
| --- | --- |
| 兼容程序及私有库 | `~/.local/share/flclash-compat/` |
| 命令启动入口 | `~/.local/bin/FlClash` |
| 用户应用菜单入口 | `~/.local/share/applications/FlClash.desktop` |
| 下载来源、哈希和安装记录 | `~/.local/share/flclash-compat/repair-state.json` |
| 原有用户启动入口备份 | `~/.local/share/flclash-compat/backups/` |

## 本次实际验证

2026-11-23 在上述环境完成：

- 依赖及重定位检查通过，进程实际加载私有 GLIBC。
- FlClash 主进程保持运行。
- X11 中的 FlClash 窗口为 `IsViewable`。
- FlClashCore 子进程启动。
- 用户级桌面入口通过 `desktop-file-validate`。

本次仅验证了启动，未验证代理联网、TUN 或系统辅助服务安装。应用内升级可能覆盖兼容修改；升级后如再次出现相同问题，应退出 FlClash 后重新运行脚本。不能据此保证其他 FlClash 版本或架构兼容。

## 恢复原启动方式

先退出 FlClash。若 `backups/` 中有之前的用户启动文件，恢复对应文件；若之前没有用户级入口，可移除本工具创建的两个入口：

```bash
rm ~/.local/bin/FlClash
rm ~/.local/share/applications/FlClash.desktop
update-desktop-database ~/.local/share/applications
```

保留或删除 `~/.local/share/flclash-compat` 均不影响原来的 `/opt/FlClash`。FlClash 自己的用户数据不在本工具删除范围内。

## 开发检查

```bash
python3 -m unittest discover -s tests -v
python3 repair.py --check
```

## 来源

- [FlClash 官方项目](https://github.com/chen08209/FlClash)
- [官方仓库中的 Ubuntu 20.04 兼容性讨论](https://github.com/chen08209/FlClash/issues/2415)
- [Ubuntu 官方 Jammy 安全更新索引](https://security.ubuntu.com/ubuntu/dists/jammy-security/main/binary-amd64/Packages.gz)

本仓库不分发 FlClash 或 Ubuntu 二进制文件；相关程序及软件包各自保留其原有许可。
