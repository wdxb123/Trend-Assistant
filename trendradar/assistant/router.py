# coding=utf-8
"""
Rule-based intent router for assistant roles.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional


DEFAULT_ROUTE_RULES = {
    "learning": ["学习", "教程", "课程", "入门", "怎么学", "如何学", "方法论", "复习", "记忆", "知识点", "面试题", "刷题"],
    "investment": ["投资", "股票", "基金", "期货", "黄金", "债券", "估值", "财报", "仓位", "止损", "收益", "风险", "市场", "宏观"],
    "cognition": ["认知", "思维", "判断", "框架", "本质", "逻辑", "趋势", "洞察", "观点", "决策"],
}

DEFAULT_SYSTEM_PROMPTS = {
    "learning": (
        "你是学习助理，帮助用户更高效地掌握知识。"
        "面对学习类问题，优先给出可执行的步骤、清晰的重点和具体练习建议。"
        "把抽象概念落到具体例子上，避免空话和套话。"
    ),
    "investment": (
        "你是投资助理。"
        "始终保持风险意识：不承诺收益、不给确定性结论、不诱导操作。"
        "回答围绕关键变量、不同情景下的影响、应对策略和风控考虑展开。"
        "信息不足时主动调用工具补充，仍不足时明确说明前提假设。"
    ),
    "cognition": (
        "你是认知助理，擅长拆解问题、识别假设和偏见、构建可验证的思考路径。"
        "根据问题复杂度灵活选择回答结构——简单问题直接答，复杂问题再分步拆解。"
        "避免'要全面''要辩证'这类空话，给出具体的角度和判断标准。"
    ),
}


# 所有助理角色共享的工具使用准则与输出规范
# 由 resolve_system_prompt 自动拼接到角色 prompt 尾部，集中维护、改一处全局生效
ASSISTANT_GUIDELINES = """
---
你可以使用以下工具获取实时新闻：
- get_news_categories：列出当前可用的新闻分类
- get_news_by_category：按分类获取新闻标题和链接

工具使用准则：
1. 用户问题涉及最新动态、行情、热点、近期事件、行业进展等带时效性的内容时，主动调用工具
2. 调用 get_news_by_category 前必须先调用 get_news_categories 拿到精确的类别名（本次会话已调过则跳过），避免拼错
3. 用户问题跨多个领域时（如"AI 投资有什么机会"同时涉及 ai 和投资），一次性传入所有相关类别，不要分多次调用
4. 概念解释、原理、教程、历史知识、闲聊、代码、计算这类问题，不要调用工具

回答输出规范：
1. 引用新闻时必须包含三要素：标题、来源平台、时间；不要输出 URL 链接。
   推荐格式：「《<标题>》— 来源：<平台> · 时间：<时间>」，可根据上下文灵活调整文案
2. 明确区分信息来源：来自新闻的部分用"根据近期新闻"等表述标注，基于通用知识的部分自然表述即可
3. 不要编造未在工具返回结果中出现的新闻；工具返回为空时直接说明"当前没有相关新闻"，再基于通用知识补充
4. 简洁优先：能用 3 句话讲清的不要写 10 句话，能用列表的不要堆大段文字
""".strip()


def route_intent(query: str, route_rules: Dict[str, list[str]] | None = None) -> Dict[str, Any]:
    rules = route_rules or DEFAULT_ROUTE_RULES
    text = (query or "").lower()
    scores = {k: 0 for k in rules}
    for intent, keywords in rules.items():
        for kw in keywords:
            if kw.lower() in text:
                scores[intent] += 1
    max_score = max(scores.values()) if scores else 0
    if max_score <= 0:
        fallback = "cognition" if "cognition" in scores else primary
        primary = fallback
        reason = f"未命中明确关键词，回退到 {primary} 助理"
    else:
        candidates = [intent for intent, score in scores.items() if score == max_score]
        if len(candidates) == 1:
            primary = candidates[0]
        else:
            # 平分时做确定性选择，优先学习，其次认知，最后投资
            priority = ["learning", "cognition", "investment"]
            primary = next((p for p in priority if p in candidates), candidates[0])
        reason = f"命中关键词数最高: {primary}={scores[primary]}（候选={candidates}）"
    return {"primary": primary, "scores": scores, "reason": reason}


def resolve_system_prompt(intent: str, system_prompts: Dict[str, str] | None = None) -> str:
    prompts = system_prompts or DEFAULT_SYSTEM_PROMPTS
    if intent in prompts:
        role_prompt = prompts[intent]
    elif "cognition" in prompts:
        role_prompt = prompts["cognition"]
    else:
        role_prompt = "你是 TrendRadar 的本地 AI 助手，请用简洁、可执行的中文回答问题。"
    return f"{role_prompt}\n\n{ASSISTANT_GUIDELINES}"


def _parse_llm_route_response(text: str, valid_intents: list[str]) -> Dict[str, Any]:
    """解析 LLM 路由返回，期望 JSON: {intent, confidence, reason}"""
    cleaned = (text or "").strip()
    if not cleaned:
        raise ValueError("llm route empty response")

    # 允许 ```json 包裹
    if "```" in cleaned:
        parts = cleaned.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("{") and p.endswith("}"):
                cleaned = p
                break

    data = json.loads(cleaned)
    intent = str(data.get("intent", "")).strip().lower()
    if intent not in valid_intents:
        raise ValueError(f"invalid intent: {intent}")
    confidence = float(data.get("confidence", 0.0))
    reason = str(data.get("reason", "")).strip()
    return {"intent": intent, "confidence": confidence, "reason": reason}


def route_intent_hybrid(
    query: str,
    route_rules: Dict[str, list[str]] | None = None,
    ai_client: Optional[Any] = None,
    llm_routing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """LLM 路由 + 规则兜底。"""
    rules = route_rules or DEFAULT_ROUTE_RULES
    fallback = route_intent(query, rules)
    fallback["source"] = "rule"

    llm_cfg = llm_routing or {}
    enabled = bool(llm_cfg.get("enabled", False))
    min_confidence = float(llm_cfg.get("min_confidence", 0.6))
    prompt = llm_cfg.get(
        "prompt",
        "你是一个意图路由器。请将用户问题路由到以下意图之一："
        "{intents}。仅返回 JSON："
        '{"intent":"...", "confidence":0-1, "reason":"..."}',
    )

    if not enabled or ai_client is None:
        return fallback

    try:
        intents = list(rules.keys())
        route_prompt = prompt.replace("{intents}", ", ".join(intents))
        messages = [
            {"role": "system", "content": route_prompt},
            {"role": "user", "content": query},
        ]
        raw = ai_client.chat(messages, temperature=0.0, max_tokens=120)
        parsed = _parse_llm_route_response(raw, intents)
        if parsed["confidence"] >= min_confidence:
            return {
                "primary": parsed["intent"],
                "scores": fallback.get("scores", {}),
                "reason": parsed["reason"] or f"LLM routing confidence={parsed['confidence']:.2f}",
                "source": "llm",
                "confidence": parsed["confidence"],
            }

        fallback["source"] = "rule_fallback"
        fallback["reason"] = (
            f"LLM置信度不足({parsed['confidence']:.2f}<{min_confidence:.2f})，"
            f"回退规则路由；LLM原因: {parsed['reason'] or 'N/A'}"
        )
        fallback["confidence"] = parsed["confidence"]
        return fallback
    except Exception as exc:
        fallback["source"] = "rule_fallback"
        fallback["reason"] = f"LLM路由失败，回退规则路由: {type(exc).__name__}: {exc}"
        return fallback
