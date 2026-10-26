<div align="center">

# MORX GMS2 MCP

**从编辑项目，到看见运行结果。**

让 AI 助手编写 GML、运行游戏、查看画面，并根据结果继续调整。

*GameMaker tools for AI agents · Local stdio MCP*

[![CI](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/MKTLMORX/morx-gms2-mcp/actions/workflows/tests.yml)
[![PyPI 0.6.2](https://img.shields.io/badge/PyPI-0.6.2-3775A9?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/morx-gms2-mcp/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/pyproject.toml)
[![Windows](https://img.shields.io/badge/Platform-Windows-0078D6?style=flat-square&logo=windows&logoColor=white)](#适用范围)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/LICENSE)

[快速开始](#快速开始) · [运行游戏](#运行游戏) · [工具与用法](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/docs/reference.md) · [更新记录](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/CHANGELOG.md)

</div>

![工作流程：编辑项目 → 隔离运行与观察 → 调整项目并重新验证](https://raw.githubusercontent.com/MKTLMORX/morx-gms2-mcp/main/docs/assets/workflow.svg)

## 功能

58 个工具，覆盖项目制作与运行检查。支持 Codex、Claude Code 等本地 MCP 客户端。

| 领域 | 支持的操作 |
| --- | --- |
| **运行与检查** | 编译运行独立副本，查看截图和日志，读写变量，模拟键鼠交互 |
| **代码与场景** | 读写 GML，创建脚本、物体、事件、房间、实例和瓦片图层 |
| **美术与音频** | 导入 PNG、WAV/OGG、字体；编辑精灵；配置声音压缩、流式播放和音频组 |
| **显示与资源** | 配置窗口、相机和视口；整理分组、重命名及删除资源 |
| **修改与恢复** | 预览变更、检测冲突、保存备份并撤销 |

## 快速开始

需要 **Windows、Python 3.10+**、已在 GameMaker 中创建并保存的项目，以及支持本地 stdio 的 MCP 客户端。以下命令使用 PowerShell。

### 1. 安装并绑定项目

在用于存放 MCP 的目录执行，将项目路径替换为自己的路径：

```powershell
python -m venv .venv
$mcpPython = (Resolve-Path '.venv\Scripts\python.exe').Path
& $mcpPython -m pip install morx-gms2-mcp

$gameProject = 'D:\Games\MyGame'
& $mcpPython -m morx_gms2_mcp bind --project $gameProject
& $mcpPython -m morx_gms2_mcp check --project $gameProject
```

`--project` 接受项目目录或 `.yyp` 路径。其他安装方式与升级步骤见[安装指南](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/docs/setup.md)。

### 2. 连接客户端

按客户端选择一条命令：

```powershell
# Codex
& $mcpPython -m morx_gms2_mcp client-config --project $gameProject --client codex --write
# Claude Code
& $mcpPython -m morx_gms2_mcp client-config --project $gameProject --client claude --write
```

将输出合并到游戏项目的 `.codex/config.toml`（Codex）或 `.mcp.json`（Claude Code），保留已有服务，然后重新加载客户端。`client-config` 只输出配置，不会自动写入配置文件。

连接后调用 `server_info` 核对绑定项目。上面的 `--write` 允许修改资源，但修改工具仍默认预览，执行需 `dry_run=false`；去掉 `--write` 即保持只读。

## 运行游戏

本机需安装 Windows x64 GameMaker Runtime，并已登录 GameMaker。为客户端配置增加运行参数（`codex` 可换为 `claude`）：

```powershell
& $mcpPython -m morx_gms2_mcp client-config --project $gameProject --client codex --write `
  --allow-run --runtime-root '<Runtime 目录>' --user-directory '<已登录用户目录>'
```

合并配置并重连后，让助手按以下步骤操作：

1. 预览并安装 `install_runtime_bridge`，在项目中加入可撤销的测试桥接。
2. 调用 `run_game`，通过 `runtime_status` 等待 `bridge_ready=true`。
3. 用 `capture_game` 查看画面、`inspect_game` 检查状态，结合输入和日志复现问题。
4. 用 `stop_game` 清理会话；修改源项目后重新 `run_game` 验证。

仅检查编译可用 `compile_game`，无需桥接。`restart_game` 保留测试存档，但使用同一项目快照。完整流程见[运行与观察](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/docs/reference.md#运行与观察)。

## 使用示例

连接后，把具体任务交给助手：

> **制作资源** — 将这组 PNG 导入为玩家精灵，设置原点和动画速度，先预览修改。

> **调整音频** — 检查背景音乐的导出设置，预览改为流式播放，并核对音频组。

> **验证效果** — 运行游戏，点击开始按钮后截图，读取玩家的 hp 和全局 score；发现问题后修改代码，重新运行验证，最后清理会话。

## 适用范围

- 支持现代 `GMProject v1` 项目；运行目标为 **Windows VM**。已验证 IDE 2026.0.0.16 / Runtime 2026.0.0.23，其他版本需自行验证。
- 截图和状态由游戏内桥接提供，支持运行中检查；**不提供 IDE 操控、原生断点或暂停/逐行调试**。默认键鼠输入使用窗口消息，物理鼠标输入需显式选择。
- 运行使用独立副本和存档，停止后清理。它仍以当前用户权限执行，仅对受信任项目启用 `--allow-run`。
- 资源修改直接作用于绑定项目，先保存 IDE 内容，避免同时写入。重命名资源不会改写 GML 中的资源名；结构校验不能代替编译和实际运行检查。

## 文档与支持

- [安装与客户端配置](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/docs/setup.md)：pipx、源码安装、升级、运行环境和连接排查。
- [工具与用法](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/docs/reference.md)：全部 58 个工具、音频与精灵设置、运行诊断及支持限制。
- [更新记录](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/CHANGELOG.md) · [报告问题](https://github.com/MKTLMORX/morx-gms2-mcp/issues) · [贡献指南](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/CONTRIBUTING.md) · [安全说明](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/SECURITY.md)

独立社区项目，与 GameMaker 官方无隶属关系。采用 [MIT 许可证](https://github.com/MKTLMORX/morx-gms2-mcp/blob/main/LICENSE)。
