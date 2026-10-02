"""Contract tests for the InfraLens 3D product shell.

These tests intentionally validate static wiring rather than browser/WebGL
rendering. Browser rendering remains a manual smoke-test concern.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def test_3d_viewer_module_exists_and_is_referenced() -> None:
    path = FRONTEND / "viewer3d.js"
    assert path.is_file()
    assert path.stat().st_size > 1000
    html = (FRONTEND / "index.html").read_text()
    assert 'type="module"' in html
    assert "./viewer3d.js" in html


def test_3d_viewer_uses_graph_data_and_exposes_selection_bridge() -> None:
    js = (FRONTEND / "viewer3d.js").read_text()
    assert "graph.nodes" in js
    assert "graph.edges" in js
    assert "window.infraLens3D" in js
    assert "OrbitControls" in js


def test_frontend_keeps_existing_analysis_endpoints() -> None:
    js = (FRONTEND / "app.js").read_text()
    for endpoint in ["/analyze", "/explain", "/impact", "/dependencies", "/security"]:
        assert f'"{endpoint}"' in js


def test_frontend_reuses_analysis_id_for_follow_up_calls() -> None:
    js = (FRONTEND / "app.js").read_text()
    assert "analysis_id: analysisId" in js or "analysis_id: state.analysisId" in js


def test_frontend_exposes_infrastructure_exploration_controls() -> None:
    html = (FRONTEND / "index.html").read_text()
    js = (FRONTEND / "app.js").read_text()
    for control in ["node-search", "node-type-filter", "security-only", "fit-graph", "reset-camera"]:
        assert control in html
    assert "getFilteredGraph" in js
    assert "setupGraphControls" in js


def test_3d_viewer_exposes_camera_controls() -> None:
    js = (FRONTEND / "viewer3d.js").read_text()
    assert "fitView" in js
    assert "resetView" in js


def test_3d_viewer_visualizes_direct_inferred_and_security_path_edges() -> None:
    js = (FRONTEND / "viewer3d.js").read_text()
    assert "edge.metadata?.origin === \"inferred\"" in js
    assert "addArrow" in js
    assert "securityPathEdgeKeys" in js
    assert 'EXPOSED_PATH_TO_SECRET' in js
    assert "0xf43f5e" in js


def test_3d_viewer_highlights_selected_neighborhood() -> None:
    js = (FRONTEND / "viewer3d.js").read_text()
    assert "applySelectionVisuals" in js
    assert "touchesSelection" in js
    assert "connected.add(edge.target)" in js
    assert "connected.add(edge.source)" in js


def test_3d_viewer_fit_view_uses_scene_bounds() -> None:
    js = (FRONTEND / "viewer3d.js").read_text()
    assert "new THREE.Box3()" in js
    assert "box.expandByObject" in js
    assert "controls.target.copy(center)" in js


def test_component_details_panel_contains_safe_overview_sections() -> None:
    html = (FRONTEND / "index.html").read_text()
    js = (FRONTEND / "app.js").read_text()
    for control in ["component-overview", "component-identity", "component-metadata", "component-relationships"]:
        assert control in html
    assert "renderComponentOverview" in js
    assert "relationshipEntries" in js
    assert "safeMetadataEntries" in js


def test_component_details_preserve_relationship_evidence() -> None:
    js = (FRONTEND / "app.js").read_text()
    assert "edge.metadata?.origin" in js
    assert "edge.metadata?.confidence" in js
    assert "edge.metadata?.basis" in js
    assert '"inferred"' in js
    assert '"parsed"' in js


def test_component_details_do_not_render_sensitive_metadata_keys() -> None:
    js = (FRONTEND / "app.js").read_text()
    assert "password" in js
    assert "api[_-]?key" in js
    assert "private[_-]?key" in js
    assert "blocked.test(key)" in js


def test_component_selection_guards_against_stale_async_responses() -> None:
    js = (FRONTEND / "app.js").read_text()
    assert "selectionRequest" in js
    assert "requestId === state.selectionRequest" in js


def test_security_overview_has_severity_metrics_and_attack_surface_control() -> None:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for marker in ["security-total", "security-high", "security-medium", "security-low", "security-focus"]:
        assert marker in html
    assert "renderSecurityOverview" in js
    assert "securityFocus" in js


def test_security_focus_filters_to_findings_and_connected_components() -> None:
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "matchesFocus" in js
    assert "relationshipEntries(node.id)" in js


def test_security_finding_cards_render_remediation() -> None:
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "finding.remediation" in js
    assert "Remediation:" in js



def test_3d_viewer_has_product_grade_visual_scene() -> None:
    js = (FRONTEND / "viewer3d.js").read_text(encoding="utf-8")
    for marker in ["scene.fog", "ACESFilmicToneMapping", "createEnvironment", "createLabel", "geometryForType", "addCurvedEdge", "TorusGeometry"]:
        assert marker in js


def test_3d_viewer_has_distinct_infrastructure_geometry() -> None:
    js = (FRONTEND / "viewer3d.js").read_text(encoding="utf-8")
    for marker in ['case "service"', 'case "database"', 'case "secret"', 'case "ingress"', 'case "container"']:
        assert marker in js


def test_3d_viewer_animates_security_and_environment_subtly() -> None:
    js = (FRONTEND / "viewer3d.js").read_text(encoding="utf-8")
    assert "requestAnimationFrame" in js
    assert "finding ? 0.75 : 0.22" in js
    assert "environment.rotation.y" in js


def test_graph_surface_has_visual_quality_shell() -> None:
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    for marker in [".graph-3d {", "backdrop-filter", "radial-gradient", "box-shadow: inset"]:
        assert marker in css


def test_step7_has_complete_analysis_workflow_views():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for marker in ["landing-view", "analysis-progress", "workspace-view", "overview-section", "map-section", "security-section"]:
        assert marker in html
    for marker in ["showView", "setProgress", "renderWorkspaceMetrics", "showWorkspaceTab", "renderSecurityPage"]:
        assert marker in js


def test_step7_keeps_analysis_session_and_builds_workspace_summary():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for marker in ["analysis-id-display", "metric-components", "metric-relationships", "metric-findings", "metric-high"]:
        assert marker in html
    assert "state.analysisId = analyzeResponse.analysis_id" in js
    assert "renderWorkspaceMetrics(analyzeResponse.repository)" in js


def test_step7_has_retryable_failure_path_and_loading_state():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "state.analysisRunning" in js
    assert 'showView("landing-view")' in js
    assert "button.disabled = false" in js
    assert "Analysis failed. Try again." in js


def test_step7_security_workspace_renders_findings_and_remediation():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "security-findings-list" in html
    assert "renderSecurityPage" in js
    assert "finding.remediation" in js
    assert "show attack surface" in js.lower()


def test_step8_has_advanced_3d_workspace_controls():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    viewer = (FRONTEND / "viewer3d.js").read_text(encoding="utf-8")
    for marker in ["graph-view-mode", "focus-selected", "clear-selection", "toggle-fullscreen", "graph-hover-card"]:
        assert marker in html
    for marker in ["state.viewMode", "renderGraphHover", "toggleGraphFullscreen", "focusNode", "clearNodeSelection"]:
        assert marker in js
    for marker in ["setViewMode", "focusNode", "clearSelection", "pointermove", "dblclick"]:
        assert marker in viewer


def test_step8_supports_topology_dependency_and_security_visual_modes():
    viewer = (FRONTEND / "viewer3d.js").read_text(encoding="utf-8")
    for marker in ['"topology"', '"dependencies"', '"security"', "viewMode === \"security\"", "viewMode === \"dependencies\""]:
        assert marker in viewer


def test_step9_surfaces_structural_graph_signals() -> None:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for marker in ["signal-entry-nodes", "signal-terminal-nodes", "signal-isolated-nodes", "signal-hotspot"]:
        assert marker in html
    assert "renderGraphSignals" in js
    assert "incoming" in js and "outgoing" in js


def test_step9_analysis_results_support_component_navigation() -> None:
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "analysis-link" in js
    assert 'showWorkspaceTab("map")' in js
    assert "selectNode(n.id)" in js


def test_step9_relationships_are_clickable_and_preserve_evidence() -> None:
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'item.addEventListener("click", () => selectNode(other.id))' in js
    assert "edge.metadata?.origin" in js
    assert "edge.metadata?.confidence" in js
    assert "edge.metadata?.basis" in js


def test_step9_analysis_explorer_keeps_safe_metadata_boundary() -> None:
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "safeMetadataEntries" in js
    assert "blocked" in js
    assert "private[_-]?key" in js


def test_step10_security_workspace_has_posture_and_attack_surface_views():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for marker in ["security-posture-summary", "attack-path-list", "security-priority-list", "security-show-high"]:
        assert marker in html
    for marker in ["renderSecurityPostureSummary", "renderAttackSurface", "renderSecurityPriorities", "attackSurfaceFindings"]:
        assert marker in js


def test_step10_attack_surface_uses_existing_exposed_path_finding():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'EXPOSED_PATH_TO_SECRET' in js
    assert 'state.securityFocus = true' in js
    assert 'focusNode(finding.component_id)' in js


def test_step10_security_findings_support_high_severity_filter_and_component_navigation():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'securityHighOnly' in js
    assert 'aria-pressed' in html
    assert 'security-filter-button' in html
    assert 'selectNode(finding.component_id)' in js


def test_step10_security_workspace_keeps_remediation_and_safe_rendering():
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'finding.remediation' in js
    assert 'escapeHtml(finding.reason' in js
    assert 'escapeHtml(finding.title' in js


def test_step11_has_recoverable_analysis_error_state():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    for marker in ["analysis-error", "analysis-error-retry", "analysis-error-home", "analysis-error-message"]:
        assert marker in html
    assert 'showView("analysis-error")' in js
    assert 'requestSubmit()' in js


def test_step11_has_polished_status_and_toast_feedback():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    for marker in ["status-chip", "status-dot", "global-toast", "progress-percent"]:
        assert marker in html or marker in css
    assert "showToast" in js
    assert "status-text" in html


def test_step11_marks_analysis_busy_during_pipeline():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'id="app" aria-busy="false"' in html
    assert 'setAttribute("aria-busy", "true")' in js
    assert 'setAttribute("aria-busy", "false")' in js


def test_step11_workspace_exposes_session_metadata():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "workspace-analysis-meta" in html
    assert "Session ${state.analysisId}" in js


def test_step11_has_accessibility_focus_and_reduced_motion_polish():
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert "focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert 'aria-live="polite"' in html


def test_step11_has_responsive_product_layout_rules():
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    assert "@media (max-width: 980px)" in css
    assert "@media (max-width: 760px)" in css
    assert "main#app { grid-template-columns: 1fr;" in css
