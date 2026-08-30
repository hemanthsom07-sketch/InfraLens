"""Deterministic security rules (Phase 7E; extended by Phase 7G).

AUDIT FINDING PHASE 7E'S RULES WERE BUILT FROM (see the module docstring
in app.services.security_service for the full write-up): inspection of
every parser's actual captured metadata found real, evidence-backed
support for exactly 5 rules at that time — not the 8 originally
speculated. In particular:

- Kubernetes, AS OF PHASE 7E, captured NO TLS field, NO Service `type`
  field, NO Secret `data`/`type` field, and NO privileged/securityContext
  field at all — so no Kubernetes-specific rule existed yet.
- Terraform captures ONLY resource_type/resource_name/reference chains
  — zero resource-body attributes (no cidr_blocks, no public-access
  flags, nothing). NO Terraform security rule is implemented here for
  the same reason, still true as of Phase 7G.

PHASE 7F then enriched app/parsers/kubernetes_parser.py with exactly the
fields the Phase 7E audit found missing (has_tls, service_type,
secret_type, has_data, privileged, run_as_non_root) — parser work only,
no rules. PHASE 7G is the rules that field enrichment was for. Its own
audit (see app.services.security_service's docstring) found real,
narrow support for exactly 4 new Kubernetes rules — not 5: the two
Secret fields (secret_type, has_data) do NOT, even combined, prove
anything a rule could responsibly fire on (secret_type=Opaque is the
single most common, entirely normal case; has_data=true is simply what
a useful Secret looks like) — so no Secret rule exists, a deliberate
evidence-first exclusion, not an oversight.

Every rule below is a pure function over already-parsed
Component.metadata, each backed by a field this project's parsers
already, verifiably capture (see each rule's docstring for exactly
which field and which parser). Each rule returns Observation objects
(app.explanation.evidence.ObservationKind.SECURITY_FINDING) — reusing
the existing evidence architecture exactly, rather than inventing a
parallel finding shape; app.api.v1.security is what maps these into the
public SecurityFinding/SecurityResponse API shape.

DETERMINISTIC ORDERING: run_security_rules() always returns findings
sorted by (component_id, rule_id) as an explicit final step — not relied
upon as an incidental byproduct of parser/rule iteration order.
"""

import re
from enum import StrEnum

from app.explanation.evidence import Observation, ObservationKind
from app.models.ikm import Component, InfrastructureModel


class Severity(StrEnum):
    """A small, fixed severity vocabulary. Assigned ONLY per the fixed
    rule definitions below — never computed, scored, or adjusted at
    runtime, and never touched by an LLM."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Deterministic relevance weight per severity, reusing the exact same
# "weight = deterministic relevance score, not a probability" convention
# app.explanation.evidence.Observation.weight already documents.
_WEIGHT_BY_SEVERITY: dict[Severity, float] = {
    Severity.HIGH: 1.0,
    Severity.MEDIUM: 0.6,
    Severity.LOW: 0.3,
}

_VARIABLE_REFERENCE_RE = re.compile(r"^\$\{.*\}$|^\$[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_KEY_MARKERS = frozenset({"PASSWORD", "SECRET", "TOKEN", "APIKEY", "PRIVATEKEY", "ACCESSKEY"})
_KNOWN_DATABASE_IMAGES = frozenset(
    {"postgres", "postgresql", "mysql", "mariadb", "mongo", "mongodb", "redis", "elasticsearch", "memcached", "cassandra", "rabbitmq"}
)
_SENSITIVE_HOST_PORTS = frozenset({21, 22, 23, 3389})  # FTP, SSH, Telnet, RDP


def _finding(
    *,
    rule_id: str,
    severity: Severity,
    title: str,
    reason: str,
    component: Component,
) -> Observation:
    """Every security Observation is built through this one function, so
    the shape (and the fields Stage 5D / app.api.v1.security both rely
    on) stays consistent across every rule below."""
    return Observation(
        kind=ObservationKind.SECURITY_FINDING,
        subject_id=component.id,
        detail={
            "rule_id": rule_id,
            "severity": severity.value,
            "title": title,
            "reason": reason,
            "component_name": component.name,
            "technology": component.technology,
            "source_file": component.metadata.get("source_file"),
        },
        weight=_WEIGHT_BY_SEVERITY[severity],
    )


# --- Rule 1: mutable/implicit image tag (":latest" or no tag at all) --------


def _has_mutable_tag(image: str) -> bool:
    """True if `image` has no explicit tag, or is explicitly tagged
    ":latest" — mutable, since the same tag can point at a different
    image tomorrow, so what actually runs isn't reproducible from the
    manifest alone. A digest-pinned reference ("image@sha256:...") is
    never flagged, regardless of tag — digest pinning is strictly
    stronger than any tag, including a non-"latest" one.

    Correctly distinguishes a registry host's own port (e.g.
    "registry.example.com:5000/app") from an actual tag separator by
    only looking for ":" after the LAST "/" — the registry/port prefix,
    if any, is never mistaken for a tag.
    """
    if "@" in image:
        return False  # digest-pinned — strictly stronger than any tag
    repository_and_tag = image.rsplit("/", 1)[-1]
    if ":" not in repository_and_tag:
        return True  # no tag at all -> implicit :latest
    tag = repository_and_tag.rsplit(":", 1)[-1]
    return tag == "latest"


def _check_mutable_image_tags(components: list[Component]) -> list[Observation]:
    """Compose service `image`, Docker `base_image` (the final/runtime
    build stage — see app.parsers.docker_parser's own base_image
    convention), and each Kubernetes workload's `images` entries — the
    three fields this project's parsers actually capture an image
    reference in. Terraform captures no image/AMI-equivalent field at
    all, so it's not checked here."""
    findings: list[Observation] = []
    for component in components:
        if component.technology == "docker-compose":
            image = component.metadata.get("image")
            if isinstance(image, str) and image and _has_mutable_tag(image):
                findings.append(
                    _finding(
                        rule_id="MUTABLE_IMAGE_TAG",
                        severity=Severity.MEDIUM,
                        title="Mutable or missing image tag",
                        reason=f"Service image '{image}' has no tag, or is tagged ':latest' — not reproducible.",
                        component=component,
                    )
                )
        elif component.technology == "docker":
            image = component.metadata.get("base_image")
            if isinstance(image, str) and image and _has_mutable_tag(image):
                findings.append(
                    _finding(
                        rule_id="MUTABLE_IMAGE_TAG",
                        severity=Severity.MEDIUM,
                        title="Mutable or missing base image tag",
                        reason=f"Base image '{image}' has no tag, or is tagged ':latest' — not reproducible.",
                        component=component,
                    )
                )
        elif component.technology == "kubernetes":
            for image in component.metadata.get("images", []):
                if isinstance(image, str) and image and _has_mutable_tag(image):
                    findings.append(
                        _finding(
                            rule_id="MUTABLE_IMAGE_TAG",
                            severity=Severity.MEDIUM,
                            title="Mutable or missing container image tag",
                            reason=f"Container image '{image}' has no tag, or is tagged ':latest' — not reproducible.",
                            component=component,
                        )
                    )
    return findings


# --- Rule 2: hardcoded secret-like value in an environment variable --------


def _looks_like_hardcoded_secret(key: str, value: str) -> bool:
    """True if `key` looks like a credential name (password/secret/token/
    API key/private key/access key — matched key-name-only, never on
    value content, since guessing at "this looks like a real secret
    value" would be exactly the kind of unfounded judgment call this
    phase's rules must avoid) AND `value` is a real, non-empty,
    non-placeholder string — not a Compose variable reference like
    "${DB_PASSWORD}" or "$DB_PASSWORD", which means the actual value
    comes from a .env file or the shell environment at deploy time, not
    from this file.
    """
    normalized_key = key.upper().replace("_", "").replace("-", "")
    if not any(marker in normalized_key for marker in _SECRET_KEY_MARKERS):
        return False
    value = value.strip()
    if not value:
        return False
    return not _VARIABLE_REFERENCE_RE.match(value)


def _check_hardcoded_secrets(components: list[Component]) -> list[Observation]:
    """Compose `environment` and Docker `environment` — the two fields
    this project's parsers actually capture as a key/value environment
    mapping. Kubernetes env vars are not captured by the current parser
    at all, so Kubernetes is not checked here."""
    findings: list[Observation] = []
    for component in components:
        if component.technology not in ("docker-compose", "docker"):
            continue
        environment = component.metadata.get("environment") or {}
        for key, value in environment.items():
            if isinstance(value, str) and _looks_like_hardcoded_secret(key, value):
                findings.append(
                    _finding(
                        rule_id="HARDCODED_SECRET_ENV_VAR",
                        severity=Severity.HIGH,
                        title="Hardcoded credential-like value in an environment variable",
                        reason=(
                            f"Environment variable '{key}' looks like a credential and has a "
                            "literal (non-variable-reference) value committed in this file."
                        ),
                        component=component,
                    )
                )
    return findings


# --- Rule 3: Docker socket bind-mounted into a container -------------------


def _is_docker_socket_mount(volume_entry: str) -> bool:
    """True if `volume_entry` bind-mounts the host's Docker socket —
    "/var/run/docker.sock:..." — into the container. Granting a
    container the host's Docker socket is equivalent to granting it
    root on the host: it can start new, unrestricted containers via the
    same daemon. One of the most well-established container escape
    patterns there is."""
    if ":" not in volume_entry:
        return False
    host_path = volume_entry.split(":", 1)[0]
    return host_path == "/var/run/docker.sock"


def _check_docker_socket_mounts(components: list[Component]) -> list[Observation]:
    """Compose `volumes` — the only field this project's parsers
    capture raw bind-mount strings in (Docker/Kubernetes parsers don't
    capture volume/mount information at all)."""
    findings: list[Observation] = []
    for component in components:
        if component.technology != "docker-compose":
            continue
        for volume_entry in component.metadata.get("volumes", []):
            if isinstance(volume_entry, str) and _is_docker_socket_mount(volume_entry):
                findings.append(
                    _finding(
                        rule_id="DOCKER_SOCKET_MOUNT",
                        severity=Severity.HIGH,
                        title="Docker socket mounted into a container",
                        reason=(
                            f"Volume '{volume_entry}' bind-mounts the host's Docker socket — "
                            "equivalent to granting this container root on the host."
                        ),
                        component=component,
                    )
                )
    return findings


# --- Rule 4: known database image with a host-published port --------------


def _image_repository_name(image: str) -> str:
    """The bare repository name from an image reference — no registry,
    no tag, no digest. "postgres:16" -> "postgres";
    "registry.example.com/library/postgres:16" -> "postgres"."""
    without_digest = image.split("@", 1)[0]
    last_segment = without_digest.rsplit("/", 1)[-1]
    return last_segment.split(":", 1)[0].lower()


def _is_known_database_image(image: str) -> bool:
    repository = _image_repository_name(image)
    return any(repository == name or repository.startswith(name) for name in _KNOWN_DATABASE_IMAGES)


def _check_database_ports_published(components: list[Component]) -> list[Observation]:
    """Compose `image` + `ports` — a service running a well-known
    database engine image that ALSO publishes a port to the host.
    Databases are usually intended to be reached only by other services
    on the same internal Compose network, not directly from the host/
    internet; publishing one is a common hardening gap worth flagging,
    even though it doesn't by itself prove the database is unauthenticated."""
    findings: list[Observation] = []
    for component in components:
        if component.technology != "docker-compose":
            continue
        image = component.metadata.get("image")
        ports = component.metadata.get("ports") or []
        if isinstance(image, str) and image and ports and _is_known_database_image(image):
            findings.append(
                _finding(
                    rule_id="DATABASE_PORT_PUBLISHED",
                    severity=Severity.MEDIUM,
                    title="Database service publishes a port to the host",
                    reason=(
                        f"Service image '{image}' is a known database engine and publishes "
                        f"port(s) {ports} to the host, rather than staying internal to the "
                        "Compose network."
                    ),
                    component=component,
                )
            )
    return findings


# --- Rule 5: sensitive administrative port published to the host ----------


def _sensitive_host_port(port_entry: str) -> int | None:
    """The host-side port number from a Compose `ports:` entry, if it
    names one of a small, well-known set of high-risk administrative
    ports (FTP 21, SSH 22, Telnet 23, RDP 3389) — None otherwise,
    including for any form this can't cleanly determine a single host
    port from (a bare container-port-only entry like "80", where Compose
    assigns a random host port; or a port range like "3000-3005:3000-3005").

    Compose `ports:` entries come in three forms:
    - "containerport"                       (no explicit host port -> None)
    - "hostport:containerport"
    - "bindip:hostport:containerport"
    """
    parts = port_entry.split(":")
    if len(parts) == 2:
        host_part = parts[0]
    elif len(parts) == 3:
        host_part = parts[1]
    else:
        return None
    if "-" in host_part:
        return None  # a port range -- not a single determinable port
    try:
        port = int(host_part)
    except ValueError:
        return None
    return port if port in _SENSITIVE_HOST_PORTS else None


def _check_sensitive_ports_published(components: list[Component]) -> list[Observation]:
    """Compose `ports` only — the only field where "this number" is
    genuinely a host-published port InfraLens can point to. Docker's
    EXPOSE is documentation-only (doesn't actually publish anything);
    Kubernetes' captured `ports` mixes containerPort and Service port,
    neither of which implies external/host reachability the way a
    Compose host-port mapping does — so this rule is Compose-only,
    rather than overclaiming exposure for a field that doesn't prove it."""
    findings: list[Observation] = []
    for component in components:
        if component.technology != "docker-compose":
            continue
        for port_entry in component.metadata.get("ports", []):
            if not isinstance(port_entry, str):
                continue
            sensitive_port = _sensitive_host_port(port_entry)
            if sensitive_port is not None:
                findings.append(
                    _finding(
                        rule_id="SENSITIVE_PORT_PUBLISHED",
                        severity=Severity.MEDIUM,
                        title="Administrative port published to the host",
                        reason=(
                            f"Port entry '{port_entry}' publishes host port {sensitive_port}, a "
                            "commonly-targeted administrative port."
                        ),
                        component=component,
                    )
                )
    return findings


# --- Rule 6 (Phase 7G): Ingress without TLS --------------------------------------


def _check_ingress_without_tls(components: list[Component]) -> list[Observation]:
    """Kubernetes Ingress `has_tls` (Phase 7F) — a clean, always-present
    boolean (never omitted/ambiguous, see app.parsers.kubernetes_parser).
    Fires only when it is explicitly False; there is no "absent/unknown"
    case for this field to worry about, but the check is still written
    as `is False` (not `is not True`) to stay exactly aligned with what
    the field actually proves, on principle."""
    findings: list[Observation] = []
    for component in components:
        if component.technology != "kubernetes" or component.metadata.get("kind") != "Ingress":
            continue
        if component.metadata.get("has_tls") is False:
            findings.append(
                _finding(
                    rule_id="INGRESS_WITHOUT_TLS",
                    severity=Severity.MEDIUM,
                    title="Ingress without TLS",
                    reason="This Ingress has no `tls` block configured — traffic is not encrypted in transit.",
                    component=component,
                )
            )
    return findings


# --- Rule 7 (Phase 7G): Service declared externally reachable -------------------


_EXTERNALLY_REACHABLE_SERVICE_TYPES = frozenset({"NodePort", "LoadBalancer"})


def _check_externally_reachable_services(components: list[Component]) -> list[Observation]:
    """Kubernetes Service `service_type` (Phase 7F). This field proves
    only how the manifest declares the Service should be reached — it
    proves NOTHING about authentication, network policy, or firewall
    rules layered in front of it. Worded and severitized accordingly:

    - "ClusterIP" (the default) is the internal-only baseline -> no
      finding.
    - "NodePort"/"LoadBalancer" mean the manifest itself declares the
      Service reachable from outside the cluster -- a plain factual
      exposure statement, not a claim that this is misconfigured or
      malicious. LOW severity: worth surfacing as a fact to review, not
      an alarm.
    - "ExternalName" is a DNS-CNAME-style redirect to an external name —
      it does not expose a workload the way the other three do at all,
      so it is deliberately NOT flagged; doing so would misrepresent
      what this field proves.
    """
    findings: list[Observation] = []
    for component in components:
        if component.technology != "kubernetes" or component.metadata.get("kind") != "Service":
            continue
        service_type = component.metadata.get("service_type")
        if service_type in _EXTERNALLY_REACHABLE_SERVICE_TYPES:
            findings.append(
                _finding(
                    rule_id="EXTERNALLY_REACHABLE_SERVICE",
                    severity=Severity.LOW,
                    title="Service declared externally reachable",
                    reason=(
                        f"This Service is type '{service_type}', which the manifest declares as "
                        "reachable from outside the cluster."
                    ),
                    component=component,
                )
            )
    return findings


# --- Rule 8 (Phase 7G): privileged container -------------------------------------


def _check_privileged_containers(components: list[Component]) -> list[Observation]:
    """Kubernetes workload `privileged` (Phase 7F) — a genuinely
    three-valued field (True/False/never-set, omitted from metadata when
    never set — see app.parsers.kubernetes_parser). Fires ONLY on an
    explicit True (`is True`, never a truthy check) — False and None
    (the field absent, i.e. component.metadata.get("privileged") is
    None) both correctly produce no finding: False is an explicit,
    confirmed-safe statement, and None means the manifest never says
    either way, which is not evidence of anything."""
    findings: list[Observation] = []
    for component in components:
        if component.technology != "kubernetes":
            continue
        if component.metadata.get("privileged") is True:
            findings.append(
                _finding(
                    rule_id="PRIVILEGED_CONTAINER",
                    severity=Severity.HIGH,
                    title="Privileged container",
                    reason=(
                        "This workload has at least one container with securityContext.privileged: "
                        "true — equivalent to root access on the host node."
                    ),
                    component=component,
                )
            )
    return findings


# --- Rule 9 (Phase 7G): container allowed to run as root ------------------------


def _check_containers_allowed_to_run_as_root(components: list[Component]) -> list[Observation]:
    """Kubernetes workload `run_as_non_root` (Phase 7F) — also
    three-valued (True/False/never-set). Fires ONLY on an explicit False
    (`is False`, never `is not True`) — None is deliberately NOT treated
    as equivalent to False and does NOT fire:

    A huge fraction of real-world manifests never set this field at all,
    and plenty of container images already run as a non-root user via
    their own Dockerfile USER instruction, entirely invisible to this
    Kubernetes-manifest-only field — treating silence as a finding would
    manufacture a claim from an absence of evidence, exactly what this
    project's rules are built to avoid. False is different: it's an
    explicit, positive statement in the manifest that the workload IS
    allowed to run as root, which is what the finding's reason says —
    not "this container runs as root" (a stronger claim this field alone
    doesn't prove; a runAsUser could still be set independently)."""
    findings: list[Observation] = []
    for component in components:
        if component.technology != "kubernetes":
            continue
        if component.metadata.get("run_as_non_root") is False:
            findings.append(
                _finding(
                    rule_id="CONTAINER_ALLOWED_TO_RUN_AS_ROOT",
                    severity=Severity.MEDIUM,
                    title="Container allowed to run as root",
                    reason="This workload's manifest explicitly declares runAsNonRoot: false.",
                    component=component,
                )
            )
    return findings


_RULES = (
    _check_mutable_image_tags,
    _check_hardcoded_secrets,
    _check_docker_socket_mounts,
    _check_database_ports_published,
    _check_sensitive_ports_published,
    _check_ingress_without_tls,
    _check_externally_reachable_services,
    _check_privileged_containers,
    _check_containers_allowed_to_run_as_root,
)


def run_security_rules(model: InfrastructureModel) -> list[Observation]:
    """Run every rule above against `model.components`, returning every
    finding as an Observation (kind=SECURITY_FINDING), deterministically
    ordered by (component_id, rule_id) regardless of rule execution or
    parser iteration order."""
    findings: list[Observation] = []
    for rule in _RULES:
        findings.extend(rule(model.components))
    findings.sort(key=lambda observation: (observation.subject_id or "", observation.detail["rule_id"]))
    return findings
