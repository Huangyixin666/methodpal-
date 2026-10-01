import logging
import os
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.api import agent, ai, analysis, chat, charts, datasets, methods, projects, reports, variables, jobs
from app.services.authentication import ORIGINS, PRODUCTION, router as auth_router
from app.services.database import connect, migrate
from app.services.security import SecurityMiddleware, authorize_references

logging.basicConfig(level=os.getenv('METHODPAL_LOG_LEVEL', 'INFO'))

@asynccontextmanager
async def lifespan(app):
    migrate()
    from app.services.background_jobs import start, stop
    start()
    yield
    stop()

app = FastAPI(title='MethodPal Backend', version='0.7.0', lifespan=lifespan,
              docs_url=None if PRODUCTION else '/docs', redoc_url=None if PRODUCTION else '/redoc',
              openapi_url=None if PRODUCTION else '/openapi.json')

app.add_middleware(SecurityMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=True,
                   allow_methods=['GET','POST','PUT','PATCH','DELETE','OPTIONS'],
                   allow_headers=['Content-Type','X-CSRF-Token','X-MethodPal-Request'])
hosts = [x.strip() for x in os.getenv('METHODPAL_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',') if x.strip()]
if PRODUCTION and ('*' in hosts or not os.getenv('METHODPAL_ALLOWED_HOSTS')):
    raise RuntimeError('Production requires METHODPAL_ALLOWED_HOSTS')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
app.include_router(auth_router, prefix='/api/auth', tags=['auth'])
for module, prefix in [(projects,'projects'), (datasets,'datasets'), (variables,'variables'),
                       (methods,'methods'), (analysis,'analysis'), (charts,'charts'), (ai,'ai'),
                       (agent,'agent'), (reports,'reports'), (jobs,'jobs')]:
    app.include_router(module.router, prefix='/api/' + prefix, dependencies=[Depends(authorize_references)])
app.include_router(chat.router, prefix='/api', dependencies=[Depends(authorize_references)])
app.include_router(reports.alias_router, prefix='/api/report', dependencies=[Depends(authorize_references)])

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic's default error includes input values, which can include passwords.
    return JSONResponse({'detail': '请求字段不符合要求'}, status_code=422)

@app.exception_handler(Exception)
async def internal_error(request: Request, exc):
    logging.getLogger('methodpal').error('Unhandled request failure: %s', type(exc).__name__)
    return JSONResponse({'detail': '服务处理失败，请稍后重试'}, status_code=500)

@app.get('/api/health')
def health():
    with connect() as db:
        db.execute('SELECT 1').fetchone()
    return {'ok': True, 'service': 'MethodPal Backend', 'version': '0.7.0'}
