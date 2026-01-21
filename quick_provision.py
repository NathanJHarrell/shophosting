#!/usr/bin/env python3
import os, sys
sys.path.insert(0, '/opt/shophosting/provisioning')
from dotenv import load_dotenv
load_dotenv('/opt/shophosting/.env')

with open('/tmp/e2e_cid.txt', 'r') as f:
    cid, port = f.read().strip().split(',')
    port = int(port)

print(f'Provisioning {cid} on port {port}...')

from provisioning_worker import ProvisioningWorker
w = ProvisioningWorker()

job = {
    'customer_id': cid,
    'domain': 'e2e.localhost',
    'platform': 'magento',
    'email': 'e2e@example.com',
    'site_title': 'E2E Test Store',
    'admin_user': 'admin',
    'web_port': port,
    'memory_limit': '2g',
    'cpu_limit': '1.0'
}

w.update_customer_status = lambda c,s,e=None: print(f'[{s}] {e if e else ""}')
w.save_customer_credentials = lambda c,cr: print(f'Admin: {cr["admin_user"]} / {cr["admin_password"]}')

r = w.provision_customer(job)
print(f'\nResult: {r["status"]}')
if r['status'] == 'success':
    print(f'Password: {r.get("admin_password")}')
else:
    print(f'Error: {r.get("error")}')
