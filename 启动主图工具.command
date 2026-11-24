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
  if [ -z "$T" ] && [ ! -f .no-auto-update ]; then
    T="$(osascript -e 'try' \
      -e 'text returned of (display dialog "自动更新：粘贴 GitHub 令牌（github_pat_ 开头，只存进本机钥匙串，以后不再问）。\n\n还没有令牌就点「先不用」，工具照常打开。" default answer "" with hidden answer buttons {"先不用", "保存"} default button "保存" with title "Temu 主图工具")' \
      -e 'on error' -e 'return ""' -e 'end try' 2>/dev/null)"
    if [ -n "$T" ]; then
      security add-generic-password -U -s temu-imggen -a GITHUB_TOKEN -w "$T" >/dev/null 2>&1
    else
      touch .no-auto-update   # 不再每次问；删掉这个文件可以重新设置
    fi
  fi
  [ -z "$T" ] && return 0
  say_step "检查更新"
  SHA="$(curl -fsS --max-time 15 -H "Authorization: Bearer $T" -H "Accept: application/vnd.github.sha" \
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
  if curl -fsSL --max-time 300 -H "Authorization: Bearer $T" -H "Accept: application/vnd.github+json" \
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

# 1. 拿最新代码（没网或失败就跳过，不影响使用）
if [ -d .git ] && command -v git >/dev/null 2>&1; then
  say_step "检查更新"
  git pull --ff-only --quiet 2>/dev/null && echo "已是最新" || echo "跳过更新（没网或有本地改动）"
elif [ "$1" != "--updated" ]; then
  update_from_github "$@"
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

# 4. 启动。API key 可在工作台设置中保存到本机钥匙串。
say_step "启动中… 浏览器会自动打开 http://127.0.0.1:8765"
echo "用完直接关掉这个窗口即可。"
.venv-app/bin/python app.py || fail "工具启动失败"
}
main "$@"
exit
