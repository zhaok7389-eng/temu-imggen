#!/bin/bash
# 双击启动 Temu 主图工具（Mac）。
# 第一次会自动安装 Python 3.12 和依赖（几分钟）；以后每次双击几秒就打开浏览器。
# 这个窗口别关：关掉窗口 = 关掉工具。

REPO="zhaok7389-eng/temu-imggen"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

# 用 GitHub 令牌（只读、只读这一个仓库，存在钥匙串里）检查并下载最新版。
# 只替换代码；workspace/（产品、出的图、账本）、config.yaml（设置）、.venv-app/ 不动。
update_from_github() {
  local T SHA HAVE TMP SRC
  T="$(security find-generic-password -s temu-imggen -a GITHUB_TOKEN -w 2>/dev/null)"
  # 仓库公开时不需要令牌；改成私有后用钥匙串里的令牌
  local AUTH=()
  [ -n "$T" ] && AUTH=(-H "Authorization: Bearer $T")
  say_step "检查更新"
  SHA="$(curl -fsS --max-time 15 "${AUTH[@]}" -H "Accept: application/vnd.github.sha" \
        "https://api.github.com/repos/$REPO/commits/main" 2>/dev/null | tr -cd '0-9a-f' | cut -c1-40)"
  [ ${#SHA} -eq 40 ] || SHA=""
  if [ -z "$SHA" ]; then
    echo "跳过更新：连不上 GitHub，或令牌失效（打开「钥匙串访问」搜 temu-imggen，删掉 GITHUB_TOKEN 那条，下次会重新问）"
    return 0
  fi
  HAVE="$(cat .app-version 2>/dev/null)"
  if [ "$SHA" = "$HAVE" ]; then
    echo "已是最新"
    return 0
  fi
  echo "发现新版本，下载中…"
  TMP="$(mktemp -d)"
  if curl -fsSL --max-time 300 "${AUTH[@]}" -H "Accept: application/vnd.github+json" \
       "https://api.github.com/repos/$REPO/zipball/$SHA" -o "$TMP/app.zip" \
     && /usr/bin/ditto -x -k "$TMP/app.zip" "$TMP/x"; then
    SRC="$(find "$TMP/x" -mindepth 1 -maxdepth 1 -type d | head -1)"
    # 下载包里只有仓库里的代码（workspace、config.yaml、.venv-app 本来就不在仓库里），直接覆盖
    if [ -n "$SRC" ] && cp -R "$SRC"/. ./; then
      chmod +x ./*.command
      echo "$SHA" > .app-version
      echo "已更新到最新版"
      rm -rf "$TMP"
      exec bash "$SELF" --updated      # 用新版启动文件继续
    fi
  fi
  rm -rf "$TMP"
  echo "更新下载失败，先用当前版本"
}

# 整个脚本先读完再执行：运行中更新了这个文件也不会出错
main() {
cd "$(dirname "$SELF")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

say_step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
fail() {
  printf '\n\033[1;31m出错了：%s\033[0m\n' "$1"
  echo "把这个窗口的内容截图发给 Claude。按回车关闭。"
  read -r _
  exit 1
}

STUDIO_URL="http://127.0.0.1:8765/studio"
studio_running() {
  curl -fsS --noproxy '*' --max-time 3 "$STUDIO_URL" 2>/dev/null | grep -q '珂学家电商套图工作台'
}

OLD_PID="$(lsof -nP -iTCP:8765 -sTCP:LISTEN -t 2>/dev/null | head -1)"
if [ -n "$OLD_PID" ]; then
  OLD_DIR="$(lsof -a -p "$OLD_PID" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
  if [ "$OLD_DIR" != "$(pwd -P)" ] || ! ps -o command= -p "$OLD_PID" 2>/dev/null | grep -Eq '(^|[ /])app\.py([[:space:]]|$)'; then
    fail "8765 已被另一份本机程序占用，请先正常退出那份程序。"
  fi
  JOBS="$(curl -fsS --noproxy '*' --max-time 3 http://127.0.0.1:8765/api/jobs 2>/dev/null)"
  [ -n "$JOBS" ] || fail "无法确认旧工作台是否有任务运行，请先正常退出旧工作台。"
  if printf '%s' "$JOBS" | grep -Eq '"state"[[:space:]]*:[[:space:]]*"running"'; then
    if studio_running; then open "$STUDIO_URL"; return; fi
    fail "旧工作台仍有任务运行，等任务完成后再更新。"
  fi
fi

# Git 更新只做快进，遇到本地文件冲突时由 Git 保留现状。
if [ "$1" != "--updated" ] && command -v git >/dev/null 2>&1 \
    && [ "$(git rev-parse --show-toplevel 2>/dev/null)" = "$(pwd -P)" ]; then
  say_step "检查更新"
  if git fetch --quiet origin main 2>/dev/null; then
    if [ "$(git rev-parse HEAD)" = "$(git rev-parse FETCH_HEAD)" ]; then
      echo "已是最新"
    elif git merge --ff-only --no-overwrite-ignore --no-autostash FETCH_HEAD >/dev/null 2>&1; then
      if [ "$(git rev-parse HEAD)" = "$(git rev-parse FETCH_HEAD)" ]; then
        echo "已更新到最新版 $(git rev-parse --short HEAD)"
        exec bash "$SELF" --updated
      fi
      echo "当前代码有本地提交，继续使用当前版本"
    else
      echo "跳过更新（与本地代码冲突，已保留本地文件）"
    fi
  else
    echo "跳过更新（连不上 GitHub）"
  fi
elif [ "$1" != "--updated" ]; then
  update_from_github "$@"
fi

if [ -n "$OLD_PID" ] && [ "$1" != "--updated" ] && studio_running; then
  open "$STUDIO_URL"
  return
fi

# Python 3.12 环境和依赖（requirements 有变化时自动重装）
STAMP=".venv-app/.installed"
WANT="$(cat requirements.txt requirements-ocr.txt | shasum | cut -c1-12)"
if [ ! -x .venv-app/bin/python ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  if ! command -v uv >/dev/null 2>&1; then
    say_step "第一次使用：安装 uv（约 1 分钟）"
    curl -LsSf https://astral.sh/uv/install.sh | sh || fail "安装 uv 失败，检查网络后再双击一次"
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  fi
  say_step "安装 Python 3.12 和依赖（第一次约 3–5 分钟）"
  [ -x .venv-app/bin/python ] || uv venv --python 3.12 .venv-app || fail "创建 Python 环境失败"
  uv pip install --python .venv-app/bin/python -r requirements.txt || fail "安装依赖失败，检查网络后再双击一次"
  uv pip install --python .venv-app/bin/python -r requirements-ocr.txt \
    || echo "（本地 OCR 没装上：工具照样能用，只是不做原标签回贴）"
  echo "$WANT" > "$STAMP"
fi

if [ -n "$OLD_PID" ]; then
  JOBS="$(curl -fsS --noproxy '*' --max-time 3 http://127.0.0.1:8765/api/jobs 2>/dev/null)"
  if [ -z "$JOBS" ] || printf '%s' "$JOBS" | grep -Eq '"state"[[:space:]]*:[[:space:]]*"running"'; then
    fail "旧工作台有任务运行或无法核实，暂缓重启。"
  fi
  say_step "重启本目录的工作台"
  kill "$OLD_PID" 2>/dev/null || fail "无法关闭旧工作台"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    lsof -nP -iTCP:8765 -sTCP:LISTEN -t >/dev/null 2>&1 || break
    sleep 0.5
  done
fi

say_step "启动中… 浏览器会自动打开 $STUDIO_URL"
echo "用完直接关掉这个窗口即可。"
.venv-app/bin/python app.py || fail "工具启动失败"
}
main "$@"
exit
