# temu-imggen

Temu 美区美妆主图批量生成工具（私有）。需求见 `docs/DEV_REQUIREMENTS.md`，进度见 `docs/CHANGELOG.md`。

产品原图、生成结果、API key 都只留在本机，不进仓库（`workspace/` 整个目录被忽略）。

## 第一次安装（Mac）

在"终端"里逐行运行：

```bash
cd ~/珂学家AI工具箱/电商智能批量出图
git clone https://github.com/zhaok7389-eng/temu-imggen
cd temu-imggen
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q tests        # 看到 "2 passed" 说明环境没问题
```

以后每次更新：

```bash
cd ~/珂学家AI工具箱/电商智能批量出图/temu-imggen
git pull
source .venv/bin/activate
pip install -r requirements.txt  # 依赖有变化时才需要，多跑一次也没关系
```

## 现在能试的：原标签回贴（不需要 API key，不花钱）

用 11/11 GPT 测试出的 26 张图，把产品原图上的标签贴回去：

```bash
python scripts/try_label_paste.py \
  ../测试结果_20261111 \
  ../产品图片 \
  workspace/paste-demo \
  --src A="1.姜黄精华液.jpg" \
  --src B="ZYZT面霜的文件名" \
  --src C="INKEEY胶原面霜的文件名" \
  --src D="INKEEY淡斑面霜的文件名" \
  --src E="白金色面膜的文件名"

python app.py --results workspace/paste-demo
```

- 第 1 个参数：GPT 测试图所在文件夹（文件名形如 `T1_A_25flare_med_1.png`）。
- 第 2 个参数：产品原图文件夹；`--src` 写每个产品原图的文件名（A=姜黄精华，B=ZYZT，C=INKEEY 胶原，D=INKEEY 淡斑，E=白金色面膜）。
- 浏览器会打开 `http://127.0.0.1:8765`：每行依次是原图、AI 生成图、贴回后。红边的是自动判为"贴不了"的（比如盒子被模型画丢了），这类图会重新生成，不会硬贴。

## API key

key 只放在环境变量里，不写进任何文件：

```bash
echo 'export OPENAI_API_KEY=你的key' >> ~/.zshrc
# 重开终端后检查（只显示有没有）：
python3 -c "import os;print('KEY_SET' if os.getenv('OPENAI_API_KEY') else 'KEY_MISSING')"
```

## 目录

```text
app.py                     本地网页工具入口
config.example.yaml        配置样例（复制成 config.yaml 再改；config.yaml 不进仓库）
imggen/repair/label_paste.py   原标签回贴
imggen/qa/label_text.py        标签文字识别与比对
scripts/                   试验脚本
tests/                     自动测试
docs/                      需求与进度
workspace/                 本机数据（不进仓库）
```
