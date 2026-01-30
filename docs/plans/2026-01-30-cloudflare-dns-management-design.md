# Cloudflare DNS Management Design

**Date:** 2026-01-30
**Branch:** feature/dns-management

## Overview

Customers connect their Cloudflare account via OAuth to manage DNS records directly from the ShopHosting dashboard. On connection, we auto-configure their domain to point to their store, with a confirmation step if existing records are detected.

## Customer Flow

1. Customer goes to Dashboard → Domains
2. Clicks "Connect Cloudflare" (with link to Cloudflare signup: https://dash.cloudflare.com/sign-up)
3. Redirected to Cloudflare OAuth, grants permission
4. Redirected back to ShopHosting with auth code
5. If existing records found: shown a confirmation screen with current records and proposed changes
6. Customer confirms → records created/updated
7. Dashboard shows current DNS records with ability to add/edit/delete

## Record Types Supported

- A (IPv4 address)
- CNAME (aliases)
- MX (email routing)
- TXT (verification, SPF, DKIM)

## Database Schema

### Table: `customer_cloudflare_connections`

| Column | Type | Description |
|--------|------|-------------|
| id | INT | Primary key |
| customer_id | INT | FK to customers |
| cloudflare_zone_id | VARCHAR(50) | The zone ID for their domain |
| access_token | TEXT | Encrypted OAuth access token |
| refresh_token | TEXT | Encrypted OAuth refresh token |
| token_expires_at | DATETIME | When access token expires |
| connected_at | DATETIME | When they connected |
| last_sync_at | DATETIME | Last time we synced records |

### Table: `dns_records_cache`

| Column | Type | Description |
|--------|------|-------------|
| id | INT | Primary key |
| customer_id | INT | FK to customers |
| cloudflare_record_id | VARCHAR(50) | Cloudflare's record ID |
| record_type | ENUM | A, CNAME, MX, TXT |
| name | VARCHAR(255) | e.g., "www" or "@" |
| content | VARCHAR(255) | IP, target, or text value |
| priority | INT | For MX records |
| proxied | BOOLEAN | Cloudflare proxy enabled |
| synced_at | DATETIME | Last sync time |

## OAuth Integration

### Cloudflare OAuth Setup

Register an OAuth app at Cloudflare's developer portal with these permissions:
- `zone:read` - List zones (domains) in their account
- `dns:read` - Read DNS records
- `dns:edit` - Create/update/delete DNS records

### OAuth Flow

1. **Initiate:** User clicks "Connect Cloudflare" → redirect to:
   ```
   https://dash.cloudflare.com/oauth2/authorize?
     client_id=YOUR_CLIENT_ID&
     redirect_uri=https://shophosting.io/dashboard/cloudflare/callback&
     response_type=code&
     scope=zone:read dns:read dns:edit
   ```

2. **Callback:** Cloudflare redirects back with `code` parameter

3. **Exchange:** Backend exchanges code for access/refresh tokens via POST to:
   ```
   https://api.cloudflare.com/client/v4/oauth/token
   ```

4. **Store:** Encrypt tokens and save to `customer_cloudflare_connections`

5. **Fetch Zone:** Call Cloudflare API to get zone ID for customer's domain

### Token Refresh

Access tokens expire. Before any API call, check `token_expires_at`. If expired, use refresh token to get new access token.

## User Interface

### Before Connection

```
┌─────────────────────────────────────────────────────────┐
│  Cloudflare DNS Management                              │
├─────────────────────────────────────────────────────────┤
│  Connect your Cloudflare account to manage DNS          │
│  records directly from this dashboard.                  │
│                                                         │
│  [Connect Cloudflare]                                   │
│                                                         │
│  Don't have Cloudflare? Sign up free →                  │
└─────────────────────────────────────────────────────────┘
```

### After Connection

```
┌─────────────────────────────────────────────────────────┐
│  DNS Records                          [+ Add Record]    │
├─────────────────────────────────────────────────────────┤
│  Type   Name              Content           Proxied     │
│  ───────────────────────────────────────────────────    │
│  A      example.com       147.135.8.170     Yes  [Edit][Delete]  │
│  CNAME  www               example.com       Yes  [Edit][Delete]  │
│  MX     example.com       mail.google.com   -    [Edit][Delete]  │
│  TXT    example.com       v=spf1 include... -    [Edit][Delete]  │
├─────────────────────────────────────────────────────────┤
│  ✓ Connected to Cloudflare    Last synced: 2 min ago   │
│                                         [Disconnect]    │
└─────────────────────────────────────────────────────────┘
```

### Add/Edit Record Modal

- Record type dropdown (A, CNAME, MX, TXT)
- Name field (with domain suffix shown)
- Content field (IP, target, or text)
- Priority field (only shown for MX)
- Proxied toggle (only for A/CNAME)

## Confirmation Flow (Existing Records)

When customer connects and existing records are found:

```
┌─────────────────────────────────────────────────────────┐
│  Review DNS Changes                                     │
├─────────────────────────────────────────────────────────┤
│  We found existing DNS records for example.com.         │
│  Review the changes below before proceeding.            │
│                                                         │
│  EXISTING RECORDS                                       │
│  ┌────────────────────────────────────────────────────┐ │
│  │ ☑ A     example.com    192.168.1.1   → REPLACE     │ │
│  │ ☑ CNAME www            oldhost.com   → REPLACE     │ │
│  │ ☑ MX    example.com    mail.google.com  KEEP       │ │
│  │ ☑ TXT   example.com    v=spf1...        KEEP       │ │
│  └────────────────────────────────────────────────────┘ │
│                                                         │
│  PROPOSED CHANGES                                       │
│  • A record → 147.135.8.170 (your server)              │
│  • www CNAME → example.com                              │
│                                                         │
│  ⚠️  Records marked REPLACE will be updated.            │
│      MX and TXT records will be preserved.              │
│                                                         │
│              [Cancel]  [Confirm & Connect]              │
└─────────────────────────────────────────────────────────┘
```

### Logic

- A record for root domain → mark as REPLACE
- CNAME for www → mark as REPLACE
- All other records (MX, TXT, other CNAMEs) → mark as KEEP
- Customer can uncheck records to skip changes

## File Structure

```
webapp/
├── cloudflare/
│   ├── __init__.py          # Blueprint registration
│   ├── oauth.py             # OAuth flow handlers
│   ├── api.py               # Cloudflare API wrapper
│   └── models.py            # CloudflareConnection, DNSRecord models
├── templates/
│   └── dashboard/
│       ├── domains.html     # Updated with DNS management UI
│       └── cloudflare_confirm.html  # Confirmation screen
migrations/
└── 014_add_cloudflare_tables.sql
```

## Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/dashboard/cloudflare/connect` | GET | Initiate OAuth redirect |
| `/dashboard/cloudflare/callback` | GET | Handle OAuth callback |
| `/dashboard/cloudflare/confirm` | GET | Show confirmation screen |
| `/dashboard/cloudflare/confirm` | POST | Apply confirmed changes |
| `/dashboard/cloudflare/disconnect` | POST | Remove connection |
| `/dashboard/api/dns/records` | GET | List DNS records |
| `/dashboard/api/dns/records` | POST | Create record |
| `/dashboard/api/dns/records/<id>` | PUT | Update record |
| `/dashboard/api/dns/records/<id>` | DELETE | Delete record |
| `/dashboard/api/dns/sync` | POST | Force sync from Cloudflare |

## Error Handling

| Scenario | Handling |
|----------|----------|
| OAuth denied/cancelled | Redirect to domains page with flash message |
| Domain not found in Cloudflare account | Show error: "Domain not found. Make sure example.com is added to your Cloudflare account." |
| Token expired + refresh fails | Show "Reconnect Cloudflare" prompt |
| Cloudflare API rate limited | Retry with backoff, show user-friendly wait message |
| API error on record create/update | Show specific error from Cloudflare, keep modal open |

## Security

- **Token encryption:** Store access/refresh tokens encrypted using app's SECRET_KEY with Fernet symmetric encryption
- **CSRF protection:** All POST/PUT/DELETE routes protected with Flask-WTF CSRF tokens
- **Scope validation:** Only request minimum required Cloudflare permissions
- **Customer isolation:** All queries filter by `customer_id` - customers can only see/modify their own connection
- **Disconnect cleanup:** On disconnect, delete tokens from database immediately

## Rate Limiting

- DNS record changes: 10 per minute per customer
- Sync requests: 1 per minute per customer
