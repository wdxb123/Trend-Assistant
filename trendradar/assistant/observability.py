# coding=utf-8
"""
助理 Observability:对话调用日志 + 简单指标聚合。

设计:
- 每次完成的对话写一条 JSON Lines（一天一个文件）
- append-only,多线程安全(threading.Lock)
- 多进程也能读取(主进程做推送时可读取最近调用)
- 不入 SQLite,因为日志是冷数据,grep/jq 即可分析

面试可讲点:
- 为什么 JSONL:append 廉价、人工可读、不锁全表
- 计时拆解:first_byte_ms / llm_ms / tools_ms 分别衡量"模型反应快不快""工具瓶颈在哪"
- atomic write 不需要(append-only),但单条写入用单次 write+flush 保证原子性
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def _today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class LogStore:
    """
    对话日志存储。

    使用方式:
        store = LogStore(base_dir="logs")
        store.record({...})            # 写一条
        store.list_logs(date, limit)   # 读取某天日志,新到旧
        store.aggregate_stats(date)    # 聚合指标
    """

    PREVIEW_LEN = 200

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, date: Optional[str] = None) -> Path:
        return self.base_dir / f"{date or _today_iso()}.jsonl"

    # ---- 写 ----

    def record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        写入一条日志。自动补 id / ts。
        返回写入后的完整 payload(含 id/ts)。
        """
        entry = {
            "id": payload.get("id") or "log-" + uuid.uuid4().hex[:10],
            "ts": payload.get("ts") or _now_iso(),
            **{k: v for k, v in payload.items() if k not in ("id", "ts")},
        }
        # 截断 answer
        if isinstance(entry.get("answer"), str):
            ans = entry.pop("answer")
            entry["answer_preview"] = ans if len(ans) <= self.PREVIEW_LEN else ans[: self.PREVIEW_LEN] + "..."
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        path = self._path()
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
        return entry

    # ---- 读 ----

    def list_logs(
        self,
        date: Optional[str] = None,
        limit: int = 50,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """读取指定日期的日志。返回最新 limit 条(按写入顺序倒序)。"""
        path = self._path(date)
        if not path.exists():
            return []
        items: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if user_id and entry.get("user_id") != user_id:
                        continue
                    items.append(entry)
        except Exception:
            return []
        items.reverse()
        return items[: max(0, int(limit))]

    def aggregate_stats(
        self,
        date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        聚合某天的指标。

        返回:
            total_calls / error_calls
            avg_latency_ms / p50 / p95
            avg_first_byte_ms
            tool_call_count(总调用) / tool_call_distribution(各工具次数)
            tokens_total(若有,litellm 不一定每次都返回)
            iterations_distribution
        """
        items = self.list_logs(date=date, limit=10**6, user_id=user_id)  # 取全部
        total = len(items)
        if total == 0:
            return {"total_calls": 0}
        errors = sum(1 for x in items if x.get("error"))
        latencies = [x.get("timings", {}).get("total_ms") for x in items]
        latencies = sorted(int(v) for v in latencies if isinstance(v, (int, float)))
        first_bytes = [x.get("timings", {}).get("first_byte_ms") for x in items]
        first_bytes = [int(v) for v in first_bytes if isinstance(v, (int, float))]
        tool_calls_total = 0
        tool_dist: Dict[str, int] = {}
        iter_dist: Dict[int, int] = {}
        token_sum = 0
        for x in items:
            tcs = x.get("tool_calls") or []
            tool_calls_total += len(tcs)
            for tc in tcs:
                name = tc.get("name", "?")
                tool_dist[name] = tool_dist.get(name, 0) + 1
            iters = int(x.get("iterations") or 0)
            iter_dist[iters] = iter_dist.get(iters, 0) + 1
            tokens = x.get("tokens") or {}
            if isinstance(tokens.get("total"), (int, float)):
                token_sum += int(tokens["total"])

        def _p(arr, q):
            if not arr:
                return None
            idx = max(0, min(len(arr) - 1, int(round((q / 100) * (len(arr) - 1)))))
            return arr[idx]

        return {
            "total_calls": total,
            "error_calls": errors,
            "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
            "p50_latency_ms": _p(latencies, 50),
            "p95_latency_ms": _p(latencies, 95),
            "avg_first_byte_ms": int(sum(first_bytes) / len(first_bytes)) if first_bytes else None,
            "tool_calls_total": tool_calls_total,
            "tool_call_distribution": tool_dist,
            "iterations_distribution": iter_dist,
            "tokens_total": token_sum or None,
        }
