from django import forms
from django.contrib.auth.forms import AuthenticationForm,UserCreationForm,SetPasswordForm
from .models import User
from .policy import auth_allowed,auth_failure


def style_form(form):
    for field in form.fields.values():
        if isinstance(field.widget,forms.HiddenInput):
            continue
        field.widget.attrs["class"]="form-check-input" if isinstance(field.widget,forms.CheckboxInput) else ("form-select" if isinstance(field.widget,forms.Select) else "form-control")
    return form


class RateLimitedAuthenticationForm(AuthenticationForm):
    def clean(self):
        username=self.cleaned_data.get("username") or ""
        ip=self.request.META.get("REMOTE_ADDR","")
        if not auth_allowed(username,ip):
            raise forms.ValidationError("Te veel pogingen of login tijdelijk niet beschikbaar. Probeer later opnieuw.")
        try:
            return super().clean()
        except forms.ValidationError:
            auth_failure(username,ip)
            raise


class ConfirmPasswordForm(forms.Form):
    password=forms.CharField(label="Je huidige wachtwoord",widget=forms.PasswordInput(attrs={"autocomplete":"current-password"}))
    def __init__(self,*args,user,request,**kwargs):
        self.user,self.request=user,request
        super().__init__(*args,**kwargs);style_form(self)
    def clean_password(self):
        password=self.cleaned_data["password"]
        ip=self.request.META.get("REMOTE_ADDR","")
        if not auth_allowed(self.user.get_username(),ip):
            raise forms.ValidationError("Te veel pogingen. Probeer later opnieuw.")
        if not self.user.check_password(password):
            auth_failure(self.user.get_username(),ip)
            raise forms.ValidationError("Wachtwoord klopt niet.")
        return password


class AccountFormMixin:
    def clean_email(self):
        email=self.cleaned_data["email"].strip()
        users=User.objects.filter(email__iexact=email)
        if self.instance.pk:
            users=users.exclude(pk=self.instance.pk)
        if users.exists():
            raise forms.ValidationError("Dit e-mailadres is al in gebruik.")
        return email


class AccountCreateForm(AccountFormMixin,UserCreationForm):
    email=forms.EmailField(label="E-mailadres",required=True)
    platform_admin=forms.BooleanField(label="Platformbeheerder",required=False)
    class Meta(UserCreationForm.Meta):
        model=User
        fields=["username","email","first_name","last_name","platform_admin","mfa_required"]
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);style_form(self)
        self.fields["mfa_required"].label="2FA verplicht voor deze gebruiker"
        for key in ("password1","password2"):
            self.fields[key].widget.attrs["autocomplete"]="new-password"


class AccountUpdateForm(AccountFormMixin,forms.ModelForm):
    email=forms.EmailField(label="E-mailadres",required=True)
    platform_admin=forms.BooleanField(label="Platformbeheerder",required=False)
    class Meta:
        model=User
        fields=["email","first_name","last_name","is_active","platform_admin","mfa_required"]
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);style_form(self)
        self.fields["platform_admin"].initial=self.instance.is_superuser
        self.fields["mfa_required"].label="2FA verplicht voor deze gebruiker"


class AdminPasswordForm(SetPasswordForm):
    actor_password=forms.CharField(label="Je eigen wachtwoord ter bevestiging",widget=forms.PasswordInput)
    def __init__(self,user,*args,actor,request,**kwargs):
        self.actor,self.request=actor,request
        super().__init__(user,*args,**kwargs);style_form(self)
    def clean_actor_password(self):
        check=ConfirmPasswordForm({"password":self.cleaned_data.get("actor_password")},user=self.actor,request=self.request)
        if not check.is_valid():
            raise forms.ValidationError("Bevestig met je eigen huidige wachtwoord, of probeer later opnieuw.")
        return self.cleaned_data["actor_password"]


class ResetMFAForm(ConfirmPasswordForm):
    reason=forms.CharField(label="Reden voor herstel",max_length=200,widget=forms.Textarea(attrs={"rows":2}))


class MemberForm(forms.Form):
    email=forms.EmailField(label="E-mailadres van bestaande gebruiker")
    role=forms.ChoiceField(label="Projectrol",choices=[("viewer","Lezer"),("editor","Bewerker"),("admin","Projectbeheerder")])
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);style_form(self)
