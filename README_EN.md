# fnos-fan-control

Fan Controller for FlyNAS (fnOS) — FPK App with Universal hwmon Support

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-fnOS%20x86-green.svg)]()
[![Python](https://img.shields.io/badge/Python-3.11%2B-yellow.svg)]()
[![Tests](https://img.shields.io/badge/Tests-112%20passed-brightgreen.svg)]()

English | [简体中文](README.md)

## Features

- **Four Operating Modes** — Default (conservative curve), Auto (custom curve), Manual (fixed speed), Full Speed
- **Custom Fan Curve** — 2-10 nodes, auto-generation + manual tuning, collapsible editor with SVG preview
- **Multi-device Compatible** — Universal hwmon detection, supports ITE/Nuvoton/Fintek chips, Intel + AMD CPU temps
- **Multi-zone Control** — Independent PWM channels, temp sources, and curves per zone
- **Web Management UI** — Dark theme, real-time monitoring, responsive, event logging
- **Multi-layer Safety** — Watchdog, degradation, pwm_enable self-healing, min speed protection (10%)
- **Zero Dependencies** — Python stdlib only, threaded HTTP, memory ≤ 15MB
- **GitHub Actions** — Auto-build FPK on Release

## Compatibility

### Supported Chips

Universal hwmon detection — any chip with `pwm` files can be controlled:

| Chip | Status | Notes |
|------|--------|-------|
| ITE IT8772E | ✅ Verified | Dev/test platform |
| ITE IT8786E/IT8688E | ✅ Supported | ITE family |
| Nuvoton NCT6775/6776/6779/6798 | ✅ Supported | Nuvoton family |
| Nuvoton NCT6687 | ✅ Supported | Next-gen Nuvoton |
| Fintek F71882FG/F71868A | ✅ Supported | Fintek family |
| Other hwmon chips | ✅ Supported | If pwm files exist |

### CPU Temperature Sensors

| Platform | Driver | Smart Matching |
|----------|--------|---------------|
| Intel | coretemp | Prefers "Package id 0" label |
| AMD Ryzen | k10temp | Prefers "Tdie", falls back to "Tctl" |
| AMD (3rd party) | zenpower | Prefers "Tdie" |
| ARM / Generic | cpu_thermal | temp1_input |

## Installation

### Option 1: Download FPK

1. Download latest `.fpk` from [Releases](https://github.com/AriesOxO/fnos-fan-control/releases)
2. fnOS App Center → Manual Install → Upload
3. Requires python312 (install from App Center first)

### Option 2: Build from Source

```bash
git clone https://github.com/AriesOxO/fnos-fan-control.git
cd fnos-fan-control

scp -r src/ user@nas:/tmp/fpk-src/
ssh user@nas 'cd /tmp/fpk-src && fnpack build'
```

### Uninstall & Reinstall

If install fails with "user group check failed", run as root:

```bash
bash /tmp/cleanup-fan-control.sh
```

## Usage

### Operating Modes

| Mode | Description |
|------|-------------|
| **Default** | Built-in conservative curve. Quiet at low temps, aggressive at high temps |
| **Auto** | User-defined curve with 2-10 nodes |
| **Manual** | Fixed speed via slider |
| **Full Speed** | 100% for emergency cooling |

### Fan Curve

- Chart always visible; click "Edit Curve" to expand editor
- **Auto-generate**: Select node count and temp range, one-click generation
- **Manual tuning**: Edit values directly, real-time SVG preview
- Editor auto-collapses on save, showing node summary tags

### Safety

| Mechanism | Description |
|-----------|-------------|
| Min speed | Absolute floor 10%, prevents fan stall |
| Temp failure | 3 consecutive failures → full speed; 5 → degrade to default |
| PWM write failure | 3 consecutive → degrade |
| pwm_enable self-heal | Verified and corrected every control cycle |
| Watchdog | Restores safe state within 5s if process crashes |
| Signal handling | SIGTERM graceful shutdown, SIGHUP config reload |

## Project Structure

```
fnos-fan-control/
├── src/
│   ├── app/bin/              # Python application
│   │   ├── main.py           # Entry point (signals, OOM protection)
│   │   ├── hardware.py       # Hardware abstraction (universal hwmon)
│   │   ├── fan_controller.py # Fan control core (multi-zone)
│   │   ├── config_manager.py # Config management (v1/v2 compatible)
│   │   ├── web_server.py     # Threaded HTTP + REST API
│   │   └── static/index.html # Web frontend (single file)
│   ├── app/ui/               # fnOS desktop entry + icons
│   ├── cmd/                  # FPK lifecycle scripts
│   ├── config/               # Privilege config
│   ├── wizard/               # Install/config wizards
│   └── manifest              # FPK metadata
├── tests/                    # 112 unit + integration tests
├── scripts/                  # Build and cleanup scripts
└── .github/workflows/        # CI auto-build
```

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web management UI |
| `/api/status` | GET | Real-time status (temp, RPM, PWM, mode, zones) |
| `/api/config` | GET/POST | Read/update configuration |
| `/api/hardware` | GET | Hardware detection results |
| `/api/mode` | POST | Switch operating mode (optional zone_id) |
| `/api/logs` | GET | Event logs |
| `/api/logs/clear` | POST | Clear logs |
| `/api/curve/generate` | POST | Auto-generate fan curve |
| `/api/zones/{id}/mode` | POST | Switch zone-specific mode |
| `/api/zones/{id}/config` | POST | Update zone-specific config |

## Testing

```bash
python -m unittest discover -s tests -v  # 112 tests
```

## Contributing

Issues and Pull Requests welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT License](LICENSE)
