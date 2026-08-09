from pathlib import Path

from scripts.validate_public_release_identity import (
    CURRENT_REPOSITORY_URL,
    CURRENT_VERSION,
    FORBIDDEN_SURFACE_MARKERS,
    PUBLIC_DOC_PATHS,
    PUBLISHED_VERSION,
    REQUIRED_SURFACES,
    STABLE_SOURCE_CLONE,
    scan_paths,
    validate_required_surfaces,
)


def test_scan_rejects_non_public_surfaces_and_pre_release_identity(
    tmp_path: Path,
) -> None:
    candidate_version = f"{0}.{8}.{0}"
    files = {
        f"docs/releases/v{candidate_version}.md": "candidate release",
        ".ai-sdlc/work-items/001-demo/handoff.md": "runtime state",
        ".ai-sdlc/state/checkpoint.yml": "current_stage: init",
        "internal-notes.md": "private material",
        "README.md": f"AI-SDLC v{candidate_version}",
    }

    findings = scan_paths(tmp_path, files)

    assert {finding.marker for finding in findings} == {
        "non-public-doc",
        "non-public-root-doc",
        "non-public-work-state",
        "pre-1.0-product-version",
        "runtime-state",
    }


def test_scan_rejects_repository_mismatch_and_local_path_disclosure(
    tmp_path: Path,
) -> None:
    local_path = "/" + "Users" + "/demo/project/sample"
    files = {
        "README.md": "https://github.com/example/sample\n" + local_path,
    }

    findings = scan_paths(tmp_path, files)

    assert {finding.marker for finding in findings} == {
        "local-path-disclosure",
        "repository-identity-mismatch",
    }


def test_required_surfaces_enforce_current_release_identity() -> None:
    files = {
        "README.md": (
            f"{CURRENT_REPOSITORY_URL}\nAI-SDLC {CURRENT_VERSION}\n{STABLE_SOURCE_CLONE}\n"
            "v1.0.5 release candidate / not published / prepared-disabled\n"
            "last published version is v1.0.2\n"
            "v1.0.4 terminal NO-GO / not released\n"
            "WorkItem 010 three-PR release migration\n"
            "active no-bypass tag ruleset protects software and Certificate tags"
        ),
    }

    findings = validate_required_surfaces(files)

    assert any(
        finding.marker == "required-public-surface-missing" for finding in findings
    )
    assert not any(finding.path == "README.md" for finding in findings)
    assert "WorkItem 008" in FORBIDDEN_SURFACE_MARKERS["README.md"]
    obsolete = validate_required_surfaces(
        {
            "README.md": (
                f"{CURRENT_REPOSITORY_URL}\nAI-SDLC {CURRENT_VERSION}\n{STABLE_SOURCE_CLONE}\n"
                "WorkItem 008 正在恢复 v1.0.4"
            )
        }
    )
    assert any(
        finding.path == "README.md"
        and finding.marker == "obsolete-release-authorization"
        for finding in obsolete
    )
    for path in ("README.md", "USER_GUIDE.zh-CN.md", "docs/product-contract.md"):
        markers = REQUIRED_SURFACES[path]
        assert "v1.0.5 release candidate / not published / prepared-disabled" in markers
        assert "last published version is v1.0.2" in markers
        assert "v1.0.4 terminal NO-GO / not released" in markers
        assert "WorkItem 010 three-PR release migration" in markers
        assert (
            "active no-bypass tag ruleset protects software and Certificate tags"
            in markers
        )
    terminal_release_surfaces = {
        "packaging/offline/README.md": "上传动作必须由有权限的维护者明确触发",
        "packaging/offline/RELEASE_CHECKLIST.md": "上传动作由有权限维护者明确执行",
        "docs/pull-request-checklist.zh.md": "当前发布版本为 `1.0.4`",
    }
    for path, obsolete_marker in terminal_release_surfaces.items():
        markers = REQUIRED_SURFACES[path]
        assert PUBLISHED_VERSION in markers
        assert "v1.0.5 release candidate / not published / prepared-disabled" in markers
        assert "v1.0.4 terminal NO-GO / not released" in markers
        assert "WorkItem 010 three-PR release migration" in markers
        assert "不得 redispatch、rerun、上传或发布 v1.0.4" in markers
        assert "不得上传、发布或下载 v1.0.5 候选" in markers
        assert obsolete_marker in FORBIDDEN_SURFACE_MARKERS[path]
    assert REQUIRED_SURFACES["packaging/install_online.sh"] == (
        "AI_SDLC_PACKAGE_SPEC=ai-sdlc==1.0.2",
    )
    assert (
        "AI_SDLC_PACKAGE_SPEC=ai-sdlc==1.0.5"
        in FORBIDDEN_SURFACE_MARKERS["packaging/install_online.sh"]
    )
    release_convention = REQUIRED_SURFACES["docs/框架自迭代开发与发布约定.md"]
    assert {
        "## v1.0.4 bootstrap 终止记录（2026-08-09）",
        "terminal NO-GO / not released / bootstrap budget exhausted",
        "0776885aeb6299bad3c13fd6c47658ad17dad5e1",
        "6125d7e80b1a66eead4ddf5654a578ec2a1e856e",
        "a6a1f2ac463d9ca2dc1ea68af73271e679449015",
        "367380686",
        "31295426083",
        "93199662116",
        "93211087289",
        "93211087697",
        "1 failed / 6219 passed / 16 skipped",
        "zero assets",
        "UNKNOWN",
        "pre-tag qualification",
        "WorkItem 009",
        "WorkItem 010",
        "active no-bypass tag ruleset protects software and Certificate tags",
        "Actions history duplicate-run detector",
        "retention and no-delete trust boundary",
        "not an immutable authority",
        "protected tag namespace becomes the durable burn authority",
    } <= set(release_convention)


def test_scan_allows_current_release_and_dependency_versions(tmp_path: Path) -> None:
    files = {
        "README.md": f"{CURRENT_REPOSITORY_URL}\nAI-SDLC {CURRENT_VERSION}",
        "uv.lock": 'name = "example"\nversion = "3.4.2"',
        "managed/frontend/package-lock.json": '{"version":"3.3.0"}',
        "src/provider.py": 'release_ref = "refs/tags/rust-v0.138.0"',
        "tests/test_attestation.py": (
            'media_type = "application/vnd.dev.sigstore.bundle.v0.3+json"'
        ),
    }

    assert scan_paths(tmp_path, files) == []


def test_public_identity_does_not_require_release_history_documents() -> None:
    public_paths = {*PUBLIC_DOC_PATHS, *REQUIRED_SURFACES}

    assert not any(path.startswith("docs/releases/") for path in public_paths)
    assert not any("prd" in path.casefold() for path in public_paths)


def test_user_guide_identity_requires_new_user_release_paths() -> None:
    markers = REQUIRED_SURFACES["USER_GUIDE.zh-CN.md"]

    assert "## 第一章：全新用户 + 全新空项目" in markers
    assert "## 第二章：全新用户 + 已有项目" in markers
    assert "ai-sdlc init ." in markers
    assert "ai-sdlc adopt ." in markers
    assert STABLE_SOURCE_CLONE not in markers
    assert PUBLISHED_VERSION == "1.0.2"
    assert CURRENT_VERSION == "1.0.5"
    assert any("releases/download/v1.0.2/" in marker for marker in markers)
    assert not any("releases/download/v1.0.4/" in marker for marker in markers)
    assert not any("releases/download/v1.0.5/" in marker for marker in markers)
    assert "v1.0.4 未发布" in markers
    assert "v1.0.5 release candidate / not published / prepared-disabled" in markers
