"""No proxies, DNS aliases or redirects: acceptance traffic stays on loopback."""
import json
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


def local_base(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'}):
        raise ValueError('Explicit http://localhost, 127.0.0.1 or [::1] base URL required')
    _ = parsed.port
    return value.rstrip('/')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirects are forbidden for local acceptance tools')


def call(base, method, path, body=None, token='', timeout=15):
    base = local_base(base)
    if not path.startswith('/api/') or '://' in path or '\\' in path:
        raise ValueError('Only relative /api/ paths are permitted')
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = Request(base + path, data=data, method=method, headers=headers)
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        payload = json.load(response)
    if not payload.get('ok'):
        raise ValueError(str(payload))
    return payload


def login(base, username, password, role):
    result = call(base, 'POST', '/api/auth/login', {'username': username, 'password': password, 'role': role})
    if result['user']['role'] != role:
        raise ValueError('Authenticated role differs from requested role')
    return result
