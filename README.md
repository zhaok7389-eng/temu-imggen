# temu-imggen

Temu 美区美妆主图批量生成工具（私有）。需求见 `docs/DEV_REQUIREMENTS.md`，进度见 `docs/CHANGELOG.md`。

产品原图、生成结果、API key 都只留在本机，不进仓库（`workspace/` 整个目录被忽略）。

## 第一次安装（Mac）

在"终端"里逐行运行：

```bash
cd ~/珂学家AI工具箱/电商智能批量出图/temu-imggen
git pull
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q tests        # 看到 "6 passed" 说明环境没问题（约 1 分钟）
```

**本地 OCR（推荐装）**：原标签回贴和大字检查要用它。它只支持 Python 3.12 及以下：

```bash
pip install -r requirements-ocr.txt
```

装不上（Mac 默认是 Python 3.14）就用 3.12 重建环境：`brew install python@3.12`，然后把上面的
`python3 -m venv .venv` 换成 `python3.12 -m venv .venv` 重新来一遍。不装也能用，只是不做回贴，
图上的包装小字就是模型画的。

## API key

key 只放在环境变量里，不写进任何文件：

```bash
echo 'export OPENAI_API_KEY=你的key' >> ~/.zshrc
```

重开终端后检查（只显示有没有）：

```bash
python3 -c "import os;print('KEY_SET' if os.getenv('OPENAI_API_KEY') else 'KEY_MISSING')"
```

## 每天使用

```bash
cd ~/珂学家AI工具箱/电商智能批量出图/temu-imggen
source .venv/bin/activate
python app.py
```

浏览器会自动打开 `http://127.0.0.1:8765`。用完在终端按 `Ctrl+C` 关掉。

**第一次可以先开演示模式**（设置 → 勾选「演示模式」）：不调用 OpenAI、不花钱，出占位图，把流程点一遍。
熟悉后关掉演示模式就是真实出图。

### 流程

1. **产品库 → 导入**：上传一个 SKU 的图片，或填一个文件夹路径（每个子文件夹 = 一个 SKU）。
2. **识别标签**（约 $0.01）：模型逐行抄写包装文字（含原有错字），本地 OCR 做第二路核对。
3. **确认标签**：逐行对照原图，黄色行重点看。包装上本来的错字（如 `Replenis h`）**原样保留**。
   勾选哪些说法可以当卖点；图上新加的英文只能来自这里、品名、容量、件数、用法。
   勾「大字」的行会在每张图上自动核对。
4. **做计划**：系统定风格锁和 8 个图位的文案，自动校验（来源、禁用词、数字、长度）。可以直接改。
   页面显示预计费用（一套约 10 次调用 ≈ $0.26）。
5. **开始出图**：8 个图位并发生成，每张 30–60 秒。完成后自动把原标签贴回去并做检查。
   中途关掉也没关系，再点「补齐没出的图位」只补缺的，不重复花钱。
6. **审核墙**：点图看大图（原图 / 生成图 / 回贴后并排）。选中一张按 **A** 通过、**R** 重出（可以写一句哪里不行）、
   **L** 在回贴版和生成原图之间切换。黄框 = 有提示（只提示，你说了算）。
7. **导出**：勾选套图 → 导出 zip：`{sku}_{变体}_{图位两位数}.jpg`、ERP 导入表 `erp_import.xlsx`、联系表。

同一个 SKU 要铺多个店铺：在 SKU 页点「新增变体」，会换一套风格。

### 费用与预算

- 设置页的「每日预算」默认 $10，到了就停止新调用。
- 「账本」页逐条记录每次调用的 tokens、费用、耗时（tokens 取 API 返回值）。
- 图位模板和禁用词在 `imggen/templates/`；要改的话复制到 `workspace/templates/` 再改，程序优先读那份。

## 目录

```text
app.py                         本地网页工具（python app.py）
config.example.yaml            配置样例（设置页保存后生成 config.yaml，不进仓库）
imggen/pipeline.py             流程：识别标签 → 计划 → 出图 → 检查
imggen/llm.py                  文本/视觉模型调用（识别标签、做计划）
imggen/plan.py                 套图计划、文案校验、提示词
imggen/gen/                    出图调用、重试、预算、账本
imggen/repair/label_paste.py   原标签回贴
imggen/repair/paste_guard.py   回贴守门：贴完不能比不贴差
imggen/qa/label_text.py        本地 OCR 与文字比对
imggen/export.py               导出 jpg / xlsx / zip
imggen/fake.py                 演示模式（假的 OpenAI）
imggen/templates/              图位模板、禁用词
scripts/try_label_paste.py     回贴离线回归（26 张测试图）
tests/                         自动测试
workspace/                     本机数据（不进仓库）
```
