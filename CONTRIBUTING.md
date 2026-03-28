# 贡献指南 | Contributing Guide

[English](#english) | [简体中文](#简体中文)

## 简体中文

感谢你对 fnos-fan-control 的关注！欢迎以下形式的贡献：

### 提交 Issue

- **Bug 报告**：请包含 fnOS 版本、传感器芯片型号、错误日志
- **功能建议**：描述使用场景和预期行为

### 提交 Pull Request

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交更改：`git commit -m "feat: 描述你的改动"`
4. 推送分支：`git push origin feature/your-feature`
5. 创建 Pull Request

### 开发环境

```bash
# 克隆仓库
git clone https://github.com/AriesOxO/fnos-fan-control.git

# 项目结构
src/app/bin/     # Python 后端代码
src/cmd/         # FPK 生命周期脚本
src/app/bin/static/  # Web 前端（单文件 HTML）
test/            # 测试脚本（需要在 fnOS 真机上运行）
```

### 代码规范

- Python：遵循 PEP 8
- 前端：原生 JS，不引入外部依赖
- 提交信息：使用 `feat:`、`fix:`、`docs:` 等前缀

### 安全注意事项

本项目直接操作硬件（/sys/class/hwmon），修改风扇控制代码时请确保：

- 任何异常路径都能恢复 `pwm_enable=2`（芯片自动模式）
- 不要移除最低转速保护（绝对下限 10%）
- 测试时注意观察风扇是否正常运转

---

## English

Thanks for your interest in fnos-fan-control! Contributions are welcome:

### Issues

- **Bug reports**: Include fnOS version, sensor chip model, error logs
- **Feature requests**: Describe use case and expected behavior

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit changes: `git commit -m "feat: describe your change"`
4. Push: `git push origin feature/your-feature`
5. Open a Pull Request

### Development

```bash
git clone https://github.com/AriesOxO/fnos-fan-control.git

# Structure
src/app/bin/         # Python backend
src/cmd/             # FPK lifecycle scripts
src/app/bin/static/  # Web frontend (single HTML file)
test/                # Test scripts (requires fnOS hardware)
```

### Code Style

- Python: PEP 8
- Frontend: Vanilla JS, no external dependencies
- Commits: Use `feat:`, `fix:`, `docs:` prefixes

### Safety

This project directly controls hardware (/sys/class/hwmon). When modifying fan control code:

- Ensure all error paths restore `pwm_enable=2` (chip auto mode)
- Do not remove minimum speed protection (absolute floor 10%)
- Monitor fan operation during testing
