from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from battle.models import Battle, Scenario


def globals_context(request):
    w = settings.WARGAME
    return {'CESIUM_VERSION': w['CESIUM_VERSION'], 'VENDOR_LOCAL': w['VENDOR_LOCAL']}


@ensure_csrf_cookie
def home(request):
    return render(request, 'home.html', {'room': request.GET.get('room', '')})


@ensure_csrf_cookie
def login_view(request):
    error = None
    if request.method == 'POST':
        user = authenticate(request, username=request.POST.get('username'), password=request.POST.get('password'))
        if user and user.is_staff:
            login(request, user)
            return redirect(request.GET.get('next') or 'console')
        error = 'Invalid staff credentials'
    return render(request, 'login.html', {'error': error})


def logout_view(request):
    logout(request)
    return redirect('home')


def _own(request, sid):
    qs = Scenario.objects.all() if request.user.is_superuser else Scenario.objects.filter(owner=request.user)
    return get_object_or_404(qs, id=sid)


@staff_member_required(login_url='login')
@ensure_csrf_cookie
def console(request):
    return render(request, 'console/index.html')


@staff_member_required(login_url='login')
@ensure_csrf_cookie
def scenario_setup(request, sid):
    return render(request, 'console/scenario.html', {'scenario': _own(request, sid)})


@staff_member_required(login_url='login')
@ensure_csrf_cookie
def live(request, sid):
    return render(request, 'battle.html', {'scenario': _own(request, sid), 'mode': 'admin'})


@staff_member_required(login_url='login')
@ensure_csrf_cookie
def assets(request):
    return render(request, 'console/assets.html')


@staff_member_required(login_url='login')
@ensure_csrf_cookie
def orbat(request):
    return render(request, 'console/orbat.html')


@ensure_csrf_cookie
def play(request, code):
    return render(request, 'battle.html', {'room_code': code.upper(), 'mode': 'player'})


@ensure_csrf_cookie
def aar(request, bid):
    b = get_object_or_404(Battle, id=bid)
    return render(request, 'aar.html', {'battle': b})
