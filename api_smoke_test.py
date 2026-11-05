"""Mutating local acceptance workflow. Start an isolated --data-dir server first."""
import argparse
import os
import secrets
from datetime import date, timedelta
from tools.local_http import call, local_base, login


def run(base, *, confirm_isolated=False, accounts=None):
    local_base(base)
    if not confirm_isolated:
        raise ValueError('This creates orders and may approve a TEST credential. Confirm an isolated server with --confirm-isolated.')
    accounts = accounts or {
        'admin': ('admin', os.environ.get('CARE_ADMIN_PASSWORD', 'Admin123!')),
        'elder': ('elder', os.environ.get('CARE_ELDER_PASSWORD', 'Elder123!')),
        'provider': ('provider', os.environ.get('CARE_PROVIDER_PASSWORD', 'Provider123!'))}
    sessions = {role: login(base, username, password, role) for role, (username, password) in accounts.items()}
    def request(role, method, path, body=None):
        result = call(base, method, path, body, token=sessions[role]['token'])
        print(f'[PASS] {role:8} {method:4} {path}')
        return result
    elder_code = sessions['elder']['user']['elder_code']
    provider_code = sessions['provider']['user']['provider_code']
    elder = request('elder', 'GET', '/api/profiles/elder/' + elder_code)['profile']
    provider = request('provider', 'GET', '/api/profiles/provider/' + provider_code)['profile']
    if provider.get('employment_status') != '在职':
        raise ValueError('Logged-in provider must be employed and eligible')
    if elder.get('community') not in str(provider.get('service_areas', '')):
        raise ValueError('Provider does not cover elder community; choose matching test accounts')
    services = request('admin', 'GET', '/api/acceptance/services?page_size=200')['items']
    eligible = [s for s in services if s.get('enabled', 1) and s.get('service_status') not in {'暂停', '暂停服务'}
                and s['service_type'].replace('类', '') in str(provider.get('skills', '')) and int(s['duration_minutes']) <= 120
                and not any(word in s['service_name'] for word in ('助餐', '助医', '送餐', '就医'))]
    if not eligible:
        raise ValueError('No eligible service for current worker; prepare matching test accounts/services')
    service = sorted(eligible, key=lambda s: ('助洁' not in s['service_name'], s['service_code']))[0]
    required = service.get('required_certificate', '').strip()
    if required:
        credentials = request('provider', 'GET', '/api/acceptance/credentials?page_size=200')['items']
        qualified = any(c.get('name') == required and c.get('status') == 'APPROVED'
                        and str(c.get('expires_on') or '') >= date.today().isoformat() for c in credentials)
        if not qualified:
            item = request('provider', 'POST', '/api/acceptance/credentials', {
                'provider_code': provider_code, 'name': required, 'certificate_no': 'TEST-SMOKE-' + secrets.token_hex(6),
                'expires_on': (date.today() + timedelta(days=90)).isoformat()})
            credential_id = item.get('id') or item['item']['credential_id']
            request('admin', 'POST', f'/api/acceptance/credentials/{credential_id}/review', {
                'status': 'APPROVED', 'review_note': '隔离冒烟测试模拟审核，仅测试库有效'})
    service_day = (date.today() + timedelta(days=30 + secrets.randbelow(3000))).isoformat()
    created = request('elder', 'POST', '/api/orders', {
        'elder_code': elder_code, 'service_code': service['service_code'], 'service_date': service_day,
        'time_slot': '上午 09:00-11:00', 'duration_minutes': service['duration_minutes'],
        'service_address': elder['address'], 'special_requirements': '隔离 API 冒烟测试订单',
        'condition_check': '隔离验收已核对服务条件'})
    order_no = created['order_no']
    request('admin', 'POST', f'/api/orders/{order_no}/assign', {'action': 'ASSIGN', 'provider_code': provider_code, 'remark': '隔离验收派单'})
    request('provider', 'POST', f'/api/orders/{order_no}/depart', {})
    request('provider', 'POST', f'/api/orders/{order_no}/arrive', {})
    request('provider', 'POST', f'/api/orders/{order_no}/complete', {})
    record = request('provider', 'POST', f'/api/orders/{order_no}/records', {
        'completion_status': '全部完成', 'process_description': '隔离验收：按预约完成服务',
        'elder_observation': '课程测试记录', 'materials_json': [], 'attachments_json': []})
    request('admin', 'POST', f"/api/acceptance/records/{record['record_id']}/review", {
        'status': '已通过', 'review_note': '隔离验收服务记录审核通过'})
    request('elder', 'POST', f'/api/orders/{order_no}/review', {
        'attitude_score': 5, 'quality_score': 5, 'punctual_score': 5, 'review_text': '隔离验收评价'})
    detail = request('elder', 'GET', f'/api/orders/{order_no}')['order']
    if detail['status'] != 'COMPLETED' or detail['provider_code'] != provider_code:
        raise AssertionError(detail)
    if not all(detail.get(key) for key in ['depart_at', 'arrive_at', 'finish_at']):
        raise AssertionError('Execution timestamps missing')
    print('Workflow verified; persistent test order:', order_no)
    return order_no


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='Explicit loopback URL; redirects are blocked')
    parser.add_argument('--confirm-isolated', action='store_true', help='Confirm server uses disposable data; enables test writes')
    args = parser.parse_args()
    run(args.base_url, confirm_isolated=args.confirm_isolated)


if __name__ == '__main__':
    main()
