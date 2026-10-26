#!/bin/bash
# 一键更新：拉取数据 -> 写入 data/macro.db -> 重建 dashboard.html
cd "$(dirname "$0")"
# 优先使用项目内虚拟环境（python3 -m venv .venv && .venv/bin/pip install -r requirements.txt）
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
"$PY" monitor/update_data.py "$@" && "$PY" monitor/build_dashboard.py
