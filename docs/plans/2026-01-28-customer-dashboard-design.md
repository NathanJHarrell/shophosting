# Customer Dashboard - Site Health & Actions

**Goal:** Add self-service troubleshooting to reduce support tickets by letting customers check status, view logs, and restart their store.

---

## Features

### 1. Container Status
- Real-time status badge: Running (green), Stopped (red), Restarting (yellow), Error (orange)
- Uptime display (e.g., "14d 6h 23m")

### 2. One-Click Restart
- Button to restart customer's Docker container
- 5-minute cooldown to prevent restart loops
- Confirmation dialog before action
- Spinner and "Restarting..." state during action

### 3. Live Resource Stats (Grafana)
- Embedded Grafana panels for CPU, Memory, Requests/sec
- Uses existing `customer-metrics` dashboard
- Dark theme, auto-refresh

### 4. Error Log Viewer
- Last 50 lines of container logs
- Collapsed by default (5 lines visible)
- Expandable to full view
- Logs sanitized to remove sensitive data (passwords, keys)
- "No recent errors" message when empty

### 5. Inline Help Tooltips
- [?] icons next to status, restart, and metrics
- Explain what each item means and when to use restart

---

## UI Layout

```
┌─────────────────────────────────────────────────────┐
│ Site Health                              [?] Help   │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Status: ● Running    [↻ Restart Store]            │
│  Uptime: 14d 6h 23m                                │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │  [Grafana: CPU gauge] [Memory gauge] [RPS]  │   │
│  │         (embedded panels, dark theme)        │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
├─────────────────────────────────────────────────────┤
│ Recent Logs                          [Refresh] [▼] │
├─────────────────────────────────────────────────────┤
│ 16:42:01  PHP Notice: Undefined index...           │
│ 16:41:58  GET /wp-admin/ 200 0.234s                │
│ (click to expand)                                  │
└─────────────────────────────────────────────────────┘
```

**Placement:** After "Your Store is Ready" credentials card, before "Resource Usage" card.

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/container/status` | GET | Container state, uptime, health |
| `/api/container/restart` | POST | Trigger container restart |
| `/api/container/logs` | GET | Last 50 lines of error logs |

Stats come from Grafana embeds, not a custom endpoint.

### Security
- All endpoints require authenticated customer session
- Customer can only access their own container (`customer-{id}`)
- Restart rate-limited: 1 per 5 minutes
- Status polling rate-limited: 60/min
- Logs sanitized to strip credentials

---

## Container States

| State | Badge | Restart | Notes |
|-------|-------|---------|-------|
| Running | 🟢 Green | Enabled | Normal operation |
| Stopped | 🔴 Red | Enabled | Container crashed or stopped |
| Restarting | 🟡 Yellow | Disabled | In progress, show spinner |
| Error/Unknown | 🟠 Orange | Enabled | Show "Contact support if persists" |

---

## Error Handling

- **Restart fails:** Error toast, re-enable button after 30s, suggest support
- **Container missing:** Show "Provisioning in progress" or support message
- **Docker API unreachable:** Show "Status unavailable" with retry
- **Logs empty:** Show "No recent errors - your site is running smoothly!"
- **Cooldown active:** Button disabled, show countdown timer

---

## Technical Implementation

### Backend
- Docker commands via subprocess: `docker inspect`, `docker restart`, `docker logs`
- Runs through existing provisioning infrastructure
- Container naming: `customer-{id}-wordpress` or `customer-{id}-magento`

### Frontend
- Status/uptime refresh every 10 seconds via JavaScript fetch
- Grafana iframes for metrics (already working pattern in codebase)
- Logs fetched on-demand (not auto-refresh to reduce load)

### Grafana
- Add stat panels to `customer-metrics` dashboard:
  - CPU % (gauge)
  - Memory MB (gauge)
  - Requests/sec (stat)
- Embed via iframe with `&panelId=X&theme=dark`

---

## Files to Create/Modify

**New:**
- `webapp/api/container.py` - Container status/restart/logs endpoints

**Modified:**
- `webapp/app.py` - Register new API blueprint
- `webapp/templates/dashboard.html` - Add Site Health card
- `monitoring/grafana/dashboards/customer-metrics.json` - Add stat panels
