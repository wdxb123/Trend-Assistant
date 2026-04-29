
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
  