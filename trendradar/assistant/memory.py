# coding=utf-8
"""
助理长记忆：用户事实卡（Facts）。

设计：
- 每个 user_id 一个 JSON 文件，存储该用户的长期事实
- 写入用 atomic write（临时文件 + rename），多进程读取安全
- 不引入账号系统，user_id 就是前端传入的字符串

文件格式：
    {
      "user_id": "default",
      "facts": [
        {"id": "f-xxx", "content": "...", "created_at": "...", "source": "manual|auto"}
      ],
      "updated_at": "2026-04-29T12:34:56"
    }
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# user_id 中的不安全字符替换为 _，防止路径穿越
_SAFE_USER_ID_RE = re.compile(r"[^\w\-.]")
_DEFAULT_USER_ID = "default"


def _safe_user_id(user_id: Optional[str]) -> str:
    uid = (user_id or "").strip() or _DEFAULT_USER_ID
    return _SAFE_USER_ID_RE.sub("_", uid)[:64]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class MemoryStore:
    """
    用户事实卡的 JSON 存储。线程安全。

    使用方式：
        store = MemoryStore(base_dir="memory")
        facts = store.list_facts("default")
        store.add_fact("default", "用户偏好简洁回答", source="manual")
        store.delete_fact("default", "f-xxx")
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- 路径 ----

    def _path(self, user_id: str) -> Path:
        return self.base_dir / f"{_safe_user_id(user_id)}.json"

    # ---- 读 ----

    def load(self, user_id: str) -> Dict[str, Any]:
        path = self._path(user_id)
        if not path.exists():
            return self._empty(user_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            return self._empty(user_id)
        # 兼容/校验
        data.setdefault("user_id", _safe_user_id(user_id))
        data.setdefault("facts", [])
        data.setdefault("updated_at", "")
        if not isinstance(data["facts"], list):
            data["facts"] = []
        return data

    def list_facts(
        self,
        user_id: str,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        列出 facts。status=None 返回全部；指定 status 时按状态过滤。
        旧数据无 status 字段时,默认按 'accepted' 处理(向前兼容)。
        """
        facts = self.load(user_id).get("facts", [])
        if status is None:
            return facts
        return [f for f in facts if f.get("status", "accepted") == status]

    # ---- 写 ----

    def add_fact(
        self,
        user_id: str,
        content: str,
        source: str = "manual",
        status: str = "accepted",
    ) -> Optional[Dict[str, Any]]:
        content = (content or "").strip()
        if not content:
            return None
        if status not in ("accepted", "pending"):
            status = "accepted"
        with self._lock:
            data = self.load(user_id)
            fact = {
                "id": "f-" + uuid.uuid4().hex[:10],
                "content": content,
                "created_at": _now_iso(),
                "source": source if source in ("manual", "auto") else "manual",
                "status": status,
            }
            data["facts"].append(fact)
            data["updated_at"] = _now_iso()
            self._atomic_save(user_id, data)
        return fact

    def update_fact_status(
        self,
        user_id: str,
        fact_id: str,
        status: str,
    ) -> bool:
        if not fact_id or status not in ("accepted", "pending"):
            return False
        with self._lock:
            data = self.load(user_id)
            changed = False
            for f in data["facts"]:
                if f.get("id") == fact_id:
                    if f.get("status") != status:
                        f["status"] = status
                        changed = True
                    break
            if not changed:
                return False
            data["updated_at"] = _now_iso()
            self._atomic_save(user_id, data)
        return True

    def delete_fact(self, user_id: str, fact_id: str) -> bool:
        if not fact_id:
            return False
        with self._lock:
            data = self.load(user_id)
            before = len(data["facts"])
            data["facts"] = [f for f in data["facts"] if f.get("id") != fact_id]
            if len(data["facts"]) == before:
                return False
            data["updated_at"] = _now_iso()
            self._atomic_save(user_id, data)
        return True

    def fact_contents(self, user_id: str) -> List[str]:
        """返回所有 fact 的 content 列表，用于抽取去重时让 LLM 知道已有事实。"""
        return [
            str(f.get("content", "")).strip()
            for f in self.load(user_id).get("facts", [])
            if str(f.get("content", "")).strip()
        ]

    # ---- 内部 ----

    def _empty(self, user_id: str) -> Dict[str, Any]:
        return {
            "user_id": _safe_user_id(user_id),
            "facts": [],
            "updated_at": "",
        }

    def _atomic_save(self, user_id: str, data: Dict[str, Any]) -> None:
        path = self._path(user_id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Path.replace 在 Windows 和 POSIX 上都是原子操作
        tmp.replace(path)


FACT_EXTRACT_PROMPT = """你是用户档案抽取器。从下面对话里识别**值得长期记住**的用户事实。

值得记的:
- 用户的身份、职业、技能、经验
- 用户当前在做的事(项目、求职、学习等)
- 用户的偏好(沟通风格、关注领域、不喜欢什么)
- 用户提到的人、公司、工具、时间节点

不要记:
- 一次性问题(如"什么是 X")
- 当下的具体内容(如"刚才那条新闻"、"今天的市场")
- AI 说过的内容(只关注用户的输入)
- 已经在【已有事实】列表里的内容(避免重复)

输出严格 JSON,不要任何额外文本:
{"new_facts": ["事实1", "事实2"], "skipped_reason": "若 new_facts 为空时说明原因,否则空字符串"}

每条 fact 用一句话表达,简洁、客观、不夹杂猜测。
"""


def build_extract_messages(
    existing_facts: List[str],
    dialogue: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """构造抽取调用的 messages 列表(给 ai_client.chat 用)。"""
    facts_text = (
        "\n".join(f"- {c}" for c in existing_facts) if existing_facts else "(暂无)"
    )
    dialogue_text = "\n".join(
        f"{m.get('role', '?')}: {m.get('content', '')}" for m in dialogue
    )
    user_content = (
        f"【已有事实】\n{facts_text}\n\n"
        f"【本次对话】\n{dialogue_text}\n\n"
        "请严格返回 JSON。"
    )
    return [
        {"role": "system", "content": FACT_EXTRACT_PROMPT},
        {"role": "user", "content": user_content},
    ]


def parse_extract_response(text: str) -> Dict[str, Any]:
    """解析 LLM 抽取返回。期望 JSON: {new_facts, skipped_reason}。"""
    cleaned = (text or "").strip()
    # 去掉可能的代码块包裹(支持 ```json ... ``` 和 ``` ... ```)
    if "```" in cleaned:
        for part in cleaned.split("```"):
            part = part.strip()
            # 去掉可能的语言标识前缀(如 "json\n")
            if "\n" in part and part.split("\n", 1)[0].strip().lower() in ("json", "javascript"):
                part = part.split("\n", 1)[1].strip()
            if part.startswith("{") and part.endswith("}"):
                cleaned = part
                break
    try:
        data = json.loads(cleaned)
    except Exception:
        return {"new_facts": [], "skipped_reason": "parse_error"}
    raw_facts = data.get("new_facts") or []
    new_facts = [
        str(c).strip()
        for c in raw_facts
        if isinstance(c, str) and str(c).strip()
    ]
    return {
        "new_facts": new_facts,
        "skipped_reason": str(data.get("skipped_reason", "")).strip(),
    }


def format_facts_for_prompt(facts: List[Dict[str, Any]]) -> str:
    """
    把 facts 列表格式化成 system prompt 片段，注入到角色 prompt 之前。
    facts 为空时返回空字符串（不注入）。
    """
    if not facts:
        return ""
    lines = ["[已知用户信息（长期记忆）]"]
    for f in facts:
        content = str(f.get("content", "")).strip()
        if content:
            lines.append(f"- {content}")
    return "\n".join(lines)
