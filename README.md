# fnos-fan-control

飞牛NAS (fnOS) 风扇控制器 — 通用 hwmon 芯片的 FPK 应用

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-fnOS%20x86-green.svg)]()
[![Python](https://img.shields.io/badge/Python-3.11%2B-yellow.svg)]()
[![Tests](https://img.shields.io/badge/Tests-120%20passed-brightgreen.svg)]()

[English](README_EN.md) | 简体中文

## 功能特性

- **四种运行模式** — 默认（保守曲线）、自动（自定义曲线）、手动（固定转速）、全速（紧急散热）
- **自定义温控曲线** — 2-10 个节点，自动生成 + 手动微调，折叠式编辑器 + SVG 实时预览
- **多设备兼容** — 通用 hwmon 探测，支持 ITE/Nuvoton/Fintek 等芯片，Intel + AMD CPU 温度
- **多风扇区域** — 每个区域独立绑定 PWM 通道、温度来源和温控曲线
- **Web 管理界面** — 深色主题，实时监控，响应式布局，事件日志
- **多层安全防护** — 看门狗、异常降级、pwm_enable 自愈、最低转速保护（10%）
- **访问密码** — 可选密码认证，Cookie + Header 双模式，安装时配置
- **零依赖** — 仅 Python 标准库，多线程 HTTP，内存 ≤ 15MB
- **GitHub Actions** — Release 自动构建 FPK

## 兼容性

### 支持的芯片

通用 hwmon 探测，任何有 `pwm` 文件的芯片均可控制：

| 芯片 | 状态 | 说明 |
|------|------|------|
| ITE IT8772E | ✅ 已验证 | 开发测试机型 |
| ITE IT8786E/IT8688E | ✅ 支持 | ITE 系列 |
| Nuvoton NCT6775/6776/6779/6798 | ✅ 支持 | Nuvoton 系列 |
| Nuvoton NCT6687 | ✅ 支持 | 新一代 Nuvoton |
| Fintek F71882FG/F71868A | ✅ 支持 | Fintek 系列 |
| 其他 hwmon 芯片 | ✅ 支持 | 有 pwm 文件即可 |

### CPU 温度传感器

| 平台 | 驱动 | 智能匹配 |
|------|------|---------|
| Intel | coretemp | 优先 "Package id 0" label |
| AMD Ryzen | k10temp | 优先 "Tdie"，回退 "Tctl" |
| AMD (第三方) | zenpower | 优先 "Tdie" |
| ARM / 通用 | cpu_thermal | temp1_input |

## 安装

### 方式一：下载安装包

1. 从 [Releases](https://github.com/AriesOxO/fnos-fan-control/releases) 下载最新 `.fpk`
2. 飞牛应用中心 → 手动安装 → 上传
3. 依赖 python312（应用中心先安装）
4. 安装向导中可设置访问密码（留空则不启用认证）

### 方式二：自行打包

```bash
git clone https://github.com/AriesOxO/fnos-fan-control.git
cd fnos-fan-control

# 上传到 fnOS 服务器打包
scp -r src/ user@nas:/tmp/fpk-src/
ssh user@nas 'cd /tmp/fpk-src && fnpack build'
```

### 卸载与重装

如果安装时提示"检查用户组失败"，以 root 执行清理脚本（随应用安装自动释放到 /tmp）：

```bash
bash /tmp/cleanup-fan-control.sh
```

如果服务器重启过导致脚本丢失，手动执行：

```bash
userdel fan-control 2>/dev/null
groupdel fan-control 2>/dev/null
for vol in $(ls -d /vol* 2>/dev/null); do
  for dir in @appconf @appdata @apphome @appmeta @apptemp @appcenter; do
    rm -rf "$vol/$dir/fan-control"
  done
done
rm -rf /var/apps/fan-control
```

## 使用说明

### 运行模式

| 模式 | 说明 |
|------|------|
| **默认模式** | 内置保守温控曲线，低温安静、高温积极散热。安装后默认选择 |
| **自动模式** | 用户自定义温控曲线，2-10 节点精细控制 |
| **手动模式** | 固定转速，滑块调节 |
| **全速模式** | 100% 转速，紧急散热 |

### 温控曲线

- 曲线图始终显示，点击"编辑曲线"展开编辑器
- **自动生成**：选择节点数和温度范围，一键生成（低温平缓、高温陡峭）
- **手动微调**：编辑温度和转速值，SVG 实时预览
- 保存后编辑器自动收拢，显示节点摘要标签

### 安全机制

| 机制 | 说明 |
|------|------|
| 最低转速 | 绝对下限 10%，防止风扇停转 |
| 温度失败保护 | 连续 3 次读取失败 → 全速；5 次 → 降级到默认模式 |
| PWM 写入失败 | 连续 3 次失败 → 降级 |
| pwm_enable 自愈 | 每个控制周期自动校验和修正 |
| 看门狗 | 主进程崩溃后 5 秒内恢复安全状态 |
| 信号处理 | SIGTERM 优雅退出，SIGHUP 热重载配置 |
| 访问认证 | 可选密码保护，Cookie / Header 双模式 |

## 项目结构

```
fnos-fan-control/
├── src/
│   ├── app/bin/              # Python 应用代码
│   │   ├── main.py           # 入口（信号处理、OOM 保护）
│   │   ├── hardware.py       # 硬件抽象层（通用 hwmon）
│   │   ├── fan_controller.py # 风扇控制核心（多区域）
│   │   ├── config_manager.py # 配置管理（v1/v2 兼容）
│   │   ├── web_server.py     # 多线程 HTTP + REST API
│   │   └── static/index.html # Web 前端（单文件）
│   ├── app/ui/               # 飞牛桌面入口 + 图标
│   ├── cmd/                  # FPK 生命周期脚本
│   ├── config/               # 权限配置
│   ├── wizard/               # 安装/配置向导
│   └── manifest              # FPK 元数据
├── tests/                    # 120 个单元测试 + 集成测试
├── scripts/                  # 构建和清理脚本
└── .github/workflows/        # CI 自动构建
```

## API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web 管理界面 |
| `/api/status` | GET | 实时状态（温度、转速、PWM、模式、区域） |
| `/api/config` | GET/POST | 读取/更新配置 |
| `/api/hardware` | GET | 硬件探测结果（芯片、传感器） |
| `/api/mode` | POST | 切换运行模式（可指定区域） |
| `/api/logs` | GET | 获取事件日志 |
| `/api/logs/clear` | POST | 清空日志 |
| `/api/curve/generate` | POST | 自动生成温控曲线 |
| `/api/zones/{id}/mode` | POST | 切换指定区域模式 |
| `/api/zones/{id}/config` | POST | 更新指定区域配置 |
| `/api/auth/status` | GET | 认证状态（是否启用、是否已认证） |
| `/api/auth/login` | POST | 登录（返回 Cookie） |

## 测试

```bash
# 运行全部测试（120 个）
python -m unittest discover -s tests -v

# 测试覆盖：
# - 配置校验（25 个）：模式/曲线/范围/并发/损坏恢复
# - 硬件抽象（30 个）：多芯片/AMD/Intel/label匹配/通道冲突
# - 控制核心（27 个）：插值/模式切换/降级/全速保护/除零
# - Web API（20 个）：所有端点/输入校验/CORS/错误处理
# - 认证（8 个）：密码启用/禁用/Cookie/Header/登录/401
# - 多区域（10 个）：状态/模式切换/配置更新
```

## 贡献

欢迎提交 Issue 和 Pull Request！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[MIT License](LICENSE)
