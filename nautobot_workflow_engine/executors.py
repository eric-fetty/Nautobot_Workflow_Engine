"""Workflow execution engine with IP address filtering capabilities."""

import contextlib
import importlib.util
import io
import ipaddress
import json
import os
import pathlib
import sys
import tempfile
import time
import traceback
from collections.abc import Mapping

import requests
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from jinja2 import Environment
from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.models import ExternalIntegration, SecretsGroupAssociation

from .models import AwxRun, WorkflowRun

PLUGIN_CFG = settings.PLUGINS_CONFIG.get("nautobot_workflow_engine", {})


def _should_show_workflow(workflow):
    """Check if a workflow should be shown based on enabled status and settings."""
    show_disabled = PLUGIN_CFG.get("show_disabled", False)
    return workflow.enabled or show_disabled


def _get_workflow_queryset():
    """Get workflow queryset respecting the show_disabled setting."""
    from .models import Workflow

    show_disabled = PLUGIN_CFG.get("show_disabled", False)
    if show_disabled:
        return Workflow.objects.all()
    else:
        return Workflow.objects.filter(enabled=True)


def _ip_address(value):
    """Convert string to IPv4 or IPv6 address object."""
    try:
        return ipaddress.ip_address(value)
    except ValueError as e:
        raise ValueError(f"Invalid IP address '{value}': {e}")


def _ip_network(value, strict=True):
    """Convert string to IPv4 or IPv6 network object."""
    try:
        return ipaddress.ip_network(value, strict=strict)
    except ValueError as e:
        raise ValueError(f"Invalid IP network '{value}': {e}")


def _ip_interface(value):
    """Convert string to IPv4 or IPv6 interface object."""
    try:
        return ipaddress.ip_interface(value)
    except ValueError as e:
        raise ValueError(f"Invalid IP interface '{value}': {e}")


def _network_address(value):
    """Get network address from IP network or interface."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    elif hasattr(value, "network"):  # interface object
        return value.network.network_address
    return value.network_address


def _broadcast_address(value):
    """Get broadcast address from IP network."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    elif hasattr(value, "network"):  # interface object
        return value.network.broadcast_address
    return value.broadcast_address


def _netmask(value):
    """Get netmask from IP network or interface."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    elif hasattr(value, "network"):  # interface object
        return value.network.netmask
    return value.netmask


def _hostmask(value):
    """Get hostmask from IP network or interface."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    elif hasattr(value, "network"):  # interface object
        return value.network.hostmask
    return value.hostmask


def _prefixlen(value):
    """Get prefix length from IP network or interface."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    elif hasattr(value, "network"):  # interface object
        return value.network.prefixlen
    return value.prefixlen


def _num_addresses(value):
    """Get number of addresses in IP network."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    elif hasattr(value, "network"):  # interface object
        return value.network.num_addresses
    return value.num_addresses


def _subnets(value, prefixlen_diff=1, new_prefix=None):
    """Get subnets from IP network."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    elif hasattr(value, "network"):  # interface object
        value = value.network

    if new_prefix is not None:
        return list(value.subnets(new_prefix=new_prefix))
    else:
        return list(value.subnets(prefixlen_diff=prefixlen_diff))


def _supernet(value, prefixlen_diff=1, new_prefix=None):
    """Get supernet from IP network."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    elif hasattr(value, "network"):  # interface object
        value = value.network

    if new_prefix is not None:
        return value.supernet(new_prefix=new_prefix)
    else:
        return value.supernet(prefixlen_diff=prefixlen_diff)


def _subnet_of(value, other):
    """Check if network is subnet of another network."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    if isinstance(other, str):
        other = _ip_network(other, strict=False)

    if hasattr(value, "network"):  # interface object
        value = value.network
    if hasattr(other, "network"):  # interface object
        other = other.network

    return value.subnet_of(other)


def _supernet_of(value, other):
    """Check if network is supernet of another network."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    if isinstance(other, str):
        other = _ip_network(other, strict=False)

    if hasattr(value, "network"):  # interface object
        value = value.network
    if hasattr(other, "network"):  # interface object
        other = other.network

    return value.supernet_of(other)


def _overlaps(value, other):
    """Check if two networks overlap."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    if isinstance(other, str):
        other = _ip_network(other, strict=False)

    if hasattr(value, "network"):  # interface object
        value = value.network
    if hasattr(other, "network"):  # interface object
        other = other.network

    return value.overlaps(other)


def _address_exclude(value, other):
    """Return networks that are in value but not in other."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    if isinstance(other, str):
        other = _ip_network(other, strict=False)

    if hasattr(value, "network"):  # interface object
        value = value.network
    if hasattr(other, "network"):  # interface object
        other = other.network

    return list(value.address_exclude(other))


def _hosts(value):
    """Get host addresses from IP network."""
    if isinstance(value, str):
        value = _ip_network(value, strict=False)
    elif hasattr(value, "network"):  # interface object
        value = value.network

    return list(value.hosts())


def _ip_in_network(ip_addr, network):
    """Check if IP address is in network."""
    if isinstance(ip_addr, str):
        ip_addr = _ip_address(ip_addr)
    if isinstance(network, str):
        network = _ip_network(network, strict=False)
    elif hasattr(network, "network"):  # interface object
        network = network.network

    return ip_addr in network


def _is_private(value):
    """Check if IP address or network is private."""
    if isinstance(value, str):
        try:
            value = _ip_address(value)
        except ValueError:
            value = _ip_network(value, strict=False)

    if hasattr(value, "network"):  # interface object
        return value.network.is_private

    return value.is_private


def _is_global(value):
    """Check if IP address or network is global."""
    if isinstance(value, str):
        try:
            value = _ip_address(value)
        except ValueError:
            value = _ip_network(value, strict=False)

    if hasattr(value, "network"):  # interface object
        return value.network.is_global

    return value.is_global


def _is_multicast(value):
    """Check if IP address or network is multicast."""
    if isinstance(value, str):
        try:
            value = _ip_address(value)
        except ValueError:
            value = _ip_network(value, strict=False)

    if hasattr(value, "network"):  # interface object
        return value.network.is_multicast

    return value.is_multicast


def _is_reserved(value):
    """Check if IP address or network is reserved."""
    if isinstance(value, str):
        try:
            value = _ip_address(value)
        except ValueError:
            value = _ip_network(value, strict=False)

    if hasattr(value, "network"):  # interface object
        return value.network.is_reserved

    return value.is_reserved


def _ip_version(value):
    """Get IP version (4 or 6) of address or network."""
    if isinstance(value, str):
        try:
            value = _ip_address(value)
        except ValueError:
            value = _ip_network(value, strict=False)

    if hasattr(value, "network"):  # interface object
        return value.network.version

    return value.version


def _from_json(value):
    """
    Convert a JSON string to a Python data structure.

    Args:
        value: JSON string to parse

    Returns:
        Parsed Python object (dict, list, etc.) or None if parsing fails
    """
    if value is None:
        return None

    if not isinstance(value, str):
        # If already a data structure, return as-is
        return value

    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        # Return None on parse error to avoid breaking template rendering
        return None


def _get_builtin_filters():
    """Return the built-in Jinja filters shipped with the plugin."""
    return {
        "ip_address": _ip_address,
        "ip_network": _ip_network,
        "ip_interface": _ip_interface,
        "network_address": _network_address,
        "broadcast_address": _broadcast_address,
        "netmask": _netmask,
        "hostmask": _hostmask,
        "prefixlen": _prefixlen,
        "num_addresses": _num_addresses,
        "subnets": _subnets,
        "supernet": _supernet,
        "subnet_of": _subnet_of,
        "supernet_of": _supernet_of,
        "overlaps": _overlaps,
        "address_exclude": _address_exclude,
        "hosts": _hosts,
        "ip_in_network": _ip_in_network,
        "is_private": _is_private,
        "is_global": _is_global,
        "is_multicast": _is_multicast,
        "is_reserved": _is_reserved,
        "ip_version": _ip_version,
        "fromjson": _from_json,
    }


_repo_pkg_cache = {}
_repo_filter_cache = {}


def _repo_revision_key(repo):
    """Get a revision key for the repository to use in caching."""
    for attr in ("current_head", "head", "commit", "tracked_commit", "last_updated"):
        val = getattr(repo, attr, None)
        if val:
            return str(val)
    return "db"


def _repo_cache_key(repo):
    """Build the cache key for a repository-backed runtime artifact."""
    return (repo.id, _repo_revision_key(repo))


def _get_internal_manager(model_class):
    """Return an unrestricted manager for internal support models when available."""
    return getattr(model_class, "_base_manager", model_class.objects)


def clear_repo_script_cache(repo):
    """Clear cached repository packages, filters, and Python modules for the given repository."""
    # Clear our package cache
    to_drop = [key for key in list(_repo_pkg_cache.keys()) if key[0] == repo.id]
    cached_dirs = []

    for key in to_drop:
        cached_dir = _repo_pkg_cache.pop(key, None)
        if cached_dir:
            cached_dirs.append(cached_dir)
            # Remove from sys.path if it exists
            if cached_dir in sys.path:
                sys.path.remove(cached_dir)

    for key in [key for key in list(_repo_filter_cache.keys()) if key[0] == repo.id]:
        _repo_filter_cache.pop(key, None)

    # Only clear Python modules that were loaded from our temporary directories
    modules_to_remove = []
    for module_name, module in list(sys.modules.items()):
        if hasattr(module, "__file__") and module.__file__:
            module_path = str(module.__file__)
            # Check if this module was loaded from any of our temp directories
            for cached_dir in cached_dirs:
                if module_path.startswith(cached_dir):
                    modules_to_remove.append(module_name)
                    break

    # Remove only the modules from our temp directories
    for module_name in modules_to_remove:
        sys.modules.pop(module_name, None)


def _ensure_materialized_package_parent(dst_file, temp_root):
    """Ensure each parent directory between dst_file and temp_root is a Python package."""
    cur = dst_file.parent
    while True:
        init_file = cur / "__init__.py"
        if not init_file.exists():
            cur.mkdir(parents=True, exist_ok=True)
            init_file.write_text("")
        if cur == temp_root:
            break
        cur = cur.parent


def _module_comes_from_temp_path(module):
    """Return True when a loaded module originates from a temporary filesystem path."""
    module_file = getattr(module, "__file__", None)
    if module_file and str(module_file).startswith(tempfile.gettempdir()):
        return True

    module_paths = getattr(module, "__path__", None)
    if module_paths:
        return any(str(path).startswith(tempfile.gettempdir()) for path in module_paths)

    return False


def _clear_temp_package_modules(*package_names):
    """Remove temp-backed top-level packages and descendants from sys.modules."""
    packages = tuple(name for name in package_names if name)
    if not packages:
        return

    modules_to_remove = []
    for module_name, module in list(sys.modules.items()):
        if not any(module_name == package or module_name.startswith(f"{package}.") for package in packages):
            continue
        if _module_comes_from_temp_path(module):
            modules_to_remove.append(module_name)

    for module_name in modules_to_remove:
        sys.modules.pop(module_name, None)


def _materialize_repo_package(repo):
    """Create a temporary Python package from repository files."""
    key = _repo_cache_key(repo)

    # Repo-backed modules live under shared top-level names like `scripts` and
    # `filters`, so clear any stale temp-backed packages before reusing or
    # recreating a materialized repository.
    _clear_temp_package_modules("scripts", "filters")

    cached_dir = _repo_pkg_cache.get(key)
    if cached_dir and os.path.isdir(cached_dir):
        if cached_dir not in sys.path:
            sys.path.insert(0, cached_dir)
        return cached_dir

    # Clear any existing modules from previous versions of this repo before creating new package
    clear_repo_script_cache(repo)

    temp_root = pathlib.Path(tempfile.mkdtemp(prefix=f"wfrepo_{repo.id}_{key[1]}_"))
    (temp_root / "__init__.py").write_text("# workflow temp package\n")

    # Import here to avoid circular import
    from .models import WorkflowFilterModule, WorkflowHelper

    materialized_paths = set()
    helper_manager = _get_internal_manager(WorkflowHelper)
    filter_manager = _get_internal_manager(WorkflowFilterModule)

    for stored_module in helper_manager.filter(source_repo=repo).order_by("file_path"):
        rel_path = pathlib.Path(stored_module.file_path)
        materialized_paths.add(rel_path.as_posix())
        dst_file = temp_root / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        _ensure_materialized_package_parent(dst_file, temp_root)
        dst_file.write_text(stored_module.content)

    for stored_module in filter_manager.filter(source_repo=repo).order_by("file_path"):
        rel_path = pathlib.Path(stored_module.file_path)
        normalized_path = rel_path.as_posix()
        if normalized_path in materialized_paths:
            raise RuntimeError(f"Duplicate Python module path found while materializing repository: {normalized_path}")
        materialized_paths.add(normalized_path)
        dst_file = temp_root / rel_path
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        _ensure_materialized_package_parent(dst_file, temp_root)
        dst_file.write_text(stored_module.content)

    sys.path.insert(0, str(temp_root))
    _repo_pkg_cache[key] = str(temp_root)
    return str(temp_root)


def _import_repo_module(module_name, file_path):
    """Import a repository-backed Python module from an exact file path."""
    _clear_temp_package_modules(module_name.split(".", 1)[0], "scripts")

    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module '{module_name}' from '{file_path}'")

    sys.modules.pop(module_name, None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise RuntimeError(f"Unable to import module '{module_name}' from '{file_path}': {exc}") from exc
    return module


def _load_repo_filter_registry(repo, force_reload=False):
    """Load and cache repo-backed Jinja filters for the given repository."""
    if not repo or not getattr(repo, "id", None):
        return {}

    key = _repo_cache_key(repo)
    if not force_reload and key in _repo_filter_cache:
        return _repo_filter_cache[key]

    from .models import WorkflowFilterModule

    pkg_root = pathlib.Path(_materialize_repo_package(repo))
    builtin_filters = _get_builtin_filters()
    repo_filters = {}

    filter_manager = _get_internal_manager(WorkflowFilterModule)
    for filter_module in filter_manager.filter(source_repo=repo).order_by("file_path"):
        rel_path = pathlib.Path(filter_module.file_path)
        module_name = rel_path.with_suffix("").as_posix().replace("/", ".")
        module = _import_repo_module(module_name, pkg_root / rel_path)
        filters = getattr(module, "FILTERS", None)
        if not isinstance(filters, dict):
            raise RuntimeError(f"Filter module '{filter_module.file_path}' must define a FILTERS dictionary")

        for filter_name, filter_callable in filters.items():
            if not isinstance(filter_name, str) or not filter_name:
                raise RuntimeError(
                    f"Filter module '{filter_module.file_path}' contains an invalid filter name: {filter_name!r}"
                )
            if not callable(filter_callable):
                raise RuntimeError(f"Filter '{filter_name}' in '{filter_module.file_path}' must be a callable object")
            if filter_name in builtin_filters:
                raise RuntimeError(
                    f"Filter '{filter_name}' in '{filter_module.file_path}' collides with a built-in filter"
                )
            if filter_name in repo_filters:
                raise RuntimeError(f"Duplicate repo filter '{filter_name}' found in '{filter_module.file_path}'")
            repo_filters[filter_name] = filter_callable

    _repo_filter_cache[key] = repo_filters
    return repo_filters


def validate_repo_filter_modules(repo):
    """Validate repo-backed filter modules using the same import path used at runtime."""
    clear_repo_script_cache(repo)
    _load_repo_filter_registry(repo, force_reload=True)


def get_jinja_environment(repo=None):
    """Build a Jinja environment with built-in filters and any repo-backed filters."""
    env = Environment(autoescape=False)  # noqa: S701
    env.filters.update(_get_builtin_filters())
    if repo and getattr(repo, "id", None):
        env.filters.update(_load_repo_filter_registry(repo))
    return env


def _render(value, ctx, repo=None, env=None):
    """Recursively render Jinja2 templates in strings, dicts, and lists."""
    env = env or get_jinja_environment(repo)
    if isinstance(value, str):
        return env.from_string(value).render(ctx)
    if isinstance(value, dict):
        return {k: _render(v, ctx, repo=repo, env=env) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, ctx, repo=repo, env=env) for v in value]
    return value


def _build_enhanced_inputs_context(workflow, inputs):
    """Build enhanced inputs context with object properties."""
    enhanced_inputs = {}

    for key, value in inputs.items():
        if not value:
            enhanced_inputs[key] = value
            continue

        # Try to resolve object and add its properties
        try:
            from .models import WorkflowInput

            wi = WorkflowInput.objects.get(workflow=workflow, key=key)
            if wi.content_type:
                model = wi.content_type.model_class()
                obj = model.objects.get(pk=value)

                # Create a custom dict-like object that behaves like an object but also like a string
                class ObjectProxy(dict):
                    def __init__(self, obj_data, pk, original_obj):
                        super().__init__(obj_data)
                        self._pk = pk
                        self._original_obj = original_obj

                    def __str__(self):
                        return str(self._pk)  # Default to ID for backward compatibility

                    def __getattr__(self, name):
                        # First try the dict lookup
                        if name in self:
                            return self[name]
                        # Then try the original object
                        if hasattr(self._original_obj, name):
                            return getattr(self._original_obj, name)
                        # Fallback to None to avoid AttributeError
                        return None

                    def __getitem__(self, key):
                        # Override to handle dot notation in templates
                        try:
                            return super().__getitem__(key)
                        except KeyError:
                            return self.__getattr__(key)

                # Add object with all useful properties
                obj_data = {
                    "id": obj.pk,
                    "name": getattr(obj, "name", str(obj)),
                    "slug": getattr(obj, "slug", None),
                    "description": getattr(obj, "description", None),
                    "__str__": str(obj),  # Default string representation
                }

                enhanced_inputs[key] = ObjectProxy(obj_data, obj.pk, obj)
            else:
                # Non-object field, just use the value
                enhanced_inputs[key] = value
        except Exception:
            # Fall back to just the value if resolution fails
            enhanced_inputs[key] = value

    return enhanced_inputs


def _wait_for_awx_completion(awx_run, workflow_run, max_wait_seconds=3600):
    """Wait for AWX job to complete and return final status with artifacts."""
    poll_seconds = int(PLUGIN_CFG.get("awx_poll_seconds", 10))
    waited = 0

    while waited < max_wait_seconds:
        # Refresh from database to get latest status
        awx_run.refresh_from_db()

        if awx_run.status in {"successful", "failed", "error", "canceled"}:
            workflow_run.log += f"\nAWX job {awx_run.job_id} completed with status: {awx_run.status}"

            # Extract artifacts from raw payload
            artifacts = None
            if awx_run.raw and isinstance(awx_run.raw, dict):
                artifacts = awx_run.raw.get("artifacts")

            workflow_run.save()
            return awx_run.status, artifacts

        time.sleep(poll_seconds)
        waited += poll_seconds

        if waited % 60 == 0:  # Log every minute
            workflow_run.log += f"\nStill waiting for AWX job {awx_run.job_id} (waited {waited}s)..."
            workflow_run.save()

    workflow_run.log += f"\nTimeout waiting for AWX job {awx_run.job_id} after {max_wait_seconds}s"
    workflow_run.save()
    return "timeout", None


def run_workflow(workflow, user, inputs: dict, workflow_run=None) -> WorkflowRun:
    """Start workflow execution asynchronously and return WorkflowRun immediately."""
    from .tasks import execute_workflow_task

    # Safety check: ensure workflow is enabled (unless show_disabled is True)
    if not _should_show_workflow(workflow):
        raise ValueError(f"Cannot execute disabled workflow '{workflow.name}' (key: {workflow.key})")

    if not user.has_perm("nautobot_workflow_engine.add_workflowrun"):
        raise ValueError(f"User '{user}' does not have permission to execute workflows")

    if not user.has_perm("nautobot_workflow_engine.view_workflow", workflow):
        raise ValueError(f"User '{user}' does not have permission to this workflow.")

    # Use provided workflow_run or create a new one
    if workflow_run is None:
        workflow_run = WorkflowRun.objects.create(workflow=workflow, requested_by=user, inputs=inputs, status="running")
    else:
        # Status is already set by caller (view) - don't override
        pass

    # Queue the entire workflow for background execution
    execute_workflow_task.delay(workflow_run.pk)

    return workflow_run


def _get_phase_results(workflow_run):
    """Return normalized phase_results structure."""
    phase_results = workflow_run.phase_results if isinstance(workflow_run.phase_results, dict) else {}
    for phase in ("pre", "main", "post"):
        phase_results.setdefault(phase, {"executed": False, "any_failed": False, "results": {}})
    return phase_results


def _build_execution_context(workflow_run, enhanced_inputs, phase_results):
    """Build execution context and preload previously stored phase results."""
    ctx = {
        "inputs": enhanced_inputs,
        "results": {},
        "last": None,
        "user": workflow_run.requested_by,
        "phase_results": phase_results,
        "phases": phase_results,
    }

    for phase in ("pre", "main", "post"):
        phase_data = phase_results.get(phase, {})
        for action_name, action_result in (phase_data.get("results") or {}).items():
            ctx["results"][action_name] = action_result
            ctx["last"] = {"name": action_name, **action_result}

    return ctx


def _make_json_safe(value):
    """Convert runtime values to JSON-safe data for persistence and templating."""
    if isinstance(value, Mapping):
        return {str(key): _make_json_safe(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]

    try:
        return json.loads(json.dumps(value, cls=DjangoJSONEncoder))
    except TypeError:
        return str(value)


def _run_action_with_context(action, ctx, workflow_run):
    """Execute one action and return normalized result payload."""
    repo = workflow_run.workflow.source_repo
    if action.action_type == "nautobot":
        params = _render(action.with_vars or {}, ctx, repo=repo)
        ret = _run_nautobot_script(action, params, workflow_run, repo)

        return _make_json_safe({"status": "success", "return_value": ret})

    if action.action_type == "awx":
        params = _render(action.awx_extra_vars or {}, ctx, repo=repo)
        awx_run = _run_awx_action(action, params, workflow_run)
        workflow_run.log += f"\nAction '{action.name}' launched AWX job {awx_run.job_id}, waiting for completion..."
        workflow_run.save(update_fields=["log"])

        final_status, artifacts = _wait_for_awx_completion(awx_run, workflow_run)
        if final_status == "successful":
            return _make_json_safe({"status": "success", "return_value": artifacts})

        return _make_json_safe(
            {
                "status": "failed",
                "return_value": None,
                "error": f"AWX job {final_status}",
            }
        )

    raise RuntimeError(f"Unsupported action type: {action.action_type}")


def _execute_phase_actions(workflow_run, ctx, phase, fail_fast=False):
    """Execute all actions for one phase and return summary data."""
    phase_results = {}
    any_failed = False
    env = get_jinja_environment(workflow_run.workflow.source_repo)

    actions = workflow_run.workflow.actions.filter(phase=phase).order_by("order", "name")

    workflow_run.log += f"\n[{phase}] Executing {actions.count()} action(s)"
    workflow_run.save(update_fields=["log"])
    for action in actions:
        cond = (action.when or "").strip() or "true"
        should = env.from_string(cond).render(ctx).lower() in ("true", "1", "yes", "on", "y")
        if not should:
            workflow_run.log += f"\n[{phase}] Skipped action '{action.name}' (when={cond!r} → False)"
            workflow_run.save(update_fields=["log"])
            phase_results[action.name] = {"status": "skipped", "return_value": None}
            continue

        workflow_run.log += f"\n[{phase}] Starting action '{action.name}' ({action.action_type})"
        workflow_run.save(update_fields=["log"])

        try:
            result = _run_action_with_context(action, ctx, workflow_run)
            if result.get("status") != "success":
                any_failed = True
            workflow_run.log += f"\n[{phase}] Results: {result!r}"
            workflow_run.save(update_fields=["log"])
        except Exception as exc:
            any_failed = True
            result = {"status": "failed", "return_value": None, "error": str(exc)}
            workflow_run.log += f"\n[{phase}] Action '{action.name}' failed: {exc}\n" + traceback.format_exc()
            workflow_run.save(update_fields=["log"])

        ctx["results"][action.name] = result
        ctx["last"] = {"name": action.name, **result}
        phase_results[action.name] = result

        if fail_fast and any_failed:
            break

    return {"any_failed": any_failed, "results": phase_results}


def run_pre_actions_for_scheduled_run(workflow_run):
    """Execute pre-actions at scheduling time and persist results.

    Raises RuntimeError if pre-actions fail.
    """
    phase_results = _get_phase_results(workflow_run)
    if phase_results.get("pre", {}).get("executed"):
        return phase_results["pre"]

    enhanced_inputs = _build_enhanced_inputs_context(workflow_run.workflow, workflow_run.inputs)
    ctx = _build_execution_context(workflow_run, enhanced_inputs, phase_results)

    summary = _execute_phase_actions(workflow_run, ctx, phase="pre", fail_fast=True)
    phase_results["pre"] = {
        "executed": True,
        "executed_at": timezone.now().isoformat(),
        "any_failed": summary["any_failed"],
        "results": summary["results"],
    }

    workflow_run.phase_results = phase_results
    workflow_run.save(update_fields=["phase_results", "log", "user_log"])

    if summary["any_failed"]:
        raise RuntimeError("Pre-actions failed; scheduled workflow execution was not created.")

    return phase_results["pre"]


def _run_workflow_synchronous(workflow_run) -> WorkflowRun:
    """Execute a workflow synchronously with the inputs from the WorkflowRun."""
    # Safety check: ensure workflow is still enabled before execution (unless show_disabled is True)
    if not _should_show_workflow(workflow_run.workflow):
        workflow_run.status = "failed"
        workflow_run.log += f"\nERROR: Cannot execute disabled workflow '{workflow_run.workflow.name}' (key: {workflow_run.workflow.key})"
        workflow_run.finished = timezone.now()
        workflow_run.save()
        return workflow_run

    # Build enhanced inputs context with object properties
    enhanced_inputs = _build_enhanced_inputs_context(workflow_run.workflow, workflow_run.inputs)

    # Debug logging to see what's in the enhanced context
    workflow_run.log += "\nEnhanced inputs context:"
    for key, value in enhanced_inputs.items():
        if hasattr(value, "_original_obj"):
            workflow_run.log += f"\n  {key}: ObjectProxy with id={value.get('id')}, name={value.get('name')}"
        else:
            workflow_run.log += f"\n  {key}: {type(value).__name__} = {value}"

    phase_results = _get_phase_results(workflow_run)
    ctx = _build_execution_context(workflow_run, enhanced_inputs, phase_results)

    try:
        # Run pre-actions at execution time only if not already done at scheduling time.
        if not phase_results.get("pre", {}).get("executed"):
            pre_summary = _execute_phase_actions(workflow_run, ctx, phase="pre", fail_fast=True)
            phase_results["pre"] = {
                "executed": True,
                "executed_at": timezone.now().isoformat(),
                "any_failed": pre_summary["any_failed"],
                "results": pre_summary["results"],
            }
            if pre_summary["any_failed"]:
                workflow_run.status = "failed"
                workflow_run.log += "\nPre-actions failed; main workflow actions were not executed."
                workflow_run.finished = timezone.now()
                workflow_run.phase_results = phase_results
                workflow_run.save(update_fields=["status", "finished", "log", "phase_results"])
                return workflow_run

        main_summary = _execute_phase_actions(workflow_run, ctx, phase="main", fail_fast=False)
        phase_results["main"] = {
            "executed": True,
            "executed_at": timezone.now().isoformat(),
            "any_failed": main_summary["any_failed"],
            "results": main_summary["results"],
        }

        # Post-actions always run and are non-blocking for final workflow status.
        post_summary = _execute_phase_actions(workflow_run, ctx, phase="post", fail_fast=False)
        phase_results["post"] = {
            "executed": True,
            "executed_at": timezone.now().isoformat(),
            "any_failed": post_summary["any_failed"],
            "results": post_summary["results"],
        }

        workflow_run.status = "success" if not main_summary["any_failed"] else "failed"
        if post_summary["any_failed"]:
            workflow_run.log += "\nWarning: one or more post-actions failed (non-blocking)."
            workflow_run.user_log = (workflow_run.user_log or "") + "\nWarning: one or more post-actions failed."

        workflow_run.finished = timezone.now()
        workflow_run.phase_results = phase_results
        workflow_run.save(update_fields=["status", "finished", "log", "user_log", "phase_results"])
        return workflow_run

    except Exception as exc:
        workflow_run.status = "failed"
        workflow_run.log += "\nERROR: " + str(exc) + "\n" + traceback.format_exc()
        workflow_run.finished = timezone.now()
        workflow_run.phase_results = phase_results
        workflow_run.save(update_fields=["status", "finished", "log", "phase_results"])
        return workflow_run


def _run_nautobot_script(action, params, run, repo):
    """Load and execute an action script from the temp package."""
    if not repo or not getattr(repo, "id", None):
        raise RuntimeError("Workflow has no source_repo; cannot run script action")

    # Get script from WorkflowHelper via the action's script_path
    script_path = action.script_path.lstrip("/")
    try:
        from .models import WorkflowHelper

        _get_internal_manager(WorkflowHelper).get(source_repo=repo, file_path=script_path)
    except WorkflowHelper.DoesNotExist:
        raise RuntimeError(f"Script not found in repository: {script_path}")

    # --- helpers (local to keep this function self-contained) ---
    def _ensure_pkg_chain(path: pathlib.Path, pkg_root: pathlib.Path) -> None:
        """Make every parent dir under pkg_root a Python package."""
        cur = path.parent
        while True:
            init = cur / "__init__.py"
            if not init.exists():
                try:
                    cur.mkdir(parents=True, exist_ok=True)
                    init.write_text("# auto-generated package\n")
                except Exception:
                    break
            if cur == pkg_root:
                break
            cur = cur.parent

    def _debug_tree(root: pathlib.Path) -> str:
        try:
            return "\n".join(p.relative_to(root).as_posix() for p in sorted(root.rglob("*.py")))
        except Exception:
            return "<unavailable>"

    # --- materialize helpers + compute module/file paths ---
    pkg_root = pathlib.Path(_materialize_repo_package(repo))  # ensures helpers like scripts/lib/utils.py are copied
    pkg_root_str = str(pkg_root)
    if pkg_root_str not in sys.path:
        sys.path.insert(0, pkg_root_str)
    rel_path = pathlib.Path(action.script_path.lstrip("/"))  # e.g. scripts/actions/do_thing.py
    file_path = pkg_root / rel_path
    module_name = rel_path.with_suffix("").as_posix().replace("/", ".")
    _clear_temp_package_modules(*rel_path.parts[:1])

    # Ensure package dirs exist (scripts/, scripts/lib/, …)
    _ensure_pkg_chain(file_path, pkg_root)

    # Fresh import from this exact file path to avoid stale sys.modules entries
    # (We purposely do not rely on a prior import of the same module name.)
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        run.log += (
            f"\n[loader] Unable to create spec for {module_name} at {file_path}"
            f"\n[loader] package root: {pkg_root}"
            f"\n[loader] tree:\n{_debug_tree(pkg_root)}"
        )
        raise RuntimeError(f"Unable to load spec for {module_name} at {file_path}")

    try:
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    except Exception as exc:
        run.log += (
            f"\n[loader] exec_module failed for {module_name} at {file_path}: {exc}"
            f"\n[loader] package root: {pkg_root}"
            f"\n[loader] tree:\n{_debug_tree(pkg_root)}"
            f"\n{traceback.format_exc()}"
        )
        raise

    # Sanity check: require run(params)
    if not hasattr(mod, "run"):
        raise RuntimeError(f"{action.script_path} must define run(params)")

    # Execute and return the script's return value, capturing and streaming print() output.
    # This allows the run-detail polling endpoint to display user_log updates during execution.
    class _LiveUserLogWriter(io.TextIOBase):
        """File-like sink that streams stdout/stderr into WorkflowRun.user_log."""

        def __init__(self, workflow_run, flush_interval_seconds=1.0):
            self.workflow_run = workflow_run
            self.flush_interval_seconds = flush_interval_seconds
            self._pending = []
            self._last_flush = time.monotonic()

        def writable(self):
            return True

        def write(self, s):
            if not s:
                return 0

            self._pending.append(s)
            now = time.monotonic()
            # Flush frequently on newline boundaries, or after a short interval.
            if "\n" in s or (now - self._last_flush) >= self.flush_interval_seconds:
                self.flush()
            return len(s)

        def flush(self):
            if not self._pending:
                return

            chunk = "".join(self._pending)
            self._pending = []
            self.workflow_run.user_log = (self.workflow_run.user_log or "") + chunk
            try:
                self.workflow_run.save(update_fields=["user_log"])
            except Exception as exc:
                self.workflow_run.log += f"\nFailed to stream WorkflowRun.user_log: {exc}"
            self._last_flush = time.monotonic()

    live_writer = _LiveUserLogWriter(run)

    try:
        with contextlib.redirect_stdout(live_writer), contextlib.redirect_stderr(live_writer):
            ret = mod.run(params)
    finally:
        # Ensure any trailing output without newline is persisted.
        live_writer.flush()

    run.log += f"\nExecuted {module_name}.run({params!r})"
    return ret


def _awx_integration():
    """Get the AWX external integration from Nautobot configuration."""
    name = PLUGIN_CFG["awx_external_integration_name"]
    return ExternalIntegration.objects.get(name=name)


def parse_awx_auth(integ: ExternalIntegration):
    """Parse AWX integration credentials."""
    if integ.secrets_group:
        try:
            result = (
                integ.secrets_group.get_secret_value(
                    access_type=SecretsGroupAccessTypeChoices.TYPE_GENERIC,
                    secret_type=SecretsGroupSecretTypeChoices.TYPE_USERNAME,
                ),
                integ.secrets_group.get_secret_value(
                    access_type=SecretsGroupAccessTypeChoices.TYPE_GENERIC,
                    secret_type=SecretsGroupSecretTypeChoices.TYPE_PASSWORD,
                ),
            )
            return result
        except SecretsGroupAssociation.DoesNotExist:
            return None


def _run_awx_action(action, extra_vars, run):
    """Execute an AWX job template and create an AwxRun record."""
    integ = _awx_integration()
    url = integ.remote_url.rstrip("/")
    verify = integ.verify_ssl
    username, password = parse_awx_auth(integ)

    resp = requests.post(
        f"{url}/api/v2/job_templates/{action.awx_template_id}/launch/",
        json={"extra_vars": extra_vars},
        auth=(username, password) if username and password else None,
        verify=verify,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    job_id = data.get("job") or data.get("id")
    job_url = f"{url}/#/jobs/playbook/{job_id}"
    awx_run = AwxRun.objects.create(
        workflow_run=run,
        template_id=action.awx_template_id,
        job_id=job_id,
        url=job_url,
        status="pending",
        raw=data,
    )

    # Start the polling task to monitor job status
    from .tasks import poll_awx_job

    poll_awx_job.delay(awx_run.pk)

    run.log += f"\nLaunched AWX template {action.awx_template_id} as job {job_id}"
    run.user_log += f"\nAnsible playbook '{action.name}' has been launched (Job ID: {job_id})"
    run.save(update_fields=["log", "user_log"])
    return awx_run  # Return the AwxRun object for monitoring
