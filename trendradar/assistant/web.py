# coding=utf-8
"""
Local assistant web service.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlsplit

import trendradar as _trendradar_pkg

_PROJECT_ROOT = Path(_trendradar_pkg.__file__).resolve().parent.parent

from trendradar.ai.client import AIClient
from trendradar.assistant.memory import (
    MemoryStore,
    build_extract_messages,
    format_facts_for_prompt,
    parse_extract_response,
)
from trendradar.assistant.news_tools import NEWS_TOOLS, NewsToolDispatcher
from trendradar.assistant.observability import LogStore
from trendradar.assistant.router import (
    DEFAULT_ROUTE_RULES,
    DEFAULT_SYSTEM_PROMPTS,
    resolve_system_prompt,
    route_intent_hybrid,
)
from trendradar.storage import get_storage_manager


ASSISTANT_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>TrendRadar 助理</title>
  <style>
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; }
    body {
      font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
      background: #f7f8fa;
      display: flex;
      flex-direction: column;
    }
    .header {
      background: #fff;
      border-bottom: 1px solid #e2e8f0;
      padding: 12px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-shrink: 0;
    }
    .header .title { font-size: 16px; font-weight: 700; color: #0f172a; }
    .header .actions { display: flex; gap: 8px; align-items: center; }
    .header input {
      border: 1px solid #dbe2ea;
      border-radius: 6px;
      padding: 4px 8px;
      font-size: 12px;
      width: 110px;
      outline: none;
    }
    .header .clear-btn {
      border: 1px solid #dbe2ea;
      background: #fff;
      border-radius: 6px;
      padding: 4px 10px;
      font-size: 12px;
      cursor: pointer;
      color: #475569;
    }
    .header .clear-btn:hover { background: #f1f5f9; }
    .memory-hint {
      font-size: 12px;
      color: #64748b;
      background: #f1f5f9;
      border-radius: 6px;
      padding: 4px 8px;
    }
    .messages {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      padding: 18px;
    }
    .messages-inner {
      max-width: 860px;
      margin: 0 auto;
    }
    .empty-hint {
      color: #94a3b8;
      text-align: center;
      margin-top: 80px;
      font-size: 14px;
    }
    .msg { display: flex; margin-bottom: 14px; }
    .msg.user { justify-content: flex-end; }
    .msg.assistant { justify-content: flex-start; }
    .msg-col { max-width: 78%; display: flex; flex-direction: column; }
    .msg.user .msg-col { align-items: flex-end; }
    .bubble {
      padding: 10px 14px;
      border-radius: 14px;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.6;
      font-size: 14px;
    }
    .msg.user .bubble {
      background: #4f46e5;
      color: #fff;
      border-bottom-right-radius: 4px;
    }
    .msg.assistant .bubble {
      background: #fff;
      color: #0f172a;
      border: 1px solid #e2e8f0;
      border-bottom-left-radius: 4px;
    }
    .msg.assistant .bubble.thinking { color: #94a3b8; font-style: italic; }
    .msg.assistant .bubble.error { color: #dc2626; border-color: #fecaca; background: #fef2f2; }
    .cursor {
      display: inline-block;
      color: #4f46e5;
      font-weight: 700;
      animation: blink 1s steps(2, start) infinite;
      margin-left: 1px;
    }
    @keyframes blink { to { visibility: hidden; } }
    .thinking-dots::after {
      content: '...';
      display: inline-block;
      animation: dots 1.2s steps(4, end) infinite;
      width: 1.2em;
      text-align: left;
      overflow: hidden;
      vertical-align: bottom;
    }
    @keyframes dots {
      0%   { content: ''; }
      25%  { content: '.'; }
      50%  { content: '..'; }
      75%  { content: '...'; }
      100% { content: ''; }
    }
    .tool-status {
      font-size: 11px;
      color: #4f46e5;
      background: #eef2ff;
      border-radius: 6px;
      padding: 4px 8px;
      margin-bottom: 4px;
      align-self: flex-start;
      animation: tool-pulse 1.4s ease-in-out infinite;
    }
    @keyframes tool-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }
    .meta-toggle {
      margin-top: 6px;
      font-size: 11px;
      color: #64748b;
      cursor: pointer;
      user-select: none;
      align-self: flex-start;
    }
    .meta-toggle:hover { color: #4f46e5; }
    .meta-content {
      display: none;
      margin-top: 6px;
      background: #0f172a;
      color: #e2e8f0;
      padding: 10px;
      border-radius: 8px;
      font-size: 11px;
      white-space: pre-wrap;
      word-break: break-word;
      max-width: 100%;
      overflow-x: auto;
    }
    .meta-content.open { display: block; }
    .input-area {
      position: relative;
      z-index: 2;
      background: #fff;
      border-top: 1px solid #e2e8f0;
      padding: 12px 18px;
      flex-shrink: 0;
    }
    .input-inner {
      max-width: 860px;
      margin: 0 auto;
      display: flex;
      gap: 8px;
      align-items: flex-end;
    }
    .input-inner textarea {
      flex: 1;
      border: 1px solid #dbe2ea;
      border-radius: 10px;
      padding: 10px;
      resize: none;
      max-height: 200px;
      min-height: 44px;
      font-family: inherit;
      font-size: 14px;
      outline: none;
      line-height: 1.5;
    }
    .input-inner textarea:focus { border-color: #4f46e5; }
    .send-btn {
      border: none;
      background: #4f46e5;
      color: #fff;
      border-radius: 10px;
      padding: 0 22px;
      cursor: pointer;
      font-size: 14px;
      height: 44px;
      flex-shrink: 0;
    }
    .send-btn:disabled { background: #cbd5e1; cursor: not-allowed; }

    /* 记忆弹窗 */
    .modal-mask {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(15, 23, 42, 0.5);
      z-index: 100;
      align-items: center;
      justify-content: center;
    }
    .modal-mask.open { display: flex; }
    .modal {
      background: #fff;
      border-radius: 12px;
      width: min(560px, 90%);
      max-height: 80vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    .modal-header {
      padding: 14px 18px;
      border-bottom: 1px solid #e2e8f0;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-header .modal-title { font-weight: 700; font-size: 15px; }
    .modal-header .close-btn {
      border: none;
      background: transparent;
      font-size: 20px;
      cursor: pointer;
      color: #64748b;
      line-height: 1;
    }
    .modal-tabs {
      display: flex;
      gap: 4px;
      padding: 8px 18px 0 18px;
      border-bottom: 1px solid #e2e8f0;
    }
    .tab-btn {
      border: none;
      background: transparent;
      padding: 8px 14px;
      cursor: pointer;
      font-size: 13px;
      color: #64748b;
      border-bottom: 2px solid transparent;
      margin-bottom: -1px;
    }
    .tab-btn.active { color: #4f46e5; border-bottom-color: #4f46e5; font-weight: 600; }
    .pending-badge {
      display: inline-block;
      background: #f97316;
      color: #fff;
      border-radius: 999px;
      padding: 1px 7px;
      font-size: 11px;
      margin-left: 4px;
      min-width: 18px;
      text-align: center;
    }
    .pending-badge.zero { background: #cbd5e1; }
    .modal-body { padding: 14px 18px; overflow-y: auto; flex: 1; }
    .fact-item {
      display: flex;
      gap: 8px;
      padding: 8px 10px;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      margin-bottom: 8px;
      align-items: center;
    }
    .fact-content { flex: 1; font-size: 13px; line-height: 1.5; word-break: break-word; }
    .fact-meta { font-size: 11px; color: #94a3b8; margin-top: 2px; }
    .fact-del {
      border: 1px solid #fecaca;
      background: #fef2f2;
      color: #dc2626;
      border-radius: 6px;
      padding: 4px 10px;
      cursor: pointer;
      font-size: 12px;
      flex-shrink: 0;
    }
    .fact-accept {
      border: 1px solid #bbf7d0;
      background: #f0fdf4;
      color: #16a34a;
      border-radius: 6px;
      padding: 4px 10px;
      cursor: pointer;
      font-size: 12px;
      flex-shrink: 0;
    }
    .fact-actions { display: flex; flex-direction: column; gap: 4px; flex-shrink: 0; }
    .fact-empty { color: #94a3b8; text-align: center; padding: 20px; font-size: 13px; }
    .modal-footer {
      padding: 14px 18px;
      border-top: 1px solid #e2e8f0;
      display: flex;
      gap: 8px;
    }
    .modal-footer input {
      flex: 1;
      border: 1px solid #dbe2ea;
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 13px;
      outline: none;
    }
    .modal-footer input:focus { border-color: #4f46e5; }
    .add-btn {
      border: none;
      background: #4f46e5;
      color: #fff;
      border-radius: 8px;
      padding: 0 16px;
      cursor: pointer;
      font-size: 13px;
    }
    .add-btn:disabled { background: #cbd5e1; cursor: not-allowed; }
  </style>
</head>
<body>
  <div class="header">
    <div class="title">TrendRadar 助理</div>
    <div class="actions">
      <span id="memoryHint" class="memory-hint">短记忆: 0 轮</span>
      <input id="userId" placeholder="user_id" value="default" />
      <button class="clear-btn" onclick="openMemoryModal()" id="memoryBtn">记忆</button>
      <button class="clear-btn" onclick="openLogsModal()">日志</button>
      <button class="clear-btn" onclick="clearMessages()">清空</button>
    </div>
  </div>

  <div class="modal-mask" id="memoryModal" onclick="onMaskClick(event)">
    <div class="modal">
      <div class="modal-header">
        <div class="modal-title">长记忆 — <span id="modalUserId">default</span></div>
        <button class="close-btn" onclick="closeMemoryModal()">×</button>
      </div>
      <div class="modal-tabs">
        <button class="tab-btn active" data-tab="accepted" onclick="switchTab('accepted')">已采纳</button>
        <button class="tab-btn" data-tab="pending" onclick="switchTab('pending')">
          待审核<span class="pending-badge zero" id="pendingBadge">0</span>
        </button>
      </div>
      <div class="modal-body" id="memoryList">
        <div class="fact-empty">加载中...</div>
      </div>
      <div class="modal-footer">
        <input id="newFactInput" placeholder="输入要 AI 长期记住的信息" />
        <button class="add-btn" id="addFactBtn" onclick="addFact()">添加</button>
      </div>
    </div>
  </div>

  <div class="messages" id="messages">
    <div class="messages-inner" id="messagesInner">
      <div class="empty-hint">输入问题开始对话</div>
    </div>
  </div>

  <div class="input-area">
    <div class="input-inner">
      <textarea id="query" placeholder="输入问题，回车发送，Shift+回车换行" rows="1"></textarea>
      <button class="send-btn" id="sendBtn" onclick="askAssistant()">发送</button>
    </div>
  </div>

  <script>
    const messages = [];
    const MAX_HISTORY_TURNS = 6;
    const EXTRACT_EVERY_TURNS = 3;
    const STORAGE_PREFIX = 'trendradar_chat_';
    const STORAGE_LIMIT = 200;  // 单 user 最多保留 200 条消息（约 100 轮）
    /** 流式接口过久无结束时中止，避免发送按钮永远卡在 disabled */
    const ASK_STREAM_TIMEOUT_MS = 600000;
    let turnsSinceExtract = 0;
    let currentTab = 'accepted';
    let activeUserId = '';
    const messagesEl = document.getElementById('messagesInner');
    const messagesScroll = document.getElementById('messages');
    const queryEl = document.getElementById('query');
    const memoryHintEl = document.getElementById('memoryHint');
    const memoryBtnEl = document.getElementById('memoryBtn');
    const pendingBadgeEl = document.getElementById('pendingBadge');

    function buildHistory() {
      // 只取已完成的 user/assistant 文本对，去掉占位、错误消息和元数据
      const flat = messages
        .filter(m => !m.thinking && !m.error)
        .map(m => ({ role: m.role, content: m.content }));
      // 滑动窗口：最多 N 轮（每轮 user+assistant 共 2 条）
      const max = MAX_HISTORY_TURNS * 2;
      return flat.length > max ? flat.slice(-max) : flat;
    }

    function updateMemoryHint() {
      const flat = messages.filter(m => !m.thinking && !m.error);
      const turns = Math.floor(flat.length / 2);
      const capped = Math.min(turns, MAX_HISTORY_TURNS);
      memoryHintEl.textContent = `短记忆: ${capped} 轮${turns > MAX_HISTORY_TURNS ? ' (已截断)' : ''}`;
    }

    queryEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        askAssistant();
      }
    });
    queryEl.addEventListener('input', () => {
      queryEl.style.height = 'auto';
      queryEl.style.height = Math.min(queryEl.scrollHeight, 200) + 'px';
    });

    function escapeHtml(s) {
      return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function buildMetaSummary(meta) {
      if (!meta) return null;
      const tools = meta.tools || {};
      return {
        route: meta.route || {},
        memory: meta.memory || {},
        tool_summary: {
          iterations: tools.iterations,
          stop_reason: tools.stop_reason,
          tool_calls_count: (tools.tool_calls || []).length
        },
        tool_calls: tools.tool_calls || [],
        full_meta: meta
      };
    }

    function renderMessages() {
      if (messages.length === 0) {
        messagesEl.innerHTML = '<div class="empty-hint">输入问题开始对话</div>';
        return;
      }
      const html = messages.map((m, idx) => {
        if (m.role === 'user') {
          return `<div class="msg user"><div class="msg-col"><div class="bubble">${escapeHtml(m.content)}</div></div></div>`;
        }
        let bubbleClass = 'bubble';
        if (m.thinking) bubbleClass += ' thinking';
        if (m.error) bubbleClass += ' error';
        const metaSummary = buildMetaSummary(m.meta);
        const metaHtml = metaSummary ? `
          <div class="meta-toggle" onclick="toggleMeta(${idx})">展开元数据 ▾</div>
          <div class="meta-content" id="meta-${idx}">${escapeHtml(JSON.stringify(metaSummary, null, 2))}</div>
        ` : '';
        const cursor = m.streaming ? '<span class="cursor">▌</span>' : '';
        const toolStatus = m.toolStatus
          ? `<div class="tool-status">${escapeHtml(m.toolStatus)}</div>`
          : '';
        const inner = m.thinking && !m.content
          ? '<span class="thinking-dots">思考中</span>'
          : escapeHtml(m.content) + cursor;
        return `<div class="msg assistant"><div class="msg-col">
          ${toolStatus}
          <div class="${bubbleClass}">${inner}</div>
          ${metaHtml}
        </div></div>`;
      }).join('');
      messagesEl.innerHTML = html;
      messagesScroll.scrollTop = messagesScroll.scrollHeight;
    }

    function toggleMeta(idx) {
      const el = document.getElementById('meta-' + idx);
      if (el) el.classList.toggle('open');
    }

    function clearMessages() {
      messages.length = 0;
      renderMessages();
      updateMemoryHint();
      persistMessages();
    }

    // ===== 对话持久化 =====
    function storageKey(userId) {
      return STORAGE_PREFIX + (userId || 'default');
    }

    function persistMessages() {
      try {
        const uid = activeUserId || currentUserId();
        // 不持久化 thinking（临时占位）；error 保留以便恢复后能看到失败上下文
        const data = messages
          .filter(m => !m.thinking)
          .map(m => ({ role: m.role, content: m.content, meta: m.meta || null, error: !!m.error }));
        const trimmed = data.length > STORAGE_LIMIT ? data.slice(-STORAGE_LIMIT) : data;
        localStorage.setItem(storageKey(uid), JSON.stringify(trimmed));
      } catch (e) { /* localStorage 满或被禁,静默忽略 */ }
    }

    function loadMessagesForUser(userId) {
      messages.length = 0;
      try {
        const raw = localStorage.getItem(storageKey(userId));
        if (raw) {
          const arr = JSON.parse(raw);
          if (Array.isArray(arr)) {
            for (const m of arr) {
              if (!m || typeof m !== 'object') continue;
              if (m.role !== 'user' && m.role !== 'assistant') continue;
              if (typeof m.content !== 'string') continue;
              messages.push({
                role: m.role,
                content: m.content,
                meta: m.meta || null,
                error: !!m.error,
              });
            }
          }
        }
      } catch (e) { /* 损坏数据忽略 */ }
      activeUserId = userId;
      renderMessages();
      updateMemoryHint();
    }

    // ===== 长记忆弹窗 =====
    function currentUserId() {
      return document.getElementById('userId').value.trim() || 'default';
    }

    async function openMemoryModal() {
      const uid = currentUserId();
      document.getElementById('modalUserId').textContent = uid;
      document.getElementById('memoryModal').classList.add('open');
      document.getElementById('memoryList').innerHTML = '<div class="fact-empty">加载中...</div>';
      await refreshFactList();
    }

    function closeMemoryModal() {
      document.getElementById('memoryModal').classList.remove('open');
    }

    function onMaskClick(event) {
      if (event.target.id === 'memoryModal') closeMemoryModal();
    }

    function switchTab(tab) {
      currentTab = tab;
      document.querySelectorAll('.tab-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.tab === tab);
      });
      refreshFactList();
    }

    function renderFactList(facts) {
      const el = document.getElementById('memoryList');
      if (!facts.length) {
        const hint = currentTab === 'pending'
          ? '没有待审核的事实,聊几轮后 AI 会自动抽取候选'
          : '还没有已采纳的长期记忆,在下方手动添加,或聊几轮后审核 AI 抽取的候选';
        el.innerHTML = `<div class="fact-empty">${hint}</div>`;
        return;
      }
      el.innerHTML = facts.map(f => {
        const meta = `${escapeHtml(f.source || 'manual')} · ${escapeHtml(f.created_at || '')}`;
        const actions = currentTab === 'pending'
          ? `<div class="fact-actions">
               <button class="fact-accept" onclick="acceptFact('${escapeHtml(f.id)}')">采纳</button>
               <button class="fact-del" onclick="deleteFact('${escapeHtml(f.id)}')">丢弃</button>
             </div>`
          : `<button class="fact-del" onclick="deleteFact('${escapeHtml(f.id)}')">删除</button>`;
        return `
          <div class="fact-item">
            <div style="flex:1">
              <div class="fact-content">${escapeHtml(f.content)}</div>
              <div class="fact-meta">${meta}</div>
            </div>
            ${actions}
          </div>`;
      }).join('');
    }

    function updatePendingBadge(count) {
      pendingBadgeEl.textContent = String(count);
      pendingBadgeEl.classList.toggle('zero', count === 0);
      memoryBtnEl.textContent = count > 0 ? `记忆 (${count})` : '记忆';
    }

    async function addFact() {
      const input = document.getElementById('newFactInput');
      const btn = document.getElementById('addFactBtn');
      const content = input.value.trim();
      if (!content) return;
      btn.disabled = true;
      try {
        await fetch('/api/assistant/memory', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: currentUserId(), content })
        });
        input.value = '';
        currentTab = 'accepted';
        document.querySelectorAll('.tab-btn').forEach(b => {
          b.classList.toggle('active', b.dataset.tab === 'accepted');
        });
        await refreshFactList();
      } finally {
        btn.disabled = false;
        input.focus();
      }
    }

    async function deleteFact(id) {
      const verb = currentTab === 'pending' ? '丢弃' : '删除';
      if (!confirm(`确定${verb}这条记忆?`)) return;
      const uid = currentUserId();
      await fetch(`/api/assistant/memory?user_id=${encodeURIComponent(uid)}&id=${encodeURIComponent(id)}`, {
        method: 'DELETE'
      });
      await refreshFactList();
    }

    async function acceptFact(id) {
      const uid = currentUserId();
      await fetch(`/api/assistant/memory?user_id=${encodeURIComponent(uid)}&id=${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'accepted' })
      });
      await refreshFactList();
    }

    async function refreshFactList() {
      const uid = currentUserId();
      try {
        const res = await fetch(`/api/assistant/memory?user_id=${encodeURIComponent(uid)}&status=${currentTab}`);
        const data = await res.json();
        renderFactList(data.facts || []);
        updatePendingBadge(data.pending_count || 0);
      } catch (e) {
        document.getElementById('memoryList').innerHTML = `<div class="fact-empty">加载失败: ${escapeHtml(String(e))}</div>`;
      }
    }

    async function refreshPendingBadgeOnly() {
      // 不打开弹窗时，也要更新顶部按钮的 pending 角标
      try {
        const uid = currentUserId();
        const res = await fetch(`/api/assistant/memory?user_id=${encodeURIComponent(uid)}`);
        const data = await res.json();
        updatePendingBadge(data.pending_count || 0);
      } catch (e) { /* 静默 */ }
    }

    // 触发自动抽取(fire-and-forget,不阻塞用户)
    function maybeTriggerExtract() {
      turnsSinceExtract += 1;
      if (turnsSinceExtract < EXTRACT_EVERY_TURNS) return;
      turnsSinceExtract = 0;
      const uid = currentUserId();
      // 取最近 EXTRACT_EVERY_TURNS 轮的对话(排除 thinking/error)
      const flat = messages
        .filter(m => !m.thinking && !m.error)
        .map(m => ({ role: m.role, content: m.content }));
      const recent = flat.slice(-EXTRACT_EVERY_TURNS * 2);
      if (recent.length === 0) return;
      fetch('/api/assistant/extract', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: uid, dialogue: recent })
      })
      .then(r => r.json())
      .then(_ => refreshPendingBadgeOnly())
      .catch(_ => { /* 抽取失败不影响主流程 */ });
    }

    // 弹窗里 Enter 触发添加
    document.getElementById('newFactInput').addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        addFact();
      }
    });
    // ESC 关闭弹窗
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeMemoryModal();
    });
    // 切换 user_id 时:加载该用户的对话历史 + 刷新 pending 角标
    document.getElementById('userId').addEventListener('change', () => {
      loadMessagesForUser(currentUserId());
      refreshPendingBadgeOnly();
    });
    // 页面加载时初始化:恢复当前 user 的对话 + 角标
    loadMessagesForUser(currentUserId());
    refreshPendingBadgeOnly();

    async function askAssistant() {
      const btn = document.getElementById('sendBtn');
      const query = queryEl.value.trim();
      if (!query) return;
      const userId = document.getElementById('userId').value.trim() || 'default';

      // 收集"当前提问之前"的对话作为记忆
      const history = buildHistory();

      messages.push({ role: 'user', content: query });
      const placeholder = {
        role: 'assistant', content: '', thinking: true, streaming: true,
        toolStatus: '', meta: null,
      };
      messages.push(placeholder);
      renderMessages();
      persistMessages();
      queryEl.value = '';
      queryEl.style.height = 'auto';
      btn.disabled = true;

      let success = false;
      const controller = new AbortController();
      let abortTimer = null;
      try {
        abortTimer = setTimeout(() => controller.abort(), ASK_STREAM_TIMEOUT_MS);
        const res = await fetch('/api/assistant/ask_stream', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query, user_id: userId, role: 'auto', history }),
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          const data = await res.json().catch(() => ({}));
          replacePlaceholder(placeholder, {
            role: 'assistant',
            content: '[请求失败] ' + (data.error || res.statusText || 'unknown'),
            meta: data.meta || null,
            error: true,
          });
        } else {
          const reader = res.body.getReader();
          const decoder = new TextDecoder('utf-8');
          let buffer = '';
          let firstDelta = true;
          let errored = false;
          while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            // NDJSON: 按 \n 切分,最后一段可能是不完整的 JSON,留在 buffer
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
              const t = line.trim();
              if (!t) continue;
              let evt;
              try { evt = JSON.parse(t); } catch { continue; }
              if (evt.type === 'delta') {
                if (firstDelta) {
                  placeholder.thinking = false;
                  placeholder.toolStatus = '';
                  firstDelta = false;
                }
                placeholder.content += (evt.content || '');
                renderMessages();
              } else if (evt.type === 'tool_start') {
                placeholder.toolStatus = `正在调用工具: ${evt.name}`;
                renderMessages();
              } else if (evt.type === 'tool_done') {
                placeholder.toolStatus = `已完成工具: ${evt.name}`;
                renderMessages();
              } else if (evt.type === 'done') {
                placeholder.thinking = false;
                placeholder.streaming = false;
                placeholder.toolStatus = '';
                placeholder.content = evt.answer || placeholder.content;
                placeholder.meta = evt.meta || null;
                renderMessages();
              } else if (evt.type === 'error') {
                errored = true;
                placeholder.thinking = false;
                placeholder.streaming = false;
                placeholder.error = true;
                placeholder.content = '[流式错误] ' + (evt.message || '');
                renderMessages();
              }
            }
          }
          // 兜底:流结束但没有 done 事件
          if (placeholder.streaming) {
            placeholder.streaming = false;
            placeholder.thinking = false;
            renderMessages();
          }
          success = !errored;
        }
        updateMemoryHint();
        persistMessages();
        if (success) maybeTriggerExtract();
      } catch (e) {
        const errMsg = e && e.name === 'AbortError'
          ? '[超时] 响应时间过长已中止，请重试或简化问题。'
          : '[请求异常] ' + String(e);
        replacePlaceholder(placeholder, {
          role: 'assistant', content: errMsg, error: true,
        });
        updateMemoryHint();
        persistMessages();
      } finally {
        if (abortTimer) clearTimeout(abortTimer);
        btn.disabled = false;
        queryEl.focus();
      }
    }

    function replacePlaceholder(placeholder, replacement) {
      const idx = messages.indexOf(placeholder);
      if (idx >= 0) {
        messages[idx] = replacement;
      } else {
        messages.push(replacement);
      }
      renderMessages();
    }
  </script>
</body>
</html>
"""


def _build_handler(config: Dict[str, Any]):
    ai_client = AIClient(config.get("AI", {}))
    router_config = config.get("ASSISTANT_ROUTER", {})
    route_rules = router_config.get("ROUTE_RULES") or DEFAULT_ROUTE_RULES
    system_prompts = router_config.get("SYSTEM_PROMPTS") or DEFAULT_SYSTEM_PROMPTS
    llm_routing = router_config.get("LLM_ROUTING") or {}
    storage_config = config.get("STORAGE", {})
    ai_filter_config = config.get("AI_FILTER", {})
    local_config = storage_config.get("LOCAL", {})
    remote_config = storage_config.get("REMOTE", {})
    pull_config = storage_config.get("PULL", {})
    assistant_context_cfg = router_config.get("CONTEXT") or config.get("ASSISTANT_CONTEXT", {})
    news_context_max_items = int(assistant_context_cfg.get("MAX_ITEMS", 10))
    news_context_interests_file = str(ai_filter_config.get("INTERESTS_FILE") or "ai_interests.txt")

    # data_dir 解析为绝对路径，避免 web 服务从非项目根目录启动时读不到本地 SQLite
    raw_data_dir = local_config.get("DATA_DIR", "output")
    resolved_data_dir = (
        raw_data_dir
        if os.path.isabs(raw_data_dir)
        else str((_PROJECT_ROOT / raw_data_dir).resolve())
    )

    # 进程级永久缓存：第一次请求触发加载，后续请求共享。
    # 数据更新由 `python -m trendradar` 主流程触发，重启 web 服务时缓存自然刷新。
    # 每次加载临时构建 storage_manager 并立即清理，避免 SQLite 连接跨线程问题。
    candidates_lock = threading.Lock()
    candidates_state: Dict[str, Any] = {"items": None}

    def _load_candidates_cached() -> List[Dict[str, Any]]:
        if candidates_state["items"] is not None:
            return candidates_state["items"]
        with candidates_lock:
            if candidates_state["items"] is not None:
                return candidates_state["items"]
            sm = get_storage_manager(
                backend_type=storage_config.get("BACKEND", "auto"),
                data_dir=resolved_data_dir,
                enable_txt=storage_config.get("FORMATS", {}).get("TXT", True),
                enable_html=storage_config.get("FORMATS", {}).get("HTML", True),
                remote_config={
                    "bucket_name": remote_config.get("BUCKET_NAME", ""),
                    "access_key_id": remote_config.get("ACCESS_KEY_ID", ""),
                    "secret_access_key": remote_config.get("SECRET_ACCESS_KEY", ""),
                    "endpoint_url": remote_config.get("ENDPOINT_URL", ""),
                    "region": remote_config.get("REGION", ""),
                },
                local_retention_days=local_config.get("RETENTION_DAYS", 0),
                remote_retention_days=remote_config.get("RETENTION_DAYS", 0),
                pull_enabled=pull_config.get("ENABLED", False),
                pull_days=pull_config.get("DAYS", 7),
                timezone=config.get("TIMEZONE", "Asia/Shanghai"),
            )
            try:
                items = (
                    sm.get_active_ai_filter_results(
                        interests_file=news_context_interests_file
                    )
                    or []
                )
            finally:
                try:
                    sm.cleanup()
                except Exception:
                    pass
            candidates_state["items"] = items
            print(f"[助手] 新闻候选已缓存: {len(items)} 条")
        return candidates_state["items"]

    # 长记忆：用户事实卡，存放在项目根 memory/ 目录下
    memory_store = MemoryStore(base_dir=_PROJECT_ROOT / "memory")

    # Observability:对话调用日志,JSONL 按天分文件
    log_store = LogStore(base_dir=_PROJECT_ROOT / "logs")

    class _AskValidationError(ValueError):
        """请求体校验错误，外层捕获后返回 400。"""

    valid, error = ai_client.validate_config()
    if not valid:
        print(f"[助手] AI 配置提示: {error}")

    class AssistantHandler(BaseHTTPRequestHandler):
        def _write_json(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _write_html(self, status: int, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _parse_query(self):
            parsed = urlsplit(self.path)
            return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items() if v}

        def _read_json_body(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            return json.loads(raw)

        def _prepare_ask_context(self, data: Dict[str, Any]) -> Dict[str, Any]:
            """
            把 /ask 和 /ask_stream 共用的"准备阶段"逻辑收敛到一处:
            校验 query → 解析 user_id → 加载长记忆 → 整理短记忆 → 路由 → 拼 messages → 构造 dispatcher。
            校验失败抛 _AskValidationError(400)，由调用方捕获。
            """
            query = (data.get("query") or "").strip()
            role = (data.get("role") or "auto").strip()
            user_id = (data.get("user_id") or "default").strip() or "default"
            if not query:
                raise _AskValidationError("query is required")

            user_facts = memory_store.list_facts(user_id, status="accepted")
            facts_block = format_facts_for_prompt(user_facts)

            MAX_HISTORY_TURNS = 6
            raw_history = data.get("history") or []
            clean_history = []
            for h in raw_history:
                if not isinstance(h, dict):
                    continue
                role_h = str(h.get("role", "")).strip()
                content_h = str(h.get("content", "")).strip()
                if role_h not in ("user", "assistant") or not content_h:
                    continue
                clean_history.append({"role": role_h, "content": content_h})
            if len(clean_history) > MAX_HISTORY_TURNS * 2:
                clean_history = clean_history[-MAX_HISTORY_TURNS * 2:]

            route = route_intent_hybrid(
                query=query,
                route_rules=route_rules,
                ai_client=ai_client,
                llm_routing=llm_routing,
            )

            if role and role != "auto":
                role_text = role
                role_mode = "custom"
            else:
                role_text = resolve_system_prompt(route["primary"], system_prompts)
                role_mode = f"route:{route['primary']}"
            system_content = f"{facts_block}\n\n{role_text}" if facts_block else role_text

            messages: List[Dict[str, Any]] = []
            messages.append({"role": "system", "content": system_content})
            messages.extend(clean_history)
            messages.append({"role": "user", "content": query})

            dispatcher = NewsToolDispatcher(
                candidates_loader=_load_candidates_cached,
                default_limit=news_context_max_items,
            )

            return {
                "query": query,
                "user_id": user_id,
                "raw_user_id": data.get("user_id", "default"),
                "user_facts": user_facts,
                "history_used": len(clean_history),
                "max_history_turns": MAX_HISTORY_TURNS,
                "route": route,
                "role_mode": role_mode,
                "messages": messages,
                "dispatcher": dispatcher,
            }

        def _handle_ask_stream(self):
            """流式版本:NDJSON over chunked HTTP，每行一个事件 JSON。"""
            try:
                data = self._read_json_body()
            except Exception:
                self._write_json(400, {"error": "invalid json"})
                return

            try:
                ctx = self._prepare_ask_context(data)
            except _AskValidationError as exc:
                self._write_json(400, {"error": str(exc)})
                return

            # 流式响应:不带 Content-Length，用 Connection: close 让客户端读到 EOF 即结束
            # 这比 Transfer-Encoding: chunked 简单可靠（http.server 不会自动做 chunked 编码）
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")  # 禁用反向代理缓冲
            self.send_header("Connection", "close")
            self.end_headers()

            def write_event(payload: Dict[str, Any]) -> bool:
                try:
                    line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                    self.wfile.write(line)
                    self.wfile.flush()
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    return False

            log_payload: Dict[str, Any] = {
                "user_id": ctx["user_id"],
                "query": ctx["query"],
                "answer": "",
                "model": ai_client.model,
                "role_mode": ctx["role_mode"],
                "route": {
                    "primary": ctx["route"].get("primary"),
                    "source": ctx["route"].get("source"),
                    "confidence": ctx["route"].get("confidence"),
                },
                "memory": {
                    "short_history": ctx["history_used"],
                    "long_facts": len(ctx["user_facts"]),
                },
                "iterations": 0,
                "stop_reason": "",
                "tool_calls": [],
                "timings": {},
                "error": None,
            }

            try:
                for event in ai_client.chat_assistant_with_tools_stream(
                    messages=ctx["messages"],
                    tools=NEWS_TOOLS,
                    tool_dispatcher=ctx["dispatcher"].dispatch,
                ):
                    if event.get("type") == "done":
                        log_payload["answer"] = event.get("answer", "")
                        log_payload["iterations"] = event.get("iterations", 0)
                        log_payload["stop_reason"] = event.get("stop_reason", "")
                        log_payload["tool_calls"] = event.get("tool_calls", [])
                        log_payload["timings"] = event.get("timings", {})
                        # 附加 meta 后再发
                        meta_payload = {
                            "type": "done",
                            "answer": event.get("answer", ""),
                            "meta": {
                                "model": ai_client.model,
                                "role_mode": ctx["role_mode"],
                                "route": ctx["route"],
                                "memory": {
                                    "history_used": ctx["history_used"],
                                    "max_history_turns": ctx["max_history_turns"],
                                    "long_facts_count": len(ctx["user_facts"]),
                                },
                                "tools": {
                                    "iterations": event.get("iterations", 0),
                                    "stop_reason": event.get("stop_reason", ""),
                                    "tool_calls": event.get("tool_calls", []),
                                    "dispatcher_log": ctx["dispatcher"].call_log,
                                    "interests_file": news_context_interests_file,
                                },
                                "timings": event.get("timings", {}),
                            },
                        }
                        if not write_event(meta_payload):
                            break
                    else:
                        if not write_event(event):
                            break
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}"
                log_payload["error"] = err_msg
                write_event({"type": "error", "message": err_msg})
            finally:
                # 无论成功失败都落一条日志（即使中途断开,记录有 error 字段也有诊断价值）
                try:
                    log_store.record(log_payload)
                except Exception as exc:
                    print(f"[助手] 日志写入失败: {type(exc).__name__}: {exc}")
            # Connection: close,wfile 关闭即流终止;BaseHTTPRequestHandler 处理收尾

        def do_GET(self):  # noqa: N802
            path, qs = self._parse_query()
            if path in ("/assistant", "/assistant/"):
                self._write_html(200, ASSISTANT_HTML)
                return
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/assistant")
                self.end_headers()
                return
            if path == "/api/assistant/memory":
                user_id = qs.get("user_id", "default") or "default"
                status_filter = qs.get("status", "").strip() or None
                facts = memory_store.list_facts(user_id, status=status_filter)
                accepted = memory_store.list_facts(user_id, status="accepted")
                pending = memory_store.list_facts(user_id, status="pending")
                self._write_json(200, {
                    "user_id": user_id,
                    "facts": facts,
                    "count": len(facts),
                    "accepted_count": len(accepted),
                    "pending_count": len(pending),
                })
                return
            if path == "/api/assistant/logs":
                date = qs.get("date") or None
                limit = int(qs.get("limit", "50"))
                user_id = qs.get("user_id") or None
                logs = log_store.list_logs(date=date, limit=limit, user_id=user_id)
                self._write_json(200, {
                    "date": date,
                    "user_id": user_id,
                    "count": len(logs),
                    "logs": logs,
                })
                return
            if path == "/api/assistant/logs/stats":
                date = qs.get("date") or None
                user_id = qs.get("user_id") or None
                stats = log_store.aggregate_stats(date=date, user_id=user_id)
                self._write_json(200, {
                    "date": date,
                    "user_id": user_id,
                    "stats": stats,
                })
                return
            self._write_json(404, {"error": "not found"})

        def do_DELETE(self):  # noqa: N802
            path, qs = self._parse_query()
            if path == "/api/assistant/memory":
                user_id = qs.get("user_id", "default") or "default"
                fact_id = qs.get("id", "").strip()
                if not fact_id:
                    self._write_json(400, {"error": "id is required"})
                    return
                ok = memory_store.delete_fact(user_id, fact_id)
                self._write_json(200 if ok else 404, {"deleted": ok, "id": fact_id})
                return
            self._write_json(404, {"error": "not found"})

        def do_PATCH(self):  # noqa: N802
            path, qs = self._parse_query()
            if path == "/api/assistant/memory":
                try:
                    body = self._read_json_body()
                except Exception:
                    self._write_json(400, {"error": "invalid json"})
                    return
                user_id = qs.get("user_id", "default") or "default"
                fact_id = qs.get("id", "").strip()
                new_status = (body.get("status") or "").strip()
                if not fact_id:
                    self._write_json(400, {"error": "id is required"})
                    return
                if new_status not in ("accepted", "pending"):
                    self._write_json(400, {"error": "status must be accepted or pending"})
                    return
                ok = memory_store.update_fact_status(user_id, fact_id, new_status)
                self._write_json(200 if ok else 404, {"updated": ok, "id": fact_id, "status": new_status})
                return
            self._write_json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            path, _ = self._parse_query()
            if path == "/api/assistant/memory":
                try:
                    data = self._read_json_body()
                except Exception:
                    self._write_json(400, {"error": "invalid json"})
                    return
                user_id = (data.get("user_id") or "default").strip() or "default"
                content = (data.get("content") or "").strip()
                if not content:
                    self._write_json(400, {"error": "content is required"})
                    return
                fact = memory_store.add_fact(user_id, content, source="manual", status="accepted")
                self._write_json(200, {"fact": fact})
                return

            if path == "/api/assistant/extract":
                # 半自动抽取:从给定的对话片段里抽取候选 fact，以 status=pending 入库
                try:
                    data = self._read_json_body()
                except Exception:
                    self._write_json(400, {"error": "invalid json"})
                    return
                user_id = (data.get("user_id") or "default").strip() or "default"
                raw_dialogue = data.get("dialogue") or []
                dialogue = []
                for m in raw_dialogue:
                    if not isinstance(m, dict):
                        continue
                    r = str(m.get("role", "")).strip()
                    c = str(m.get("content", "")).strip()
                    if r in ("user", "assistant") and c:
                        dialogue.append({"role": r, "content": c})
                if not dialogue:
                    self._write_json(400, {"error": "dialogue is required"})
                    return

                existing = memory_store.fact_contents(user_id)
                try:
                    raw = ai_client.chat(
                        build_extract_messages(existing, dialogue),
                        temperature=0.0,
                        max_tokens=400,
                    )
                except Exception as exc:
                    self._write_json(500, {
                        "error": f"{type(exc).__name__}: {exc}",
                        "new_pending": [],
                    })
                    return

                parsed = parse_extract_response(raw)
                added = []
                for content in parsed.get("new_facts", []):
                    fact = memory_store.add_fact(user_id, content, source="auto", status="pending")
                    if fact:
                        added.append(fact)
                pending = memory_store.list_facts(user_id, status="pending")
                self._write_json(200, {
                    "new_pending": added,
                    "total_pending": len(pending),
                    "skipped_reason": parsed.get("skipped_reason", ""),
                })
                return

            if path == "/api/assistant/ask_stream":
                self._handle_ask_stream()
                return

            if path != "/api/assistant/ask":
                self._write_json(404, {"error": "not found"})
                return
            try:
                data = self._read_json_body()
            except Exception:
                self._write_json(400, {"error": "invalid json"})
                return

            try:
                ctx = self._prepare_ask_context(data)
            except _AskValidationError as exc:
                self._write_json(400, {"error": str(exc)})
                return

            query = ctx["query"]
            user_id = ctx["user_id"]
            user_facts = ctx["user_facts"]
            history_used = ctx["history_used"]
            MAX_HISTORY_TURNS = ctx["max_history_turns"]
            route = ctx["route"]
            role_mode = ctx["role_mode"]
            messages = ctx["messages"]
            dispatcher = ctx["dispatcher"]

            try:
                tool_result = ai_client.chat_assistant_with_tools(
                    messages=messages,
                    tools=NEWS_TOOLS,
                    tool_dispatcher=dispatcher.dispatch,
                )
            except Exception as exc:
                self._write_json(
                    500,
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "meta": {
                            "model": ai_client.model,
                            "role_mode": role_mode,
                            "route": route,
                            "memory": {
                                "history_used": history_used,
                                "max_history_turns": MAX_HISTORY_TURNS,
                                "long_facts_count": len(user_facts),
                            },
                            "tools": {
                                "iterations": 0,
                                "stop_reason": "error",
                                "tool_calls": dispatcher.call_log,
                                "interests_file": news_context_interests_file,
                            },
                        },
                    },
                )
                return

            self._write_json(
                200,
                {
                    "user_id": data.get("user_id", "default"),
                    "query": query,
                    "answer": tool_result["answer"],
                    "meta": {
                        "model": ai_client.model,
                        "role_mode": role_mode,
                        "route": route,
                        "memory": {
                            "history_used": history_used,
                            "max_history_turns": MAX_HISTORY_TURNS,
                        },
                        "tools": {
                            "iterations": tool_result["iterations"],
                            "stop_reason": tool_result["stop_reason"],
                            "tool_calls": tool_result["tool_calls"],
                            "dispatcher_log": dispatcher.call_log,
                            "interests_file": news_context_interests_file,
                        },
                    },
                },
            )

        def log_message(self, format: str, *args):  # noqa: A003
            return

    return AssistantHandler


def run_assistant_web(
    config: Dict[str, Any],
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    handler = _build_handler(config)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"[助手] Web 服务已运行: http://{host}:{port}/assistant")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def start_assistant_web_background(
    config: Dict[str, Any],
    host: str = "127.0.0.1",
    port: int = 8765,
) -> bool:
    """后台启动助理 Web 服务（独立进程），失败时返回 False。"""
    try:
        # 已有服务在监听时直接认为成功
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                print(f"[助手] Web 服务已运行: http://{host}:{port}/assistant")
                return True

        # 启动独立子进程，避免主进程退出后服务消失
        creationflags = 0
        if sys.platform.startswith("win"):
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "trendradar",
                "--assistant-web",
                "--assistant-web-host",
                host,
                "--assistant-web-port",
                str(port),
                "--assistant-web-no-open",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        print(f"[助手] Web 服务已启动: http://{host}:{port}/assistant")
        return True
    except Exception as exc:
        print(f"[助手] 启动失败: {type(exc).__name__}: {exc}")
        return False
