# coding=utf-8
"""
助理新闻工具：定义供 LLM 调用的工具 schema 和执行 dispatcher。

设计：
- LLM 通过 get_news_categories 发现当前可用类别
- LLM 通过 get_news_by_category 获取指定类别下的新闻标题和链接
- 决定"要不要注入新闻 / 注入哪些类别"完全由 LLM 自主判断
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional


# OpenAI / LiteLLM 兼容的 tools schema
NEWS_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_news_categories",
            "description": (
                "列出当前可用的所有新闻分类名称。\n"
                "使用规则：\n"
                "- 调用 get_news_by_category 之前必须先调用本工具拿到准确的类别名（本次会话已调用过则可跳过）\n"
                "- 这一步是为了避免在 get_news_by_category 中传入不存在的类别名"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news_by_category",
            "description": (
                "根据指定类别获取该类别下的近期新闻标题和链接。\n"
                "需要调用的情况：\n"
                "- 用户问题涉及近期事件、最新动态、行业进展、热点、行情、市场\n"
                "- 用户问题中含有'最近/今天/最新/现在/近期'等时效性表达\n"
                "- 用户隐式询问某领域当前情况（如'OpenAI 又出了什么'）\n"
                "不要调用的情况：\n"
                "- 纯概念解释、教程、原理（如'什么是 transformer'）\n"
                "- 历史性、已固化的知识\n"
                "- 闲聊、情感支持、代码、数学计算\n"
                "重要：\n"
                "- categories 必须使用 get_news_categories 返回的精确类别名，不能自己编造\n"
                "- 用户问题跨多个领域时（如'AI 投资'同时涉及 ai 和投资），一次性传入所有相关类别，"
                "不要分多次调用\n"
                "- 引用新闻时必须包含标题、来源平台、时间三要素，不要输出 URL 链接"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要获取的新闻类别名称数组，可多选；必须来自 get_news_categories 的返回结果。",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "每个类别返回的最大新闻条数，默认 10，上限 20。",
                        "default": 10,
                    },
                },
                "required": ["categories"],
            },
        },
    },
]


class NewsToolDispatcher:
    """
    把 LLM 的工具调用映射为对本地新闻库的真实查询。

    通过 candidates_loader 回调获取候选新闻，使本类不直接耦合 storage。
    上层可以在 loader 里实现缓存策略（进程级缓存、TTL、按文件 mtime 失效等）。

    使用方式：
        dispatcher = NewsToolDispatcher(candidates_loader=load_fn)
        result = ai_client.chat_assistant_with_tools(
            messages=[...],
            tools=NEWS_TOOLS,
            tool_dispatcher=dispatcher.dispatch,
        )
        # 调用日志在 dispatcher.call_log
    """

    MAX_PER_CATEGORY = 20

    def __init__(
        self,
        candidates_loader: Callable[[], List[Dict[str, Any]]],
        default_limit: int = 10,
    ):
        self._loader = candidates_loader
        self.default_limit = default_limit
        self._candidates_cache: Optional[List[Dict[str, Any]]] = None
        self.call_log: List[Dict[str, Any]] = []

    def dispatch(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        if tool_name == "get_news_categories":
            return self._handle_get_categories()
        if tool_name == "get_news_by_category":
            return self._handle_get_news_by_category(arguments or {})
        return json.dumps(
            {"error": f"unknown tool: {tool_name}"},
            ensure_ascii=False,
        )

    # ---- 内部实现 ----

    def _load_candidates(self) -> List[Dict[str, Any]]:
        if self._candidates_cache is not None:
            return self._candidates_cache
        try:
            self._candidates_cache = self._loader() or []
        except Exception as exc:
            self.call_log.append({
                "tool": "_load_candidates",
                "error": f"{type(exc).__name__}: {exc}",
            })
            self._candidates_cache = []
        return self._candidates_cache

    def _handle_get_categories(self) -> str:
        candidates = self._load_candidates()
        categories = sorted({
            str(item.get("tag", "")).strip()
            for item in candidates
            if str(item.get("tag", "")).strip()
        })
        self.call_log.append({
            "tool": "get_news_categories",
            "result_count": len(categories),
        })
        return json.dumps(
            {"categories": categories, "total": len(categories)},
            ensure_ascii=False,
        )

    def _handle_get_news_by_category(self, args: Dict[str, Any]) -> str:
        raw_categories = args.get("categories") or []
        categories = [
            str(c).strip() for c in raw_categories if str(c).strip()
        ]
        if not categories:
            self.call_log.append({
                "tool": "get_news_by_category",
                "error": "categories empty",
            })
            return json.dumps(
                {"error": "categories 不能为空"},
                ensure_ascii=False,
            )

        try:
            limit = int(args.get("limit") or self.default_limit)
        except (TypeError, ValueError):
            limit = self.default_limit
        limit = max(1, min(limit, self.MAX_PER_CATEGORY))

        candidates = self._load_candidates()
        wanted = set(categories)
        grouped: Dict[str, List[Dict[str, Any]]] = {c: [] for c in categories}

        for item in candidates:
            tag = str(item.get("tag", "")).strip()
            if tag not in wanted:
                continue
            if len(grouped[tag]) >= limit:
                continue
            grouped[tag].append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "source": item.get("source_name", ""),
                "time": item.get("last_time") or item.get("first_time", ""),
            })

        total = sum(len(v) for v in grouped.values())
        self.call_log.append({
            "tool": "get_news_by_category",
            "categories": categories,
            "limit": limit,
            "result_count": total,
        })
        return json.dumps(
            {"news_by_category": grouped, "total": total},
            ensure_ascii=False,
        )
