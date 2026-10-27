"""Builder REST API."""

import ast
import hashlib
import json

from django.contrib.contenttypes.models import ContentType
from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from nautobot.extras.models import ExternalIntegration, GitRepository
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.parsers import BaseParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from nautobot_workflow_engine.dry_run import dry_run, render_sample
from nautobot_workflow_engine.models import Workflow, WorkflowFilterModule, WorkflowHelper, WorkflowRevision
from nautobot_workflow_engine.schema import load_schema
from nautobot_workflow_engine.schema.builder_payload import (
    builder_from_definition,
    definition_from_builder,
    payload_to_raw,
)
from nautobot_workflow_engine.schema.errors import BuilderValidationError, FieldError
from nautobot_workflow_engine.schema.normalize import normalize
from nautobot_workflow_engine.schema.policy import can_build, content_type_allowed, script_path_allowed
from nautobot_workflow_engine.schema.references import find_references, rename_reference
from nautobot_workflow_engine.schema.validate import validate
from nautobot_workflow_engine.schema.yaml_io import from_yaml, pointer_lines, to_yaml
from nautobot_workflow_engine.services import (
    ServiceError,
    adopt_workflow,
    archive_workflow,
    create_draft,
    discard_draft,
    duplicate_workflow,
    load_published_into_draft,
    publish,
    restore_revision,
    set_enabled,
    unarchive_workflow,
    update_draft,
)

from .serializers import (
    AdoptRequestSerializer,
    ArchiveRequestSerializer,
    BaseSerializer,
    DetailSerializer,
    DiffSerializer,
    DuplicateSerializer,
    ErrorListSerializer,
    GitStatusSerializer,
    RefactorRequestSerializer,
    RefactorResponseSerializer,
    RefreshSerializer,
    RevisionSerializer,
    StaleSerializer,
    ValidateResponseSerializer,
    WorkflowDetailSerializer,
    WorkflowSummarySerializer,
)


class BodyParser(BaseParser):
    """Leave the body as bytes so bad UTF-8 and bad JSON become field errors."""

    media_type = "*/*"

    def parse(self, stream, media_type=None, parser_context=None):
        """Return the raw body."""
        return stream.read()


def _errors_payload(errors):
    return {"errors": [dict(item) for item in errors]}


def _service_response(exc):
    return Response(exc.payload, status=exc.status)


def _as_bool(value):
    """Treat JSON booleans and form values the same."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _load_body(request):
    """Return a JSON object, or a 400 response for bad UTF-8 or bad JSON."""
    raw = request.data if isinstance(request.data, (bytes, bytearray)) else request.body
    if not raw:
        return {}, None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, Response(
            _errors_payload([FieldError("", "yaml", "Request body is not valid text.")]),
            status=400,
        )
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, Response(
            _errors_payload([FieldError("", "yaml", "Request body is not valid JSON.")]),
            status=400,
        )
    if not isinstance(parsed, dict):
        return None, Response(
            _errors_payload([FieldError("", "invalid_type", "Request body must be an object.")]),
            status=400,
        )
    return parsed, None


def _wants_strict(data):
    """Draft saves may send ``strict: false`` so an unfinished form can be kept."""
    if not isinstance(data, dict) or "strict" not in data:
        return True
    return data.get("strict") is not False


def _definition_from_body(data, *, strict=True):
    """Normalize a definition, YAML document, or the current builder payload."""
    if data.get("yaml") not in (None, ""):
        try:
            loaded = from_yaml(data["yaml"])
        except Exception as exc:
            raise ServiceError(400, errors=[dict(FieldError("", "yaml", f"YAML could not be read: {exc}"))])
        if not isinstance(loaded, dict):
            raise ServiceError(400, errors=[dict(FieldError("", "yaml", "YAML must be a mapping."))])
        return normalize(loaded)
    if data.get("definition") not in (None, ""):
        definition = data["definition"]
        if isinstance(definition, str):
            try:
                definition = json.loads(definition)
            except json.JSONDecodeError as exc:
                raise ServiceError(400, errors=[dict(FieldError("", "yaml", f"Definition is not valid JSON: {exc}"))])
        if not isinstance(definition, dict):
            raise ServiceError(400, errors=[dict(FieldError("", "invalid_type", "Definition must be an object."))])
        return normalize(definition)
    if "payload" in data:
        if not strict:
            raw, _build_errors = payload_to_raw(data["payload"])
            if not isinstance(raw, dict):
                raise ServiceError(
                    400, errors=[dict(FieldError("", "invalid_type", "Builder payload must be an object."))]
                )
            return normalize(raw)
        try:
            return definition_from_builder(data["payload"])
        except BuilderValidationError as exc:
            raise ServiceError(400, errors=exc.errors)
    raise ServiceError(400, errors=[dict(FieldError("", "required", "Send a definition, yaml, or payload."))])


def _repository(user, raw_id):
    if raw_id in (None, ""):
        return None
    visible = GitRepository.objects.restrict(user, "view")
    try:
        return visible.get(pk=raw_id)
    except (GitRepository.DoesNotExist, ValueError, TypeError):
        raise ServiceError(
            400,
            errors=[
                dict(
                    FieldError(
                        "/source_repo",
                        "repository_required",
                        "That Git repository no longer exists.",
                        path="source_repo",
                    )
                )
            ],
        )


def _revision_payload(revision, *, include_body=True):
    if revision is None:
        return None
    payload = {
        "number": revision.number,
        "state": revision.state,
        "source": revision.source,
        "comment": revision.comment,
        "created": revision.created,
        "created_by": getattr(revision.created_by, "username", None),
        "published_at": revision.published_at,
        "published_by": getattr(revision.published_by, "username", None),
        "git_commit": revision.git_commit,
        "git_author": revision.git_author,
        "base_revision": revision.base_revision.number if revision.base_revision_id else None,
        "outdated": revision.is_outdated,
        "superseded_reason": revision.superseded_reason,
        "content_hash": revision.content_hash,
    }
    if include_body:
        payload["definition"] = revision.definition
        payload["yaml"] = revision.yaml
    return payload


def _summary(workflow):
    published = workflow.published_revision
    draft = workflow.draft_revision
    return {
        "key": workflow.key,
        "name": workflow.name,
        "category": workflow.category,
        "managed_by": workflow.managed_by,
        "enabled": workflow.enabled,
        "archived": workflow.archived,
        "git_status": workflow.git_status or "not_configured",
        "published_revision": published.number if published is not None else None,
        "draft_revision": draft.number if draft is not None else None,
        "updated": getattr(workflow, "last_updated", None),
    }


def _detail(workflow):
    head = workflow.draft_revision or workflow.published_revision
    return {
        "workflow": _summary(workflow),
        "published": _revision_payload(workflow.published_revision),
        "draft": _revision_payload(workflow.draft_revision),
        "base": {
            "revision": head.number if head is not None else None,
            "git_commit": workflow.git_commit or "",
            "git_content_hash": workflow.git_content_hash or "",
        },
    }


def _require(user, perm):
    if not user.has_perm(perm):
        raise PermissionDenied(detail="You do not have permission to perform this action.")


def _visible(user, key, action):
    try:
        return Workflow.objects.restrict(user, action).get(key=key)
    except Workflow.DoesNotExist:
        raise NotFound(detail="Not found.")


def _module_doc(content):
    try:
        tree = ast.parse(content or "")
    except SyntaxError:
        return ""
    doc = ast.get_docstring(tree) or ""
    return doc.strip().splitlines()[0] if doc.strip() else ""


def _filter_entries(content):
    try:
        tree = ast.parse(content or "")
    except SyntaxError:
        return []
    found = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "FILTERS" and isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        found.append({"name": key.value, "doc": ""})
    return found


class BuilderSchemaView(APIView):
    """Return the workflow JSON Schema. The response is cached with an ETag."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    @extend_schema(responses={200: OpenApiResponse(description="Workflow JSON Schema"), 403: DetailSerializer})
    def get(self, request):
        """Return the schema document."""
        _require(request.user, "nautobot_workflow_engine.view_workflow")
        document = load_schema()
        etag = hashlib.sha256(json.dumps(document, sort_keys=True).encode("utf-8")).hexdigest()
        if request.META.get("HTTP_IF_NONE_MATCH") == etag:
            response = Response(status=304)
            response["ETag"] = etag
            return response
        response = Response(document)
        response["ETag"] = etag
        return response


class BuilderContextView(APIView):
    """Pickers for the builder. Every list is restricted to what this user can view."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    @extend_schema(
        parameters=[OpenApiParameter(name="repository", required=False, type=str)],
        responses={200: OpenApiResponse(description="Builder pickers"), 403: DetailSerializer},
    )
    def get(self, request):
        """Return repositories, scripts, models, integrations, categories, and filters."""
        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.add_workflow")
            or user.has_perm("nautobot_workflow_engine.change_workflow")
        ):
            raise PermissionDenied(detail="You do not have permission to perform this action.")
        repositories = []
        visible_ids = []
        for repo in GitRepository.objects.restrict(user, "view").order_by("name"):
            visible_ids.append(repo.pk)
            repositories.append(
                {
                    "id": str(repo.pk),
                    "name": repo.name,
                    "branch": repo.branch,
                    "can_publish": user.has_perm("nautobot_workflow_engine.publish_workflow"),
                    "has_credentials": bool(repo.secrets_group_id),
                }
            )
        requested = (request.query_params.get("repository") or "").strip()
        helper_qs = WorkflowHelper.objects.filter(source_repo_id__in=visible_ids)
        if requested:
            helper_qs = helper_qs.filter(source_repo_id=requested)
        scripts = []
        for repo_id, file_path, content in helper_qs.values_list("source_repo_id", "file_path", "content"):
            if not script_path_allowed(file_path) or file_path.endswith("__init__.py"):
                continue
            if "run(" not in (content or ""):
                continue
            scripts.append({"path": file_path, "repo_id": str(repo_id), "doc": _module_doc(content)})
        models = []
        for content_type in ContentType.objects.order_by("app_label", "model"):
            model = content_type.model_class()
            if model is None:
                continue
            label = f"{content_type.app_label}.{model.__name__}"
            if not content_type_allowed(label):
                continue
            models.append(
                {
                    "value": label,
                    "label": f"{content_type.app_label} | {model._meta.verbose_name}",
                    "group": content_type.app_label,
                }
            )
        filters = []
        filter_qs = WorkflowFilterModule.objects.filter(source_repo_id__in=visible_ids)
        if requested:
            filter_qs = filter_qs.filter(source_repo_id=requested)
        for content in filter_qs.values_list("content", flat=True):
            filters.extend(_filter_entries(content))
        categories = list(
            Workflow.objects.restrict(user, "view")
            .exclude(category="")
            .order_by("category")
            .values_list("category", flat=True)
            .distinct()
        )
        return Response(
            {
                "repositories": repositories,
                "scripts": scripts,
                "models": models,
                "integrations": [
                    {"name": name}
                    for name in ExternalIntegration.objects.restrict(user, "view")
                    .order_by("name")
                    .values_list("name", flat=True)
                ],
                "categories": categories,
                "filters": filters,
                "capabilities": {
                    "can_add": user.has_perm("nautobot_workflow_engine.add_workflow"),
                    "can_author_code": can_build(user),
                    "can_publish": user.has_perm("nautobot_workflow_engine.publish_workflow"),
                },
            }
        )


class BuilderValidateView(APIView):
    """Normalize and validate without writing. This endpoint does not return 500."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    @extend_schema(
        request=RefactorRequestSerializer,
        responses={200: ValidateResponseSerializer, 400: ErrorListSerializer, 403: DetailSerializer},
    )
    def post(self, request):
        """Return the canonical definition, YAML, errors, and references."""
        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.add_workflow")
            or user.has_perm("nautobot_workflow_engine.change_workflow")
        ):
            raise PermissionDenied(detail="You do not have permission to perform this action.")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        build_errors = []
        try:
            if isinstance(data, dict) and "payload" in data:
                raw, build_errors = payload_to_raw(data["payload"])
                try:
                    definition = normalize(raw) if isinstance(raw, dict) else {}
                except Exception as exc:
                    definition = raw if isinstance(raw, dict) else {}
                    build_errors = list(build_errors) + [FieldError("", "yaml", str(exc))]
                try:
                    repo = _repository(user, data.get("repository"))
                except ServiceError as exc:
                    repo = None
                    build_errors = list(build_errors) + list(exc.payload.get("errors") or [])
            else:
                definition = _definition_from_body(data)
                repo = _repository(user, data.get("repository") if isinstance(data, dict) else None)
        except ServiceError as exc:
            if exc.status != 400:
                return _service_response(exc)
            return Response(exc.payload, status=400)
        except Exception as exc:
            return Response(_errors_payload([FieldError("", "yaml", str(exc))]), status=200)
        try:
            errors = list(build_errors) + list(
                validate(definition, repo=repo, enforce_policy=True, check_scripts=repo is not None)
            )
        except Exception as exc:
            errors = list(build_errors) + [FieldError("", "yaml", str(exc))]
        yaml_text = ""
        references = {"inputs": [], "results": []}
        try:
            yaml_text = to_yaml(definition)
            references = find_references(definition)
        except Exception:
            yaml_text = yaml_text or ""
        return Response(
            {
                "definition": definition,
                "yaml": yaml_text,
                "errors": [dict(item) for item in errors],
                "warnings": [],
                "references": references,
            }
        )


class BuilderRefactorView(APIView):
    """Rename an input or an action and the references that point at it."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    @extend_schema(
        request=RefactorRequestSerializer,
        responses={200: RefactorResponseSerializer, 400: ErrorListSerializer, 403: DetailSerializer},
    )
    def post(self, request):
        """Return the renamed definition and the pointers that changed."""
        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.add_workflow")
            or user.has_perm("nautobot_workflow_engine.change_workflow")
        ):
            raise PermissionDenied(detail="You do not have permission to perform this action.")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        try:
            definition = normalize(data.get("definition") or {})
        except Exception as exc:
            return Response(_errors_payload([FieldError("", "yaml", str(exc))]), status=400)
        operation = data.get("op")
        if operation not in ("rename_input", "rename_action"):
            return Response(
                _errors_payload([FieldError("/op", "enum", "op must be rename_input or rename_action.")]),
                status=400,
            )
        updated, changes = rename_reference(definition, operation, data.get("from") or "", data.get("to") or "")
        return Response({"definition": updated, "changes": changes})


class BuilderLaunchPreviewView(APIView):
    """Render the launch form for a definition that has not been published yet."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    @extend_schema(responses={200: OpenApiResponse(description="HTML launch-form preview"), 403: DetailSerializer})
    def post(self, request):
        """Return an HTML fragment. Labels are escaped by the template."""
        from django.shortcuts import render

        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.add_workflow")
            or user.has_perm("nautobot_workflow_engine.change_workflow")
            or user.has_perm("nautobot_workflow_engine.view_workflow")
        ):
            raise PermissionDenied(detail="You do not have permission to perform this action.")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        try:
            definition = _definition_from_body(data, strict=False)
        except ServiceError as exc:
            return _service_response(exc)
        if not isinstance(definition, dict):
            definition = {}
        fields = [_preview_field(item) for item in definition.get("inputs") or [] if isinstance(item, dict)]
        response = render(
            getattr(request, "_request", request),
            "nautobot_workflow_engine/partials/launch_preview.html",
            {"fields": fields},
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


def _preview_field(item):
    """Describe one launch-form control without writing database rows."""
    kind = item.get("type") or "string"
    choices = []
    raw_choices = item.get("choices")
    if isinstance(raw_choices, list):
        for choice in raw_choices:
            if isinstance(choice, dict):
                choices.append({"value": choice.get("value"), "label": choice.get("label") or choice.get("value")})
            elif choice not in (None, ""):
                choices.append({"value": choice, "label": str(choice)})
    show_if = item.get("show_if")
    return {
        "key": item.get("key") or "",
        "label": item.get("label") or item.get("key") or "Field",
        "type": kind,
        "required": bool(item.get("required")),
        "help": item.get("help_text") or "",
        "placeholder": item.get("placeholder") or "",
        "choices": choices,
        "model": item.get("model") or "",
        "depends_on": item.get("depends_on") if isinstance(item.get("depends_on"), list) else [],
        "show_if": show_if if isinstance(show_if, (dict, str)) else "",
    }


class BuilderDryRunView(APIView):
    """Preview run for a definition that may not be saved yet. Nothing is executed."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    def post(self, request):
        """Render conditions and parameters. This does not execute the workflow."""
        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.add_workflow")
            or user.has_perm("nautobot_workflow_engine.change_workflow")
        ):
            raise PermissionDenied(detail="You do not have permission to perform this action.")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        try:
            definition = _definition_from_body(data, strict=False)
            repo = _repository(user, data.get("repository") or (data.get("payload") or {}).get("source_repo"))
        except ServiceError as exc:
            return _service_response(exc)
        result = dry_run(
            definition,
            data.get("inputs") or {},
            data.get("mock_results") or {},
            user,
            repo=repo,
        )
        return Response(result)


def _script_request(user, data):
    """Build the file a "New script" request describes. Only the parameters and body come from the user."""
    from nautobot_workflow_engine.script_files import (
        MAX_BODY_CHARS,
        SCRIPT_PERMISSION,
        check_script,
        parameters_spec,
        render_script,
        script_path,
    )

    name = str(data.get("name") or "").strip()
    path = script_path(name)
    spec = parameters_spec(data.get("parameters") if isinstance(data.get("parameters"), list) else [])
    can_write = user.has_perm(SCRIPT_PERMISSION)
    body = data.get("body") if can_write and isinstance(data.get("body"), str) else None
    problems = []
    if not path:
        problems.append({"line": 0, "message": "Give the script a name."})
    if body is not None and len(body) > MAX_BODY_CHARS:
        problems.append({"line": 0, "message": f"The code is too long. Keep it under {MAX_BODY_CHARS} characters."})
        body = None
    content = render_script(path=path or "scripts/new_script.py", title=data.get("title") or name, spec=spec, body=body)
    lines = content.splitlines()
    first_body_line = lines.index("def run(params):") + 3 if "def run(params):" in lines else 0
    for problem in check_script(content):
        line = problem.get("line") or 0
        if body is not None and first_body_line and line >= first_body_line:
            own = line - first_body_line + 1
            text = problem["message"].split(": ", 1)[-1]
            problem = {"line": own, "message": f"Line {own} of your code: {text}"}
        problems.append(problem)
    repository = None
    try:
        repository = _repository(user, data.get("repository"))
    except ServiceError:
        repository = None
    if (
        path
        and repository is not None
        and WorkflowHelper.objects.filter(source_repo=repository, file_path=path).exists()
    ):
        problems.append({"line": 0, "message": f"{path} already exists. Pick another name, or choose it in the list."})
    return path, content, problems, can_write


class BuilderScriptPreviewView(APIView):
    """Show the file a new script would be. The code is parsed to check it and never run."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    @extend_schema(request=None, responses={200: OpenApiResponse(description="Script preview")})
    def post(self, request):
        """Return the path, full file text, and plain problems."""
        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.add_workflow")
            or user.has_perm("nautobot_workflow_engine.change_workflow")
        ):
            raise PermissionDenied(detail="You do not have permission to perform this action.")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        path, content, problems, can_write = _script_request(user, data if isinstance(data, dict) else {})
        from nautobot_workflow_engine.script_files import STARTER_NOTE

        return Response(
            {"path": path, "content": content, "problems": problems, "can_write": can_write, "note": STARTER_NOTE}
        )


class BuilderScriptCreateView(APIView):
    """Commit a new starter script to the workflow repository. Needs the Write scripts permission."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    @extend_schema(request=None, responses={201: OpenApiResponse(description="Script created")})
    def post(self, request):
        """Write, commit and push the file, then return it the way the script picker lists scripts."""
        from nautobot_workflow_engine.script_files import NO_PERMISSION_MESSAGE, SCRIPT_PERMISSION, STARTER_NOTE
        from nautobot_workflow_engine.script_params import script_parameters
        from nautobot_workflow_engine.utilities.script_commit import ScriptCommitError, commit_script

        user = request.user
        if not user.has_perm(SCRIPT_PERMISSION):
            return Response({"detail": NO_PERMISSION_MESSAGE}, status=403)
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        data = data if isinstance(data, dict) else {}
        try:
            repository = _repository(user, data.get("repository"))
        except ServiceError:
            repository = None
        if repository is None:
            return Response({"detail": "Choose the workflow’s repository first, on Details."}, status=400)
        path, content, problems, _can_write = _script_request(user, data)
        if problems:
            return Response({"detail": problems[0]["message"], "problems": problems}, status=400)
        try:
            commit_script(user=user, repository=repository, path=path, content=content)
        except ScriptCommitError as exc:
            return Response({"detail": exc.message}, status=exc.status)
        meta = script_parameters(content)
        return Response(
            {
                "script": {
                    "repo": str(repository.pk),
                    "path": path,
                    "name": path.rsplit("/", 1)[-1][:-3].replace("_", " ").capitalize(),
                    "params": meta["params"],
                    "declared": meta["declared"],
                },
                "detail": f"Created {path} in {repository.name}. {STARTER_NOTE}",
            },
            status=201,
        )


class BuilderRenderView(APIView):
    """Preview one Jinja template, such as computed options."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    def post(self, request):
        """Return the rendered text or a sandbox error."""
        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.add_workflow")
            or user.has_perm("nautobot_workflow_engine.change_workflow")
        ):
            raise PermissionDenied(detail="You do not have permission to perform this action.")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        return Response(
            render_sample(data.get("template") or "", data.get("inputs") or {}, user, results=data.get("results") or {})
        )


class BuilderYamlApplyView(APIView):
    """Parse Edit YAML text, validate it, and return the builder payload."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    def post(self, request):
        """Apply YAML to the form. A parse error leaves the payload empty."""
        user = request.user
        if not can_build(user):
            raise PermissionDenied(detail="You need the Build workflows permission to edit YAML.")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        text = data.get("yaml") if isinstance(data.get("yaml"), str) else ""
        lines, parse_error = pointer_lines(text)
        if parse_error is not None:
            return Response({"payload": None, "definition": None, "yaml": text, "errors": [parse_error]})
        try:
            loaded = from_yaml(text)
            if not isinstance(loaded, dict):
                return Response(
                    {
                        "payload": None,
                        "definition": None,
                        "yaml": text,
                        "errors": [
                            {
                                "line": 1,
                                "column": 1,
                                "pointer": "",
                                "code": "yaml",
                                "message": "YAML must be a mapping.",
                            }
                        ],
                    }
                )
            definition = normalize(loaded)
            repo = _repository(user, data.get("repository"))
            errors = [
                dict(item)
                for item in validate(definition, repo=repo, enforce_policy=True, check_scripts=repo is not None)
            ]
        except ServiceError as exc:
            return _service_response(exc)
        except Exception as exc:
            return Response(
                {
                    "payload": None,
                    "definition": None,
                    "yaml": text,
                    "errors": [{"line": 1, "column": 1, "pointer": "", "code": "yaml", "message": str(exc)}],
                }
            )
        for item in errors:
            item["line"] = lines.get(item.get("pointer") or "")
        return Response(
            {
                "payload": builder_from_definition(definition),
                "definition": definition,
                "yaml": to_yaml(definition),
                "errors": errors,
            }
        )


def _apply_tags(workflow, user, data):
    """Store tag names on the workflow. Tags are not part of the YAML."""
    if not isinstance(data, dict):
        return
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    if "tags" in data:
        names = data.get("tags")
    elif "tags" in payload:
        names = payload.get("tags")
    else:
        return
    if not isinstance(names, list):
        return
    from nautobot.extras.models import Tag

    wanted = [str(name).strip() for name in names if str(name).strip()]
    tags = list(Tag.objects.restrict(user, "view").filter(name__in=wanted))
    workflow.tags.set(tags)


_WRITE = {
    200: RevisionSerializer,
    201: WorkflowDetailSerializer,
    202: WorkflowDetailSerializer,
    204: OpenApiResponse(description="Draft discarded"),
    400: ErrorListSerializer,
    403: DetailSerializer,
    404: DetailSerializer,
    409: StaleSerializer,
}


class BuilderFilterCountView(APIView):
    """Count objects for the visual filter builder. The query is not stored."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    @extend_schema(responses={200: OpenApiResponse(description="Matching object count")})
    def post(self, request):
        """Return how many objects match a filter, plus a few names."""
        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.view_workflow")
            or user.has_perm("nautobot_workflow_engine.add_workflow")
            or user.has_perm("nautobot_workflow_engine.change_workflow")
        ):
            raise PermissionDenied(detail="You do not have permission to perform this action.")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        model_label = str((data or {}).get("model") or "")
        if not content_type_allowed(model_label) or "." not in model_label:
            return Response({"detail": "That model is not available."}, status=400)
        from django.apps import apps

        from nautobot_workflow_engine.executors import SafeUser, _render
        from nautobot_workflow_engine.schema.filters import expanded_lookups, split_filter
        from nautobot_workflow_engine.schema.policy import lookup_allowed

        app_label, model_name = model_label.split(".", 1)
        try:
            model = apps.get_model(app_label, model_name)
        except Exception as exc:
            if exc.__class__.__name__ != "LookupError":
                raise
            return Response({"detail": "That model is not available."}, status=400)
        query = data.get("query") or {}
        rendered = query if isinstance(query, dict) else {}
        if rendered:
            inputs = {
                str(key): {"id": value, "name": value}
                for key, value in (data.get("values") or {}).items()
                if isinstance(key, str)
            }
            try:
                rendered = _render(query, {"inputs": inputs, "user": SafeUser.from_user(user)}, max_output=100_000)
            except Exception as exc:
                return Response({"count": None, "names": [], "error": str(exc)})
        if not isinstance(rendered, dict):
            rendered = {}
        for lookup in expanded_lookups(rendered):
            if not lookup_allowed(model, lookup):
                return Response({"detail": f"Filter lookup '{lookup}' is not allowed."}, status=400)
        include, exclude = split_filter(rendered)
        queryset = model.objects.restrict(user, "view")
        if include:
            queryset = queryset.filter(**include)
        if exclude:
            queryset = queryset.exclude(**exclude)
        names = []
        examples = []
        for obj in queryset.distinct()[:8]:
            label = str(getattr(obj, "name", None) or getattr(obj, "display", None) or obj)
            names.append(label)
            examples.append({"id": str(obj.pk), "label": label})
        return Response({"count": queryset.distinct().count(), "names": names, "examples": examples})


class BuilderFilterFieldsView(APIView):
    """List a model's filterable fields for the visual filter builder."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]

    @extend_schema(responses={200: OpenApiResponse(description="Filterable fields")})
    def post(self, request):
        """Return fields, operators, and typed values for one model."""
        user = request.user
        if not (
            user.has_perm("nautobot_workflow_engine.view_workflow")
            or user.has_perm("nautobot_workflow_engine.add_workflow")
            or user.has_perm("nautobot_workflow_engine.change_workflow")
        ):
            raise PermissionDenied(detail="You do not have permission to perform this action.")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        model_label = str((data or {}).get("model") or "")
        if not content_type_allowed(model_label) or "." not in model_label:
            return Response({"detail": "That model is not available."}, status=400)
        from django.apps import apps

        from nautobot_workflow_engine.schema.filter_fields import example_objects, filter_fields_for_model

        app_label, model_name = model_label.split(".", 1)
        try:
            model = apps.get_model(app_label, model_name)
        except Exception as exc:
            if exc.__class__.__name__ != "LookupError":
                raise
            return Response({"detail": "That model is not available."}, status=400)
        return Response({"fields": filter_fields_for_model(model, user), "examples": example_objects(model, user)})


class WorkflowViewSet(ViewSet):
    """List, draft, and publish workflows. Git push and archive arrive in M3."""

    permission_classes = [IsAuthenticated]
    parser_classes = [BodyParser]
    lookup_field = "key"
    lookup_value_regex = "[^/]+"

    @extend_schema(responses={200: WorkflowSummarySerializer(many=True), 403: DetailSerializer})
    def list(self, request):
        """Return workflows this user can view."""
        _require(request.user, "nautobot_workflow_engine.view_workflow")
        rows = Workflow.objects.restrict(request.user, "view").order_by("name")
        return Response([_summary(workflow) for workflow in rows])

    @extend_schema(responses={201: WorkflowDetailSerializer, 400: ErrorListSerializer, 403: DetailSerializer})
    def create(self, request):
        """Create a draft. Runtime rows stay empty until publish."""
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        try:
            strict = _wants_strict(data)
            definition = _definition_from_body(data, strict=strict)
            repo = _repository(request.user, data.get("repository") or (data.get("payload") or {}).get("source_repo"))
            workflow, _revision = create_draft(
                user=request.user,
                definition=definition,
                repository=repo,
                comment=data.get("comment") or "",
                source=WorkflowRevision.SOURCE_API,
                strict=strict,
            )
            _apply_tags(workflow, request.user, data)
        except ServiceError as exc:
            return _service_response(exc)
        return Response(
            {
                "workflow": _summary(workflow),
                "revision": _revision_payload(workflow.draft_revision),
                "errors": list(getattr(workflow, "_draft_errors", []) or []),
            },
            status=201,
        )

    @extend_schema(responses={200: WorkflowDetailSerializer, 403: DetailSerializer, 404: DetailSerializer})
    def retrieve(self, request, key=None):
        """Return the published revision, the draft, and the base the editor must send back."""
        _require(request.user, "nautobot_workflow_engine.view_workflow")
        return Response(_detail(_visible(request.user, key, "view")))

    @extend_schema(request=BaseSerializer, responses=_WRITE, methods=["PUT"])
    @extend_schema(
        responses={204: OpenApiResponse(description="Draft discarded"), 404: DetailSerializer}, methods=["DELETE"]
    )
    def draft(self, request, key=None):
        """Save or discard the shared draft. A draft does not change the published definition."""
        _require(request.user, "nautobot_workflow_engine.change_workflow")
        workflow = _visible(request.user, key, "change")
        if request.method == "DELETE":
            try:
                discard_draft(user=request.user, workflow=workflow)
            except ServiceError as exc:
                return _service_response(exc)
            return Response(status=204)
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        if "base_revision" not in data:
            return Response(
                _errors_payload([FieldError("/base_revision", "required", "Send the revision you started from.")]),
                status=400,
            )
        try:
            strict = _wants_strict(data)
            definition = _definition_from_body(data, strict=strict)
            revision = update_draft(
                user=request.user,
                workflow=workflow,
                definition=definition,
                base_revision=data.get("base_revision"),
                comment=data.get("comment") or "",
                base_git_commit=data.get("base_git_commit"),
                base_content_hash=data.get("base_content_hash"),
                require_base=True,
                source=WorkflowRevision.SOURCE_API,
                strict=strict,
            )
            _apply_tags(workflow, request.user, data)
        except ServiceError as exc:
            return _service_response(exc)
        return Response(
            {"revision": _revision_payload(revision), "errors": list(getattr(revision, "_draft_errors", []) or [])}
        )

    @extend_schema(responses=_WRITE)
    def publish_revision(self, request, key=None):
        """Publish the definition. Git push is M3, so git.status is not_configured."""
        _require(request.user, "nautobot_workflow_engine.change_workflow")
        workflow = _visible(request.user, key, "change")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        if "base_revision" not in data:
            return Response(
                _errors_payload([FieldError("/base_revision", "required", "Send the revision you started from.")]),
                status=400,
            )
        try:
            definition = _definition_from_body(data)
            repo = _repository(request.user, data.get("repository"))
            if data.get("repository") not in (None, ""):
                from nautobot_workflow_engine.utilities.git_publish import publish_blocker

                blocker = publish_blocker(repo) if repo is not None else ""
                if blocker:
                    payload = _errors_payload(
                        [FieldError("/source_repo", "repository_required", blocker, path="source_repo")]
                    )
                    payload["detail"] = blocker
                    return Response(payload, status=400)
            saved, revision = publish(
                user=request.user,
                workflow=workflow,
                definition=definition,
                base_revision=data.get("base_revision"),
                enable=data.get("enable", None) if "enable" in data else None,
                commit_summary=data.get("commit_summary") or "",
                approval_confirm=bool(data.get("approval_confirm")),
                repository=repo if repo is not None else workflow.source_repo,
                base_git_commit=data.get("base_git_commit"),
                base_content_hash=data.get("base_content_hash"),
                require_base=True,
                source=WorkflowRevision.SOURCE_API,
            )
            _apply_tags(saved, request.user, data)
        except ServiceError as exc:
            return _service_response(exc)
        saved.refresh_from_db()
        body = _detail(saved)
        body["revision"] = _revision_payload(revision)
        body["git"] = {
            "status": saved.git_status or "not_configured",
            "job_result": str(saved.git_job_result_id) if saved.git_job_result_id else None,
        }
        body["warnings"] = list(getattr(saved, "_git_warnings", []) or [])
        return Response(body, status=202)

    def dry_run(self, request, key=None):
        """Preview run against a saved workflow. A body definition overrides the stored one. Nothing is executed."""
        _require(request.user, "nautobot_workflow_engine.change_workflow")
        workflow = _visible(request.user, key, "change")
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        try:
            if any(data.get(item) not in (None, "") for item in ("definition", "payload", "yaml")):
                definition = _definition_from_body(data, strict=False)
            elif data.get("revision"):
                revision = workflow.revisions.filter(number=data.get("revision")).first()
                if revision is None:
                    return Response(
                        _errors_payload([FieldError("/revision", "not_found", "That revision does not exist.")]),
                        status=404,
                    )
                definition = revision.definition
            else:
                draft = workflow.draft_revision
                definition = draft.definition if draft is not None else (workflow.raw_definition or {})
        except ServiceError as exc:
            return _service_response(exc)
        result = dry_run(
            definition,
            data.get("inputs") or {},
            data.get("mock_results") or {},
            request.user,
            repo=workflow.source_repo,
        )
        return Response(result)

    @extend_schema(responses={200: WorkflowSummarySerializer, 403: DetailSerializer, 404: DetailSerializer})
    def enable(self, request, key=None):
        """Enable a published workflow."""
        return self._set_enabled(request, key, True)

    @extend_schema(responses={200: WorkflowSummarySerializer, 403: DetailSerializer, 404: DetailSerializer})
    def disable(self, request, key=None):
        """Disable a published workflow."""
        return self._set_enabled(request, key, False)

    def _set_enabled(self, request, key, enabled):
        _require(request.user, "nautobot_workflow_engine.publish_workflow")
        workflow = _visible(request.user, key, "publish")
        try:
            saved = set_enabled(user=request.user, workflow=workflow, enabled=enabled)
        except ServiceError as exc:
            return _service_response(exc)
        return Response(_summary(saved))

    @extend_schema(responses={200: RevisionSerializer(many=True), 403: DetailSerializer, 404: DetailSerializer})
    def revisions(self, request, key=None):
        """Return revision history, including superseded revisions."""
        _require(request.user, "nautobot_workflow_engine.view_workflow")
        workflow = _visible(request.user, key, "view")
        return Response([_revision_payload(revision) for revision in workflow.revisions.order_by("number")])

    @extend_schema(responses={200: RevisionSerializer, 404: DetailSerializer})
    def revision_detail(self, request, key=None, number=None):
        """Return one revision, including its YAML."""
        _require(request.user, "nautobot_workflow_engine.view_workflow")
        workflow = _visible(request.user, key, "view")
        revision = _revision_or_404(workflow, number)
        return Response(_revision_payload(revision))

    @extend_schema(
        parameters=[OpenApiParameter(name="against", required=False, type=str)],
        responses={200: DiffSerializer, 404: DetailSerializer},
    )
    def revision_diff(self, request, key=None, number=None):
        """Return YAML for Nautobot's diff view."""
        _require(request.user, "nautobot_workflow_engine.view_workflow")
        workflow = _visible(request.user, key, "view")
        revision = _revision_or_404(workflow, number)
        against = request.query_params.get("against") or "published"
        if against == "published":
            original = workflow.published_revision.yaml if workflow.published_revision_id else ""
        else:
            original = _revision_or_404(workflow, against).yaml
        return Response({"original_yaml": original or "", "modified_yaml": revision.yaml or ""})

    @extend_schema(responses={200: RevisionSerializer, 400: ErrorListSerializer, 404: DetailSerializer})
    def revision_restore(self, request, key=None, number=None):
        """Copy an old revision into the shared draft."""
        _require(request.user, "nautobot_workflow_engine.change_workflow")
        workflow = _visible(request.user, key, "change")
        revision = _revision_or_404(workflow, number)
        try:
            restored = restore_revision(user=request.user, workflow=workflow, revision=revision)
        except ServiceError as exc:
            return _service_response(exc)
        return Response({"revision": _revision_payload(restored)})

    @extend_schema(
        request=DuplicateSerializer,
        responses={201: WorkflowDetailSerializer, 400: ErrorListSerializer, 403: DetailSerializer},
    )
    def duplicate(self, request, key=None):
        """Copy a workflow into a disabled Nautobot-managed draft."""
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        source = _visible(request.user, key, "view")
        new_key = (data.get("key") or "").strip()
        new_name = (data.get("name") or "").strip()
        if not new_key or not new_name:
            return Response(
                _errors_payload([FieldError("/key", "required", "Send a key and a name for the copy.")]),
                status=400,
            )
        try:
            workflow, revision = duplicate_workflow(user=request.user, source=source, key=new_key, name=new_name)
        except ServiceError as exc:
            return _service_response(exc)
        return Response({"workflow": _summary(workflow), "revision": _revision_payload(revision)}, status=201)

    @extend_schema(responses={200: RefreshSerializer, 404: DetailSerializer})
    def git_refresh(self, request, key=None):
        """Refresh the repository when the editor opens."""
        _require(request.user, "nautobot_workflow_engine.view_workflow")
        workflow = _visible(request.user, key, "view")
        from nautobot_workflow_engine.utilities.git_publish import refresh_on_open

        return Response(refresh_on_open(workflow, request.user))

    @extend_schema(responses={200: RevisionSerializer, 400: DetailSerializer, 404: DetailSerializer})
    def load_git(self, request, key=None):
        """Copy the live Git version into the draft. The previous draft stays in history."""
        _require(request.user, "nautobot_workflow_engine.change_workflow")
        workflow = _visible(request.user, key, "change")
        try:
            revision = load_published_into_draft(user=request.user, workflow=workflow)
        except ServiceError as exc:
            return _service_response(exc)
        return Response({"revision": _revision_payload(revision)})

    @extend_schema(request=AdoptRequestSerializer, responses={200: WorkflowSummarySerializer, 400: DetailSerializer})
    def adopt(self, request, key=None):
        """Make a Git-managed workflow editable. Includes are listed until confirm is true."""
        data, failure = _load_body(request)
        if failure is not None and request.POST:
            data = request.POST.dict()
        elif failure is not None:
            return failure
        _require(request.user, "nautobot_workflow_engine.change_workflow")
        workflow = _visible(request.user, key, "change")
        try:
            result = adopt_workflow(user=request.user, workflow=workflow, confirm=_as_bool(data.get("confirm")))
        except ServiceError as exc:
            return _service_response(exc)
        body = _summary(result["workflow"])
        body["adopted"] = result["adopted"]
        body["includes"] = result["includes"]
        body["warning"] = result["warning"]
        return Response(body)

    @extend_schema(responses={202: GitStatusSerializer, 404: DetailSerializer})
    def git_push(self, request, key=None):
        """Retry the push now. The database row is already saved."""
        _require(request.user, "nautobot_workflow_engine.publish_workflow")
        workflow = _visible(request.user, key, "publish")
        if workflow.source_repo_id is None:
            return Response({"detail": "This workflow has no Git repository."}, status=400)
        from nautobot.extras.secrets.exceptions import SecretError

        from nautobot_workflow_engine.utilities.git_publish import publish_blocker, publish_repository

        blocker = publish_blocker(workflow.source_repo)
        if blocker:
            workflow.git_status = "disabled_repo"
            workflow.git_error = blocker
            workflow.save(update_fields=["git_status", "git_error", "last_updated"])
            return Response(_git_status_payload(workflow), status=202)
        workflow.git_status = "pending"
        workflow.git_next_retry_at = None
        workflow.save(update_fields=["git_status", "git_next_retry_at", "last_updated"])
        try:
            publish_repository(workflow.source_repo_id, request.user)
        except SecretError:
            # The row already records a sanitized failure. Retry now should show that, not a 500.
            pass
        workflow.refresh_from_db()
        return Response(_git_status_payload(workflow), status=202)

    @extend_schema(responses={200: GitStatusSerializer, 404: DetailSerializer})
    def git_status(self, request, key=None):
        """Return the Git badge fields for polling."""
        _require(request.user, "nautobot_workflow_engine.view_workflow")
        workflow = _visible(request.user, key, "view")
        return Response(_git_status_payload(workflow))

    @extend_schema(request=ArchiveRequestSerializer, responses={200: WorkflowSummarySerializer, 400: DetailSerializer})
    def archive(self, request, key=None):
        """Archive a workflow and remove its file on the next push."""
        data, failure = _load_body(request)
        if failure is not None:
            return failure
        _require(request.user, "nautobot_workflow_engine.delete_workflow")
        workflow = _visible(request.user, key, "delete")
        try:
            saved = archive_workflow(user=request.user, workflow=workflow, confirm_key=data.get("confirm_key") or "")
        except ServiceError as exc:
            return _service_response(exc)
        saved.refresh_from_db()
        return Response(_summary(saved))

    @extend_schema(responses={200: WorkflowSummarySerializer, 404: DetailSerializer})
    def unarchive(self, request, key=None):
        """Restore an archived workflow and publish its file again."""
        _require(request.user, "nautobot_workflow_engine.change_workflow")
        workflow = _visible(request.user, key, "change")
        try:
            saved = unarchive_workflow(user=request.user, workflow=workflow)
        except ServiceError as exc:
            return _service_response(exc)
        saved.refresh_from_db()
        return Response(_summary(saved))


def _git_status_payload(workflow):
    from django.urls import reverse

    url = ""
    if workflow.git_job_result_id:
        try:
            url = reverse("extras:jobresult", kwargs={"pk": workflow.git_job_result_id})
        except Exception:
            url = ""
    return {
        "git_status": workflow.git_status,
        "git_commit": workflow.git_commit or "",
        "git_error": workflow.git_error or "",
        "git_retry_count": workflow.git_retry_count or 0,
        "git_next_retry_at": workflow.git_next_retry_at,
        "job_result_url": url,
    }


def _revision_or_404(workflow, number):
    try:
        return workflow.revisions.get(number=int(number))
    except (WorkflowRevision.DoesNotExist, TypeError, ValueError):
        raise Http404("Not found.")
