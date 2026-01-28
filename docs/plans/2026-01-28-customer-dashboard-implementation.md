# Customer Dashboard - Site Health Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add self-service container status, restart, and logs to customer dashboard to reduce support tickets.

**Architecture:** Three new API endpoints in app.py for container operations, new "Site Health" card in dashboard template with JavaScript for real-time updates, Grafana embeds for metrics.

**Tech Stack:** Python/Flask, Docker CLI via subprocess, Jinja2 templates, vanilla JavaScript

---

## Task 1: Add Container Status API Endpoint

**Files:**
- Modify: `webapp/app.py`

**Step 1: Add the endpoint after existing `/api/status` route (~line 903)**

```python
@app.route('/api/container/status')
@login_required
def api_container_status():
    """Get container status for current customer"""
    import subprocess

    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        return jsonify({'error': 'Store not active'}), 400

    # Determine container name based on platform
    if customer.platform == 'magento':
        container_name = f"customer-{customer.id}-magento"
    else:
        container_name = f"customer-{customer.id}-wordpress"

    try:
        # Get container status
        result = subprocess.run(
            ['docker', 'inspect', container_name, '--format',
             '{{.State.Status}} {{.State.Running}} {{.State.StartedAt}}'],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode != 0:
            return jsonify({
                'status': 'unknown',
                'running': False,
                'uptime': None,
                'message': 'Container not found'
            })

        parts = result.stdout.strip().split()
        status = parts[0] if parts else 'unknown'
        running = parts[1].lower() == 'true' if len(parts) > 1 else False
        started_at = parts[2] if len(parts) > 2 else None

        # Calculate uptime
        uptime_str = None
        if started_at and running:
            from datetime import datetime
            try:
                # Docker returns ISO format with timezone
                started = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                now = datetime.now(started.tzinfo)
                delta = now - started
                days = delta.days
                hours, remainder = divmod(delta.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                uptime_str = f"{days}d {hours}h {minutes}m"
            except:
                uptime_str = "unknown"

        return jsonify({
            'status': status,
            'running': running,
            'uptime': uptime_str,
            'container_name': container_name
        })

    except subprocess.TimeoutExpired:
        return jsonify({'status': 'timeout', 'running': False, 'uptime': None}), 504
    except Exception as e:
        return jsonify({'status': 'error', 'running': False, 'message': str(e)}), 500
```

**Step 2: Verify syntax**

```bash
python3 -m py_compile webapp/app.py && echo "Syntax OK"
```

**Step 3: Commit**

```bash
git add webapp/app.py
git commit -m "feat(api): add container status endpoint"
```

---

## Task 2: Add Container Restart API Endpoint

**Files:**
- Modify: `webapp/app.py`

**Step 1: Add restart endpoint with rate limiting**

```python
@app.route('/api/container/restart', methods=['POST'])
@login_required
@limiter.limit("1 per 5 minutes", error_message="Please wait 5 minutes between restarts.")
@csrf.exempt
def api_container_restart():
    """Restart container for current customer"""
    import subprocess

    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        return jsonify({'error': 'Store not active'}), 400

    # Determine container name based on platform
    if customer.platform == 'magento':
        container_name = f"customer-{customer.id}-magento"
    else:
        container_name = f"customer-{customer.id}-wordpress"

    try:
        # Restart the container
        result = subprocess.run(
            ['docker', 'restart', container_name],
            capture_output=True, text=True, timeout=60
        )

        if result.returncode != 0:
            return jsonify({
                'success': False,
                'message': f'Restart failed: {result.stderr}'
            }), 500

        return jsonify({
            'success': True,
            'message': 'Container restart initiated. Your store will be back online in ~30 seconds.'
        })

    except subprocess.TimeoutExpired:
        return jsonify({
            'success': False,
            'message': 'Restart timed out. Please try again or contact support.'
        }), 504
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Error: {str(e)}'
        }), 500
```

**Step 2: Verify syntax**

```bash
python3 -m py_compile webapp/app.py && echo "Syntax OK"
```

**Step 3: Commit**

```bash
git add webapp/app.py
git commit -m "feat(api): add container restart endpoint with rate limiting"
```

---

## Task 3: Add Container Logs API Endpoint

**Files:**
- Modify: `webapp/app.py`

**Step 1: Add logs endpoint with sanitization**

```python
@app.route('/api/container/logs')
@login_required
@limiter.limit("30 per minute")
def api_container_logs():
    """Get recent container logs for current customer"""
    import subprocess
    import re

    customer = Customer.get_by_id(current_user.id)

    if customer.status != 'active':
        return jsonify({'error': 'Store not active'}), 400

    # Determine container name based on platform
    if customer.platform == 'magento':
        container_name = f"customer-{customer.id}-magento"
    else:
        container_name = f"customer-{customer.id}-wordpress"

    lines = request.args.get('lines', 50, type=int)
    lines = min(lines, 100)  # Cap at 100 lines

    try:
        result = subprocess.run(
            ['docker', 'logs', container_name, '--tail', str(lines), '--timestamps'],
            capture_output=True, text=True, timeout=30
        )

        # Combine stdout and stderr (logs can be in either)
        logs = result.stdout + result.stderr

        # Sanitize sensitive data
        patterns_to_redact = [
            (r'password["\s:=]+[^\s"]+', 'password=***REDACTED***'),
            (r'api[_-]?key["\s:=]+[^\s"]+', 'api_key=***REDACTED***'),
            (r'secret["\s:=]+[^\s"]+', 'secret=***REDACTED***'),
            (r'token["\s:=]+[^\s"]+', 'token=***REDACTED***'),
            (r'Authorization:\s*\S+', 'Authorization: ***REDACTED***'),
        ]

        for pattern, replacement in patterns_to_redact:
            logs = re.sub(pattern, replacement, logs, flags=re.IGNORECASE)

        # Split into lines and return
        log_lines = logs.strip().split('\n') if logs.strip() else []

        return jsonify({
            'logs': log_lines,
            'container_name': container_name,
            'line_count': len(log_lines)
        })

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Log retrieval timed out'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

**Step 2: Verify syntax**

```bash
python3 -m py_compile webapp/app.py && echo "Syntax OK"
```

**Step 3: Commit**

```bash
git add webapp/app.py
git commit -m "feat(api): add container logs endpoint with sanitization"
```

---

## Task 4: Add Site Health CSS Styles

**Files:**
- Modify: `webapp/templates/dashboard.html`

**Step 1: Add styles to the `<style>` section (after resource usage styles, before `@media`)**

```css
/* Site Health */
.site-health-card {
    margin-top: 24px;
}

.health-status-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 20px 28px;
    border-bottom: 1px solid var(--border-subtle);
}

.status-info {
    display: flex;
    align-items: center;
    gap: 16px;
}

.status-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    border-radius: 20px;
    font-size: 0.875rem;
    font-weight: 600;
}

.status-badge.running {
    background: rgba(34, 197, 94, 0.15);
    color: var(--success);
}

.status-badge.stopped {
    background: rgba(239, 68, 68, 0.15);
    color: var(--error);
}

.status-badge.restarting {
    background: rgba(245, 158, 11, 0.15);
    color: var(--warning);
}

.status-badge.unknown {
    background: rgba(156, 163, 175, 0.15);
    color: var(--text-secondary);
}

.status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
}

.uptime-text {
    color: var(--text-secondary);
    font-size: 0.9rem;
}

.restart-btn {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 20px;
    background: var(--bg-surface);
    border: 1px solid var(--border-default);
    border-radius: var(--radius-md);
    color: var(--text-primary);
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
}

.restart-btn:hover:not(:disabled) {
    background: var(--bg-elevated);
    border-color: var(--border-accent);
}

.restart-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.restart-btn.loading {
    color: var(--warning);
}

.health-metrics {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    padding: 20px 28px;
    border-bottom: 1px solid var(--border-subtle);
}

.health-metrics iframe {
    border-radius: var(--radius-md);
    background: var(--bg-surface);
}

.logs-section {
    padding: 0;
}

.logs-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 28px;
    border-bottom: 1px solid var(--border-subtle);
    cursor: pointer;
}

.logs-header:hover {
    background: var(--bg-surface);
}

.logs-title {
    font-size: 0.95rem;
    font-weight: 500;
    color: var(--text-secondary);
}

.logs-actions {
    display: flex;
    gap: 8px;
    align-items: center;
}

.logs-toggle {
    color: var(--text-muted);
    transition: transform 0.2s ease;
}

.logs-toggle.expanded {
    transform: rotate(180deg);
}

.logs-content {
    max-height: 0;
    overflow: hidden;
    transition: max-height 0.3s ease;
}

.logs-content.expanded {
    max-height: 400px;
    overflow-y: auto;
}

.logs-list {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    padding: 16px 28px;
    background: var(--bg-deep);
    margin: 0;
    white-space: pre-wrap;
    word-break: break-all;
}

.log-line {
    padding: 2px 0;
    border-bottom: 1px solid var(--border-subtle);
}

.log-line:last-child {
    border-bottom: none;
}

.logs-empty {
    padding: 20px 28px;
    color: var(--success);
    text-align: center;
}

.help-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--bg-surface);
    color: var(--text-muted);
    font-size: 0.7rem;
    cursor: help;
    margin-left: 6px;
}

.help-icon:hover {
    background: var(--bg-elevated);
    color: var(--text-secondary);
}

@media (max-width: 640px) {
    .health-status-row {
        flex-direction: column;
        gap: 16px;
        align-items: flex-start;
    }

    .health-metrics {
        grid-template-columns: 1fr;
    }
}
```

**Step 2: Commit**

```bash
git add webapp/templates/dashboard.html
git commit -m "feat(dashboard): add site health CSS styles"
```

---

## Task 5: Add Site Health HTML Card

**Files:**
- Modify: `webapp/templates/dashboard.html`

**Step 1: Add the Site Health card after credentials card (after line 594, before Resource Usage)**

Find this line:
```html
        </div>

        <!-- Resource Usage -->
```

Insert before it:

```html
        <!-- Site Health -->
        <div class="info-card site-health-card">
            <div class="info-card-header">
                <h2 class="info-card-title">
                    Site Health
                    <span class="help-icon" title="Monitor your store's server status and restart if needed">?</span>
                </h2>
            </div>

            <div class="health-status-row">
                <div class="status-info">
                    <span id="container-status" class="status-badge unknown">
                        <span class="status-dot"></span>
                        <span class="status-text">Checking...</span>
                    </span>
                    <span id="container-uptime" class="uptime-text"></span>
                </div>
                <button id="restart-btn" class="restart-btn" onclick="restartContainer()">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M23 4v6h-6"></path>
                        <path d="M1 20v-6h6"></path>
                        <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
                    </svg>
                    <span>Restart Store</span>
                    <span class="help-icon" title="Restarts your store's server. Use if your site is unresponsive. Takes ~30 seconds.">?</span>
                </button>
            </div>

            <div class="health-metrics">
                <iframe src="https://shophosting.io/grafana/d-solo/customer-metrics/customer-metrics?orgId=1&var-customer_id={{ customer.id }}&panelId=5&theme=dark"
                        width="100%" height="80" frameborder="0" title="CPU Usage"></iframe>
                <iframe src="https://shophosting.io/grafana/d-solo/customer-metrics/customer-metrics?orgId=1&var-customer_id={{ customer.id }}&panelId=6&theme=dark"
                        width="100%" height="80" frameborder="0" title="Memory Usage"></iframe>
                <iframe src="https://shophosting.io/grafana/d-solo/customer-metrics/customer-metrics?orgId=1&var-customer_id={{ customer.id }}&panelId=7&theme=dark"
                        width="100%" height="80" frameborder="0" title="Requests/sec"></iframe>
            </div>

            <div class="logs-section">
                <div class="logs-header" onclick="toggleLogs()">
                    <span class="logs-title">Recent Logs</span>
                    <div class="logs-actions">
                        <button class="btn btn-sm" onclick="event.stopPropagation(); refreshLogs()">Refresh</button>
                        <svg id="logs-toggle" class="logs-toggle" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="6 9 12 15 18 9"></polyline>
                        </svg>
                    </div>
                </div>
                <div id="logs-content" class="logs-content">
                    <div id="logs-list" class="logs-list">Loading logs...</div>
                </div>
            </div>
        </div>

```

**Step 2: Commit**

```bash
git add webapp/templates/dashboard.html
git commit -m "feat(dashboard): add site health HTML card"
```

---

## Task 6: Add Site Health JavaScript

**Files:**
- Modify: `webapp/templates/dashboard.html`

**Step 1: Add JavaScript in the `{% block extra_js %}` section**

Find the existing JavaScript block and add this code inside the `{% if customer.status == 'active' %}` section (or create one if the active state doesn't have JS):

```javascript
{% if customer.status == 'active' and credentials %}
<script nonce="{{ csp_nonce() }}">
    // Site Health Functions
    let restartCooldown = false;
    let logsExpanded = false;

    // Fetch container status
    async function fetchContainerStatus() {
        try {
            const response = await fetch('/api/container/status');
            const data = await response.json();

            const statusEl = document.getElementById('container-status');
            const uptimeEl = document.getElementById('container-uptime');
            const statusText = statusEl.querySelector('.status-text');

            // Update status badge
            statusEl.className = 'status-badge ' + (data.running ? 'running' :
                data.status === 'restarting' ? 'restarting' :
                data.status === 'exited' ? 'stopped' : 'unknown');

            statusText.textContent = data.running ? 'Running' :
                data.status === 'restarting' ? 'Restarting...' :
                data.status === 'exited' ? 'Stopped' : 'Unknown';

            // Update uptime
            if (data.uptime) {
                uptimeEl.textContent = 'Uptime: ' + data.uptime;
            } else {
                uptimeEl.textContent = '';
            }

        } catch (error) {
            console.error('Failed to fetch status:', error);
            document.getElementById('container-status').className = 'status-badge unknown';
            document.querySelector('.status-text').textContent = 'Error';
        }
    }

    // Restart container
    async function restartContainer() {
        if (restartCooldown) {
            alert('Please wait 5 minutes between restarts.');
            return;
        }

        if (!confirm('Are you sure you want to restart your store? It will be unavailable for ~30 seconds.')) {
            return;
        }

        const btn = document.getElementById('restart-btn');
        btn.disabled = true;
        btn.classList.add('loading');
        const btnText = btn.querySelector('span:not(.help-icon)');
        btnText.textContent = 'Restarting...';

        try {
            const response = await fetch('/api/container/restart', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            const data = await response.json();

            if (data.success) {
                alert(data.message);
                restartCooldown = true;
                setTimeout(() => {
                    restartCooldown = false;
                    btn.disabled = false;
                    btnText.textContent = 'Restart Store';
                }, 300000); // 5 minutes

                // Poll status more frequently after restart
                setTimeout(fetchContainerStatus, 5000);
                setTimeout(fetchContainerStatus, 15000);
                setTimeout(fetchContainerStatus, 30000);
            } else {
                alert('Restart failed: ' + data.message);
                btn.disabled = false;
                btnText.textContent = 'Restart Store';
            }
        } catch (error) {
            alert('Failed to restart: ' + error.message);
            btn.disabled = false;
            btnText.textContent = 'Restart Store';
        }

        btn.classList.remove('loading');
    }

    // Toggle logs visibility
    function toggleLogs() {
        const content = document.getElementById('logs-content');
        const toggle = document.getElementById('logs-toggle');
        logsExpanded = !logsExpanded;

        content.classList.toggle('expanded', logsExpanded);
        toggle.classList.toggle('expanded', logsExpanded);

        if (logsExpanded) {
            refreshLogs();
        }
    }

    // Fetch logs - uses safe DOM methods to prevent XSS
    async function refreshLogs() {
        const logsEl = document.getElementById('logs-list');
        logsEl.textContent = 'Loading logs...';

        try {
            const response = await fetch('/api/container/logs?lines=50');
            const data = await response.json();

            // Clear existing content
            logsEl.textContent = '';

            if (data.logs && data.logs.length > 0) {
                // Use safe DOM methods instead of innerHTML
                data.logs.forEach(line => {
                    const lineDiv = document.createElement('div');
                    lineDiv.className = 'log-line';
                    lineDiv.textContent = line;
                    logsEl.appendChild(lineDiv);
                });
            } else {
                const emptyDiv = document.createElement('div');
                emptyDiv.className = 'logs-empty';
                emptyDiv.textContent = 'No recent errors - your site is running smoothly!';
                logsEl.appendChild(emptyDiv);
            }
        } catch (error) {
            const errorDiv = document.createElement('div');
            errorDiv.className = 'log-line';
            errorDiv.style.color = 'var(--error)';
            errorDiv.textContent = 'Failed to load logs: ' + error.message;
            logsEl.textContent = '';
            logsEl.appendChild(errorDiv);
        }
    }

    // Initialize
    fetchContainerStatus();
    setInterval(fetchContainerStatus, 10000); // Poll every 10 seconds
</script>
{% endif %}
```

**Step 2: Verify the template renders**

```bash
python3 -c "from jinja2 import Environment; print('Template syntax OK')"
```

**Step 3: Commit**

```bash
git add webapp/templates/dashboard.html
git commit -m "feat(dashboard): add site health JavaScript functionality"
```

---

## Task 7: Add Grafana Stat Panels

**Files:**
- Modify: `monitoring/grafana/dashboards/customer-metrics.json`

**Step 1: Add three new stat panels for CPU, Memory, and RPS**

Add these panels to the `panels` array (after existing panels):

```json
    {
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              { "color": "green", "value": null },
              { "color": "yellow", "value": 70 },
              { "color": "red", "value": 90 }
            ]
          },
          "unit": "percent",
          "max": 100
        },
        "overrides": []
      },
      "gridPos": { "h": 4, "w": 4, "x": 0, "y": 8 },
      "id": 5,
      "options": {
        "colorMode": "background",
        "graphMode": "area",
        "justifyMode": "auto",
        "orientation": "auto",
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "textMode": "auto"
      },
      "pluginVersion": "10.2.2",
      "targets": [
        {
          "expr": "container_cpu_usage_seconds_total{name=~\"customer-$customer_id-.*\"}",
          "refId": "A"
        }
      ],
      "title": "CPU %",
      "type": "stat",
      "transparent": true
    },
    {
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              { "color": "green", "value": null },
              { "color": "yellow", "value": 70 },
              { "color": "red", "value": 90 }
            ]
          },
          "unit": "percent",
          "max": 100
        },
        "overrides": []
      },
      "gridPos": { "h": 4, "w": 4, "x": 4, "y": 8 },
      "id": 6,
      "options": {
        "colorMode": "background",
        "graphMode": "area",
        "justifyMode": "auto",
        "orientation": "auto",
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "textMode": "auto"
      },
      "pluginVersion": "10.2.2",
      "targets": [
        {
          "expr": "container_memory_usage_bytes{name=~\"customer-$customer_id-.*\"} / container_spec_memory_limit_bytes{name=~\"customer-$customer_id-.*\"} * 100",
          "refId": "A"
        }
      ],
      "title": "Memory %",
      "type": "stat",
      "transparent": true
    },
    {
      "datasource": { "type": "prometheus", "uid": "prometheus" },
      "fieldConfig": {
        "defaults": {
          "color": { "mode": "thresholds" },
          "mappings": [],
          "thresholds": {
            "mode": "absolute",
            "steps": [
              { "color": "green", "value": null },
              { "color": "yellow", "value": 50 },
              { "color": "red", "value": 100 }
            ]
          },
          "unit": "reqps"
        },
        "overrides": []
      },
      "gridPos": { "h": 4, "w": 4, "x": 8, "y": 8 },
      "id": 7,
      "options": {
        "colorMode": "background",
        "graphMode": "area",
        "justifyMode": "auto",
        "orientation": "auto",
        "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false },
        "textMode": "auto"
      },
      "pluginVersion": "10.2.2",
      "targets": [
        {
          "expr": "rate(nginx_http_requests_total{customer_id=\"$customer_id\"}[1m])",
          "refId": "A"
        }
      ],
      "title": "Req/s",
      "type": "stat",
      "transparent": true
    }
```

**Step 2: Commit**

```bash
git add monitoring/grafana/dashboards/customer-metrics.json
git commit -m "feat(grafana): add CPU, memory, and RPS stat panels"
```

---

## Task 8: Integration Testing & Final Commit

**Step 1: Verify all Python syntax**

```bash
python3 -m py_compile webapp/app.py && echo "app.py OK"
```

**Step 2: Test API endpoints manually (requires running server)**

```bash
# Start the webapp if not running, then test:
curl -s http://localhost:5000/api/container/status | head
```

**Step 3: Verify git status**

```bash
git status
git log --oneline -8
```

**Step 4: Create final integration commit if needed**

```bash
git add -A
git status
# If there are uncommitted changes:
git commit -m "feat(dashboard): complete site health implementation

- Container status, restart, and logs API endpoints
- Site Health card with real-time status updates
- Grafana embeds for CPU, memory, and request metrics
- Inline help tooltips for self-service guidance"
```

---

## Post-Implementation Verification

1. **Load the dashboard** as a logged-in customer with an active store
2. **Verify status badge** shows "Running" with uptime
3. **Click restart** and confirm the cooldown works
4. **Expand logs** and verify they load (sanitized)
5. **Check Grafana panels** display metrics (may need Prometheus data)

---

## Deployment Notes

- Grafana dashboard changes require Grafana restart or re-import
- Rate limits may need tuning based on actual usage patterns
- Docker socket access required for container operations (already configured for provisioning)
