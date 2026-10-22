# 大彪996服务器工具箱（核心预览版）

大彪996是独立的中文服务器总工具箱，不是大彪TV安装项目。这里先实现菜单、只读系统检查、模块规范与注册、状态查询、日志、自检和独立的工具箱更新。大彪TV尚未接入。

目标为 Debian/Ubuntu 的 amd64/arm64，运行时需要 Bash 与 Python 3.9+（仅标准库）。macOS 可用于开发和测试，但 `c doctor` 返回不支持，模块动作不会运行。当前没有 apt、Docker 安装、SSH/网络/防火墙修改、卸载、重启或远程脚本执行功能。菜单里的未完成分组明确标为“待接入”。

## 直接运行

```sh
cd dabiao996
./c help
./c
./c doctor
./c status
./c logs
./c system info
./c apps list
./c update check
```

## 一键安装

在 Debian/Ubuntu amd64/arm64 服务器运行（需要 `curl`、`sha256sum`、Python 3.9+）：

```sh
bash <(curl -fsSL https://raw.githubusercontent.com/dabiao996/dabiao996-toolbox/main/get.sh)
```

入口 `get.sh` 固定 v0.1.0 的提交和两个 SHA256 值；它下载并校验安装器，再由安装器校验完整发布包。入口无法下载时不会安装，安装器或发布包校验失败时会报错。此命令从当前 `main` 获取短入口；若需固定入口本身，可把 URL 中的 `main` 替换为某次已核验的提交 SHA。

`install.py` 是独立的发布包安装器，目标为 Debian/Ubuntu amd64/arm64。它要求固定版本、HTTPS（或测试用本地 file URL）发布包和完整 SHA256；下载后验证摘要、拒绝危险归档路径与链接、检查脚本语法，再安装源码并建立 `c` 命令。重复运行会识别相同版本，遇到已有其他 `c` 命令或异常目录会拒绝覆盖。应用数据、配置和日志始终在源码目录外。

服务器以 root 运行时默认安装到 `/opt/dabiao996`，命令位于 `/usr/local/bin/c`；普通用户默认安装到 `~/.local/share/dabiao996/toolbox`，命令位于 `~/.local/bin/c`。若后者尚未加入 PATH，安装器会显示可直接运行的完整路径。可用 `--prefix`、`--bin-dir` 指定隔离目录。

**GitHub 发布完成后**，使用固定提交的 `install.py` 和发布包生成一行安装命令。该命令会先下载并校验安装器自身，再由安装器校验完整发布包；不使用 `curl | bash`。仓库或发布包尚未就绪时，不要把示意 URL 当成可运行命令。此阶段不在本机执行默认安装，也不部署任何服务器。

命令退出码：`0` 为命令完成（含“已安装/未安装”等成功查询），`2` 为参数或配置错误，`4` 为模块或 I/O 异常，`5` 为系统不支持，`6` 为更新失败。错误详情也会在终端给出。`c doctor` 在 macOS 返回 `5` 属于预期。

`./c install-command /tmp/my-bin/c` 可以在指定目录建立快捷命令的符号链接；重复运行识别已有链接，遇到其他同名文件拒绝覆盖。不要在开发机运行不带路径的 `install-command`，它会指向 `$HOME/.local/bin/c`。全局安装需要用户另行决定；本项目没有执行。

配置优先使用 `XDG_CONFIG_HOME/dabiao996`，默认位于 `$HOME/.config/dabiao996`；状态和日志优先使用 `XDG_STATE_HOME/dabiao996`，默认位于 `$HOME/.local/state/dabiao996`。测试可用 `DABIAO_CONFIG_DIR` 和 `DABIAO_STATE_DIR` 重定向。状态目录只写固定事件名的 `events.log`，不记录模块输出、命令参数、密码或 Token。项目源码目录不存运行数据。

## 应用接入与状态

`c modules register /绝对路径/module.json` 将已审核的本地模块加入配置注册表；重复注册保持幂等。`c modules unregister ID` 只移除注册，不删除模块文件或数据。`c apps list`、`c apps info ID`、`c apps run ID ACTION` 可发现和调用模块。安装或更新动作返回后，总脚本会立即回查模块的实时状态，避免只凭动作退出码宣称成功。只有显式注册的可信代码会被调用；不得直接注册未知来源脚本。元信息、动作、路径和状态格式会校验，入口只能位于模块目录中。具体契约见 [MODULES.md](MODULES.md)。

示例位于 `examples/sample-app`，仅有只读 `status`，不会安装服务。macOS 上可以注册和列出，但运行会返回“不支持”。它演示未安装/异常两种状态，绝不声称大彪TV已经接入。真实应用的地址、端口、目录和管理命令必须由模块实时探测，再由总脚本展示；不能以默认值或假 URL 冒充实际结果。

## 总工具箱更新

默认**没有发布源**，`c update check` 和 `c update` 会返回“未配置”。应用更新与总工具箱更新分开，未来应用的 `update` 动作由对应模块提供。

发布者可在配置目录创建 `update.json`：

```json
{
  "source": "file:///绝对路径/dabiao996-release.tar.gz",
  "version": "0.2.0",
  "sha256": "完整的64位SHA256十六进制值"
}
```

正式源可用无凭据的 HTTPS URL；本地 `file://` 只用于离线验证。发布包是从项目根目录打包的 `.tar.gz`，必须包含 `c`、`toolbox.py`、`VERSION`，其中 VERSION 与配置相同。`c update check` 仅检查本地发布配置和版本号；它不请求网络，不声称已经验证远端。`c update` 会下载/读取、限制大小、校验固定 SHA256、拒绝危险路径与链接、检查语法、临时备份、替换并在替换错误或中断时恢复。更新源配置和状态均在源码目录外，不会覆盖。更新会拒绝源码目录中出现发布包没有覆盖的文件，避免丢失用户数据。异常断电或 `SIGKILL` 无法由运行中的程序执行恢复；若恰在目录替换的瞬间发生，可检查同级 `.dabiao-backup-*` 目录手工恢复。发布包和更新临时目录在正常成功或失败后会清理。

测试环境中的本地发布包由测试代码从本项目源码复制生成，绝不运行远程脚本。没有正式 release 时请保持更新源未配置。

## 测试

```sh
./tests/test.sh
```

测试只使用临时目录，不触碰真实配置、系统服务或项目外仓库；包括菜单/帮助、模块校验与幂等注册、路径拒绝、无更新源、校验失败、下载失败、本地更新成功和替换失败恢复。在 macOS 上通过不代表真实 Debian/Ubuntu 服务已验证；生产操作和未来应用安装必须在隔离 Linux 测试机单独验证。

## 已知边界与下一步

系统更新维护、网站、SSL、网络、SSH、备份、文件、定时任务、迁移尚未实现。Docker 当前只有只读版本检测。应用商店目前只展示已注册模块，没有预装第三方应用。下一步接入大彪TV时，在其**独立项目**内实现 `module.json` 与 `status`/`install` 动作，先在隔离 Debian/Ubuntu 验证幂等、真实服务探测和回执，再显式注册；不需要修改本工具箱核心，也不要把 TV 仓库当作工具箱仓库。
