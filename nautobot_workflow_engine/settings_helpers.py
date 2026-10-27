"""Read and write the Workflow Engine settings.

Every setting can be changed on the app's Settings page (the gear next to Workflow
Engine under Installed Apps), which stores it with Nautobot's Constance config. A key
that is also set in ``PLUGINS_CONFIG`` in ``nautobot_config.py`` wins and is shown
locked on the page.
"""

import os

from django.conf import settings

APP_NAME = "nautobot_workflow_engine"

DEFAULT_MODEL_ALLOWLIST = [
    "dcim.*",
    "ipam.*",
    "circuits.*",
    "tenancy.*",
    "virtualization.*",
    "cloud.*",
    "wireless.*",
    "vpn.*",
    "load_balancers.*",
    "extras.tag",
    "extras.status",
    "extras.role",
    "extras.dynamicgroup",
]

DEFAULTS = {
    "workflow_dir": "workflows",
    "filters_dir": "filters",
    "scripts_dir": "scripts",
    "show_disabled": False,
    "show_add_new_item": [item.strip() for item in os.getenv("SHOW_ADD_NEW_ITEM", "").split(",") if item.strip()],
    "builder_model_allowlist": list(DEFAULT_MODEL_ALLOWLIST),
    "git_committer_name": "Nautobot Workflow Engine",
    "git_committer_email": "nautobot-workflow-engine@localhost",
    "git_author_email_domain": "users.noreply.nautobot.local",
    "git_sync_interval_minutes": 5,
    "git_remote_timeout_seconds": 10,
    "git_push_lock_timeout": 300,
    "git_push_retry_backoff_minutes": [1, 5, 15, 60],
    "awx_external_integration_name": "AWX",
    "awx_poll_seconds": 5,
}

# Label, help text and the group each setting is shown in on the Settings page.
SETTING_META = {
    "workflow_dir": ("Workflows folder", "Folder in each repository that holds the workflow files.", "repository"),
    "filters_dir": ("Filters folder", "Folder in each repository that holds custom template filters.", "repository"),
    "scripts_dir": (
        "Scripts folder",
        "Folder in each repository that holds the scripts workflow steps can run. New scripts from the builder are saved here.",
        "repository",
    ),
    "show_disabled": (
        "Show switched-off workflows",
        "List workflows that are switched off as well, so people can see them but not run them.",
        "builder",
    ),
    "show_add_new_item": (
        "Offer “Create new” for",
        "Object types whose pickers on the run form start with a “Create new” choice.",
        "builder",
    ),
    "builder_model_allowlist": (
        "Object types the builder offers",
        "One per line. Use app.model, or app.* for a whole app, for example dcim.* or extras.tag. Leave empty for the usual set.",
        "builder",
    ),
    "git_committer_name": (
        "Committer name",
        "Name shown as the committer on changes published to Git. The author is always the person who published.",
        "publishing",
    ),
    "git_committer_email": (
        "Committer email",
        "Email shown as the committer on changes published to Git.",
        "publishing",
    ),
    "git_author_email_domain": (
        "Author email domain",
        "Used to make an author email for people who have no email address in Nautobot.",
        "publishing",
    ),
    "git_sync_interval_minutes": (
        "Check repositories every (minutes)",
        "How often Nautobot checks repositories for new changes and retries publishes that are waiting.",
        "publishing",
    ),
    "git_remote_timeout_seconds": (
        "Give up reaching a repository after (seconds)",
        "How long to wait for a repository server before saying it can't be reached.",
        "publishing",
    ),
    "git_push_lock_timeout": (
        "Longest a publish may take (seconds)",
        "A publish that takes longer is treated as stuck, so the next one can go ahead.",
        "publishing",
    ),
    "git_push_retry_backoff_minutes": (
        "Retry a waiting publish after (minutes)",
        "Comma-separated waits between retries when the repository can't be reached, for example 1, 5, 15, 60.",
        "publishing",
    ),
    "awx_external_integration_name": (
        "AWX connection",
        "The External Integration used for steps that run AWX job templates.",
        "awx",
    ),
    "awx_poll_seconds": ("Check AWX jobs every (seconds)", "How often a running AWX job is checked.", "awx"),
}

SETTING_GROUPS = (
    ("repository", "Repository folders"),
    ("builder", "Builder and run form"),
    ("publishing", "Publishing to Git"),
    ("awx", "AWX"),
)

SETTING_KEYS = tuple(SETTING_META)

FILE_OVERRIDE_HELP = "Set in nautobot_config.py, so it can't be changed here."


def constance_key(key):
    """Return the Constance attribute name for an app setting."""
    return f"{APP_NAME}__{key}"


def is_file_locked(key):
    """True when ``key`` is set in ``PLUGINS_CONFIG``, which then wins over the Settings page."""
    return key in (settings.PLUGINS_CONFIG.get(APP_NAME) or {})


def get_setting(key, fallback=None):
    """Return one setting: ``PLUGINS_CONFIG`` first, then the Settings page, then the default."""
    plugin_config = settings.PLUGINS_CONFIG.get(APP_NAME) or {}
    if key in plugin_config:
        return plugin_config[key]
    if key in DEFAULTS:
        try:
            from constance import config

            return getattr(config, constance_key(key))
        except Exception:  # pylint: disable=broad-except
            # The Constance table may not exist yet, for example during the first migrate.
            return DEFAULTS[key]
    return fallback


def content_type_setting_value(content_type):
    """Return the stored value for a ContentType, ``app_label.model``."""
    return f"{content_type.app_label}.{content_type.model}"


def content_type_matches(content_type, configured):
    """True when a ContentType is listed, by ``app_label.model`` or ``app_label.<verbose name>``."""
    labels = {str(item).strip().lower() for item in (configured or []) if str(item).strip()}
    if not labels:
        return False
    return bool(
        {content_type_setting_value(content_type).lower(), f"{content_type.app_label}.{content_type.name}".lower()}
        & labels
    )


def apply_git_sync_interval(minutes):
    """Point the Git sync schedule at a new interval. Returns True when it changed."""
    from nautobot.extras.models import ScheduledJob

    from .signals import GIT_SCHEDULE_NAME, git_sync_crontab

    crontab = git_sync_crontab(minutes)
    changed = False
    # save() rather than update(), so the scheduler notices the change.
    for scheduled in ScheduledJob.objects.filter(name=GIT_SCHEDULE_NAME).exclude(crontab=crontab):
        scheduled.crontab = crontab
        scheduled.save()
        changed = True
    return changed
