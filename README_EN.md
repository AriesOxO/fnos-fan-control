# fnos-fan-control

Fan Controller for FlyNAS (fnOS) — FPK App Based on IT8772 Chip

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-fnOS%20x86-green.svg)]()
[![Python](https://img.shields.io/badge/Python-3.11%2B-yellow.svg)]()

English | [简体中文](README.md)

## Features

- **Four Operating Modes** — Default (chip auto), Auto (custom curve), Manual (fixed speed), Full Speed (emergency)
- **Custom Fan Curve** — 2-10 nodes, auto-generation and manual fine-tuning, linear interpolation
- **Web Management UI** — Dark theme, real-time temperature/RPM/PWM monitoring, responsive layout
- **Multi-layer Safety** — Watchdog process, degradation, pwm_enable self-healing, minimum speed protection
- **Zero Dependencies** — Python standard library only, memory ≤ 15MB
- **Event Logging** — Operations, warnings, errors with clear support

## Hardware Requirements

| Item | Requirement |
|------|-------------|
| System | fnOS (Debian 12) x86 |
| Sensor Chip | IT8772 (ITE Super I/O) |
| Dependency | python312 (install via fnOS App Center) |

## Installation

### Option 1: FPK Package

1. Download `fan-control.fpk` from [Releases](https://github.com/AriesOxO/fnos-fan-control/releases)
2. fnOS App Center → Manual Install → Upload FPK file
3. Start from the app list after installation

### Option 2: Build from Source

```bash
# Clone the repository
git clone https://github.com/AriesOxO/fnos-fan-control.git
cd fnos-fan-control

# Build on fnOS server
scp -r src/ user@nas:/tmp/fpk-src/
ssh user@nas 'cd /tmp/fpk-src && fnpack build'
```

## Usage

### Operating Modes

| Mode | Description |
|------|-------------|
| **Default** | No intervention. IT8772 chip controls the fan autonomously. Safest option |
| **Auto** | Adjusts fan speed based on custom temperature curve (2-10 nodes) |
| **Manual** | Fixed speed via PWM percentage slider |
| **Full Speed** | 100% fan speed for emergency cooling |

### Fan Curve

- **Auto-generate**: Select node count (4/6/8/10) and temperature range
- **Manual tuning**: Edit temperature and PWM values directly, real-time SVG preview
- Non-linear distribution: gentle at low temps, steep at high temps

### Safety Mechanisms

- Minimum speed protection (absolute floor 10%), prevents fan stall
- Temperature sensor failure → full speed protection → degrade to default mode
- PWM write failure → auto restore chip autonomous control
- Watchdog process: restores safe state within 5 seconds if main process crashes
- Per-cycle pwm_enable consistency check (self-healing)

## Uninstall & Reinstall

Fan automatically returns to system default control after uninstall.

If reinstall fails with "user group check failed", run cleanup as root:

```bash
bash /tmp/cleanup-fan-control.sh
```

## Project Structure

```
fnos-fan-control/
├── src/
│   ├── app/bin/              # Python application code
│   │   ├── main.py           # Entry point
│   │   ├── hardware.py       # Hardware abstraction layer
│   │   ├── fan_controller.py # Fan control core
│   │   ├── config_manager.py # Configuration management
│   │   ├── web_server.py     # HTTP server + REST API
│   │   └── static/index.html # Web frontend
│   ├── cmd/                  # FPK lifecycle scripts
│   ├── config/               # Privilege and resource config
│   ├── wizard/               # Install/config wizards
│   └── manifest              # FPK metadata
├── scripts/                  # Build and cleanup scripts
└── test/                     # Test scripts
```

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web management UI |
| `/api/status` | GET | Real-time status (temp, RPM, PWM, mode) |
| `/api/config` | GET/POST | Read/update configuration |
| `/api/mode` | POST | Switch operating mode |
| `/api/logs` | GET | Get event logs |
| `/api/logs/clear` | POST | Clear logs |
| `/api/curve/generate` | POST | Auto-generate fan curve |

## Contributing

Issues and Pull Requests are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT License](LICENSE)
