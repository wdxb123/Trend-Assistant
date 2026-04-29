# TrendRadar

## 原作来源 / Acknowledgements

本项目基于开源项目 **[TrendRadar](https://github.com/sansan0/TrendRadar)**。

- **原作者 / Original author：** [@sansan0](https://github.com/sansan0)
- **上游仓库 / Upstream：** <https://github.com/sansan0/TrendRadar>

完整文档、部署方式、版本说明等请以 **上游仓库 README** 为准。

## 本仓库新增：本地 AI 助理

在保留上游「热点采集与推送」等能力的基础上，本 fork 增加了可在 **本机浏览器** 使用的 **对话式 AI 助理**：

| 能力 | 说明 |
|------|------|
| **本地 Web 界面** | 页面路径 **`/assistant`**（由 `trendradar/assistant/web.py` 提供 HTTP 服务）。 |
| **流式对话** | `POST /api/assistant/ask_stream`，NDJSON 行协议推送增量文本。 |
| **工具与新闻联动** | LiteLLM `tools` + 自定义调度，与 TrendRadar 候选新闻等联动（见 `trendradar/assistant/news_tools.py`）。 |
| **路由与提示词** | `trendradar/assistant/router.py`；配置默认见 **`config/assistant_router.yaml`**（可用环境变量 `ASSISTANT_ROUTER_CONFIG` 覆盖路径）。 |
| **长记忆（JSON 文件）** | `trendradar/assistant/memory.py`，按 `user_id` 分文件存储。 |
| **报告页内嵌** | HTML 报告可从页面打开「AI 助理」弹层（与 `http://127.0.0.1:8765/assistant` 同源或跨页嵌入，见 `trendradar/report/html.py`）。 |

常规运行主程序时，会在后台尝试自动启动本机 **8765** 端口的助理服务（失败则仅提示，不影响主流程）；也可 **单独启动**：

```bash
python -m trendradar --assistant-web
```

启动后一般在浏览器打开 **`http://127.0.0.1:8765/assistant`**（以终端输出为准）。可选参数：`--assistant-web-host`、`--assistant-web-port`（默认端口 `8765`）、`--assistant-web-no-open`（不自动打开浏览器）。

## 许可证

继承上游 **[GPL-3.0](LICENSE)**。使用、分发或修改时请遵守 GPLv3，并保留对上游作者与许可证的合规说明。
