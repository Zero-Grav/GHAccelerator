#!/bin/bash
# macOS Finder 双击启动（若被 Gatekeeper 拦截，请走系统设置允许，或使用 run.sh）
cd "$(dirname "$0")" || exit 1
chmod +x ./run.sh 2>/dev/null || true
exec ./run.sh "$@"
