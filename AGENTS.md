# Codex 接手指南

这是在本机运行的 Temu 美区商品套图工作台，不是托管的在线服务。先读 `README.md`、`CLAUDE.md`、`docs/DEV_REQUIREMENTS.md` 和 `更新说明.md`。

- 正式启动：`python app.py --no-browser`（默认只监听 `127.0.0.1:8765`）。首页 `/`，工作台 `/library`，再到 `/plan` → `/sku/<SKU>` → `/review` → `/export`；设置和账本在内页左栏。
- 首页的 HTML 在 `web/landing.html`；`web/static/` 提供样式、静态配图与 10 个演示视频。13 个卡片复用部分视频。桌面悬停只播当前卡片，移开停止；减少动态效果时不播。工作台使用 `web/static/studio.css`，不要加首页动效。
- 仓库里的视频是用于展示 UI 的 AI 示例，不是商品数据。保留原导出的生成标识及内容凭证；本机可在工作区 `landing-media/` 放个性化替换。不要提交实际产品、生成工作区、设置、钥匙串、Token、聊天或原始设计过程。
- 测试默认使用临时 workspace 和 `demo_mode=True`，不调真实 API：`python -m pytest -q tests`。CI 或核验使用本机入口和同端口导航。
- 发布仅把代码和示例素材送入仓库。遵守现有主分支发布流程，用户的 `workspace/` 和 `config.yaml` 必须留在本机。
