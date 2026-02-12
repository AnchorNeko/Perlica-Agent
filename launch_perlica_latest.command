#!/bin/bash
set -u

PROJECT_DIR="/Users/anchorcat/Desktop/Perlica-Agent"
PREFERRED_PY="/Users/anchorcat/miniconda3/bin/python"

wait_before_close() {
  if [ -t 0 ]; then
    echo
    read -n 1 -s -r -p "按任意键关闭窗口... (Press any key to close)"
    echo
  fi
}

if [ -x "$PREFERRED_PY" ]; then
  PY_BIN="$PREFERRED_PY"
else
  PY_BIN="$(command -v python3 || true)"
fi

if [ -z "${PY_BIN:-}" ]; then
  echo "错误: 未找到可用的 Python3 (No Python3 found)."
  wait_before_close
  exit 1
fi

cd "$PROJECT_DIR" || {
  echo "错误: 无法进入项目目录: $PROJECT_DIR"
  wait_before_close
  exit 1
}

if ! "$PY_BIN" - <<'PY' >/dev/null 2>&1
import importlib.util
mods = ["typer", "rich", "textual", "tomli"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
raise SystemExit(1 if missing else 0)
PY
then
  echo "正在安装运行所需依赖... (Installing runtime dependencies)"
  "$PY_BIN" -m pip install \
    "typer>=0.12.5,<1.0.0" \
    "rich>=13.9.4,<14.0.0" \
    "textual>=0.76.0,<1.0.0" \
    "tomli>=2.0.1,<3.0.0" >/dev/null
fi

export PYTHONPATH="$PROJECT_DIR/src"
echo "启动 Perlica 最新源码版本... (Starting latest source version)"
echo "Python: $PY_BIN"
echo "Project: $PROJECT_DIR"
echo

"$PY_BIN" -m perlica.cli
status=$?

echo
echo "Perlica 已退出，状态码: $status"
wait_before_close
exit $status
