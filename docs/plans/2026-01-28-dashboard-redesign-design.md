# Customer Dashboard Redesign

**Goal:** Restructure the customer dashboard with sidebar navigation and separate page routes while keeping existing colors/gradients.

---

## Layout Structure

```
┌─────────────────────────────────────────────────────────────┐
│  Top Navbar (account, notifications, logout)                │
├──────────┬──────────────────────────────────────────────────┤
│          │                                                  │
│  Sidebar │           Main Content Area                      │
│   240px  │           (changes per route)                    │
│          │                                                  │
│ Overview │                                                  │
│ Health   │                                                  │
│ Backups  │                                                  │
│ Staging  │                                                  │
│ Domains  │                                                  │
│ Billing  │                                                  │
│ Settings │                                                  │
│ ──────── │                                                  │
│ Support  │                                                  │
│          │                                                  │
└──────────┴──────────────────────────────────────────────────┘
```

---

## Routes

| Route | Page | Content |
|-------|------|---------|
| `/dashboard` | Overview | Quick actions, stat cards, store details |
| `/dashboard/health` | Site Health | Container status, restart, logs, Grafana metrics |
| `/dashboard/backups` | Backups | Backup list, create/restore |
| `/dashboard/staging` | Staging | Staging environments |
| `/dashboard/domains` | Domains | Domain management |
| `/dashboard/billing` | Billing | Plan, invoices, upgrade |
| `/dashboard/settings` | Settings | Account settings |
| `/dashboard/support` | Support | Tickets, help |

---

## Sidebar Navigation

- Fixed 240px width
- Logo/brand at top
- Navigation items with icons
- Active state: accent background + left border
- Support separated at bottom
- Collapses to hamburger on mobile (<768px)

---

## Overview Page (`/dashboard`)

```
┌─────────────────────────────────────────────────────────────┐
│  Overview                                                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Quick Actions                                              │
│  [Restart Store] [Create Backup] [View Logs] [Visit Store] │
│                                                             │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐  │
│  │ ● Status  │ │ CPU       │ │ Memory    │ │ Disk      │  │
│  │ Running   │ │ 23%       │ │ 512MB     │ │ 4.2GB     │  │
│  │ 14d 6h    │ │ ████░░░░  │ │ ████░░░░  │ │ ██░░░░░░  │  │
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────────┐
│  │  Store Details                                          │
│  │  Domain: mystore.com          Platform: WooCommerce     │
│  │  Plan: Pro ($29/mo)           Created: Jan 15, 2026     │
│  │  [Admin Panel] [phpMyAdmin]                             │
│  └─────────────────────────────────────────────────────────┘
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Design Constraints

**Keep existing:**
- All CSS variables (`--bg-elevated`, `--text-primary`, etc.)
- Gradients (`--gradient-border`, `--shadow-glow`)
- Card styling (borders, hover effects)
- Color scheme

**Add new:**
- Sidebar component (240px fixed)
- Dashboard layout wrapper
- Route-based content switching
- Stat card components
- Quick action buttons
- Mobile responsive sidebar (hamburger)

---

## Files to Create/Modify

**New:**
- `webapp/templates/dashboard/_sidebar.html` - Sidebar partial
- `webapp/templates/dashboard/base_dashboard.html` - Dashboard layout wrapper
- `webapp/templates/dashboard/overview.html` - Overview page
- `webapp/templates/dashboard/health.html` - Site Health page
- `webapp/templates/dashboard/backups.html` - Backups page
- `webapp/templates/dashboard/staging.html` - Staging page
- `webapp/templates/dashboard/domains.html` - Domains page
- `webapp/templates/dashboard/billing.html` - Billing page
- `webapp/templates/dashboard/settings.html` - Settings page
- `webapp/templates/dashboard/support.html` - Support page

**Modify:**
- `webapp/app.py` - Add new routes
- `webapp/static/css/` or inline styles - Dashboard layout CSS
