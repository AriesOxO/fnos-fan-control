# 贡献指南 | Contributing Guide

[English](#english) | [简体中文](#简体中文)

## 简体中文

感谢你对 fnos-fan-control 的关注！欢迎以下形式的贡献：

### 提交 Issue

- **Bug 报告**：请包含 fnOS 版本、传感器芯片型号（`cat /sys/class/hwmon/hwmon*/name`）、错误日志
- **功能建议**：描述使用场景和预期行为
- **新芯片适配**：如果你的 NAS 使用了未列出的芯片，欢迎提交适配报告

### 提交 Pull Request

1. Fork 本仓库
2. 基于 `dev` 分支创建特性分支：`git checkout -b feature/your-feature dev`
3. 提交更改：`git commit -m "feat: 描述你的改动"`
4. 推送分支：`git push origin feature/your-feature`
5. 向 `dev` 分支创建 Pull Request

### 开发环境

```bash
git clone https://github.com/AriesOxO/fnos-fan-control.git
cd fnos-fan-control

# 运行测试（112 个，不需要真实硬件）
python -m unittest discover -s tests -v
```

### 项目结构

```
src/app/bin/         # Python 后端
src/app/bin/static/  # Web 前端（单文件 HTML）
src/cmd/             # FPK 生命周期脚本
tests/               # 单元测试 + 集成测试
tests/mock_hardware.py  # 共享 Mock 硬件
```

### 分支策略

- `main`：稳定发布分支，仅通过 `dev` 合并
- `dev`：开发分支，日常开发在此

### 代码规范

- Python：PEP 8，类型注解
- 前端：原生 JS，零外部依赖
- 提交信息：`feat:` / `fix:` / `docs:` / `ci:` / `chore:` 前缀
- 测试：新功能必须附带测试

### 安全注意事项

本项目直接操作硬件（/sys/class/hwmon），修改风扇控制代码时请确保：

- 所有模式使用 `pwm_enable=1`（手动控制），不依赖芯片自动模式
- 任何异常路径都有兜底（全速保护或降级到默认曲线）
- 不要移除最低转速保护（绝对下限 10%）
- 温度读取失败时走全速保护而非静默忽略
- 测试时注意观察风扇是否正常运转
- 多芯片场景下注意通道名冲突处理

---

## English

Thanks for your interest in fnos-fan-control!

### Issues

- **Bug reports**: Include fnOS version, sensor chip (`cat /sys/class/hwmon/hwmon*/name`), error logs
- **Feature requests**: Describe use case and expected behavior
- **New chip support**: If your NAS uses an unlisted chip, please submit a compatibility report

### Pull Requests

1. Fork the repository
2. Branch from `dev`: `git checkout -b feature/your-feature dev`
3. Commit: `git commit -m "feat: describe your change"`
4. Push: `git push origin feature/your-feature`
5. Open PR against `dev`

### Development

```bash
git clone https://github.com/AriesOxO/fnos-fan-control.git
cd fnos-fan-control

# Run tests (112, no real hardware needed)
python -m unittest discover -s tests -v
```

### Branch Strategy

- `main`: Stable release, merged from `dev` only
- `dev`: Development branch

### Code Style

- Python: PEP 8, type annotations
- Frontend: Vanilla JS, zero external dependencies
- Commits: `feat:` / `fix:` / `docs:` / `ci:` / `chore:` prefixes
- Tests: New features must include tests

### Safety

This project directly controls hardware (/sys/class/hwmon):

- All modes use `pwm_enable=1` (manual), never rely on chip auto mode
- Every error path has a fallback (full speed or default curve)
- Do not remove minimum speed protection (absolute floor 10%)
- Temp read failures must trigger full-speed protection, not silent ignore
- Multi-chip scenarios: handle channel name conflicts
