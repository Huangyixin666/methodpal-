import re
from contextvars import ContextVar

_current_user_id = ContextVar('methodpal_current_user_id', default=None)

def normalize_user_id(user_id):
    if not user_id or not re.fullmatch(r'[a-zA-Z0-9_\-]{1,128}', str(user_id)):
        raise RuntimeError('A trusted user context is required')
    return str(user_id)

def get_current_user_id():
    return normalize_user_id(_current_user_id.get())

def set_current_user_id(user_id):
    return _current_user_id.set(normalize_user_id(user_id))

def reset_current_user_id(token):
    _current_user_id.reset(token)
