# temu-imggen

Temu 美区美妆主图批量生成工具（私有）。需求见 `docs/DEV_REQUIREMENTS.md`，每次更新的内容见 `更新说明.md`。

产品原图、生成结果、API key 都只留在本机，不进仓库（`workspace/` 整个目录被忽略）。

## 怎么打开（Mac）

在访达里打开 `temu-imggen` 文件夹，**双击 `启动主图工具.command`**。

- 第一次：会自动安装需要的东西（Python 3.12、依赖、本地 OCR），大约 3–5 分钟；然后弹窗让你粘贴一次
  OpenAI API key，key 只存进 Mac 的钥匙串，以后不再问。没有 key 就点「先不用」，在设置里开演示模式先熟悉流程。
- 以后：双击后几秒，浏览器自动打开工具；每次还会自动下载最新版（见下面「自动更新」）。
- 弹出的终端窗口别关，**关掉窗口 = 关掉工具**。
- 如果双击后提示「无法打开，来自身份不明的开发者」：右键这个文件 → 打开 → 再点打开（只需一次）。
- 换 key：打开「钥匙串访问」，搜 `temu-imggen`，删掉那一条，下次双击会重新问。

出错时，终端窗口会停住并显示原因，截图发给 Claude。

### 自动更新（设置一次）

双击启动时会用一个 GitHub 令牌检查并下载最新版。只替换代码，`workspace/`（产品、出的图、账本）和设置不会动。

生成令牌（只读、只能读这一个仓库）：

1. 登录 GitHub（zhaok7389-eng 账号），打开 https://github.com/settings/personal-access-tokens/new
2. Token name 填 `temu-imggen`；Expiration 选最长的。
3. Repository access 选 **Only select repositories**，选 `temu-imggen`。
4. Permissions → Repository permissions → **Contents** 选 **Read-only**（其他都不用动）。
5. 点 Generate token，复制 `github_pat_` 开头的那串。
6. 双击启动时弹窗「自动更新」，粘贴进去点保存。以后不再问。

之前点过「先不用」、现在想设置：删掉文件夹里的 `.no-auto-update` 文件（访达里按 ⌘⇧. 显示隐藏文件），再双击启动。
令牌过期或想换：打开「钥匙串访问」搜 `temu-imggen`，删掉 GITHUB_TOKEN 那条，下次双击会重新问。

## 流程

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

## 费用与预算

- 设置页的「每日预算」默认 $10，到了就停止新调用。
- 「账本」页逐条记录每次调用的 tokens、费用、耗时（tokens 取 API 返回值）。
- 图位模板和禁用词在 `imggen/templates/`；要改的话复制到 `workspace/templates/` 再改，程序优先读那份。

## 给开发者：手动运行

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-ocr.txt
python -m pytest -q tests
python app.py
```

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
