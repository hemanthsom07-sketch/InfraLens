"""Phase 6C.1/6C.2/6C.3: Kubernetes coverage expansion.

6C.1 - DaemonSet/Job/CronJob recognized as workload kinds, reusing the
       exact same container/label/ref extraction Deployment/StatefulSet
       already use (CronJob via a one-level jobTemplate.spec remapping).
       All five are valid Service-selector inference targets (rule 1 in
       app/graph/inference.py) - Kubernetes' Service->Pod routing doesn't
       distinguish by controller kind, so neither does this rule.
6C.2 - PersistentVolumeClaim + ServiceAccount recognized as component
       kinds; workload references to them resolve the same
       namespace-scoped way ConfigMap/Secret already do.
6C.3 - HorizontalPodAutoscaler recognized; its scaleTargetRef resolves
       only against {"Deployment", "StatefulSet"} - deliberately
       NARROWER than the 5-kind Service-selector target set, since those
       are the only kinds a real Kubernetes cluster actually allows an
       HPA to scale (DaemonSet has no replica count; Job/CronJob aren't
       valid scale targets in the Kubernetes API at all).
"""

from pathlib import Path

from app.graph.engine import GraphEngine
from app.graph.inference import infer_service_workload_edges
from app.models.ikm import Component, InfrastructureModel
from app.parsers.kubernetes_parser import KubernetesParser, resolve_references
from tests.conftest import write


def _parse(tmp_repo: Path, filename: str, yaml_text: str):
    path = write(tmp_repo, filename, yaml_text)
    return KubernetesParser().parse(path, tmp_repo).components


# --- 6C.1: DaemonSet ----------------------------------------------------------


def test_daemonset_is_recognized_with_containers_and_images(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "daemonset.yaml",
        """
        apiVersion: apps/v1
        kind: DaemonSet
        metadata:
          name: node-agent
        spec:
          template:
            metadata:
              labels:
                app: node-agent
            spec:
              containers:
                - name: agent
                  image: myagent:1.0
                  ports:
                    - containerPort: 9100
        """,
    )
    assert len(components) == 1
    assert components[0].metadata["kind"] == "DaemonSet"
    assert components[0].metadata["images"] == ["myagent:1.0"]
    assert components[0].metadata["ports"] == [9100]
    assert components[0].metadata["pod_labels"] == {"app": "node-agent"}


def test_service_resolves_to_daemonset_via_label_selector(tmp_repo: Path) -> None:
    service = _parse(
        tmp_repo,
        "service.yaml",
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: agent-svc\nspec:\n  selector:\n    app: node-agent\n",
    )
    daemonset = _parse(
        tmp_repo,
        "daemonset.yaml",
        """
        apiVersion: apps/v1
        kind: DaemonSet
        metadata:
          name: node-agent
        spec:
          template:
            metadata:
              labels:
                app: node-agent
            spec:
              containers:
                - name: agent
                  image: myagent:1.0
        """,
    )
    edges = infer_service_workload_edges(service + daemonset)
    assert len(edges) == 1
    assert edges[0].metadata["confidence"] == "high"


def test_daemonset_service_selector_respects_namespace_scoping(tmp_repo: Path) -> None:
    service = _parse(
        tmp_repo,
        "service.yaml",
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: agent-svc\n  namespace: staging\nspec:\n  selector:\n    app: node-agent\n",
    )
    daemonset = _parse(
        tmp_repo,
        "daemonset.yaml",
        """
        apiVersion: apps/v1
        kind: DaemonSet
        metadata:
          name: node-agent
          namespace: production
        spec:
          template:
            metadata:
              labels:
                app: node-agent
            spec:
              containers:
                - name: agent
                  image: myagent:1.0
        """,
    )
    edges = infer_service_workload_edges(service + daemonset)
    assert edges == []


# --- 6C.1: Job -----------------------------------------------------------------


def test_job_is_recognized_with_containers(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "job.yaml",
        """
        apiVersion: batch/v1
        kind: Job
        metadata:
          name: migrate
        spec:
          template:
            spec:
              containers:
                - name: migrate
                  image: myapp/migrate:1.0
        """,
    )
    assert len(components) == 1
    assert components[0].metadata["kind"] == "Job"
    assert components[0].metadata["images"] == ["myapp/migrate:1.0"]


def test_job_configmap_reference_resolves(tmp_repo: Path) -> None:
    job = _parse(
        tmp_repo,
        "job.yaml",
        """
        apiVersion: batch/v1
        kind: Job
        metadata:
          name: migrate
        spec:
          template:
            spec:
              containers:
                - name: migrate
                  image: myapp/migrate:1.0
                  envFrom:
                    - configMapRef:
                        name: app-config
        """,
    )
    config = _parse(
        tmp_repo, "config.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: app-config\n"
    )
    relationships = resolve_references(job + config)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"


def test_service_resolves_to_job_via_label_selector(tmp_repo: Path) -> None:
    """A Service selecting a Job's pods is an unusual pattern in
    practice, but the underlying label-matching mechanism doesn't
    distinguish by controller kind - see inference.py's rule 1 comment.
    This is a deliberate inclusion, not an oversight."""
    service = _parse(
        tmp_repo,
        "service.yaml",
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: job-svc\nspec:\n  selector:\n    app: migrate\n",
    )
    job = _parse(
        tmp_repo,
        "job.yaml",
        """
        apiVersion: batch/v1
        kind: Job
        metadata:
          name: migrate
        spec:
          template:
            metadata:
              labels:
                app: migrate
            spec:
              containers:
                - name: migrate
                  image: myapp/migrate:1.0
        """,
    )
    edges = infer_service_workload_edges(service + job)
    assert len(edges) == 1
    assert edges[0].metadata["confidence"] == "high"


# --- 6C.1: CronJob (deeper nesting) -------------------------------------------


def test_cronjob_containers_extracted_via_job_template(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "cronjob.yaml",
        """
        apiVersion: batch/v1
        kind: CronJob
        metadata:
          name: nightly-cleanup
        spec:
          schedule: "0 2 * * *"
          jobTemplate:
            spec:
              template:
                metadata:
                  labels:
                    app: cleanup
                spec:
                  containers:
                    - name: cleanup
                      image: myapp/cleanup:1.0
                      ports:
                        - containerPort: 8080
        """,
    )
    assert len(components) == 1
    metadata = components[0].metadata
    assert metadata["kind"] == "CronJob"
    assert metadata["images"] == ["myapp/cleanup:1.0"]
    assert metadata["ports"] == [8080]
    assert metadata["pod_labels"] == {"app": "cleanup"}


def test_cronjob_configmap_reference_resolves_via_job_template(tmp_repo: Path) -> None:
    cronjob = _parse(
        tmp_repo,
        "cronjob.yaml",
        """
        apiVersion: batch/v1
        kind: CronJob
        metadata:
          name: nightly-cleanup
        spec:
          schedule: "0 2 * * *"
          jobTemplate:
            spec:
              template:
                spec:
                  containers:
                    - name: cleanup
                      image: myapp/cleanup:1.0
                      envFrom:
                        - configMapRef:
                            name: cleanup-config
        """,
    )
    config = _parse(
        tmp_repo, "config.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: cleanup-config\n"
    )
    relationships = resolve_references(cronjob + config)
    assert len(relationships) == 1


def test_service_resolves_to_cronjob_via_label_selector(tmp_repo: Path) -> None:
    service = _parse(
        tmp_repo,
        "service.yaml",
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: cleanup-svc\nspec:\n  selector:\n    app: cleanup\n",
    )
    cronjob = _parse(
        tmp_repo,
        "cronjob.yaml",
        """
        apiVersion: batch/v1
        kind: CronJob
        metadata:
          name: nightly-cleanup
        spec:
          schedule: "0 2 * * *"
          jobTemplate:
            spec:
              template:
                metadata:
                  labels:
                    app: cleanup
                spec:
                  containers:
                    - name: cleanup
                      image: myapp/cleanup:1.0
        """,
    )
    edges = infer_service_workload_edges(service + cronjob)
    assert len(edges) == 1


def test_cronjob_with_missing_job_template_does_not_crash(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "cronjob.yaml",
        'apiVersion: batch/v1\nkind: CronJob\nmetadata:\n  name: broken\nspec:\n  schedule: "0 2 * * *"\n',
    )
    assert len(components) == 1
    assert components[0].metadata["images"] == []
    assert components[0].metadata["ports"] == []
    assert "pod_labels" not in components[0].metadata


# --- 6C.1: regression - existing kinds unaffected ---------------------------


def test_deployment_still_recognized_and_unaffected(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo,
        "deployment.yaml",
        """
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
                  image: myapp:1.0
        """,
    )
    assert len(components) == 1
    assert components[0].metadata["kind"] == "Deployment"
    assert components[0].metadata["pod_labels"] == {"app": "web"}


# --- 6C.2: PersistentVolumeClaim ----------------------------------------------


def test_pvc_reference_resolves_same_namespace(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
              volumes:
                - name: data
                  persistentVolumeClaim:
                    claimName: web-data
        """,
    )
    pvc = _parse(
        tmp_repo,
        "pvc.yaml",
        "apiVersion: v1\nkind: PersistentVolumeClaim\nmetadata:\n  name: web-data\n  namespace: staging\n",
    )
    relationships = resolve_references(deploy + pvc)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"


def test_pvc_reference_does_not_resolve_across_namespaces(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
          namespace: staging
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
              volumes:
                - name: data
                  persistentVolumeClaim:
                    claimName: web-data
        """,
    )
    pvc = _parse(
        tmp_repo,
        "pvc.yaml",
        "apiVersion: v1\nkind: PersistentVolumeClaim\nmetadata:\n  name: web-data\n  namespace: production\n",
    )
    relationships = resolve_references(deploy + pvc)
    assert relationships == []


def test_pvc_reference_missing_target_produces_no_relationship(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
              volumes:
                - name: data
                  persistentVolumeClaim:
                    claimName: does-not-exist
        """,
    )
    relationships = resolve_references(deploy)
    assert relationships == []


def test_pvc_reference_resolves_with_no_namespace_on_either_side(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
              volumes:
                - name: data
                  persistentVolumeClaim:
                    claimName: web-data
        """,
    )
    pvc = _parse(tmp_repo, "pvc.yaml", "apiVersion: v1\nkind: PersistentVolumeClaim\nmetadata:\n  name: web-data\n")
    relationships = resolve_references(deploy + pvc)
    assert len(relationships) == 1


def test_pvc_reference_resolves_across_files(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
        spec:
          template:
            spec:
              containers:
                - name: web
                  image: myapp:1.0
              volumes:
                - name: data
                  persistentVolumeClaim:
                    claimName: web-data
        """,
    )
    pvc = _parse(tmp_repo, "storage.yaml", "apiVersion: v1\nkind: PersistentVolumeClaim\nmetadata:\n  name: web-data\n")
    relationships = resolve_references(deploy + pvc)
    assert len(relationships) == 1


def test_pvc_relationship_has_parsed_provenance() -> None:
    components = [
        Component(
            id="kubernetes:deployment.yaml:Deployment:web", name="web", type="kubernetes_resource",
            technology="kubernetes",
            metadata={"source_file": "deployment.yaml", "kind": "Deployment", "images": [], "ports": [], "pvc_refs": ["web-data"]},
        ),
        Component(
            id="kubernetes:pvc.yaml:PersistentVolumeClaim:web-data", name="web-data", type="kubernetes_resource",
            technology="kubernetes",
            metadata={"source_file": "pvc.yaml", "kind": "PersistentVolumeClaim", "images": [], "ports": []},
        ),
    ]
    relationships = resolve_references(components)
    model = InfrastructureModel(components=components, relationships=relationships)
    engine = GraphEngine.from_infrastructure_model(model, infer=True)
    edge_model = engine.to_model()
    pvc_edge = next(e for e in edge_model.edges if e.edge_type == "uses")
    assert pvc_edge.metadata["origin"] == "parsed"


# --- 6C.2: ServiceAccount ------------------------------------------------------


def test_service_account_reference_resolves_same_namespace(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
          namespace: staging
        spec:
          template:
            spec:
              serviceAccountName: web-sa
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    sa = _parse(tmp_repo, "sa.yaml", "apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: web-sa\n  namespace: staging\n")
    relationships = resolve_references(deploy + sa)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"


def test_service_account_reference_does_not_resolve_across_namespaces(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
          namespace: staging
        spec:
          template:
            spec:
              serviceAccountName: web-sa
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    sa = _parse(tmp_repo, "sa.yaml", "apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: web-sa\n  namespace: production\n")
    relationships = resolve_references(deploy + sa)
    assert relationships == []


def test_service_account_reference_missing_target(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
        spec:
          template:
            spec:
              serviceAccountName: does-not-exist
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    assert resolve_references(deploy) == []


def test_service_account_reference_namespace_less_compatibility(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
        spec:
          template:
            spec:
              serviceAccountName: web-sa
              containers:
                - name: web
                  image: myapp:1.0
        """,
    )
    sa = _parse(tmp_repo, "sa.yaml", "apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: web-sa\n")
    relationships = resolve_references(deploy + sa)
    assert len(relationships) == 1


# --- 6C.2: regression - existing ConfigMap/Secret unaffected ----------------


def test_configmap_and_secret_references_still_resolve_alongside_pvc_and_sa(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        """
        apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: web
        spec:
          template:
            spec:
              serviceAccountName: web-sa
              containers:
                - name: web
                  image: myapp:1.0
                  envFrom:
                    - configMapRef:
                        name: app-config
                    - secretRef:
                        name: app-secret
              volumes:
                - name: data
                  persistentVolumeClaim:
                    claimName: web-data
        """,
    )
    config = _parse(tmp_repo, "config.yaml", "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: app-config\n")
    secret = _parse(tmp_repo, "secret.yaml", "apiVersion: v1\nkind: Secret\nmetadata:\n  name: app-secret\n")
    pvc = _parse(tmp_repo, "pvc.yaml", "apiVersion: v1\nkind: PersistentVolumeClaim\nmetadata:\n  name: web-data\n")
    sa = _parse(tmp_repo, "sa.yaml", "apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: web-sa\n")

    relationships = resolve_references(deploy + config + secret + pvc + sa)
    assert len(relationships) == 4
    assert {r.relationship_type for r in relationships} == {"uses"}


# --- 6C.3: HorizontalPodAutoscaler ---------------------------------------------


def test_hpa_resolves_deployment_target(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n    spec:\n      containers:\n        - name: web\n          image: myapp:1.0\n",
    )
    hpa = _parse(
        tmp_repo,
        "hpa.yaml",
        """
        apiVersion: autoscaling/v2
        kind: HorizontalPodAutoscaler
        metadata:
          name: web-hpa
        spec:
          scaleTargetRef:
            kind: Deployment
            name: web
          minReplicas: 1
          maxReplicas: 5
        """,
    )
    relationships = resolve_references(deploy + hpa)
    assert len(relationships) == 1
    assert relationships[0].relationship_type == "uses"


def test_hpa_resolves_statefulset_target(tmp_repo: Path) -> None:
    statefulset = _parse(
        tmp_repo,
        "statefulset.yaml",
        "apiVersion: apps/v1\nkind: StatefulSet\nmetadata:\n  name: db\nspec:\n  template:\n    spec:\n      containers:\n        - name: db\n          image: postgres:15\n",
    )
    hpa = _parse(
        tmp_repo,
        "hpa.yaml",
        "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n  name: db-hpa\nspec:\n  scaleTargetRef:\n    kind: StatefulSet\n    name: db\n",
    )
    relationships = resolve_references(statefulset + hpa)
    assert len(relationships) == 1


def test_hpa_does_not_resolve_daemonset_target() -> None:
    """DaemonSet has no replica count - not a valid HPA scale target in
    real Kubernetes, so this must never resolve, even though DaemonSet
    IS a valid Service-selector inference target elsewhere."""
    daemonset = Component(
        id="kubernetes:ds.yaml:DaemonSet:agent", name="agent", type="kubernetes_resource", technology="kubernetes",
        metadata={"source_file": "ds.yaml", "kind": "DaemonSet", "images": [], "ports": []},
    )
    hpa = Component(
        id="kubernetes:hpa.yaml:HorizontalPodAutoscaler:agent-hpa", name="agent-hpa", type="kubernetes_resource",
        technology="kubernetes",
        metadata={
            "source_file": "hpa.yaml", "kind": "HorizontalPodAutoscaler", "images": [], "ports": [],
            "scale_target_kind": "DaemonSet", "scale_target_name": "agent",
        },
    )
    assert resolve_references([daemonset, hpa]) == []


def test_hpa_does_not_resolve_job_or_cronjob_target() -> None:
    job = Component(
        id="kubernetes:job.yaml:Job:migrate", name="migrate", type="kubernetes_resource", technology="kubernetes",
        metadata={"source_file": "job.yaml", "kind": "Job", "images": [], "ports": []},
    )
    hpa = Component(
        id="kubernetes:hpa.yaml:HorizontalPodAutoscaler:migrate-hpa", name="migrate-hpa", type="kubernetes_resource",
        technology="kubernetes",
        metadata={
            "source_file": "hpa.yaml", "kind": "HorizontalPodAutoscaler", "images": [], "ports": [],
            "scale_target_kind": "Job", "scale_target_name": "migrate",
        },
    )
    assert resolve_references([job, hpa]) == []


def test_hpa_wrong_namespace_does_not_resolve(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n  namespace: staging\nspec:\n  template:\n    spec:\n      containers:\n        - name: web\n          image: myapp:1.0\n",
    )
    hpa = _parse(
        tmp_repo,
        "hpa.yaml",
        "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n  name: web-hpa\n  namespace: production\nspec:\n  scaleTargetRef:\n    kind: Deployment\n    name: web\n",
    )
    relationships = resolve_references(deploy + hpa)
    assert relationships == []


def test_hpa_missing_target_produces_no_relationship(tmp_repo: Path) -> None:
    hpa = _parse(
        tmp_repo,
        "hpa.yaml",
        "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n  name: web-hpa\nspec:\n  scaleTargetRef:\n    kind: Deployment\n    name: does-not-exist\n",
    )
    assert resolve_references(hpa) == []


def test_hpa_with_malformed_or_missing_scale_target_ref_does_not_crash(tmp_repo: Path) -> None:
    components = _parse(
        tmp_repo, "hpa.yaml", "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n  name: broken\nspec:\n  minReplicas: 1\n"
    )
    assert len(components) == 1
    assert "scale_target_kind" not in components[0].metadata
    assert resolve_references(components) == []


def test_hpa_relationship_has_parsed_provenance(tmp_repo: Path) -> None:
    deploy = _parse(
        tmp_repo,
        "deployment.yaml",
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n    spec:\n      containers:\n        - name: web\n          image: myapp:1.0\n",
    )
    hpa = _parse(
        tmp_repo,
        "hpa.yaml",
        "apiVersion: autoscaling/v2\nkind: HorizontalPodAutoscaler\nmetadata:\n  name: web-hpa\nspec:\n  scaleTargetRef:\n    kind: Deployment\n    name: web\n",
    )
    relationships = resolve_references(deploy + hpa)
    model = InfrastructureModel(components=deploy + hpa, relationships=relationships)
    engine = GraphEngine.from_infrastructure_model(model, infer=True)
    edge_model = engine.to_model()
    hpa_edge = next(e for e in edge_model.edges if e.edge_type == "uses")
    assert hpa_edge.metadata["origin"] == "parsed"
