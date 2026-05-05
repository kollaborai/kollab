/**
 * kollabor-webui - Ultimate Multi-Panel Terminal Interface
 * Full API integration, multi-session support, all the bells and whistles
 */

// ============================================================================
// API Client - Clean wrapper for all engine endpoints
// ============================================================================

class EngineAPI {
  constructor(baseUrl) {
    this.baseUrl = baseUrl || 'http://127.0.0.1:7433';
    this.token = null;
  }

  setToken(token) {
    this.token = token;
  }

  setBaseUrl(url) {
    this.baseUrl = url;
  }

  headers() {
    const h = { 'Content-Type': 'application/json' };
    if (this.token) h['Authorization'] = `Bearer ${this.token}`;
    return h;
  }

  async request(method, path, body = null) {
    const url = `${this.baseUrl}${path}`;
    const opts = {
      method,
      headers: this.headers(),
    };
    if (body) opts.body = JSON.stringify(body);

    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp;
  }

  async get(path) {
    const resp = await this.request('GET', path);
    return resp.json();
  }

  async post(path, body = null) {
    const resp = await this.request('POST', path, body);
    return resp.json();
  }

  async put(path, body = null) {
    const resp = await this.request('PUT', path, body);
    return resp.json();
  }

  async delete(path) {
    const resp = await this.request('DELETE', path);
    return resp.json();
  }

  // Health/Status
  async health() {
    return this.get('/health');
  }

  async status() {
    return this.get('/status');
  }

  async version() {
    return this.get('/version');
  }

  // Sessions
  async listSessions() {
    return this.get('/sessions');
  }

  async getSession(sessionId) {
    return this.get(`/sessions/${sessionId}`);
  }

  async createSession(config = {}) {
    return this.post('/sessions', {
      profile: config.profile || 'default',
      system_prompt: config.systemPrompt,
      workspace: config.workspace,
      approval_mode: config.approvalMode || 'confirm_all',
      mcp_servers: config.mcpServers || [],
      metadata: config.metadata || {},
      credentials: config.credentials,
    });
  }

  async deleteSession(sessionId) {
    return this.delete(`/sessions/${sessionId}`);
  }

  async getHistory(sessionId, limit = null) {
    const path = limit ? `/sessions/${sessionId}/history?limit=${limit}` : `/sessions/${sessionId}/history`;
    return this.get(path);
  }

  async clearHistory(sessionId) {
    return this.delete(`/sessions/${sessionId}/history`);
  }

  async getSystemPrompt(sessionId) {
    return this.get(`/sessions/${sessionId}/system-prompt`);
  }

  async rebuildSystemPrompt(sessionId) {
    return this.post(`/sessions/${sessionId}/system-prompt/rebuild`);
  }

  // Messages
  async sendMessage(sessionId, content) {
    const url = `${this.baseUrl}/sessions/${sessionId}/message`;
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        ...this.headers(),
        'Accept': 'text/event-stream',
      },
      body: JSON.stringify({ content }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || `HTTP ${resp.status}`);
    }
    return resp.body.getReader();
  }

  async cancelTurn(sessionId) {
    return this.post(`/sessions/${sessionId}/cancel`);
  }

  // Permissions
  async getPermissions(sessionId) {
    return this.get(`/sessions/${sessionId}/permissions`);
  }

  async setApprovalMode(sessionId, mode) {
    return this.post(`/sessions/${sessionId}/permissions/mode`, { mode });
  }

  async respondPermission(sessionId, toolId, decision, scope = 'once') {
    return this.post(`/sessions/${sessionId}/permission`, {
      tool_id: toolId,
      decision,
      scope,
    });
  }

  // Profiles
  async listProfiles() {
    return this.get('/profiles');
  }

  async getProfile(name) {
    return this.get(`/profiles/${name}`);
  }

  async createProfile(profile) {
    return this.post('/profiles', profile);
  }

  async updateProfile(name, updates) {
    return this.put(`/profiles/${name}`, updates);
  }

  async deleteProfile(name) {
    return this.delete(`/profiles/${name}`);
  }

  async testProfile(name) {
    return this.post(`/profiles/${name}/test`);
  }

  // MCP
  async listMCPServers() {
    return this.get('/mcp/servers');
  }

  async addMCPServer(server) {
    return this.post('/mcp/servers', server);
  }

  async updateMCPServer(name, config) {
    return this.put(`/mcp/servers/${name}`, config);
  }

  async removeMCPServer(name) {
    return this.delete(`/mcp/servers/${name}`);
  }

  async getSessionMCP(sessionId) {
    return this.get(`/sessions/${sessionId}/mcp`);
  }

  async connectMCP(sessionId, serverName) {
    return this.post(`/sessions/${sessionId}/mcp/${serverName}/connect`);
  }

  async disconnectMCP(sessionId, serverName) {
    return this.post(`/sessions/${sessionId}/mcp/${serverName}/disconnect`);
  }

  async getMCPServerTools(sessionId, serverName) {
    return this.get(`/sessions/${sessionId}/mcp/${serverName}/tools`);
  }

  async getMCPServerStatus(sessionId, serverName) {
    return this.get(`/sessions/${sessionId}/mcp/${serverName}/status`);
  }

  // Hub
  async hubAgents(refresh = false) {
    return this.get(`/hub/agents${refresh ? '?refresh=true' : ''}`);
  }

  async hubAgentStatus(agentId) {
    return this.get(`/hub/agents/${agentId}/status`);
  }

  async hubSendMessage(target, content, fromIdentity = 'webui') {
    return this.post('/hub/messages', { target, content, from_identity: fromIdentity });
  }
}

// ============================================================================
// SSE Parser - Robust event stream handling
// ============================================================================

class SSEParser {
  constructor() {
    this.buffer = '';
  }

  feed(chunk) {
    this.buffer += chunk;
  }

  *parse() {
    const lines = this.buffer.split('\n');
    this.buffer = lines.pop() || '';

    for (const line of lines) {
      if (line.startsWith('data:')) {
        const data = line.substring(5).trim();
        if (data) {
          try {
            yield JSON.parse(data);
          } catch (e) {
            // Skip malformed events
          }
        }
      }
    }
  }

  reset() {
    this.buffer = '';
  }
}

// ============================================================================
// Terminal Session - Single chat panel
// ============================================================================

class TerminalSession {
  constructor(id, panel, api, manager) {
    this.id = id;
    this.panel = panel;
    this.api = api;
    this.manager = manager;
    this.container = panel.querySelector('.terminal-output');
    this.input = panel.querySelector('.terminal-input');
    this.sendBtn = panel.querySelector('.send-btn');
    this.titleEl = panel.querySelector('.session-title');
    this.statusEl = panel.querySelector('.session-status');
    this.tokensEl = panel.querySelector('.token-count');
    
    this.history = [];
    this.historyIndex = -1;
    this.isStreaming = false;
    this.currentAiElement = null;
    this.autoScroll = true;
    
    this.init();
  }

  init() {
    // Input handling
    this.sendBtn.addEventListener('click', () => this.handleSend());
    
    this.input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.handleSend();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        this.navigateHistory(-1);
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        this.navigateHistory(1);
      }
    });

    // Auto-resize textarea
    this.input.addEventListener('input', () => {
      this.input.style.height = 'auto';
      this.input.style.height = Math.min(this.input.scrollHeight, 120) + 'px';
    });

    // Scroll detection
    this.container.addEventListener('scroll', () => {
      const nearBottom = this.container.scrollHeight - this.container.scrollTop 
                        < this.container.clientHeight + 100;
      this.autoScroll = nearBottom;
    });

    // Focus on click
    this.container.addEventListener('click', () => this.input.focus());

    // Panel controls
    panel.querySelector('.close-btn').addEventListener('click', () => {
      this.manager.closeSession(this.id);
    });

    panel.querySelector('.minimize-btn').addEventListener('click', () => {
      this.toggleMinimize();
    });

    panel.querySelector('.popout-btn').addEventListener('click', () => {
      this.manager.popoutSession(this.id);
    });
  }

  toggleMinimize() {
    this.panel.classList.toggle('minimized');
    const btn = this.panel.querySelector('.minimize-btn');
    btn.textContent = this.panel.classList.contains('minimized') ? '□' : '−';
  }

  navigateHistory(direction) {
    if (this.history.length === 0) return;

    this.historyIndex += direction;

    if (this.historyIndex < 0) {
      this.historyIndex = 0;
    } else if (this.historyIndex >= this.history.length) {
      this.historyIndex = -1;
      this.input.value = '';
      return;
    }

    this.input.value = this.history[this.historyIndex];
  }

  handleSend() {
    const text = this.input.value.trim();
    if (!text || this.isStreaming) return;

    this.history.unshift(text);
    this.historyIndex = -1;
    this.input.value = '';
    this.input.style.height = 'auto';

    this.executeCommand(text);
  }

  async executeCommand(text) {
    // Check for slash commands
    if (text.startsWith('/')) {
      const handled = await this.handleCommand(text);
      if (handled) return;
    }

    // Send as message to AI
    await this.sendMessage(text);
  }

  async handleCommand(text) {
    const parts = text.slice(1).split(' ');
    const cmd = parts[0].toLowerCase();
    const args = parts.slice(1);

    switch (cmd) {
      case 'help':
        this.showHelp();
        return true;

      case 'clear':
        this.container.innerHTML = '';
        return true;

      case 'export':
        this.exportSession();
        return true;

      case 'history':
        await this.showHistory();
        return true;

      case 'reset':
        await this.resetHistory();
        return true;

      case 'mcp':
        await this.showMCP();
        return true;

      case 'mode':
        if (args[0]) {
          await this.setMode(args[0]);
        } else {
          this.printSystem(`usage: /mode <confirm_all|default|auto_approve_edits|trust_all>`);
        }
        return true;

      case 'cancel':
        await this.cancelCurrent();
        return true;

      default:
        return false;
    }
  }

  showHelp() {
    this.printLine('');
    this.printSystem('commands:');
    this.printSystem('  /help     show this help');
    this.printSystem('  /clear    clear terminal');
    this.printSystem('  /export   export session to file');
    this.printSystem('  /history  show conversation history');
    this.printSystem('  /reset    reset conversation history');
    this.printSystem('  /mcp      show MCP server status');
    this.printSystem('  /mode <mode>  set approval mode');
    this.printSystem('  /cancel   cancel current turn');
    this.printLine('');
    return true;
  }

  async showHistory() {
    try {
      const data = await this.api.getHistory(this.id);
      const history = data.history || [];
      
      if (history.length === 0) {
        this.printSystem('no history');
        return;
      }

      this.printLine('');
      this.printSystem(`conversation history (${history.length} messages):`);
      history.forEach((msg, i) => {
        const role = msg.role.toUpperCase().padEnd(9);
        const preview = (msg.content || '').substring(0, 100);
        this.printSystem(`  ${i + 1}. [${role}] ${preview}${msg.content?.length > 100 ? '...' : ''}`);
      });
      this.printLine('');
    } catch (e) {
      this.printError(`failed to get history: ${e.message}`);
    }
  }

  async resetHistory() {
    try {
      await this.api.clearHistory(this.id);
      this.container.innerHTML = '';
      this.printSuccess('conversation history reset');
    } catch (e) {
      this.printError(`failed to reset: ${e.message}`);
    }
  }

  async showMCP() {
    try {
      const data = await this.api.getSessionMCP(this.id);
      const servers = data.servers || {};

      this.printLine('');
      this.printSystem(`MCP servers for session ${this.id.substring(0, 12)}:`);

      for (const [name, info] of Object.entries(servers)) {
        const status = info.status === 'connected' ? '✓' : '✗';
        const tools = info.tools ? ` (${info.tools.length} tools)` : '';
        this.printSystem(`  ${status} ${name}${tools}`);
      }

      this.printLine('');
      this.printSystem(`total tools available: ${data.total_tools}`);
      this.printLine('');
    } catch (e) {
      this.printError(`failed to get MCP status: ${e.message}`);
    }
  }

  async setMode(mode) {
    try {
      await this.api.setApprovalMode(this.id, mode);
      this.printSuccess(`approval mode set to: ${mode}`);
    } catch (e) {
      this.printError(`failed to set mode: ${e.message}`);
    }
  }

  async cancelCurrent() {
    try {
      await this.api.cancelTurn(this.id);
      this.printSuccess('turn cancelled');
    } catch (e) {
      this.printError(`failed to cancel: ${e.message}`);
    }
  }

  exportSession() {
    const blob = new Blob([this.container.innerText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `kollabor-${this.id.substring(0, 12)}-${Date.now()}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    this.printSuccess('session exported');
  }

  async sendMessage(message) {
    if (!this.manager.ensureConnected()) return;

    this.printUser(message);
    this.setStreaming(true);
    this.currentAiElement = null;

    try {
      const reader = await this.api.sendMessage(this.id, message);
      const decoder = new TextDecoder();
      const parser = new SSEParser();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        parser.feed(decoder.decode(value, { stream: true }));

        for (const event of parser.parse()) {
          this.handleSSEEvent(event);
        }
      }
    } catch (e) {
      this.printError(`connection error: ${e.message}`);
    }

    this.setStreaming(false);
    this.printLine('');
    this.input.focus();
  }

  handleSSEEvent(event) {
    switch (event.type) {
      case 'token':
        if (!this.currentAiElement) {
          this.currentAiElement = this.startAiResponse();
        }
        this.currentAiElement.textContent += event.text;
        this.scrollToBottom();
        break;

      case 'thinking':
        this.printThinking(event.text);
        break;

      case 'tool_start':
        this.printToolStart(event.tool_name, event.tool_id, event.input, event.risk_level);
        break;

      case 'tool_result':
        this.printToolResult(event.tool_name, event.success, event.output, event.execution_time);
        break;

      case 'permission_request':
        this.printPermissionRequest(event);
        break;

      case 'permission_granted':
        this.printPermissionGranted(event.tool_id, event.scope);
        break;

      case 'permission_denied':
        this.printPermissionDenied(event.tool_id);
        break;

      case 'turn_complete':
        this.handleTurnComplete(event);
        break;

      case 'error':
        this.printError(`${event.code}: ${event.message}`);
        break;

      case 'question_gate':
        this.printQuestion(event.question);
        break;
    }
  }

  handleTurnComplete(event) {
    if (this.currentAiElement && !this.currentAiElement.textContent) {
      this.currentAiElement.textContent = '[no response]';
    }

    const total = event.input_tokens + event.output_tokens;
    this.tokensEl.textContent = total;
    this.tokensEl.title = `${event.input_tokens} input / ${event.output_tokens} output`;

    this.printLine('');
    this.printTimestamp(`turn complete | ${event.input_tokens}in/${event.output_tokens}out | ${event.tool_calls} tools | ${event.stop_reason}`);
  }

  setStreaming(streaming) {
    this.isStreaming = streaming;
    this.statusEl.textContent = streaming ? 'STREAMING' : 'READY';
    this.statusEl.className = `session-status ${streaming ? 'streaming' : ''}`;
    this.sendBtn.disabled = streaming;
    this.sendBtn.textContent = streaming ? '...' : 'SEND';
  }

  setTitle(title) {
    this.titleEl.textContent = title;
  }

  // Output helpers
  printLine(text = '') {
    const line = document.createElement('div');
    line.className = 'output-line';
    line.textContent = text;
    this.container.appendChild(line);
    this.scrollToBottom();
    return line;
  }

  printUser(text) {
    const line = document.createElement('div');
    line.className = 'output-line user';
    line.textContent = text;
    this.container.appendChild(line);
    this.scrollToBottom();
  }

  startAiResponse() {
    const line = document.createElement('div');
    line.className = 'output-line assistant';
    this.container.appendChild(line);
    this.scrollToBottom();
    return line;
  }

  printThinking(text) {
    const line = document.createElement('div');
    line.className = 'output-line thinking';
    line.textContent = text;
    this.container.appendChild(line);
    this.scrollToBottom();
  }

  printSystem(text) {
    const line = document.createElement('div');
    line.className = 'output-line system';
    line.textContent = `[sys] ${text}`;
    this.container.appendChild(line);
    this.scrollToBottom();
  }

  printSuccess(text) {
    const line = document.createElement('div');
    line.className = 'output-line success';
    line.innerHTML = `<span style="color: var(--accent)">[ok]</span> ${this.escapeHtml(text)}`;
    this.container.appendChild(line);
    this.scrollToBottom();
  }

  printWarning(text) {
    const line = document.createElement('div');
    line.className = 'output-line warning';
    line.textContent = `[warn] ${text}`;
    this.container.appendChild(line);
    this.scrollToBottom();
  }

  printError(text) {
    const line = document.createElement('div');
    line.className = 'output-line error';
    line.textContent = `[err] ${text}`;
    this.container.appendChild(line);
    this.scrollToBottom();
  }

  printTimestamp(text) {
    const line = document.createElement('div');
    line.className = 'output-line timestamp';
    line.textContent = text;
    this.container.appendChild(line);
    this.scrollToBottom();
  }

  printToolStart(name, id, input, riskLevel) {
    const line = document.createElement('div');
    line.className = 'output-line tool';
    line.id = `tool-${this.id}-${id}`;

    const riskColor = riskLevel === 'high' ? 'var(--error)' : riskLevel === 'medium' ? 'var(--warning)' : 'var(--text-dim)';
    
    line.innerHTML = `<span class="tool-info">[tool] <span class="tool-name">${this.escapeHtml(name)}</span> <span class="tool-status running">running</span> <span style="color: ${riskColor}">[${riskLevel || 'low'}]</span></span>`;

    this.container.appendChild(line);

    if (input && Object.keys(input).length > 0) {
      const inputLine = document.createElement('div');
      inputLine.className = 'output-line tool';
      inputLine.style.fontSize = '11px';
      inputLine.style.color = 'var(--text-dim)';
      inputLine.textContent = `  input: ${JSON.stringify(input)}`;
      this.container.appendChild(inputLine);
    }

    this.scrollToBottom();
  }

  printToolResult(name, success, output, execTime) {
    const line = document.createElement('div');
    line.className = 'output-line tool';

    const statusClass = success ? 'success' : 'error';
    const statusText = success ? 'ok' : 'fail';
    const truncated = output && output.length > 300 ? output.substring(0, 300) + '...' : (output || '');

    line.innerHTML = `<span class="tool-info">[tool] <span class="tool-name">${this.escapeHtml(name)}</span> <span class="tool-status ${statusClass}">${statusText}</span> ${execTime ? `<span style="color: var(--text-dim)">${execTime.toFixed(2)}ms</span>` : ''}</span>`;

    this.container.appendChild(line);

    if (truncated) {
      const outputLine = document.createElement('div');
      outputLine.className = 'output-line tool';
      outputLine.style.fontSize = '11px';
      outputLine.style.color = success ? 'var(--text-secondary)' : 'var(--error)';
      outputLine.style.whiteSpace = 'pre-wrap';
      outputLine.style.wordBreak = 'break-all';
      outputLine.textContent = truncated.split('\n').map(l => '  ' + l).join('\n');
      this.container.appendChild(outputLine);
    }

    this.scrollToBottom();
  }

  printPermissionRequest(event) {
    const line = document.createElement('div');
    line.className = 'output-line permission';
    line.id = `perm-${this.id}-${event.tool_id}`;

    const riskColor = event.risk_level === 'high' ? 'var(--error)' : 'var(--warning)';

    line.innerHTML = `
      <div class="tool-info">[perm] <span class="tool-name">${this.escapeHtml(event.tool_name)}</span> <span style="color: ${riskColor}">[${event.risk_level}]</span></div>
      <div style="font-size: 11px; color: var(--text-dim); margin-top: 4px;">${this.escapeHtml(event.risk_reason)}</div>
      <div class="permission-buttons">
        <button class="btn btn-approve" data-tool-id="${event.tool_id}">approve</button>
        <button class="btn btn-deny" data-tool-id="${event.tool_id}">deny</button>
      </div>
    `;

    // Add event listeners
    line.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', () => this.respondPermission(event.tool_id, btn.classList.contains('btn-approve') ? 'approve' : 'deny'));
    });

    this.container.appendChild(line);
    this.scrollToBottom();
  }

  async respondPermission(toolId, decision) {
    const permLine = document.getElementById(`perm-${this.id}-${toolId}`);
    if (!permLine) return;

    // Disable buttons
    permLine.querySelectorAll('button').forEach(btn => btn.disabled = true);

    try {
      await this.api.respondPermission(this.id, toolId, decision);

      const statusText = decision === 'approve' ? 'approved' : 'denied';
      const color = decision === 'approve' ? 'var(--accent)' : 'var(--error)';
      permLine.innerHTML += `<span style="color: ${color}; margin-left: 8px;">[${statusText}]</span>`;
    } catch (e) {
      this.printError(`permission response failed: ${e.message}`);
      permLine.querySelectorAll('button').forEach(btn => btn.disabled = false);
    }
  }

  printPermissionGranted(toolId, scope) {
    this.printSystem(`permission granted for ${toolId} [${scope}]`);
  }

  printPermissionDenied(toolId) {
    this.printWarning(`permission denied for ${toolId}`);
  }

  printQuestion(question) {
    const line = document.createElement('div');
    line.className = 'output-line permission';
    line.innerHTML = `<div style="color: var(--warning)">[question] ${this.escapeHtml(question)}</div>`;
    this.container.appendChild(line);
    this.scrollToBottom();
  }

  scrollToBottom() {
    if (this.autoScroll) {
      this.container.scrollTop = this.container.scrollHeight;
    }
  }

  escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  focus() {
    this.input.focus();
  }

  boot() {
    this.printSystem(`session: ${this.id}`);
    this.printSystem('type /help for commands');
    this.printLine('');
  }
}

// ============================================================================
// Terminal Manager - Multi-panel orchestrator
// ============================================================================

class TerminalManager {
  constructor() {
    this.api = new EngineAPI();
    this.sessions = new Map();
    this.sessionIdToTerminal = new Map();
    this.activePanelId = null;
    this.isConnected = false;
    this.healthCheckInterval = null;

    // DOM elements
    this.sessionsContainer = document.getElementById('sessions-container');
    this.statusBar = document.querySelector('.status-bar');
    this.engineStatus = document.getElementById('status-engine');
    this.streamStatus = document.getElementById('status-stream');
    this.sessionCount = document.getElementById('sessions-count');
    this.modalOverlay = document.getElementById('modal-overlay');
    this.modal = document.querySelector('.modal');
    this.modalTitle = document.getElementById('modal-title');
    this.modalBody = document.getElementById('modal-body');
    this.modalClose = document.getElementById('modal-close');

    this.init();
  }

  init() {
    // Modal close
    this.modalClose.addEventListener('click', () => this.closeModal());
    this.modalOverlay.addEventListener('click', (e) => {
      if (e.target === this.modalOverlay) this.closeModal();
    });

    // Toolbar buttons
    document.getElementById('btn-new-session').addEventListener('click', () => this.showNewSessionModal());
    document.getElementById('hub-btn').addEventListener('click', () => this.showHubModal());
    document.getElementById('profiles-btn').addEventListener('click', () => this.showProfilesModal());
    document.getElementById('mcp-btn').addEventListener('click', () => this.showMCPModal());
    document.getElementById('settings-btn').addEventListener('click', () => this.showSettingsModal());

    // Config panel (right panel toggle)
    document.getElementById('config-toggle')?.addEventListener('click', () => {
      document.getElementById('right-panel')?.classList.toggle('open');
    });

    // Load token and config
    this.loadConfig();
    this.loadToken();

    // Initial check
    this.bootSequence();

    // Network status
    window.addEventListener('offline', () => this.handleOffline());
    window.addEventListener('online', () => this.handleOnline());

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => this.handleGlobalKeydown(e));
  }

  handleGlobalKeydown(e) {
    // Ctrl/Cmd + N: New session
    if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
      e.preventDefault();
      this.showNewSessionModal();
    }
    // Escape: Close modal
    if (e.key === 'Escape') {
      this.closeModal();
    }
    // Ctrl/Cmd + 1-9: Switch to panel N
    if ((e.ctrlKey || e.metaKey) && e.key >= '1' && e.key <= '9') {
      e.preventDefault();
      const idx = parseInt(e.key) - 1;
      const panels = Array.from(this.sessions.values());
      if (panels[idx]) {
        panels[idx].focus();
      }
    }
  }

  loadToken() {
    const stored = localStorage.getItem('kollabor_token');
    if (stored) {
      this.api.setToken(stored);
    }
  }

  loadConfig() {
    const saved = localStorage.getItem('kollabor_config');
    if (saved) {
      const config = JSON.parse(saved);
      if (config.engineUrl) {
        this.api.setBaseUrl(config.engineUrl);
        document.getElementById('engine-url').value = config.engineUrl;
      }
      if (config.profile) {
        document.getElementById('profile').value = config.profile;
      }
      if (config.approvalMode) {
        document.getElementById('approval-mode').value = config.approvalMode;
      }
      if (config.workspace) {
        document.getElementById('workspace').value = config.workspace;
      }
    }

    // Listen for config changes (elements may not exist until modal opens)
    ['engine-url', 'profile', 'approval-mode', 'workspace'].forEach(id => {
      document.getElementById(id)?.addEventListener('change', () => this.saveConfig());
    });
  }

  saveConfig() {
    const config = {
      engineUrl: document.getElementById('engine-url').value,
      profile: document.getElementById('profile').value,
      approvalMode: document.getElementById('approval-mode').value,
      workspace: document.getElementById('workspace').value,
    };
    localStorage.setItem('kollabor_config', JSON.stringify(config));
    this.api.setBaseUrl(config.engineUrl);
  }

  async bootSequence() {
    this.printGlobal('[sys] initializing kollabor terminal...');
    await this.sleep(200);
    this.printGlobal('[sys] loading neural interface modules...');
    await this.sleep(150);

    await this.checkEngineStatus();

    if (this.isConnected) {
      await this.loadExistingSessions();
    }

    this.startHealthCheck();
    this.printGlobal('[sys] ready.');
  }

  async checkEngineStatus() {
    try {
      const data = await this.api.health();
      this.setConnected(true);
      this.printGlobal(`[ok] engine online [uptime: ${data.uptime}s]`);
    } catch (e) {
      this.setConnected(false);
      this.printGlobal('[err] engine not responding');
      this.printGlobal('[sys] start engine with: kollabor-engine serve');
    }
  }

  async loadExistingSessions() {
    try {
      const data = await this.api.listSessions();
      const sessions = data.sessions || [];

      if (sessions.length > 0) {
        this.printGlobal(`[sys] found ${sessions.length} existing session(s)`);

        // Auto-restore first session
        const first = sessions[sessions.length - 1];
        await this.restoreSession(first.session_id);
      } else {
        this.printGlobal('[sys] no active sessions - click + to create one');
      }

      this.updateSessionCount();
    } catch (e) {
      this.printGlobal(`[err] failed to load sessions: ${e.message}`);
    }
  }

  setConnected(connected) {
    this.isConnected = connected;
    if (this.engineStatus) {
      this.engineStatus.className = `status-dot ${connected ? 'active' : ''}`;
      this.engineStatus.title = connected ? 'Engine connected' : 'Engine disconnected';
    }
    const dot = document.getElementById('global-connection-dot');
    const label = document.getElementById('global-connection-status');
    if (dot) dot.className = `connection-dot ${connected ? 'active' : ''}`;
    if (label) label.textContent = connected ? 'connected' : 'disconnected';
  }

  startHealthCheck() {
    this.healthCheckInterval = setInterval(async () => {
      if (!this.isAnyStreaming()) {
        try {
          await this.api.health();
          if (!this.isConnected) {
            this.setConnected(true);
            this.printGlobal('[ok] engine reconnected');
          }
        } catch (e) {
          if (this.isConnected) {
            this.setConnected(false);
            this.printGlobal('[warn] engine disconnected');
          }
        }
      }
    }, 30000);
  }

  isAnyStreaming() {
    for (const terminal of this.sessions.values()) {
      if (terminal.isStreaming) return true;
    }
    return false;
  }

  handleOffline() {
    this.printGlobal('[warn] network disconnected');
    this.setConnected(false);
  }

  handleOnline() {
    this.printGlobal('[ok] network restored');
    this.checkEngineStatus();
  }

  updateSessionCount() {
    this.sessionCount.textContent = this.sessions.size;
  }

  // Session management
  createPanel(sessionId) {
    const panel = document.createElement('div');
    panel.className = 'session-panel';
    panel.id = `panel-${sessionId}`;
    panel.innerHTML = `
      <div class="panel-header">
        <div class="panel-header-left">
          <span class="session-title">${sessionId.substring(0, 12)}</span>
          <span class="session-status">READY</span>
          <span class="token-count" title="Token count">0</span>
        </div>
        <div class="panel-header-right">
          <button class="panel-btn popout-btn" title="Pop out">⤢</button>
          <button class="panel-btn minimize-btn" title="Minimize">−</button>
          <button class="panel-btn close-btn" title="Close">×</button>
        </div>
      </div>
      <div class="terminal-output"></div>
      <div class="input-area">
        <div class="input-wrapper">
          <span class="input-prompt">></span>
          <textarea class="terminal-input" placeholder="type your message..." rows="1"></textarea>
        </div>
        <button class="send-btn">SEND</button>
      </div>
    `;

    this.sessionsContainer.appendChild(panel);

    const terminal = new TerminalSession(sessionId, panel, this.api, this);
    this.sessions.set(sessionId, terminal);
    this.sessionIdToTerminal.set(sessionId, sessionId);

    this.updateLayout();
    this.updateSessionCount();

    return terminal;
  }

  updateLayout() {
    const count = this.sessions.size;
    this.sessionsContainer.className = `sessions-container layout-${Math.min(count, 6)}`;
  }

  async createSession(config = {}) {
    if (!this.ensureConnected()) return null;

    try {
      const data = await this.api.createSession({
        profile: document.getElementById('profile').value || 'default',
        approvalMode: document.getElementById('approval-mode').value || 'confirm_all',
        workspace: document.getElementById('workspace').value || undefined,
        ...config,
      });

      const sessionId = data.session_id || data.id;
      const terminal = this.createPanel(sessionId);
      terminal.boot();

      this.printGlobal(`[ok] session created: ${sessionId.substring(0, 12)}`);

      return terminal;
    } catch (e) {
      this.printGlobal(`[err] failed to create session: ${e.message}`);
      return null;
    }
  }

  async restoreSession(sessionId) {
    const terminal = this.createPanel(sessionId);
    terminal.boot();
    return terminal;
  }

  async closeSession(sessionId) {
    const terminal = this.sessions.get(sessionId);
    if (!terminal) return;

    if (terminal.isStreaming) {
      this.printGlobal('[warn] cannot close session while streaming');
      return;
    }

    try {
      await this.api.deleteSession(sessionId);
    } catch (e) {
      // Ignore
    }

    terminal.panel.remove();
    this.sessions.delete(sessionId);
    this.sessionIdToTerminal.delete(sessionId);

    this.updateLayout();
    this.updateSessionCount();

    this.printGlobal(`[sys] session closed: ${sessionId.substring(0, 12)}`);
  }

  popoutSession(sessionId) {
    const terminal = this.sessions.get(sessionId);
    if (!terminal) return;

    // Open in new window
    const win = window.open('', `_blank_${sessionId}`, 'width=800,height=600');
    win.document.write(`
      <!DOCTYPE html>
      <html>
      <head>
        <title>kollabor - ${sessionId.substring(0, 12)}</title>
        <style>
          body { margin: 0; background: #0a0a0a; color: #00ff41; font-family: monospace; }
          .output { padding: 16px; height: calc(100vh - 60px); overflow-y: auto; }
          .input-area { position: fixed; bottom: 0; left: 0; right: 0; background: #0d0d0d; padding: 12px; border-top: 1px solid #1a1a1a; }
          input { width: 100%; background: #111; border: 1px solid #1a1a1a; color: #00ff41; padding: 8px; font-family: monospace; }
        </style>
      </head>
      <body>
        <div class="output" id="output"></div>
        <div class="input-area">
          <input type="text" id="input" placeholder="Disconnected from session..." disabled>
        </div>
        <script>
          document.getElementById('output').innerHTML = ${JSON.stringify(terminal.container.innerHTML)};
          document.title = 'kollabor - ${sessionId.substring(0, 12)} (disconnected)';
        </script>
      </body>
      </html>
    `);

    this.printGlobal(`[sys] session popped out: ${sessionId.substring(0, 12)}`);
  }

  ensureConnected() {
    if (!this.api.token) {
      this.printGlobal('[err] no auth token - set token in settings');
      return false;
    }
    if (!this.isConnected) {
      this.printGlobal('[err] not connected to engine - check /status');
      return false;
    }
    return true;
  }

  printGlobal(text) {
    // Print to all terminals
    for (const terminal of this.sessions.values()) {
      terminal.printSystem(text.replace(/^\[(sys|ok|err|warn)\]\s*/, (m, type) => {
        if (type === 'ok') return '[ok] ';
        if (type === 'err') return '[err] ';
        if (type === 'warn') return '[warn] ';
        return '[sys] ';
      }));
    }
  }

  // Modals
  openModal(title, content) {
    this.modalTitle.textContent = title;
    this.modalBody.innerHTML = content;
    this.modalOverlay.classList.add('open');
  }

  closeModal() {
    this.modalOverlay.classList.remove('open');
  }

  async showNewSessionModal() {
    this.openModal('New Session', `
      <div class="form-group">
        <label>Profile</label>
        <select id="new-session-profile">
          <option value="default">default</option>
        </select>
      </div>
      <div class="form-group">
        <label>Approval Mode</label>
        <select id="new-session-mode">
          <option value="confirm_all">confirm_all</option>
          <option value="default">default</option>
          <option value="auto_approve_edits">auto_approve_edits</option>
          <option value="trust_all">trust_all</option>
        </select>
      </div>
      <div class="form-group">
        <label>Workspace (optional)</label>
        <input type="text" id="new-session-workspace" placeholder="/path/to/project">
      </div>
      <div class="form-group">
        <label>System Prompt (optional)</label>
        <textarea id="new-session-prompt" rows="3" placeholder="Custom system prompt..."></textarea>
      </div>
      <div class="form-group">
        <label>API Key (optional, for inline credentials)</label>
        <input type="password" id="new-session-apikey" placeholder="sk-...">
      </div>
      <div class="form-group">
        <label>Model (if API key set)</label>
        <input type="text" id="new-session-model" placeholder="claude-sonnet-4-6">
      </div>
      <div class="form-actions">
        <button class="btn" id="new-session-cancel">Cancel</button>
        <button class="btn btn-primary" id="new-session-create">Create</button>
      </div>
    `);

    // Load profiles
    try {
      const data = await this.api.listProfiles();
      const select = document.getElementById('new-session-profile');
      data.profiles.forEach(p => {
        const opt = document.createElement('option');
        opt.value = p.name;
        opt.textContent = p.name;
        select.appendChild(opt);
      });
    } catch (e) {
      // Ignore
    }

    document.getElementById('new-session-cancel').addEventListener('click', () => this.closeModal());
    document.getElementById('new-session-create').addEventListener('click', async () => {
      const config = {
        profile: document.getElementById('new-session-profile').value,
        approvalMode: document.getElementById('new-session-mode').value,
        workspace: document.getElementById('new-session-workspace').value || undefined,
        systemPrompt: document.getElementById('new-session-prompt').value || undefined,
      };

      const apiKey = document.getElementById('new-session-apikey').value;
      if (apiKey) {
        config.credentials = {
          provider: 'anthropic',
          api_key: apiKey,
          model: document.getElementById('new-session-model').value || 'claude-sonnet-4-6',
        };
      }

      this.closeModal();
      await this.createSession(config);
    });
  }

  async showProfilesModal() {
    try {
      const data = await this.api.listProfiles();
      const profiles = data.profiles || [];

      let html = `
        <div class="profiles-list">
          ${profiles.map(p => `
            <div class="profile-item">
              <div class="profile-info">
                <span class="profile-name">${p.name}</span>
                <span class="profile-provider">${p.provider}</span>
                <span class="profile-model">${p.model}</span>
              </div>
              <div class="profile-actions">
                <button class="btn btn-small" data-profile="${p.name}" data-action="test">Test</button>
                <button class="btn btn-small btn-danger" data-profile="${p.name}" data-action="delete">Delete</button>
              </div>
            </div>
          `).join('')}
        </div>
        <div class="form-actions">
          <button class="btn btn-primary" id="create-profile-btn">Create Profile</button>
        </div>
      `;

      this.openModal('Profiles', html);

      // Event delegation for profile actions
      document.querySelector('.profiles-list').addEventListener('click', async (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;

        const profile = btn.dataset.profile;
        const action = btn.dataset.action;

        if (action === 'test') {
          btn.disabled = true;
          btn.textContent = 'Testing...';
          try {
            const result = await this.api.testProfile(profile);
            if (result.success) {
              btn.textContent = `✓ ${result.latency_ms}ms`;
              btn.classList.remove('btn-danger');
              btn.classList.add('btn-success');
            } else {
              btn.textContent = `✗ ${result.error}`;
              btn.classList.add('btn-danger');
            }
          } catch (err) {
            btn.textContent = `✗ ${err.message}`;
            btn.classList.add('btn-danger');
          }
          setTimeout(() => {
            btn.disabled = false;
            btn.textContent = 'Test';
            btn.classList.remove('btn-success', 'btn-danger');
          }, 3000);
        } else if (action === 'delete') {
          if (confirm(`Delete profile "${profile}"?`)) {
            try {
              await this.api.deleteProfile(profile);
              this.showProfilesModal(); // Refresh
            } catch (err) {
              alert(`Failed to delete: ${err.message}`);
            }
          }
        }
      });

      document.getElementById('create-profile-btn').addEventListener('click', () => {
        this.showCreateProfileModal();
      });

    } catch (e) {
      this.openModal('Profiles', `<p class="error">Failed to load profiles: ${e.message}</p>`);
    }
  }

  showCreateProfileModal() {
    this.openModal('Create Profile', `
      <div class="form-group">
        <label>Name</label>
        <input type="text" id="profile-name" placeholder="my-profile">
      </div>
      <div class="form-group">
        <label>Provider</label>
        <select id="profile-provider">
          <option value="anthropic">anthropic</option>
          <option value="openai">openai</option>
          <option value="custom">custom</option>
        </select>
      </div>
      <div class="form-group">
        <label>Model</label>
        <input type="text" id="profile-model" placeholder="claude-sonnet-4-6">
      </div>
      <div class="form-group">
        <label>API Key</label>
        <input type="password" id="profile-apikey" placeholder="sk-...">
      </div>
      <div class="form-group">
        <label>Base URL (optional)</label>
        <input type="text" id="profile-baseurl" placeholder="https://api.anthropic.com">
      </div>
      <div class="form-group">
        <label>Temperature</label>
        <input type="number" id="profile-temp" value="0.7" min="0" max="2" step="0.1">
      </div>
      <div class="form-group">
        <label>Max Tokens</label>
        <input type="number" id="profile-maxtokens" placeholder="4096">
      </div>
      <div class="form-actions">
        <button class="btn" id="profile-cancel">Cancel</button>
        <button class="btn btn-primary" id="profile-create">Create</button>
      </div>
    `);

    document.getElementById('profile-cancel').addEventListener('click', () => this.showProfilesModal());
    document.getElementById('profile-create').addEventListener('click', async () => {
      try {
        await this.api.createProfile({
          name: document.getElementById('profile-name').value,
          provider: document.getElementById('profile-provider').value,
          model: document.getElementById('profile-model').value,
          api_key: document.getElementById('profile-apikey').value,
          base_url: document.getElementById('profile-baseurl').value,
          temperature: parseFloat(document.getElementById('profile-temp').value),
          max_tokens: document.getElementById('profile-maxtokens').value ? parseInt(document.getElementById('profile-maxtokens').value) : null,
        });
        this.showProfilesModal(); // Refresh
      } catch (e) {
        alert(`Failed to create profile: ${e.message}`);
      }
    });
  }

  async showMCPModal() {
    try {
      const data = await this.api.listMCPServers();
      const servers = data.servers || {};

      let html = `
        <div class="mcp-list">
          ${Object.entries(servers).map(([name, config]) => `
            <div class="mcp-item">
              <div class="mcp-info">
                <span class="mcp-name">${name}</span>
                <span class="mcp-command">${config.command || 'N/A'}</span>
                <span class="mcp-status ${config.enabled ? 'enabled' : 'disabled'}">${config.enabled ? 'enabled' : 'disabled'}</span>
              </div>
              <div class="mcp-actions">
                <button class="btn btn-small" data-server="${name}" data-action="toggle">${config.enabled ? 'Disable' : 'Enable'}</button>
                <button class="btn btn-small btn-danger" data-server="${name}" data-action="delete">Delete</button>
              </div>
            </div>
          `).join('') || '<p>No MCP servers configured</p>'}
        </div>
        <div class="form-actions">
          <button class="btn btn-primary" id="add-mcp-btn">Add Server</button>
        </div>
      `;

      this.openModal('MCP Servers', html);

      // Event delegation
      const list = document.querySelector('.mcp-list');
      if (list) {
        list.addEventListener('click', async (e) => {
          const btn = e.target.closest('button');
          if (!btn) return;

          const server = btn.dataset.server;
          const action = btn.dataset.action;

          if (action === 'toggle') {
            try {
              const current = servers[server];
              await this.api.updateMCPServer(server, {
                ...current,
                enabled: !current.enabled,
              });
              this.showMCPModal(); // Refresh
            } catch (err) {
              alert(`Failed to toggle: ${err.message}`);
            }
          } else if (action === 'delete') {
            if (confirm(`Delete MCP server "${server}"?`)) {
              try {
                await this.api.removeMCPServer(server);
                this.showMCPModal(); // Refresh
              } catch (err) {
                alert(`Failed to delete: ${err.message}`);
              }
            }
          }
        });
      }

      document.getElementById('add-mcp-btn').addEventListener('click', () => {
        this.showAddMCPModal();
      });

    } catch (e) {
      this.openModal('MCP Servers', `<p class="error">Failed to load MCP servers: ${e.message}</p>`);
    }
  }

  showAddMCPModal() {
    this.openModal('Add MCP Server', `
      <div class="form-group">
        <label>Name</label>
        <input type="text" id="mcp-name" placeholder="my-server">
      </div>
      <div class="form-group">
        <label>Command</label>
        <input type="text" id="mcp-command" placeholder="npx -y @modelcontextprotocol/server-filesystem /path">
      </div>
      <div class="form-group">
        <label>Description (optional)</label>
        <input type="text" id="mcp-description" placeholder="Filesystem access server">
      </div>
      <div class="form-group">
        <label>Enabled</label>
        <input type="checkbox" id="mcp-enabled" checked>
      </div>
      <div class="form-actions">
        <button class="btn" id="mcp-cancel">Cancel</button>
        <button class="btn btn-primary" id="mcp-add">Add</button>
      </div>
    `);

    document.getElementById('mcp-cancel').addEventListener('click', () => this.showMCPModal());
    document.getElementById('mcp-add').addEventListener('click', async () => {
      try {
        await this.api.addMCPServer({
          name: document.getElementById('mcp-name').value,
          command: document.getElementById('mcp-command').value,
          description: document.getElementById('mcp-description').value,
          enabled: document.getElementById('mcp-enabled').checked,
        });
        this.showMCPModal(); // Refresh
      } catch (e) {
        alert(`Failed to add server: ${e.message}`);
      }
    });
  }

  showSettingsModal() {
    this.openModal('Settings', `
      <div class="form-group">
        <label>Engine URL</label>
        <input type="text" id="settings-engine-url" value="${this.api.baseUrl}">
      </div>
      <div class="form-group">
        <label>Auth Token</label>
        <input type="password" id="settings-token" value="${this.api.token || ''}" placeholder="Enter token...">
        <small>Token is stored in localStorage</small>
      </div>
      <div class="form-group">
        <label>Theme</label>
        <select id="settings-theme">
          <option value="hacker">Hacker (Green)</option>
          <option value="amber">Amber</option>
          <option value="matrix">Matrix</option>
          <option value="dracula">Dracula</option>
        </select>
      </div>
      <div class="form-actions">
        <button class="btn btn-danger" id="settings-clear">Clear All Data</button>
        <button class="btn btn-primary" id="settings-save">Save</button>
      </div>
    `);

    document.getElementById('settings-save').addEventListener('click', () => {
      const engineUrl = document.getElementById('settings-engine-url').value;
      const token = document.getElementById('settings-token').value;
      const theme = document.getElementById('settings-theme').value;

      this.api.setBaseUrl(engineUrl);
      this.api.setToken(token);
      localStorage.setItem('kollabor_token', token);
      this.saveConfig();

      document.body.dataset.theme = theme;
      localStorage.setItem('kollabor_theme', theme);

      this.closeModal();
      this.checkEngineStatus();
    });

    document.getElementById('settings-clear').addEventListener('click', () => {
      if (confirm('Clear all local data (token, config, sessions)?')) {
        localStorage.clear();
        location.reload();
      }
    });

    // Load saved theme
    const savedTheme = localStorage.getItem('kollabor_theme') || 'hacker';
    document.getElementById('settings-theme').value = savedTheme;
  }

  async showHubModal(refresh = false) {
    try {
      const data = await this.api.hubAgents(refresh);
      const agents = data.agents || [];

      const stateColor = (s) => {
        if (s === 'working') return 'var(--accent)';
        if (s === 'waiting') return 'var(--warning)';
        return 'var(--text-dim)';
      };

      const agentRows = agents.map(a => `
        <div class="hub-agent-row" data-id="${a.agent_id}" data-identity="${a.identity || ''}">
          <div class="hub-agent-info">
            <span class="hub-agent-identity" style="color: var(--accent)">${a.identity || a.agent_id}</span>
            <span class="hub-agent-state" style="color: ${stateColor(a.state)}">${a.state || 'unknown'}</span>
            ${a.current_task ? `<span class="hub-agent-task" style="color: var(--text-dim); font-size: 11px; display: block; margin-top: 2px;">${a.current_task}</span>` : ''}
          </div>
          <div class="hub-agent-actions">
            <button class="btn btn-small" data-action="msg" data-identity="${a.identity || a.agent_id}">MSG</button>
            <button class="btn btn-small" data-action="status" data-id="${a.agent_id}">STATUS</button>
          </div>
        </div>
      `).join('');

      const html = `
        <div style="margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center;">
          <span style="color: var(--text-dim); font-size: 11px;">${agents.length} agent${agents.length !== 1 ? 's' : ''} online</span>
          <button class="btn btn-small" id="hub-refresh-btn">REFRESH</button>
        </div>
        <div class="hub-agent-list">
          ${agentRows || '<p style="color: var(--text-dim)">No agents online</p>'}
        </div>
        <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border);">
          <button class="btn btn-primary" id="hub-broadcast-btn">BROADCAST TO ALL</button>
        </div>
      `;

      this.openModal('Hub Agents', html);

      document.getElementById('hub-refresh-btn').addEventListener('click', () => {
        this.closeModal();
        this.showHubModal(true);
      });

      document.getElementById('hub-broadcast-btn').addEventListener('click', () => {
        this.showHubSendModal('*');
      });

      document.querySelector('.hub-agent-list')?.addEventListener('click', (e) => {
        const btn = e.target.closest('button');
        if (!btn) return;
        const action = btn.dataset.action;
        if (action === 'msg') {
          const identity = btn.dataset.identity;
          this.showHubSendModal(identity);
        } else if (action === 'status') {
          const agentId = btn.dataset.id;
          this.showHubStatusModal(agentId);
        }
      });

    } catch (e) {
      this.openModal('Hub Agents', `<p style="color: var(--error)">Failed to load hub agents: ${e.message}</p>`);
    }
  }

  showHubSendModal(target) {
    const isAll = target === '*';
    const label = isAll ? 'all agents (broadcast)' : target;
    this.openModal(`Send to ${label}`, `
      <div class="form-group">
        <label>To</label>
        <input type="text" id="hub-send-target" value="${target}" ${isAll ? '' : 'readonly'}>
      </div>
      <div class="form-group">
        <label>Message</label>
        <textarea id="hub-send-content" rows="4" placeholder="Message to agent..." style="width: 100%; background: var(--bg-tertiary); color: var(--text-primary); border: 1px solid var(--border); padding: 8px; font-family: inherit; resize: vertical;"></textarea>
      </div>
      <div class="form-actions">
        <button class="btn" id="hub-send-back">Back</button>
        <button class="btn btn-primary" id="hub-send-submit">Send</button>
      </div>
    `);

    document.getElementById('hub-send-back').addEventListener('click', () => {
      this.closeModal();
      this.showHubModal();
    });

    document.getElementById('hub-send-submit').addEventListener('click', async () => {
      const to = document.getElementById('hub-send-target').value.trim();
      const content = document.getElementById('hub-send-content').value.trim();
      if (!content) return;

      try {
        await this.api.hubSendMessage(to, content);
        this.closeModal();
        this.showNotification(`Message sent to ${to}`);
      } catch (e) {
        this.showNotification(`Failed: ${e.message}`, 'error');
      }
    });
  }

  async showHubStatusModal(agentId) {
    try {
      const data = await this.api.hubAgentStatus(agentId);
      const status = data.status || {};
      const html = `
        <pre style="color: var(--text-primary); font-size: 12px; white-space: pre-wrap;">${JSON.stringify(status, null, 2)}</pre>
        <div class="form-actions">
          <button class="btn" id="hub-status-back">Back</button>
        </div>
      `;
      this.openModal(`Agent Status: ${agentId}`, html);
      document.getElementById('hub-status-back').addEventListener('click', () => {
        this.closeModal();
        this.showHubModal();
      });
    } catch (e) {
      this.openModal('Agent Status', `<p style="color: var(--error)">Failed: ${e.message}</p>`);
    }
  }

  showNotification(msg, type = 'info') {
    // Reuse status bar or create ephemeral notification
    const bar = document.querySelector('.status-bar .status-left');
    if (!bar) return;
    const el = document.createElement('div');
    el.className = 'status-item';
    el.style.color = type === 'error' ? 'var(--error)' : 'var(--accent)';
    el.textContent = msg;
    bar.appendChild(el);
    setTimeout(() => el.remove(), 3000);
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

// ============================================================================
// Initialize
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
  window.terminalManager = new TerminalManager();
});
