# Codex 接手指南（2026-11-24 更新）

这是在用户 Mac 本机运行的 Temu 美区商品套图批量生产工作台，不是托管的在线服务。**以 GitHub `main` 为唯一基线**，不参考任何以前未提交的本地版本。

## 你这次的任务：只做前端界面

**先完整读 `docs/决策记录与设计需求.md`**（前因后果、现行决定、明确排除、体验流程、后端接口、交付物和验收清单都在里面），再读 `CLAUDE.md` 和 `更新说明.md` 最上面两条。

- 你负责：界面概念图（一套方案）→ 用户确认 → 实现新的单页工作台 `/studio`（`web/studio/`）。
- 不要改：`imggen/` 下的生成、提示词、`templates/slots.yaml`、检查、调度、费用和归档逻辑；`app.py` 里已有接口的行为。需要新接口或接口改动，写进你的交付说明，由 Claude 加。
- 不要调用真实 OpenAI API。测试和本机演示都用 demo 模式：`python -m pytest -q tests`，或在 `config.yaml` 里设 `demo_mode: true` 后 `python app.py --no-browser`（只监听 `127.0.0.1:8765`）。
- 原展示首页在 `/showcase`（`web/landing.html` 和首页动效素材），不要再改它，也不要把它的动效带进工作台。
- 不要提交实际产品图、`workspace/`、`config.yaml`、钥匙串、Token、聊天记录。

## 发布

推到 `main` = 发布给用户（用户双击 `启动主图工具.command` 会自动更新到 `main`）。推送前跑 `python -m pytest -q tests`，并在 `更新说明.md` 最上面写一条给用户看的说明（格式见 `CLAUDE.md`）。概念图阶段的文件放 `docs/ui-concept/`。
