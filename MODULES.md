# 模块开发规范 v1

每个应用是独立目录，至少包含 `module.json` 与入口 Python 脚本。脚本可直接运行（`python3 module.py status`），也可通过 `c apps run ID status` 运行。模块不应依赖总脚本内部函数；安装等动作应自行检查幂等与当前状态。总脚本不自动请求 root、不执行 shell 字符串。

元信息示例见 `examples/sample-app/module.json`。字段必须恰好为：`schema`（整数 1）、`id`（2–32 位小写字母开头，之后数字/连字符）、`name`、`version`（数字点号格式）、`category`、`entry`、`actions`、`os`、`arch`。`entry` 是模块目录内的相对路径，不允许 `..`、绝对路径、符号链接或目录外文件。`actions` 至少含 `status`，可增加 `install`、`update`、`uninstall`；`os` 只可含 `debian`/`ubuntu`，`arch` 只可含 `amd64`/`arm64`。总脚本会在不支持的平台阻止模块动作。

`status` 必须在标准输出写**一个 JSON 对象**，包含且只包含：

```json
{
  "status": "not_installed",
  "address": null,
  "port": null,
  "directory": null,
  "management": "c apps info sample-app",
  "message": "未发现示例应用"
}
```

`status` 只能是 `not_installed`、`installed`、`degraded`、`unsupported`。地址仅接受实际检测到的 IPv4/IPv6 字面值；端口是实际检测到的 `1..65535` 数字。未知值用 `null`，不输出 `<服务器IP>`、默认端口或猜测 URL。目录、管理命令和说明不能带密码、Token、密钥，也不能包含换行。不要在状态里报告尚未验证的“运行正常”；`installed` 应至少确认应用的数据和进程/服务真实存在，冲突或服务异常用 `degraded`。`status` 无附加参数。非零退出码表示模块执行失败，总脚本返回 `4` 并记录固定错误事件。安装成功后总脚本建议再运行 `c apps info ID` 获取实时结果。

模块自身的 `install`/`update`/`uninstall` 为未来扩展点，本版示例没有这些动作。应用更新不能调用 `c update` 来假装更新应用。应用变更必须在模块内设计权限、备份、错误恢复、确认和数据保留策略；生产部署需另行授权与验证。大彪TV接入的最小步骤是：先在 TV 独立目录实现元信息和只读 `status`，在测试机通过；再增加经过隔离测试的 `install`；最后执行 `c modules register /path/to/dbtv/module.json`。本工具箱不会自动寻找或修改 TV 仓库。
