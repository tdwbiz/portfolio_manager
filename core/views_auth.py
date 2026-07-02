"""Authentication views and helpers."""

import logging

from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.conf import settings
from django.core.cache import cache

from core.views import _get_client_ip, _check_login_attempts, _record_login_attempt, is_safe_url

logger = logging.getLogger(__name__)


def login(request):
    """Login view - handles both GET and POST with rate limiting."""
    if request.method == 'POST':
        client_ip = _get_client_ip(request)
        
        # SECURITY: Rate limit login attempts
        if not _check_login_attempts(client_ip):
            logger.warning(f'Login rate limit exceeded for IP: {client_ip}')
            return render(request, 'core/login.html', {
                'error': 'Too many login attempts. Please try again later.',
            }, status=429)
        
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        
        # SECURITY: Validate inputs
        if not username or not password:
            _record_login_attempt(client_ip)
            return render(request, 'core/login.html', {
                'error': 'Username and password are required.',
            })
        
        user = User.objects.filter(username=username).first()
        if user and user.check_password(password):
            # Clear rate limit on successful login
            cache.delete(f'login_attempts:{client_ip}')
            auth_login(request, user)
            logger.info(f'User {username} logged in successfully from IP: {client_ip}')
            next_url = request.POST.get('next', '/')
            # SECURITY: Validate redirect URL to prevent open redirect
            if is_safe_url(next_url):
                return redirect(next_url)
            return redirect('/')
        else:
            _record_login_attempt(client_ip)
            logger.warning(f'Failed login attempt for user {username} from IP: {client_ip}')
            return render(request, 'core/login.html', {
                'error': 'Invalid username or password.',
            })
    else:
        return render(request, 'core/login.html')


@login_required
def logout(request):
    """Logout view - handles POST redirect."""
    auth_logout(request)
    return redirect('login')
