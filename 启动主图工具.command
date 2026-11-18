#!/bin/bash
# 双击启动 Temu 主图工具（Mac）。
# 第一次会自动安装 Python 3.12 和依赖（几分钟）；以后每次双击几秒就打开浏览器。
# 这个窗口别关：关掉窗口 = 关掉工具。

# 整个脚本先读完再执行：运行中 git pull 更新了这个文件也不会出错
main() {
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

say_step() { printf '\n\033[1;34m==> %s\033[0m\n' "$1"; }
fail() {
  printf '\n\033[1;31m出错了：%s\033[0m\n' "$1"
  echo "把这个窗口的内容截图发给 Claude。按回车关闭。"
  read -r _
  exit 1
}

# 1. 拿最新代码（没网或有本地改动就跳过，不影响使用）
if [ -d .git ] && command -v git >/dev/null 2>&1; then
  say_step "检查更新"
  git pull --ff-only --quiet 2>/dev/null && echo "已是最新" || echo "跳过更新（没网或有本地改动）"
fi

# 2. 第一次：安装 uv（装 Python 和依赖的工具）
if ! command -v uv >/dev/null 2>&1; then
  say_step "第一次使用：安装 uv（约 1 分钟）"
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "安装 uv 失败，检查网络后再双击一次"
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# 3. Python 3.12 环境 + 依赖（requirements 有变化时自动重装）
STAMP=".venv-app/.installed"
WANT="$(cat requirements.txt requirements-ocr.txt | shasum | cut -c1-12)"
if [ ! -x .venv-app/bin/python ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  say_step "安装 Python 3.12 和依赖（第一次约 3–5 分钟）"
  [ -x .venv-app/bin/python ] || uv venv --python 3.12 .venv-app || fail "创建 Python 环境失败"
  uv pip install --python .venv-app/bin/python -r requirements.txt || fail "安装依赖失败，检查网络后再双击一次"
  uv pip install --python .venv-app/bin/python -r requirements-ocr.txt \
    || echo "（本地 OCR 没装上：工具照样能用，只是不做原标签回贴）"
  echo "$WANT" > "$STAMP"
fi

# 4. API key：环境变量 > 钥匙串 > 弹窗问一次（存进钥匙串，不写进任何文件）
if [ -z "$OPENAI_API_KEY" ]; then
  OPENAI_API_KEY="$(security find-generic-password -s temu-imggen -a OPENAI_API_KEY -w 2>/dev/null)"
fi
if [ -z "$OPENAI_API_KEY" ]; then
  KEY="$(osascript -e 'try' \
    -e 'text returned of (display dialog "粘贴 OpenAI API key（只存进本机钥匙串，以后不再问）。\n\n没有 key 就点「先不用」，可以在设置里开演示模式。" default answer "" with hidden answer buttons {"先不用", "保存"} default button "保存" with title "Temu 主图工具")' \
    -e 'on error' -e 'return ""' -e 'end try' 2>/dev/null)"
  if [ -n "$KEY" ]; then
    security add-generic-password -U -s temu-imggen -a OPENAI_API_KEY -w "$KEY" >/dev/null 2>&1 \
      && echo "key 已存进钥匙串"
    OPENAI_API_KEY="$KEY"
  fi
fi
[ -n "$OPENAI_API_KEY" ] && export OPENAI_API_KEY

# 5. 启动（浏览器自动打开）
say_step "启动中… 浏览器会自动打开 http://127.0.0.1:8765"
echo "用完直接关掉这个窗口即可。"
.venv-app/bin/python app.py || fail "工具启动失败"
}
main "$@"
exit
