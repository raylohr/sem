from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.urls import reverse
from .policy import has_mfa,mfa_required


class AccountPolicyMiddleware:
    """Enforce password changes and MFA for HTML, API, downloads and admin alike."""
    def __init__(self,get_response):
        self.get_response=get_response

    def __call__(self,request):
        response=self.enforce(request)
        if request.path.startswith(('/accounts/','/workspace/users/')):
            from django.utils.cache import patch_cache_control
            patch_cache_control(response,no_cache=True,no_store=True,private=True,must_revalidate=True)
            # Keep same-origin form provenance: no-referrer makes Chromium send
            # Origin: null on navigation POSTs, which Django must reject.
            response['Referrer-Policy']='same-origin'
        return response

    def enforce(self,request):
        user=request.user
        path=request.path
        public={reverse("login"),reverse("logout"),reverse("password_reset"),reverse("password_reset_done"),reverse("password_reset_complete"),"/health/","/ready/"}
        if path.startswith("/static/") or path in public or path.startswith("/accounts/reset/") or not user.is_authenticated:
            return self.get_response(request)
        if path.startswith("/admin/") and path!=reverse("admin:login") and not user.is_superuser:
            return HttpResponseForbidden("Geen platformbeheerrechten.")
        if user.must_change_password and path!=reverse("password_change"):
            return redirect_to_login(request.path,login_url=reverse("password_change"))
        if has_mfa(user):
            if not user.is_verified():
                return redirect_to_login(request.path,login_url=reverse("login"))
        elif mfa_required(user):
            allowed={reverse("two_factor:setup"),reverse("two_factor:qr"),reverse("two_factor:profile"),reverse("accounts:confirm_password"),reverse("password_change")}
            if path not in allowed:
                return redirect_to_login(request.path,login_url=reverse("two_factor:setup"))
        return self.get_response(request)
