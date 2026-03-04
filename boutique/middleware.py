from django.shortcuts import redirect
from django.urls import reverse


class SpaceAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.public_paths = {
            "/connexion/",
            "/deconnexion/",
            "/admin/login/",
            "/favicon.ico",
            "/robots.txt",
        }

    def __call__(self, request):
        path = request.path or "/"
        if path.startswith("/static/") or path.startswith("/media/") or path.startswith("/admin/"):
            return self.get_response(request)

        if path in self.public_paths:
            return self.get_response(request)

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            login_url = reverse("login_page")
            return redirect(f"{login_url}?next={path}")

        if user.is_superuser:
            return self.get_response(request)

        role = getattr(user, "role", "")
        if path.startswith("/magasin/") and role != "magasinier":
            return redirect("portal_entry")
        if path.startswith("/boutique/") and role == "magasinier":
            return redirect("portal_entry")
        if path == "/" and role == "magasinier":
            return redirect("magasin_dashboard")
        return self.get_response(request)
