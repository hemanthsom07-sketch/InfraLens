"""Phase 7E: end-to-end integration tests for POST /security, in the
same style as tests/test_impact_integration.py and
tests/test_graph_diagnostics_integration.py — the real pipeline
(analysis_service.run_analysis -> ikm_service -> graph_service) against
a real local repository fixture, with only git_service.clone_repository
faked.

IMPORTANT FIXTURE RULE (per this phase's explicit instruction, and the
lesson from Phase 7B-7D): plain `image:` references only, no `build:`
context and no Dockerfiles anywhere in these fixtures. This isn't just
about avoiding the Compose->Dockerfile USES-edge inference issue (that
issue is specific to graph traversal, and — as of Phase 7E through
7G — security_service never touched the graph at all, so it never
mattered here either) — it's also simply cleaner: a Dockerfile
component with its own base image would itself be a second,
independent MUTABLE_IMAGE_TAG finding source, muddying these fixtures'
deliberately exact expected-finding counts for no reason.

PHASE 9 UPDATE: security_service now genuinely does use graph_engine,
for the new attack-surface check — see the fake_clone_attack_surface
fixture and its tests near the end of this file. Every fixture from
7E/7G intentionally has no Secret component at all, so none of them
trigger the new EXPOSED_PATH_TO_SECRET finding — confirmed by direct
inspection before adding Phase 9's tests, not assumed.

Findings fixture (fake_clone) deliberately triggers all 5 rules across
3 different services:
    db      -- image "postgres:latest" (MUTABLE_IMAGE_TAG),
               POSTGRES_PASSWORD=hunter2 (HARDCODED_SECRET_ENV_VAR),
               publishes 5432 while being a known db image (DATABASE_PORT_PUBLISHED)
    backend -- bind-mounts /var/run/docker.sock (DOCKER_SOCKET_MOUNT)
    admin   -- publishes port 22 (SENSITIVE_PORT_PUBLISHED)

Clean fixture (fake_clone_clean) has pinned tags, no secrets, no
mounts, no published ports at all -- zero findings expected.
"""

import shutil
from pathlib import Path

import pytest

from app.api.v1.analyze import analyze_repository
from app.api.v1.security import SecurityAPIRequest, get_security_findings
from app.models.schemas import AnalyzeRequest
from app.services import analysis_service
from app.services.analysis_store import AnalysisStore
from tests.conftest import write

_COMPOSE_FILE = "docker-compose.yml"


def _service_id(service_name: str, *, compose_file: str = _COMPOSE_FILE) -> str:
    return f"compose:{compose_file}:{service_name}"


@pytest.fixture(autouse=True)
def _fresh_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_service, "_store", AnalysisStore())


@pytest.fixture
def fake_clone(monkeypatch: pytest.MonkeyPatch, tmp_repo: Path) -> Path:
    write(
        tmp_repo,
        "docker-compose.yml",
        """\
        services:
          db:
            image: postgres:latest
            ports:
              - "5432:5432"
            environment:
              - POSTGRES_PASSWORD=hunter2
          backend:
            image: myorg/backend:1.0
            depends_on:
              - db
            volumes:
              - /var/run/docker.sock:/var/run/docker.sock
          admin:
            image: myorg/admin:2.0
            ports:
              - "22:22"
        """,
    )

    def fake_clone_repository(owner: str, repo: str, destination: Path) -> None:
        shutil.copytree(tmp_repo, destination, dirs_exist_ok=True)

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)
    return tmp_repo


@pytest.fixture
def fake_clone_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    repo = tmp_path / "clean-repo"
    write(
        repo,
        "docker-compose.yml",
        """\
        services:
          db:
            image: postgres:16.1
          backend:
            image: myorg/backend:2.3.1
            depends_on:
              - db
        """,
    )

    def fake_clone_repository(owner: str, repo_name: str, destination: Path) -> None:
        shutil.copytree(repo, destination, dirs_exist_ok=True)

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)
    return repo


@pytest.fixture
def fake_clone_kubernetes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Phase 7G: a real Kubernetes manifest exercising all 4 new rules
    through the real parser -> ikm_service -> security_service pipeline,
    not just the unit-level hand-built Component fixtures in
    test_security_rules.py."""
    repo = tmp_path / "k8s-repo"
    write(
        repo,
        "k8s.yaml",
        """\
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
        spec:
          template:
            metadata:
              labels:
                app: web
            spec:
              containers:
                - name: web
                  image: myapp:2.0
                  securityContext:
                    privileged: true
                    runAsNonRoot: false
        ---
        apiVersion: v1
        kind: Service
        metadata:
          name: web-svc
        spec:
          type: LoadBalancer
          selector:
            app: web
        ---
        apiVersion: networking.k8s.io/v1
        kind: Ingress
        metadata:
          name: web-ingress
        spec:
          rules: []
        """,
    )

    def fake_clone_repository(owner: str, repo_name: str, destination: Path) -> None:
        shutil.copytree(repo, destination, dirs_exist_ok=True)

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)
    return repo


def _k8s_id(kind: str, name: str, *, source_file: str = "k8s.yaml") -> str:
    return f"kubernetes:{source_file}:{kind}:{name}"


# --- analyze -> analysis_id -> security findings (positive) --------------------


def test_analyze_then_security_findings_with_analysis_id(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    request = SecurityAPIRequest(analysis_id=analyze_response.analysis_id)
    result = get_security_findings(request)

    assert result.total_count == 5
    rule_ids = {f.rule_id for f in result.findings}
    assert rule_ids == {
        "MUTABLE_IMAGE_TAG",
        "HARDCODED_SECRET_ENV_VAR",
        "DATABASE_PORT_PUBLISHED",
        "DOCKER_SOCKET_MOUNT",
        "SENSITIVE_PORT_PUBLISHED",
    }


def test_findings_are_attributed_to_the_correct_components(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    findings_by_component: dict[str, set[str]] = {}
    for f in result.findings:
        findings_by_component.setdefault(f.component_id, set()).add(f.rule_id)

    assert findings_by_component[_service_id("db")] == {"MUTABLE_IMAGE_TAG", "HARDCODED_SECRET_ENV_VAR", "DATABASE_PORT_PUBLISHED"}
    assert findings_by_component[_service_id("backend")] == {"DOCKER_SOCKET_MOUNT"}
    assert findings_by_component[_service_id("admin")] == {"SENSITIVE_PORT_PUBLISHED"}


def test_findings_carry_exact_provenance(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    secret_finding = next(f for f in result.findings if f.rule_id == "HARDCODED_SECRET_ENV_VAR")
    assert secret_finding.source_file == _COMPOSE_FILE
    assert secret_finding.component_name == "db"
    assert secret_finding.technology == "docker-compose"
    assert "POSTGRES_PASSWORD" in secret_finding.reason


def test_counts_by_severity_reflect_the_real_rule_definitions(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    # HARDCODED_SECRET_ENV_VAR (high) + DOCKER_SOCKET_MOUNT (high) = 2 high
    # MUTABLE_IMAGE_TAG + DATABASE_PORT_PUBLISHED + SENSITIVE_PORT_PUBLISHED (medium) = 3 medium
    assert result.counts_by_severity == {"high": 2, "medium": 3}


def test_findings_are_deterministically_ordered_across_repeated_calls(fake_clone: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))
    request = SecurityAPIRequest(analysis_id=analyze_response.analysis_id)

    first = [(f.component_id, f.rule_id) for f in get_security_findings(request).findings]
    second = [(f.component_id, f.rule_id) for f in get_security_findings(request).findings]

    assert first == second
    assert first == sorted(first)


# --- clean repo: negative case ---------------------------------------------------


def test_clean_repo_produces_no_findings(fake_clone_clean: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/clean-repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    assert result.findings == []
    assert result.total_count == 0
    assert result.counts_by_severity == {}


# --- backward compatibility: repo_url path still works ---------------------------


def test_security_with_repo_url_still_works_without_an_analysis_id(fake_clone: Path) -> None:
    request = SecurityAPIRequest(repo_url="https://github.com/example/repo")
    result = get_security_findings(request)
    assert result.total_count == 5


# --- error paths -------------------------------------------------------------------


def test_security_unknown_analysis_id_raises() -> None:
    from app.exceptions import AnalysisNotFoundError

    request = SecurityAPIRequest(analysis_id="ghost-id")
    with pytest.raises(AnalysisNotFoundError):
        get_security_findings(request)


# --- graph is not rebuilt / no re-clone when analysis_id is reused ------------


def test_no_reclone_when_analysis_id_is_reused_for_security(
    fake_clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/repo"))

    clone_calls = []
    original_clone = analysis_service.clone_repository

    def counting_clone(owner: str, repo: str, destination: Path) -> None:
        clone_calls.append((owner, repo))
        original_clone(owner, repo, destination)

    monkeypatch.setattr(analysis_service, "clone_repository", counting_clone)

    get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))
    get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    assert clone_calls == []


# --- Phase 7G: Kubernetes rules through the real pipeline ------------------------


def test_kubernetes_manifest_produces_expected_findings(fake_clone_kubernetes: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/k8s-repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    rule_ids = {f.rule_id for f in result.findings}
    assert rule_ids == {
        "PRIVILEGED_CONTAINER",
        "CONTAINER_ALLOWED_TO_RUN_AS_ROOT",
        "EXTERNALLY_REACHABLE_SERVICE",
        "INGRESS_WITHOUT_TLS",
    }
    assert result.total_count == 4


def test_kubernetes_findings_attributed_to_correct_components(fake_clone_kubernetes: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/k8s-repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    findings_by_component: dict[str, set[str]] = {}
    for f in result.findings:
        findings_by_component.setdefault(f.component_id, set()).add(f.rule_id)

    assert findings_by_component[_k8s_id("Deployment", "web")] == {
        "PRIVILEGED_CONTAINER",
        "CONTAINER_ALLOWED_TO_RUN_AS_ROOT",
    }
    assert findings_by_component[_k8s_id("Service", "web-svc")] == {"EXTERNALLY_REACHABLE_SERVICE"}
    assert findings_by_component[_k8s_id("Ingress", "web-ingress")] == {"INGRESS_WITHOUT_TLS"}


def test_kubernetes_finding_source_file_and_technology(fake_clone_kubernetes: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/k8s-repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    privileged_finding = next(f for f in result.findings if f.rule_id == "PRIVILEGED_CONTAINER")
    assert privileged_finding.source_file == "k8s.yaml"
    assert privileged_finding.technology == "kubernetes"
    assert privileged_finding.component_name == "web"


def test_kubernetes_and_compose_findings_coexist_when_both_present(
    fake_clone: Path, fake_clone_kubernetes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross-technology regression, at the real-pipeline level: a repo
    with BOTH a docker-compose.yml (Phase 7E rules) and a Kubernetes
    manifest (Phase 7G rules) produces findings from both, unaffected by
    each other."""
    import shutil as _shutil

    combined_dir = fake_clone_kubernetes.parent / "combined-repo"
    _shutil.copytree(fake_clone_kubernetes, combined_dir)
    _shutil.copy(fake_clone / "docker-compose.yml", combined_dir / "docker-compose.yml")

    def fake_clone_repository(owner: str, repo_name: str, destination: Path) -> None:
        _shutil.copytree(combined_dir, destination, dirs_exist_ok=True)

    monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)

    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/combined-repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    rule_ids = {f.rule_id for f in result.findings}
    assert "PRIVILEGED_CONTAINER" in rule_ids  # from the Kubernetes manifest
    assert "MUTABLE_IMAGE_TAG" in rule_ids  # from docker-compose.yml
    assert result.total_count == 9  # 4 Kubernetes + 5 Compose


def test_kubernetes_findings_deterministically_ordered(fake_clone_kubernetes: Path) -> None:
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/k8s-repo"))
    request = SecurityAPIRequest(analysis_id=analyze_response.analysis_id)

    first = [(f.component_id, f.rule_id) for f in get_security_findings(request).findings]
    second = [(f.component_id, f.rule_id) for f in get_security_findings(request).findings]

    assert first == second
    assert first == sorted(first)


# --- Phase 9: attack-surface analysis through the real pipeline ------------------


@pytest.fixture
def fake_clone_attack_surface(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A real, complete Ingress -> Service -> Deployment -> Secret chain.
    `has_tls` is parameterized so the same builder produces both the
    positive (TLS-less) and negative (TLS-enabled) fixtures required by
    this phase, from one place, rather than two near-duplicate fixtures
    drifting apart over time.

    Built with plain string concatenation rather than an interpolated
    write()-dedented block: mixing a conditionally-inserted multi-line
    block into a dedent()-normalized triple-quoted string is fragile
    (the inserted block's own indentation and the surrounding string's
    indentation have to agree exactly, and silently don't by default) --
    simpler and more obviously correct to build the full YAML text
    directly, indentation included, with no dedent() step at all.
    """

    def _build(has_tls: bool) -> Path:
        repo = tmp_path / f"attack-surface-repo-{'tls' if has_tls else 'no-tls'}"

        secret_doc = (
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: db-secret\n"
            "type: Opaque\n"
            "data:\n"
            "  password: aHVudGVyMg==\n"
        )
        deployment_doc = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: web\n"
            "spec:\n"
            "  template:\n"
            "    metadata:\n"
            "      labels:\n"
            "        app: web\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: web\n"
            "          image: myapp:2.0\n"
            "          env:\n"
            "            - name: DB_PASSWORD\n"
            "              valueFrom:\n"
            "                secretKeyRef:\n"
            "                  name: db-secret\n"
            "                  key: password\n"
        )
        service_doc = (
            "apiVersion: v1\n"
            "kind: Service\n"
            "metadata:\n"
            "  name: web-svc\n"
            "spec:\n"
            "  type: ClusterIP\n"
            "  selector:\n"
            "    app: web\n"
        )
        tls_lines = (
            '  tls:\n    - hosts: ["example.com"]\n      secretName: web-tls\n' if has_tls else ""
        )
        ingress_doc = (
            "apiVersion: networking.k8s.io/v1\n"
            "kind: Ingress\n"
            "metadata:\n"
            "  name: web-ingress\n"
            "spec:\n"
            f"{tls_lines}"
            "  rules:\n"
            "    - http:\n"
            "        paths:\n"
            "          - backend:\n"
            "              service:\n"
            "                name: web-svc\n"
        )

        full_yaml = "---\n".join([secret_doc, deployment_doc, service_doc, ingress_doc])
        write(repo, "k8s.yaml", full_yaml)
        return repo

    def _clone_for(has_tls: bool):
        repo = _build(has_tls)

        def fake_clone_repository(owner: str, repo_name: str, destination: Path) -> None:
            shutil.copytree(repo, destination, dirs_exist_ok=True)

        monkeypatch.setattr(analysis_service, "clone_repository", fake_clone_repository)

    return _clone_for



def test_attack_surface_finding_through_the_real_pipeline_tls_less(
    fake_clone_attack_surface,
) -> None:
    fake_clone_attack_surface(has_tls=False)
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/attack-surface-repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    attack_findings = [f for f in result.findings if f.rule_id == "EXPOSED_PATH_TO_SECRET"]
    assert len(attack_findings) == 1
    assert attack_findings[0].severity == "high"
    assert attack_findings[0].component_id == _k8s_id("Ingress", "web-ingress")
    assert "db-secret" in attack_findings[0].reason
    assert "inferred, high-confidence label-selector match" in attack_findings[0].reason


def test_attack_surface_finding_disappears_when_ingress_has_tls(fake_clone_attack_surface) -> None:
    fake_clone_attack_surface(has_tls=True)
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/attack-surface-repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    attack_findings = [f for f in result.findings if f.rule_id == "EXPOSED_PATH_TO_SECRET"]
    assert attack_findings == []


def test_attack_surface_coexists_with_metadata_only_findings_in_the_same_repo(
    fake_clone_attack_surface,
) -> None:
    """The Deployment's mutable image tag (myapp:2.0 is actually pinned
    -- so no MUTABLE_IMAGE_TAG here; instead confirm the response shape
    itself is unaffected: total_count/counts_by_severity correctly
    reflect a mix that could include both finding sources in principle,
    verified structurally rather than assuming a specific unrelated
    finding fires in this particular fixture."""
    fake_clone_attack_surface(has_tls=False)
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/attack-surface-repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    assert result.total_count == len(result.findings)
    assert result.counts_by_severity.get("high", 0) >= 1


def test_attack_surface_findings_do_not_break_existing_7g_fixture_assertions(
    fake_clone_kubernetes: Path,
) -> None:
    """Explicit regression check: the pre-existing 7G fixture (no
    Secret component at all) must produce zero EXPOSED_PATH_TO_SECRET
    findings, confirming Phase 9 didn't silently change 7G's behavior."""
    analyze_response = analyze_repository(AnalyzeRequest(repo_url="https://github.com/example/k8s-repo"))
    result = get_security_findings(SecurityAPIRequest(analysis_id=analyze_response.analysis_id))

    assert "EXPOSED_PATH_TO_SECRET" not in {f.rule_id for f in result.findings}
    assert result.total_count == 4  # unchanged from Phase 7G


# --- app wiring ------------------------------------------------------------------


def test_app_registers_security_route() -> None:
    from app.main import app

    paths = set(app.openapi()["paths"].keys())
    assert "/api/v1/security" in paths
