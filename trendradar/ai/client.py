# coding=utf-8
"""
AI 客户端模块

基于 LiteLLM 的统一 AI 模型接口
支持 100+ AI 提供商（OpenAI、DeepSeek、Gemini、Claude、国内模型等）
"""

import json
import os
from typing import Any, Callable, Dict, List, Optional

from litellm import completion


class AIClient:
    """统一的 AI 客户端（基于 LiteLLM）"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 AI 客户端

        Args:
            config: AI 配置字典
                - MODEL: 模型标识（格式: provider/model_name）
                - API_KEY: API 密钥
                - API_BASE: API 基础 URL（可选）
                - TEMPERATURE: 采样温度
                - MAX_TOKENS: 最大生成 token 数
                - TIMEOUT: 请求超时时间（秒）
                - NUM_RETRIES: 重试次数（可选）
                - FALLBACK_MODELS: 备用模型列表（可选）
        """
        self.model = config.get("MODEL", "deepseek/deepseek-chat")
        self.api_key = config.get("API_KEY") or os.environ.get("AI_API_KEY", "")
        self.api_base = config.get("API_BASE", "")
        self.temperature = config.get("TEMPERATURE", 1.0)
        self.max_tokens = config.get("MAX_TOKENS", 5000)
        self.timeout = config.get("TIMEOUT", 120)
        self.num_retries = config.get("NUM_RETRIES", 2)
        self.fallback_models = config.get("FALLBACK_MODELS", [])

    def chat(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> str:
        """
        调用 AI 模型进行对话

        Args:
            messages: 消息列表，格式: [{"role": "system/user/assistant", "content": "..."}]
            **kwargs: 额外参数，会覆盖默认配置

        Returns:
            str: AI 响应内容

        Raises:
            Exception: API 调用失败时抛出异常
        """
        # 构建请求参数
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "timeout": kwargs.get("timeout", self.timeout),
            "num_retries": kwargs.get("num_retries", self.num_retries),
        }

        # 添加 API Key
        if self.api_key:
            params["api_key"] = self.api_key

        # 添加 API Base（如果配置了）
        if self.api_base:
            params["api_base"] = self.api_base

        # 添加 max_tokens（如果配置了且不为 0）
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        if max_tokens and max_tokens > 0:
            params["max_tokens"] = max_tokens

        # 添加 fallback 模型（如果配置了）
        if self.fallback_models:
            params["fallbacks"] = self.fallback_models

        # 合并其他额外参数
        for key, value in kwargs.items():
            if key not in params:
                params[key] = value

        # 调用 LiteLLM
        response = completion(**params)

        # 提取响应内容
        # 某些模型/提供商返回 list（内容块）而非 str，统一转为 str
        content = response.choices[0].message.content
        if isinstance(content, list):
            content = "\n".join(
                item.get("text", str(item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return content or ""

    def chat_assistant_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_dispatcher: Callable[[str, Dict[str, Any]], str],
        max_iters: int = 5,
    ) -> Dict[str, Any]:
        """
        专为助理场景设计的多轮工具调用对话。

        与 chat() 的区别：
        - 接收 tools schema，让模型自行决定是否调用工具
        - 自动循环执行 tool_call → 回填 tool_result → 再调用，直到模型给出最终回答
        - 工具的真实执行通过 tool_dispatcher 回调注入，本方法不感知具体业务

        Args:
            messages: 初始消息列表（包含 system / user 等）
            tools: OpenAI 兼容的 tools schema 列表
            tool_dispatcher: 回调 (tool_name, arguments_dict) -> 工具执行结果字符串
            max_iters: 最大循环轮数，防止异常下死循环

        Returns:
            {
                "answer": str,           # 最终回答文本
                "iterations": int,       # 实际经历的轮数
                "tool_calls": List[Dict],# 调用日志，便于调试和前端展示
                "stop_reason": str,      # "end_turn" | "max_iters"
            }
        """
        history: List[Dict[str, Any]] = list(messages)
        tool_call_log: List[Dict[str, Any]] = []

        base_params: Dict[str, Any] = {
            "model": self.model,
            "tools": tools,
            "temperature": 0.0,
            "timeout": self.timeout,
            "num_retries": self.num_retries,
        }
        if self.api_key:
            base_params["api_key"] = self.api_key
        if self.api_base:
            base_params["api_base"] = self.api_base
        if self.max_tokens and self.max_tokens > 0:
            base_params["max_tokens"] = self.max_tokens
        if self.fallback_models:
            base_params["fallbacks"] = self.fallback_models

        for i in range(max_iters):
            response = completion(messages=history, **base_params)
            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []

            if not tool_calls:
                content = msg.content or ""
                if isinstance(content, list):
                    content = "\n".join(
                        item.get("text", str(item)) if isinstance(item, dict) else str(item)
                        for item in content
                    )
                return {
                    "answer": content,
                    "iterations": i + 1,
                    "tool_calls": tool_call_log,
                    "stop_reason": "end_turn",
                }

            history.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                try:
                    result = tool_dispatcher(name, args)
                except Exception as exc:
                    result = json.dumps(
                        {"error": f"{type(exc).__name__}: {exc}"},
                        ensure_ascii=False,
                    )
                preview = result if len(result) <= 300 else result[:300] + "..."
                tool_call_log.append({
                    "name": name,
                    "arguments": args,
                    "result_preview": preview,
                })
                history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        return {
            "answer": "（达到最大工具调用轮数，未得到最终回答）",
            "iterations": max_iters,
            "tool_calls": tool_call_log,
            "stop_reason": "max_iters",
        }

    def chat_assistant_with_tools_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_dispatcher: Callable[[str, Dict[str, Any]], str],
        max_iters: int = 5,
    ):
        """
        chat_assistant_with_tools 的流式版本，返回 generator。

        产出事件 dict（按发生顺序）：
            {"type": "delta", "content": "..."}                    流式文本片段
            {"type": "tool_start", "name": "...", "arguments": {...}}
            {"type": "tool_done", "name": "...", "result_preview": "..."}
            {"type": "done", "answer": "完整回答", "iterations": N,
             "tool_calls": [...], "stop_reason": "end_turn"|"max_iters"}

        消费方按事件类型增量渲染 UI；tool 阶段没有 delta，但有 tool_start/tool_done
        让前端能显示"正在查询..."的过渡状态。
        """
        import time as _time

        history: List[Dict[str, Any]] = list(messages)
        tool_call_log: List[Dict[str, Any]] = []
        final_answer_pieces: List[str] = []
        # 计时:用于 Observability。total_ms 由 web 层算,这里只算 LLM 流式的细分。
        run_start = _time.perf_counter()
        first_byte_ms: Optional[float] = None
        llm_total_ms = 0.0
        tools_total_ms = 0.0

        base_params: Dict[str, Any] = {
            "model": self.model,
            "tools": tools,
            "temperature": 0.0,
            "timeout": self.timeout,
            "num_retries": self.num_retries,
            "stream": True,
        }
        if self.api_key:
            base_params["api_key"] = self.api_key
        if self.api_base:
            base_params["api_base"] = self.api_base
        if self.max_tokens and self.max_tokens > 0:
            base_params["max_tokens"] = self.max_tokens
        if self.fallback_models:
            base_params["fallbacks"] = self.fallback_models

        iterations = 0
        stop_reason = "max_iters"

        for i in range(max_iters):
            iterations = i + 1
            accumulated_content = ""
            # 按 index 累积工具调用片段（id / name 一般首块到达，arguments 分多块）
            tool_acc: Dict[int, Dict[str, str]] = {}

            llm_start = _time.perf_counter()
            stream = completion(messages=history, **base_params)
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue

                content_piece = getattr(delta, "content", None)
                if content_piece:
                    if first_byte_ms is None:
                        first_byte_ms = (_time.perf_counter() - run_start) * 1000
                    accumulated_content += content_piece
                    yield {"type": "delta", "content": content_piece}

                tc_pieces = getattr(delta, "tool_calls", None)
                if tc_pieces:
                    for tc in tc_pieces:
                        idx = getattr(tc, "index", 0) or 0
                        slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                slot["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                slot["arguments"] += fn.arguments

            llm_total_ms += (_time.perf_counter() - llm_start) * 1000

            # 没有工具调用 -> 模型已给出最终回答，结束
            if not tool_acc:
                final_answer_pieces.append(accumulated_content)
                stop_reason = "end_turn"
                break

            # 有工具调用：构造 assistant 消息，执行工具，结果回填，进入下一轮
            sorted_calls = [tool_acc[k] for k in sorted(tool_acc.keys())]
            tool_calls_for_msg = []
            for j, tc in enumerate(sorted_calls):
                tool_calls_for_msg.append({
                    "id": tc["id"] or f"call_{i}_{j}",
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"] or "{}",
                    },
                })

            history.append({
                "role": "assistant",
                "content": accumulated_content,
                "tool_calls": tool_calls_for_msg,
            })

            # 按位置一一对应执行工具（避免同名工具被错配）
            for tc, msg_tc in zip(sorted_calls, tool_calls_for_msg):
                name = tc["name"]
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except Exception:
                    args = {}

                yield {"type": "tool_start", "name": name, "arguments": args}

                tool_start_t = _time.perf_counter()
                try:
                    result = tool_dispatcher(name, args)
                except Exception as exc:
                    result = json.dumps(
                        {"error": f"{type(exc).__name__}: {exc}"},
                        ensure_ascii=False,
                    )
                tool_dur_ms = (_time.perf_counter() - tool_start_t) * 1000
                tools_total_ms += tool_dur_ms
                preview = result if len(result) <= 300 else result[:300] + "..."
                tool_call_log.append({
                    "name": name,
                    "arguments": args,
                    "result_preview": preview,
                    "duration_ms": int(tool_dur_ms),
                })

                yield {
                    "type": "tool_done",
                    "name": name,
                    "result_preview": preview,
                    "duration_ms": int(tool_dur_ms),
                }

                history.append({
                    "role": "tool",
                    "tool_call_id": msg_tc["id"],
                    "content": result,
                })

        total_ms = (_time.perf_counter() - run_start) * 1000
        yield {
            "type": "done",
            "answer": "".join(final_answer_pieces),
            "iterations": iterations,
            "tool_calls": tool_call_log,
            "stop_reason": stop_reason,
            "timings": {
                "total_ms": int(total_ms),
                "first_byte_ms": int(first_byte_ms) if first_byte_ms is not None else None,
                "llm_ms": int(llm_total_ms),
                "tools_ms": int(tools_total_ms),
            },
        }

    def validate_config(self) -> tuple[bool, str]:
        """
        验证配置是否有效

        Returns:
            tuple: (是否有效, 错误信息)
        """
        if not self.model:
            return False, "未配置 AI 模型（model）"

        if not self.api_key:
            return False, "未配置 AI API Key，请在 config.yaml 或环境变量 AI_API_KEY 中设置"

        # 验证模型格式（应该包含 provider/model）
        if "/" not in self.model:
            return False, f"模型格式错误: {self.model}，应为 'provider/model' 格式（如 'deepseek/deepseek-chat'）"

        return True, ""
