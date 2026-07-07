from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import ClickFunnelsConfig
from .forms import ClickFunnelsSettingsForm, TeamSelectionForm, WorkspaceSelectionForm
from .services import ConfigurationService
from integrations.clickfunnels.client import ClickFunnelsClient

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
                messages.success(request, "Basic settings saved.")
                return redirect("configuration:settings")
        
        elif action == "verify_token":
            if not config:
                messages.error(request, "Save configuration first.")
            else:
                try:
                    client = ClickFunnelsClient.from_configuration(config)
                    service.client = client
                    service.verify_token(config)
                    service.fetch_teams(config) # Auto-fetch teams on success
                    messages.success(request, "Token verified and teams loaded.")
                except Exception as e:
                    messages.error(request, f"The API access token is invalid or unauthorized. Error: {str(e)}")
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
                    messages.success(request, "Team selected and workspaces loaded.")
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
                    messages.success(request, f"Workspace '{config.workspace_name}' is now active.")
                except Exception as e:
                    messages.error(request, str(e))
            return redirect("configuration:settings")

    context = {
        "config": config,
        "token_form": token_form,
        "team_form": team_form,
        "workspace_form": workspace_form,
    }
    return render(request, "configuration/settings.html", context)

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
                messages.success(request, "Full integration bridge refreshed.")
            except Exception as e:
                messages.error(request, str(e))
    return redirect("configuration:settings")
