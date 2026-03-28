# fnos-fan-control

飞牛NAS (fnOS) 风扇控制器 — 基于 IT8772 芯片的 FPK 应用

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-fnOS%20x86-green.svg)]()
[![Python](https://img.shields.io/badge/Python-3.11%2B-yellow.svg)]()

[English](README_EN.md) | 简体中文

## 功能特性

- **四种运行模式** — 默认（芯片自控）、自动（温控曲线）、手动（固定转速）、全速（紧急散热）
- **自定义温控曲线** — 2-10 个节点，支持自动生成和手动微调，线性插值平滑过渡
- **Web 管理界面** — 深色主题，实时温度/转速/PWM 监控，响应式布局
- **多层安全防护** — 看门狗进程、异常降级、pwm_enable 自愈、最低转速保护
- **零依赖** — 仅使用 Python 标准库，内存占用 ≤ 15MB
- **事件日志** — 记录操作、告警、错误，支持清空

## 截图

```
┌─────────────────────────────────────────┐
│  风扇控制器                    ● 运行中  │
├─────────────────────────────────────────┤
│  🌡 CPU 温度   💾 硬盘温度   🌀 转速    │
│    47°C         35°C       2500 RPM    │
├─────────────────────────────────────────┤
│  [默认模式] [自动模式] [手动模式] [全速]  │
├─────────────────────────────────────────┤
│  📊 温控曲线（SVG 可视化 + 节点编辑）     │
└─────────────────────────────────────────┘
```

## 硬件要求

| 项目 | 要求 |
|------|------|
| 系统 | fnOS (Debian 12) x86 |
| 传感器芯片 | IT8772 (ITE Super I/O) |
| 依赖 | python312（飞牛应用中心安装） |

## 安装

### 方式一：FPK 安装包

1. 从 [Releases](https://github.com/AriesOxO/fnos-fan-control/releases) 下载 `fan-control.fpk`
2. 在飞牛应用中心 → 手动安装 → 上传 FPK 文件
3. 安装完成后在应用列表中启动

### 方式二：自行打包

```bash
# 克隆仓库
git clone https://github.com/AriesOxO/fnos-fan-control.git
cd fnos-fan-control

# 在 fnOS 服务器上打包
scp -r src/ user@nas:/tmp/fpk-src/
ssh user@nas 'cd /tmp/fpk-src && fnpack build'
```

## 使用说明

### 运行模式

| 模式 | 说明 |
|------|------|
| **默认模式** | 不干预风扇，由 IT8772 芯片自主温控。安装后默认选择，最安全 |
| **自动模式** | 根据自定义温控曲线自动调节转速，支持 2-10 个节点 |
| **手动模式** | 固定转速运行，通过滑块设置 PWM 百分比 |
| **全速模式** | 风扇 100% 全速，用于紧急散热 |

### 温控曲线

- **自动生成**：选择节点数（4/6/8/10）和温度范围，系统自动计算合理的转速分布
- **手动微调**：直接编辑节点的温度和转速值，曲线图实时预览
- 低温区平缓、高温区陡峭的非线性分布

### 安全机制

- 最低转速保护（绝对下限 10%），防止风扇停转
- 温度传感器连续读取失败 → 全速保护 → 异常降级到默认模式
- PWM 写入失败 → 自动恢复芯片自动温控
- 看门狗进程：主进程崩溃后 5 秒内恢复安全状态
- 每个控制周期自动校验 pwm_enable 一致性（自愈机制）

## 卸载与重装

卸载后风扇自动恢复系统原始温控。

如果重装时提示"检查用户组失败"，以 root 身份执行清理脚本：

```bash
bash /tmp/cleanup-fan-control.sh
```

## 项目结构

```
fnos-fan-control/
├── src/
│   ├── app/bin/              # Python 应用代码
│   │   ├── main.py           # 入口
│   │   ├── hardware.py       # 硬件抽象层
│   │   ├── fan_controller.py # 风扇控制核心
│   │   ├── config_manager.py # 配置管理
│   │   ├── web_server.py     # HTTP 服务 + API
│   │   └── static/index.html # Web 前端
│   ├── cmd/                  # FPK 生命周期脚本
│   ├── config/               # 权限和资源配置
│   ├── wizard/               # 安装/配置向导
│   └── manifest              # FPK 元数据
├── scripts/                  # 构建和清理脚本
├── test/                     # 测试脚本
└── docs/                     # 设计文档（仅本地）
```

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web 管理界面 |
| `/api/status` | GET | 实时状态（温度、转速、PWM、模式） |
| `/api/config` | GET/POST | 读取/更新配置 |
| `/api/mode` | POST | 切换运行模式 |
| `/api/logs` | GET | 获取事件日志 |
| `/api/logs/clear` | POST | 清空日志 |
| `/api/curve/generate` | POST | 自动生成温控曲线 |

## 贡献

欢迎提交 Issue 和 Pull Request！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT License](LICENSE)
