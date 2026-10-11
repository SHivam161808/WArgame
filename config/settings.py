"""Django settings for the AI-DSSW Armoured Battle Console.

Everything is configurable through environment variables; defaults give a zero-setup local
install (SQLite, in-memory channel layer).
"""
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# Load KEY=VALUE pairs from .env (real environment variables win).
_env = BASE_DIR / '.env'
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith('#') and '=' in _line:
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip())
DATA_DIR = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    if os.environ.get('SECRET_KEY'):
        return os.environ['SECRET_KEY']
    f = DATA_DIR / '.secret-key'
    if not f.exists():
        f.write_text(secrets.token_urlsafe(50))
        f.chmod(0o600)
    return f.read_text().strip()


SECRET_KEY = _secret_key()
DEBUG = os.environ.get('DEBUG', '1') == '1'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')
CSRF_TRUSTED_ORIGINS = [o for o in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if o]

INSTALLED_APPS = [
    'daphne',  # ASGI runserver (HTTP + WebSockets)
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'rest_framework',
    'drf_spectacular',
    'battle',
    'webapp',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',  # replay frames compress ~10x
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'webapp.views.globals_context',
    ]},
}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'


def _database(url: str) -> dict:
    if url.startswith('postgres'):
        u = urlparse(url)
        return {'ENGINE': 'django.db.backends.postgresql', 'NAME': u.path.lstrip('/'), 'USER': u.username or '',
                'PASSWORD': u.password or '', 'HOST': u.hostname or 'localhost', 'PORT': str(u.port or 5432),
                'CONN_MAX_AGE': 60}
    # SQLite is only for quick tests: DATABASE_URL=sqlite:///path or sqlite:
    path = url.replace('sqlite:///', '/').replace('sqlite:', '') or str(DATA_DIR / 'wargame.sqlite3')
    return {'ENGINE': 'django.db.backends.sqlite3', 'NAME': path,
            'OPTIONS': {'timeout': 30, 'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;'}}


# PostgreSQL is the primary database (tools/pg_local.sh starts a project-local cluster).
DATABASES = {'default': _database(os.environ.get('DATABASE_URL', 'postgres://wargame@127.0.0.1:5433/wargame'))}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Single-process in-memory layer by default; set REDIS_URL to scale out (channels-redis, BSD).
if os.environ.get('REDIS_URL'):
    CHANNEL_LAYERS = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer',
                                  'CONFIG': {'hosts': [os.environ['REDIS_URL']]}}}
else:
    CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer', 'CONFIG': {'capacity': 2000}}}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
]
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/console/'

LANGUAGE_CODE = 'en-gb'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = False
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Security headers (AC-3)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False  # the JS client reads it for X-CSRFToken
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = os.environ.get('HTTPS', '0') == '1'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.AnonRateThrottle', 'rest_framework.throttling.UserRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {'anon': '120/min', 'user': '2000/min', 'join': '20/min'},
}
SPECTACULAR_SETTINGS = {
    'TITLE': 'AI-DSSW Armoured Battle Console API',
    'DESCRIPTION': 'Scenario administration, terrain, zones, force keys, battles/ticks, analytics, assets and ORBAT. '
                   'Live play uses the WebSocket protocol at /ws/battle/<room_code>/ (see SKILL.md §6).',
    'VERSION': '1.0.0',
}

LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {'battle': {'handlers': ['console'], 'level': os.environ.get('LOG_LEVEL', 'INFO')}},
}

# Game settings
WARGAME = {
    'TILE_CACHE': DATA_DIR / 'tiles',
    'OFFLINE_TERRAIN': os.environ.get('OFFLINE_TERRAIN', '0') == '1',
    'ADMIN_USER': os.environ.get('ADMIN_USER', 'admin'),
    'ADMIN_PASSWORD': os.environ.get('ADMIN_PASSWORD', 'admin123'),
    'MAX_PLAYERS_PER_SIDE': 4,
    'VENDOR_LOCAL': os.environ.get('VENDOR_LOCAL', '0') == '1',
    'CESIUM_VERSION': '1.124',
}
