from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render


def _space_for_user(user):
    if user.is_superuser:
        return "choice"
    if getattr(user, "role", "") == "magasinier":
        return "magasin"
    return "boutique"


def portal_entry(request):
    if not request.user.is_authenticated:
        return redirect("login_page")

    space = _space_for_user(request.user)
    if space == "choice":
        return redirect("space_choice")
    if space == "magasin":
        return redirect("magasin_dashboard")
    return redirect("dashboard")


def login_page(request):
    if request.user.is_authenticated:
        return redirect("portal_entry")

    if request.method == "POST":
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=username, password=password)
        if not user:
            messages.error(request, "Identifiants invalides.")
            return render(request, "portail/login.html")

        login(request, user)
        next_url = (request.POST.get("next") or "").strip()
        if next_url.startswith("/"):
            return redirect(next_url)
        return redirect("portal_entry")

    return render(request, "portail/login.html")


@login_required
def logout_page(request):
    logout(request)
    return redirect("login_page")


@login_required
def space_choice(request):
    if not request.user.is_superuser:
        messages.error(request, "Seul le super utilisateur peut choisir librement un espace.")
        return redirect("portal_entry")
    return render(request, "portail/space_choice.html")
