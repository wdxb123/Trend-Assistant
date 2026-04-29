# -*- coding: utf-8 -*-
"""
精简版：对照 trendradar/ai/client.py 里的 chat_assistant_with_tools_stream

原版干了三件事（从外到内）：
  1) 外层 for i in range(max_iters)
       → 允许「模型先说要调工具 → 你本地执行工具 → 把 tool 结果塞回 history → 再问一轮模型」
       → 若这轮模型不再发起工具调用，就 break。
  2) 内层 stream = completion(..., stream=True); for chunk in stream
       → 从每个 chunk 里抠 delta.content，立刻 yield {"type":"delta","content":...}
       → 同时把 delta 里的 tool_calls 片段按 index 拼完整（流式时 arguments 可能多个 chunk 才来齐）。
  3) 若本轮拼出了工具调用：yield tool_start / tool_done，再往 history 里追加 assistant+tool 消息，continue 外层下一轮。
     若本轮没有工具：把 accumulated_content 当作答案，最后 yield done。

本文件用「假 chunk」跑通 ② + ③ 的形状，不连真实 API，双击心智模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Generator, Iterator, List, Optional


# ---------- 假装 LiteLLM 返回的 chunk（属性路径与 client.py 里 getattr 一致） ----------


@dataclass
class _FakeDelta:
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


@dataclass
class _FakeChoice:
    delta: _FakeDelta


@dataclass
class _FakeChunk:
    choices: List[_FakeChoice] = field(default_factory=list)


def _fake_tool_delta_piece(index: int, name: str, args_fragment: str) -> _FakeChunk:
    """模仿流式里某一帧只带了 tool call 的一小段（简化：单个工具）。"""
    tc = type(
        "TC",
        (),
        {
            "index": index,
            "id": "call_1",
            "function": type("FN", (), {"name": name, "arguments": args_fragment})(),
        },
    )()
    return _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content=None, tool_calls=[tc]))])


# ---------- 教学版：只做「内层」——把 completion 流 yield 成 delta 事件 ----------


def inner_loop_yield_deltas(chunks: Iterator[_FakeChunk]) -> Generator[Dict[str, Any], None, None]:
    """对应 client.py 里 stream + for chunk in stream 的主体（累积 tool_acc，但不执行工具）。"""
    accumulated_content = ""
    tool_acc: Dict[int, Dict[str, str]] = {}

    for chunk in chunks:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue

        content_piece = getattr(delta, "content", None)
        if content_piece:
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


def demo_pure_text_stream() -> None:
    """只有文本增量，没有工具——看一眼 delta 长什么样。"""
    chunks = [
        _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="你"))]),
        _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="好"))]),
        _FakeChunk(choices=[_FakeChoice(delta=_FakeDelta(content="。"))]),
    ]

    acc = ""
    for ev in inner_loop_yield_deltas(iter(chunks)):
        if ev["type"] == "delta":
            acc += ev["content"]
            print("事件:", ev)
    print("拼完的文本:", acc)


def demo_tool_arguments_split_across_chunks() -> None:
    """工具参数跨多个 chunk 到达——对照 client.py 里 slot['arguments'] += ..."""
    chunks = [
        _fake_tool_delta_piece(0, "demo_tool", '{"q":'),
        _fake_tool_delta_piece(0, "demo_tool", '"hello"}'),
    ]
    tool_acc: Dict[int, Dict[str, str]] = {}
    for chunk in chunks:
        choice = chunk.choices[0]
        delta = choice.delta
        tc_pieces = delta.tool_calls or []
        for tc in tc_pieces:
            idx = getattr(tc, "index", 0) or 0
            slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            fn = getattr(tc, "function", None)
            if fn is not None and getattr(fn, "arguments", None):
                slot["arguments"] += fn.arguments
    print("拼完的 JSON 字符串:", tool_acc[0]["arguments"])


def print_outer_loop_cheat_sheet() -> None:
    """外层循环一句话对照（没有代码执行，只看逻辑）。"""
    print(
        """
外层（示意）:

    history = list(messages)
    for round in range(max_iters):
        accumulated_content = ""
        tool_acc = {}

        # === 内层：completion(..., stream=True)，yield delta，累积 tool_acc ===

        if not tool_acc:
            # 模型这轮没在「发起工具调用」，accumulated_content 就是最终回复 → break
            break

        # 否则：把 assistant(tool_calls) + 每条 tool 结果写入 history，再来一轮

    yield {"type": "done", "answer": ...}
"""
    )


if __name__ == "__main__":
    print("=== 1) 纯文本流 delta ===")
    demo_pure_text_stream()
    print("\n=== 2) 工具 arguments 分片拼接 ===")
    demo_tool_arguments_split_across_chunks()
    print("\n=== 3) 外层循环备忘 ===")
    print_outer_loop_cheat_sheet()
