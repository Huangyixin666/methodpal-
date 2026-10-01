"""Authentication, CSRF, body-size limits and resource reference authorization."""
import hmac
import json
import logging
import os
import time
import uuid
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from app.services.authentication import COOKIE, ORIGINS, PRODUCTION, lookup_session
from app.services.user_context import reset_current_user_id, set_current_user_id
from app.services.store import PROJECTS, DATASETS, ANALYSES, CHARTS, REPORTS

MAX_BODY = int(os.getenv('METHODPAL_MAX_BODY_BYTES', str(20 * 1024 * 1024)))
PUBLIC = {'/api/health', '/api/auth/login', '/api/auth/register', '/api/auth/config', '/api/auth/captcha', '/api/auth/email/code', '/api/auth/password/reset'}
log = logging.getLogger('methodpal.security')

class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        request = Request(scope)
        request_id = uuid.uuid4().hex
        started = time.monotonic()
        status = 500
        async def send_secure(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
                headers = list(message.get('headers', []))
                headers.extend([(b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'), (b'x-frame-options', b'DENY'), (b'referrer-policy', b'no-referrer'), (b'x-request-id', request_id.encode())])
                if PRODUCTION:
                    headers.append((b'strict-transport-security', b'max-age=31536000'))
                message['headers'] = headers
            await send(message)
        async def reject(code, detail):
            await JSONResponse({'detail': detail}, status_code=code)(scope, receive, send_secure)
        if request.method == 'OPTIONS':
            return await self.app(scope, receive, send_secure)
        path = scope['path']
        session = await run_in_threadpool(lookup_session, request.cookies.get(COOKIE))
        if path not in PUBLIC and session is None:
            return await reject(401, '请先登录，或登录已过期')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            origin = request.headers.get('origin')
            if origin and origin not in ORIGINS:
                return await reject(403, '请求来源不受信任')
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return await reject(403, '跨站请求已拒绝')
            if path in PUBLIC:
                # Custom header prevents login CSRF, including forms without Origin.
                if request.headers.get('x-methodpal-request') != '1':
                    return await reject(403, '缺少请求校验头')
            elif not session or not hmac.compare_digest(request.headers.get('x-csrf-token', ''), session['csrf_token']):
                return await reject(403, '会话校验失败，请刷新页面')
        # Read with a hard cap before multipart parsing; also covers chunked bodies.
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > MAX_BODY:
                return await reject(413, '请求或上传文件过大')
            chunks.append(message.get('body', b''))
            if not message.get('more_body', False):
                break
        body = b''.join(chunks)
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()
        scope.setdefault('state', {})['session'] = session
        token = set_current_user_id(session['user_id']) if session else None
        try:
            await self.app(scope, replay, send_secure)
        finally:
            if token is not None:
                reset_current_user_id(token)
            # No bodies, usernames, query strings, passwords or cookies in logs.
            log.info('request=%s method=%s status=%s duration_ms=%d', request_id, request.method, status, (time.monotonic()-started)*1000)

RESOURCE_FIELDS = {'project_id': PROJECTS, 'dataset_id': DATASETS, 'data_id': DATASETS, 'analysis_id': ANALYSES, 'analysisId': ANALYSES, 'chart_id': CHARTS, 'report_id': REPORTS}

async def authorize_references(request: Request):
    def check(value, depth=0):
        if depth > 40:
            raise HTTPException(422, '请求嵌套层级过深')
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'rows', 'cells', 'data', 'matrix', 'messages', 'analysis_result', 'dataset_summary', 'research_spec'}:
                    continue
                if key in RESOURCE_FIELDS and item:
                    if key == 'project_id' and item == 'no_project':
                        continue
                    if key in {'analysis_id', 'analysisId'} and isinstance(item, str) and item.startswith('dataset:') and DATASETS.get(item[8:]) is not None:
                        continue
                    if not isinstance(item, str) or RESOURCE_FIELDS[key].get(item) is None:
                        raise HTTPException(404, '资源不存在')
                elif key == 'chart_ids' and isinstance(item, list):
                    for chart_id in item:
                        if not isinstance(chart_id, str) or CHARTS.get(chart_id) is None:
                            raise HTTPException(404, '资源不存在')
                elif isinstance(item, (dict, list)):
                    check(item, depth+1)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    check(item, depth+1)
    check(request.path_params)
    check(dict(request.query_params))
    content_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if (not content_type and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}) or content_type == 'application/json' or content_type.endswith('+json'):
        if not await request.body():
            return
        try:
            body = await request.json()
        except (ValueError, RecursionError):
            raise HTTPException(422, '无效 JSON')
        check(body)
