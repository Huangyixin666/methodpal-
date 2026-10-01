import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import threading
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from app.services.database import connect
from app.services import account_verification as verification

PRODUCTION = os.getenv('METHODPAL_ENV', 'development') == 'production'
COOKIE = '__Host-methodpal_session' if PRODUCTION else 'methodpal_session'
SESSION_SECONDS = int(os.getenv('METHODPAL_SESSION_SECONDS', '43200'))
ORIGINS = [x.strip().rstrip('/') for x in os.getenv('METHODPAL_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if x.strip()]
if PRODUCTION and (not ORIGINS or any(not x.startswith('https://') or '*' in x for x in ORIGINS)):
    raise RuntimeError('Production requires explicit HTTPS METHODPAL_ORIGINS')
router = APIRouter()
_hash_slots = threading.BoundedSemaphore(2)

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    with _hash_slots:
        value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=131072, r=8, p=1, maxmem=256 * 1024 * 1024).hex()
    return 'scrypt$' + salt + '$' + value

def verify_password(password, stored):
    _, salt, _ = stored.split('$')
    return hmac.compare_digest(hash_password(password, salt), stored)

def lookup_session(token):
    if not token or len(token) > 200:
        return None
    with connect() as db:
        row = db.execute('SELECT s.*, u.username, u.email, u.email_verified_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=? AND expires_at>?', (digest(token), int(time.time()))).fetchone()
    return dict(row) if row else None

def throttle(request, username):
    now = int(time.time())
    buckets = ['ip:' + verification.client_ip(request), 'name:' + username.lower()]
    blocked = False
    with connect() as db:
        db.execute('DELETE FROM auth_attempts WHERE started<?', (now - 86400,))
        for bucket, limit in zip(buckets, [60, 12]):
            key = digest(bucket)
            db.execute('INSERT INTO auth_attempts VALUES (?, ?, 1) ON CONFLICT(bucket) DO UPDATE SET attempts=CASE WHEN started<=? THEN 1 ELSE attempts+1 END, started=CASE WHEN started<=? THEN excluded.started ELSE started END', (key, now, now-900, now-900))
            blocked |= db.execute('SELECT attempts FROM auth_attempts WHERE bucket=?', (key,)).fetchone()[0] > limit
    if blocked:
        raise HTTPException(429, '尝试过于频繁，请在 15 分钟后重试')

class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    captcha_id: Optional[str] = Field(default=None, max_length=100)
    captcha_answer: Optional[str] = Field(default=None, max_length=20)
    email: Optional[str] = Field(default=None, max_length=254)
    verification_id: Optional[str] = Field(default=None, max_length=100)
    email_code: Optional[str] = Field(default=None, max_length=6)

def public_user(user):
    item = dict(user)
    return {'id':item.get('id') or item.get('user_id'), 'username':item['username'],
        'email':item.get('email'), 'email_verified':bool(item.get('email_verified_at'))}

def consume_code(db, cid, email, purpose, code, user_id=None):
    if not verification.valid_code(db, cid, email, purpose, code, user_id, count_attempt=False):
        raise HTTPException(400, '邮箱验证码错误、过期或已使用，请重新获取')
    db.execute('UPDATE email_challenges SET consumed=1 WHERE id=?', (cid,))

def start_session(user, request, response):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expiry = int(time.time()) + SESSION_SECONDS
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE expires_at<=? OR token_hash=?', (int(time.time()), digest(request.cookies.get(COOKIE, ''))))
        db.execute('INSERT INTO sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES (?,?,?,?,?)', (digest(token), user['id'], csrf, expiry, int(time.time())))
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=PRODUCTION, samesite='lax', path='/')
    return {'user': public_user(user), 'csrf_token': csrf, 'expires_at': expiry}

@router.post('/register', status_code=201)
def register(req: Credentials, request: Request, response: Response):
    if os.getenv('METHODPAL_REGISTRATION', 'open') != 'open':
        raise HTTPException(403, '注册暂未开放')
    username = req.username.strip()
    if not re.fullmatch(r'[A-Za-z0-9_\-]{3,40}', username):
        raise HTTPException(422, '用户名须为 3–40 位字母、数字、下划线或短横线')
    if len(req.password) < 12:
        raise HTTPException(422, '密码至少需要 12 个字符')
    throttle(request, username)
    verification.check_captcha(request, req.captcha_id, req.captcha_answer, verification.captcha_required())
    email = verification.normalize_email(req.email) if req.email else None
    if verification.email_required() and not email:
        raise HTTPException(422, '注册需要验证邮箱')
    if email:
        verification.verify_code(req.verification_id, email, 'register', req.email_code or '')
    encoded = hash_password(req.password)
    user = {'id': secrets.token_hex(16), 'username': username, 'email':email,
            'email_verified_at':int(time.time()) if email else None}
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if email:
                consume_code(db, req.verification_id, email, 'register', req.email_code or '')
            db.execute('INSERT INTO users(id,username,password_hash,created_at,email,email_verified_at) VALUES (?,?,?,?,?,?)', (user['id'], username, encoded, int(time.time()),email,user['email_verified_at']))
    except sqlite3.IntegrityError:
        raise HTTPException(409, '该用户名或邮箱已被使用')
    return start_session(user, request, response)

@router.post('/login')
def login(req: Credentials, request: Request, response: Response):
    throttle(request, req.username.strip())
    verification.check_captcha(request, req.captcha_id, req.captcha_answer, verification.captcha_required())
    with connect() as db:
        user = db.execute('SELECT * FROM users WHERE username=? OR (email=? AND email_verified_at IS NOT NULL)', (req.username.strip(), req.username.strip().lower())).fetchone()
    stored = user['password_hash'] if user else 'scrypt$' + '00' * 16 + '$' + '00' * 64
    valid = verify_password(req.password, stored)
    if not user or not valid:
        raise HTTPException(401, '用户名/邮箱或密码错误')
    return start_session(user, request, response)

@router.get('/me')
def me(request: Request):
    session = request.state.session
    return {'user': public_user(session), 'csrf_token': session['csrf_token'], 'expires_at': session['expires_at']}

@router.get('/config')
def auth_config():
    return {'captcha_required':verification.captcha_required(), 'email_enabled':verification.mail_enabled(),
        'email_required':verification.email_required(), 'registration_open':os.getenv('METHODPAL_REGISTRATION','open') == 'open'}

@router.get('/captcha')
def captcha(request: Request):
    return verification.create_captcha(request)

class EmailCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    purpose: Literal['register','reset','bind']
    captcha_id: str = Field(max_length=100)
    captcha_answer: str = Field(max_length=20)

@router.post('/email/code')
def email_code(req: EmailCodeRequest, request: Request):
    owner = None
    if req.purpose == 'bind':
        session = request.state.session
        if not session or not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf_token']):
            raise HTTPException(403, '请先登录并刷新页面')
        owner = session['user_id']
    if req.purpose == 'register' and os.getenv('METHODPAL_REGISTRATION','open') != 'open':
        raise HTTPException(403, '注册暂未开放')
    verification.check_captcha(request, req.captcha_id, req.captcha_answer)
    return verification.issue_email_code(request, req.email, req.purpose, owner)

class EmailProof(BaseModel):
    email: str = Field(min_length=3,max_length=254)
    verification_id: str = Field(max_length=100)
    email_code: str = Field(pattern=r'^\d{6}$')

class ResetRequest(EmailProof):
    new_password: str = Field(min_length=12,max_length=128)
    captcha_id: str = Field(max_length=100)
    captcha_answer: str = Field(max_length=20)

@router.post('/password/reset')
def reset_password(req: ResetRequest, request: Request):
    verification.limited('reset:'+verification.client_ip(request), 20, 900)
    verification.check_captcha(request, req.captcha_id, req.captcha_answer)
    email = verification.normalize_email(req.email)
    verification.verify_code(req.verification_id,email,'reset',req.email_code)
    encoded = hash_password(req.new_password)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        consume_code(db,req.verification_id,email,'reset',req.email_code)
        user = db.execute('SELECT id FROM users WHERE email=? AND email_verified_at IS NOT NULL', (email,)).fetchone()
        if user:
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(encoded,user['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
    return {'ok':True, 'message':'如果该邮箱已绑定账号，密码已重置，请重新登录。'}

class BindRequest(EmailProof):
    current_password: str = Field(min_length=1,max_length=128)

@router.post('/email/bind')
def bind_email(req: BindRequest, request: Request):
    owner = request.state.session['user_id']
    verification.limited('bind:'+owner, 10, 900)
    with connect() as db:
        user = db.execute('SELECT * FROM users WHERE id=?',(owner,)).fetchone()
    if not verify_password(req.current_password,user['password_hash']):
        raise HTTPException(400, '当前密码错误')
    if user['email_verified_at']:
        raise HTTPException(409, '邮箱已绑定；更换邮箱需要另外的安全验证流程')
    email = verification.normalize_email(req.email)
    verification.verify_code(req.verification_id,email,'bind',req.email_code,owner)
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            consume_code(db,req.verification_id,email,'bind',req.email_code,owner)
            count = db.execute('UPDATE users SET email=?,email_verified_at=? WHERE id=? AND email_verified_at IS NULL AND password_hash=?',(email,int(time.time()),owner,user['password_hash'])).rowcount
            if count != 1:
                raise HTTPException(409, '邮箱已绑定，请刷新账户设置')
    except sqlite3.IntegrityError:
        raise HTTPException(409, '此邮箱已绑定其他账号')
    return {'ok':True, 'message':'邮箱已验证并绑定，可用于找回密码。'}

class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1,max_length=128)
    new_password: str = Field(min_length=12,max_length=128)

@router.post('/password/change')
def change_password(req: ChangePasswordRequest, request: Request, response: Response):
    owner = request.state.session['user_id']
    verification.limited('password:'+owner, 10, 900)
    with connect() as db:
        stored = db.execute('SELECT password_hash FROM users WHERE id=?',(owner,)).fetchone()[0]
    if not verify_password(req.current_password,stored):
        raise HTTPException(400, '当前密码错误')
    encoded = hash_password(req.new_password)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        count = db.execute('UPDATE users SET password_hash=? WHERE id=? AND password_hash=?',(encoded,owner,stored)).rowcount
        if count != 1:
            raise HTTPException(409, '密码已发生变化，请重新登录')
        db.execute('DELETE FROM sessions WHERE user_id=?',(owner,))
    response.delete_cookie(COOKIE,path='/',secure=PRODUCTION,httponly=True,samesite='lax')
    return {'ok':True, 'message':'密码已修改，所有设备需要重新登录。'}

@router.get('/sessions')
def sessions(request: Request):
    current = request.state.session
    with connect() as db:
        rows = db.execute('SELECT token_hash,created_at,expires_at FROM sessions WHERE user_id=? AND expires_at>? ORDER BY created_at DESC', (current['user_id'],int(time.time()))).fetchall()
    return {'items':[{'session_id':r['token_hash'],'created_at':r['created_at'], 'expires_at':r['expires_at'], 'current':r['token_hash']==current['token_hash']} for r in rows]}

@router.delete('/sessions/others')
def revoke_other_sessions(request: Request):
    current = request.state.session
    with connect() as db:
        count = db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash<>?', (current['user_id'],current['token_hash'])).rowcount
    return {'ok':True, 'revoked':count}

@router.post('/logout')
def logout(request: Request, response: Response):
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?', (request.state.session['token_hash'],))
    response.delete_cookie(COOKIE, path='/', secure=PRODUCTION, httponly=True, samesite='lax')
    return {'ok': True}
