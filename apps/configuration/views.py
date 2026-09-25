from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import ClickFunnelsConfig
from .forms import ClickFunnelsSettingsForm, TaraConfigSettingsForm, TeamSelectionForm, WorkspaceSelectionForm
from .services import ConfigurationService
from integrations.clickfunnels.client import ClickFunnelsClient
from apps.payments.models import TaraConfig, TaraWebhookEvent
from apps.payments.services import TaraConfigService

@login_required
def settings_view(request):
    """
    Refactored settings view for multi-step configuration.
    """
    service = ConfigurationService()
    config = service.get_active_config() or ClickFunnelsConfig.objects.first()
    
    # Initialize forms
    token_form = ClickFunnelsSettingsForm(instance=config)
    team_form = None
    workspace_form = None

    if config:
        # Step 2: Fetch teams if token valid
        if config.validation_status == ClickFunnelsConfig.ValidationStatus.VALID:
            try:
                client = ClickFunnelsClient.from_configuration(config)
                service.client = client
                teams = config.raw_payload.get("available_teams")
                if not teams:
                    teams = service.fetch_teams(config)
                
                team_form = TeamSelectionForm(initial={"team_id": config.team_id})
                team_form.fields["team_id"].choices = [(t["id"], t["name"]) for t in teams]
            except Exception as e:
                messages.error(request, str(e))

        # Step 3: Fetch workspaces if team selected
        if config.team_id:
            try:
                client = ClickFunnelsClient.from_configuration(config)
                service.client = client
                workspaces = config.raw_payload.get("available_workspaces")
                if not workspaces:
                    workspaces = service.fetch_workspaces(config, config.team_id)
                
                workspace_form = WorkspaceSelectionForm(initial={"workspace_id": config.workspace_id})
                workspace_form.fields["workspace_id"].choices = [(w["id"], f"{w['name']} ({w.get('subdomain', 'no subdomain')})") for w in workspaces]
            except Exception as e:
                messages.error(request, str(e))

    if request.method == "POST":
        action = request.POST.get("action")
        
        if action == "save_token":
            form = ClickFunnelsSettingsForm(request.POST, instance=config)
            if form.is_valid():
                token = form.cleaned_data.pop("api_access_token", None)
                instance = form.save(commit=False)
                service.update_credentials(instance, token)
                messages.success(request, "Clé enregistrée. Cliquez maintenant sur « Tester la connexion ».")
                return redirect("configuration:settings")
        
        elif action == "verify_token":
            if not config:
                messages.error(request, "Enregistrez d’abord votre clé d’accès ClickFunnels.")
            else:
                try:
                    client = ClickFunnelsClient.from_configuration(config)
                    service.client = client
                    service.verify_token(config)
                    service.fetch_teams(config) # Auto-fetch teams on success
                    messages.success(request, "Connexion réussie ✓ — choisissez maintenant votre équipe.")
                except Exception as e:
                    messages.error(request, f"ClickFunnels refuse cette clé d’accès. Vérifiez qu’elle a été copiée en entier, enregistrez-la à nouveau puis réessayez. (Détail : {str(e)})")
            return redirect("configuration:settings")

        elif action == "select_team":
            form = TeamSelectionForm(request.POST)
            # Need to re-populate choices before validation
            teams = config.raw_payload.get("available_teams", [])
            form.fields["team_id"].choices = [(t["id"], t["name"]) for t in teams]
            
            if form.is_valid():
                team_id = form.cleaned_data["team_id"]
                service.select_team(config, team_id)
                try:
                    client = ClickFunnelsClient.from_configuration(config)
                    service.client = client
                    service.fetch_workspaces(config, team_id)
                    messages.success(request, "Équipe choisie — choisissez maintenant votre espace de travail.")
                except Exception as e:
                    messages.error(request, str(e))
            return redirect("configuration:settings")

        elif action == "select_workspace":
            form = WorkspaceSelectionForm(request.POST)
            workspaces = config.raw_payload.get("available_workspaces", [])
            form.fields["workspace_id"].choices = [(w["id"], w["name"]) for w in workspaces]
            
            if form.is_valid():
                try:
                    service.select_workspace(config, form.cleaned_data["workspace_id"])
                    messages.success(request, f"C’est fait ✓ — ClickFunnels est connecté à « {config.workspace_name} ».")
                except Exception as e:
                    messages.error(request, str(e))
            return redirect("configuration:settings")

    token_ok = bool(config and config.validation_status == ClickFunnelsConfig.ValidationStatus.VALID)
    context = {
        "config": config,
        "token_form": token_form,
        "team_form": team_form,
        "workspace_form": workspace_form,
        # Display-only flags for the plain-language status banner.
        "token_ok": token_ok,
        "connected": bool(token_ok and config.workspace_subdomain),
    }
    return render(request, "configuration/settings.html", context)

@login_required
def tara_settings_view(request):
    """
    Separate sibling page to settings_view above — same @login_required
    authorization rule (no repository evidence for a stricter/Tara-specific
    permission), same "reload the current config server-side, bind a
    ModelForm to it, pop the write-only secrets, hand them to the *Service.
    update_credentials() helper, save" shape as settings_view's "save_token"
    branch. Deliberately has no verify/team/workspace steps and no
    connection-test action — TaraConfigService has no such method (Phase 2:
    no safe, documented, read-only Tara endpoint exists to check credentials
    against) and none is added here.
    """
    service = TaraConfigService()
    config = service.get_active_config() or TaraConfig.objects.first()
    form = TaraConfigSettingsForm(instance=config)

    if request.method == "POST":
        form = TaraConfigSettingsForm(request.POST, instance=config)
        if form.is_valid():
            api_key = form.cleaned_data.pop("api_key", None)
            webhook_secret = form.cleaned_data.pop("webhook_secret", None)
            instance = form.save(commit=False)
            service.update_credentials(instance, api_key, webhook_secret)
            instance.save()
            messages.success(request, "Connexion Tara enregistrée.")
            return redirect("configuration:tara_settings")
        messages.error(request, "Merci de corriger les erreurs ci-dessous.")

    webhook_url = f"{settings.PUBLIC_BASE_URL}/api/tara/webhook/" if settings.PUBLIC_BASE_URL else ""

    context = {
        "config": config,
        "form": form,
        "webhook_url": webhook_url,
        "api_key_configured": bool(config.api_key) if config else False,
        "webhook_secret_configured": bool(config.webhook_secret) if config else False,
        # Display-only: Tara offers no credential test, so the last notification
        # received is the one honest signal that the connection works.
        "last_tara_notification": TaraWebhookEvent.objects.order_by("-received_at").values_list("received_at", flat=True).first(),
    }
    return render(request, "configuration/tara_settings.html", context)


@login_required
def verify_connection(request):
    """Action remains for backward compatibility or direct trigger."""
    if request.method == "POST":
        service = ConfigurationService()
        config = service.get_active_config()
        if config:
            try:
                client = ClickFunnelsClient.from_configuration(config)
                service.client = client
                service.verify_token(config)
                service.fetch_teams(config)
                if config.team_id:
                    service.fetch_workspaces(config, config.team_id)
                messages.success(request, "Connexion ClickFunnels actualisée.")
            except Exception as e:
                messages.error(request, str(e))
    return redirect("configuration:settings")
