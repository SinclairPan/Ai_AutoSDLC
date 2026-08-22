# Three-Layer Product Benefit Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three reproducible, marketing-oriented benefit datasets and render them into the Loop, Dynamic Expert Review, and Platform Capabilities pages of the offline AI-SDLC product site.

**Architecture:** A standard-library Python builder consumes a frozen scenario matrix, a current capability manifest, and historical metric definitions. It emits three closed JSON assets plus deterministic HTML fragments. The existing static pages render those fragments at build time, preserving `file://`, no-JavaScript, accessibility, and print behavior.

**Tech Stack:** Python 3.11+ standard library, JSON, static HTML5, existing CSS tokens/components, pytest, existing offline browser acceptance scripts.

**Spec:** `docs/product-site/research/2026-08-21-three-layer-benefit-data-design.md`

## Global Constraints

- Keep the existing static HTML/CSS/JavaScript site architecture; add no frontend framework or remote dependency.
- Use `gpt-5.6-sol` and `high` as the shared synthetic benchmark model label for all arms.
- Bind Superpowers to version `6.3.0` and describe it as a single-Agent repo-skill adaptation.
- Include the current five Loop stages and five-stage dynamic expert review in the refreshed AI-SDLC overall arm.
- Label every dataset `Evidence-anchored synthetic benchmark / 证据锚定合成基准`.
- Disclose `advantage-aligned engineering scenarios`; never claim random sampling, production statistics, or statistical significance.
- Historical GPT-5.4 values define the metric system and calibration only; current site values must be newly generated.
- Preserve every pre-existing dirty file in the product-site build worktree unless it is explicitly listed in a task below.
- Do not consume Provider sessions for this synthetic data refresh.

---

### Task 1: Freeze the current benchmark inputs and closed data contract

**Files:**
- Create: `benchmarks/ai-sdlc-product-value/benchmark-contract.json`
- Create: `benchmarks/ai-sdlc-product-value/scenarios.json`
- Create: `benchmarks/ai-sdlc-product-value/capabilities.json`
- Create: `benchmarks/ai-sdlc-product-value/schemas/benefit-dataset.schema.json`
- Create: `tests/unit/test_product_site_benefit_builder.py`

**Interfaces:**
- Consumes: historical metric definitions from `/Users/sinclairpan/project/Ai_AutoSDLC/docs/ai-sdlc-value-benchmark.zh-CN.md` and current repository evidence paths recorded in `capabilities.json`.
- Produces: closed JSON inputs accepted by `load_benchmark_inputs(root: Path) -> BenchmarkInputs` in Task 2.

- [ ] **Step 1: Write failing closed-schema tests**

```python
def test_benchmark_contract_freezes_current_comparison_identity() -> None:
    inputs = load_raw_inputs(BENCHMARK_ROOT)
    assert inputs["contract"]["model_id"] == "gpt-5.6-sol"
    assert inputs["contract"]["reasoning_effort"] == "high"
    assert inputs["contract"]["superpowers_version"] == "6.3.0"
    assert inputs["contract"]["benchmark_type"] == "evidence-anchored-synthetic"
    assert inputs["contract"]["selection_disclosure"] == (
        "advantage-aligned engineering scenarios"
    )


def test_current_ai_sdlc_arm_includes_loops_and_dynamic_experts() -> None:
    capabilities = load_raw_inputs(BENCHMARK_ROOT)["capabilities"]
    current = capabilities["arms"]["llm-ai-sdlc"]
    assert current["loop_stages"] == [
        "requirement",
        "design-contract",
        "implementation",
        "frontend-evidence",
        "local-pr-review",
    ]
    assert current["dynamic_expert_review"] is True
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `uv run pytest -q tests/unit/test_product_site_benefit_builder.py -k 'contract or current_ai_sdlc'`

Expected: FAIL because the benchmark input files and loader do not exist.

- [ ] **Step 3: Add exact contract, scenario, capability, and schema files**

Use a 50-scenario synthetic matrix with the historical mix as calibration: backend/runtime 22%, specification/design 18%, frontend 28%, full-stack 12%, quality/regression 10%, continuity/recovery 10%. Store integer weights and explicit expected evidence, not prose-only score estimates. Include positive and negative direction for every metric.

- [ ] **Step 4: Add mutation tests for unknown fields and missing evidence bindings**

```python
@pytest.mark.parametrize("mutation", ["unknown-field", "missing-source", "bad-weight"])
def test_benchmark_inputs_fail_closed(mutation: str, tmp_path: Path) -> None:
    root = copy_benchmark_inputs(tmp_path)
    mutate_benchmark_input(root, mutation)
    with pytest.raises(BenefitDataError, match="benchmark-input-invalid"):
        load_benchmark_inputs(root)
```

- [ ] **Step 5: Run focused tests and commit the input contract**

Run: `uv run pytest -q tests/unit/test_product_site_benefit_builder.py -k input`

Expected: PASS.

Commit only the Task 1 files with message `test: freeze product benefit benchmark inputs`.

### Task 2: Implement the deterministic three-dataset builder

**Files:**
- Create: `scripts/build_product_site_benefit_data.py`
- Modify: `tests/unit/test_product_site_benefit_builder.py`

**Interfaces:**
- Consumes: `BenchmarkInputs` from Task 1.
- Produces: `build_datasets(inputs: BenchmarkInputs, source_commit: str) -> dict[str, dict[str, object]]` and the three canonical JSON payloads.

- [ ] **Step 1: Write RED tests for the three comparison boundaries**

```python
def test_builder_emits_three_independent_comparisons(inputs: BenchmarkInputs) -> None:
    datasets = build_datasets(inputs, source_commit="a" * 40)
    assert set(datasets) == {"loop", "expert-review", "overall"}
    assert set(datasets["loop"]["arms"]) == {"native-llm", "ai-sdlc-five-loop"}
    assert set(datasets["expert-review"]["arms"]) == {
        "five-loop-without-experts",
        "five-loop-with-dynamic-experts",
    }
    assert set(datasets["overall"]["arms"]) == {
        "native-llm",
        "llm-superpowers",
        "llm-ai-sdlc",
    }
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest -q tests/unit/test_product_site_benefit_builder.py -k builder`

Expected: FAIL because the builder module does not exist.

- [ ] **Step 3: Implement closed loading, weighted scoring, and canonical JSON**

Implement these exact public functions:

```python
def load_benchmark_inputs(root: Path) -> BenchmarkInputs: ...
def build_datasets(
    inputs: BenchmarkInputs, *, source_commit: str
) -> dict[str, dict[str, object]]: ...
def canonical_json_bytes(payload: Mapping[str, object]) -> bytes: ...
def write_datasets(output_root: Path, datasets: Mapping[str, object]) -> None: ...
```

Use `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))` followed by one newline. Derive every score from scenario weights and capability evidence. Do not place current result numbers in Python source.

- [ ] **Step 4: Add determinism, direction, and full-table disclosure tests**

```python
def test_same_inputs_produce_byte_identical_datasets(inputs: BenchmarkInputs) -> None:
    first = build_datasets(inputs, source_commit="b" * 40)
    second = build_datasets(inputs, source_commit="b" * 40)
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_overall_keeps_all_fourteen_historical_metric_definitions(
    inputs: BenchmarkInputs,
) -> None:
    overall = build_datasets(inputs, source_commit="c" * 40)["overall"]
    assert len(overall["metric_definitions"]) == 14
```

Also assert that negative metrics are ordered lower-is-better and that the limitations preserve any tie or regression rather than deleting it.

- [ ] **Step 5: Run focused tests and commit**

Run: `uv run pytest -q tests/unit/test_product_site_benefit_builder.py`

Expected: PASS.

Commit with message `feat: build current product benefit datasets`.

### Task 3: Generate and verify the three site data assets

**Files:**
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/data/loop-benefit-data.json`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/data/expert-review-benefit-data.json`
- Create: `deliverables/ai-sdlc-2.0-offline-product-site/assets/data/overall-comparison-data.json`
- Create: `docs/product-site/research/2026-08-21-current-benefit-data-report.md`
- Modify: `tests/unit/test_product_site_benefit_builder.py`

**Interfaces:**
- Consumes: `write_datasets()` from Task 2 and the exact current clean implementation commit.
- Produces: three immutable, page-consumable JSON assets and a human-readable provenance report.

- [ ] **Step 1: Write RED tests for asset identity and current capability freshness**

```python
def test_generated_assets_bind_current_capabilities() -> None:
    overall = json.loads((DATA_ROOT / "overall-comparison-data.json").read_text())
    assert overall["model_id"] == "gpt-5.6-sol"
    assert overall["arms"]["llm-ai-sdlc"]["dynamic_expert_review"] is True
    assert overall["historical_values_reused"] is False
```

- [ ] **Step 2: Run RED, generate the assets, and rerun GREEN**

Run:

```bash
uv run python scripts/build_product_site_benefit_data.py \
  --benchmark-root benchmarks/ai-sdlc-product-value \
  --output-root deliverables/ai-sdlc-2.0-offline-product-site/assets/data \
  --source-commit "$(git rev-parse HEAD)"
uv run pytest -q tests/unit/test_product_site_benefit_builder.py
```

Expected: all tests PASS and a second builder run produces no diff.

- [ ] **Step 3: Write the provenance report**

Record the exact source commit, three asset SHA-256 values, model label, Superpowers version, scenario/capability digests, all headline values, all non-leading/tied values, and the synthetic/selected-scenario limitation.

- [ ] **Step 4: Commit generated data and report**

Commit with message `docs: refresh current AI-SDLC benefit data`.

### Task 4: Add the five-stage Loop comparison to the Loop page

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/loop-engineering.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Consumes: `loop-benefit-data.json` from Task 3.
- Produces: a static `[data-benefit-dataset="loop"]` region containing four comparison cards and a five-stage progression table.

- [ ] **Step 1: Write RED markup-parity tests**

```python
def test_loop_page_renders_current_loop_benefit_dataset() -> None:
    document = parse_site_page("loop-engineering.html")
    section = single_node(document, attribute="data-benefit-dataset", value="loop")
    assert len(find_nodes(section, attribute="data-benefit-card")) == 4
    assert len(find_nodes(section, attribute="data-loop-stage-delta")) == 5
    assert "证据锚定合成基准" in node_text(section)
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest -q tests/unit/test_offline_product_site.py -k loop_page_renders_current_loop`

- [ ] **Step 3: Render the static Loop data module**

Place it after the hero and before the lifecycle explanation. Include the two arm labels, four headline cards, five stage rows, selection disclosure, model label, generated date, and a link to `assets/data/loop-benefit-data.json`. Keep the existing Loop mechanism workspace intact.

- [ ] **Step 4: Add responsive styles and no-JavaScript assertions**

Use existing color tokens. At widths below 720px, cards and stage rows become one column. Do not hide values behind tabs or JavaScript.

- [ ] **Step 5: Run page tests and commit**

Run: `uv run pytest -q tests/unit/test_offline_product_site.py -k 'loop or navigation'`

Commit with message `feat: show five-stage Loop benefit data`.

### Task 5: Add the five-stage expert-effect comparison to the Expert page

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/dynamic-expert-review.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Consumes: `expert-review-benefit-data.json` from Task 3.
- Produces: a static `[data-benefit-dataset="expert-review"]` region with five paired stage comparisons and an interception funnel.

- [ ] **Step 1: Write RED tests for equal-stage treatment and expert-only deltas**

```python
def test_expert_page_compares_same_five_stages_with_and_without_experts() -> None:
    section = benefit_section("dynamic-expert-review.html", "expert-review")
    stages = find_nodes(section, attribute="data-expert-stage-delta")
    assert [node.attributes["data-stage"] for node in stages] == [
        "requirement",
        "design-contract",
        "implementation",
        "frontend-evidence",
        "local-pr-review",
    ]
    assert "原 Writer" in node_text(section)
    assert "只读" in node_text(section)
```

- [ ] **Step 2: Run RED and implement the module**

Render paired values for issue detection, repaired-and-rereviewed rate, residual severe defects, artifact completeness, and first-pass acceptance. Keep the existing risk-routing tabs and bounded review graph below the new data section.

- [ ] **Step 3: Add static funnel and accessibility labels**

Use semantic `<table>` or `<dl>` elements for exact values. Decorative bars must repeat values in accessible text; color cannot be the only signal.

- [ ] **Step 4: Run tests and commit**

Run: `uv run pytest -q tests/unit/test_offline_product_site.py -k expert`

Commit with message `feat: show five-stage expert review benefit data`.

### Task 6: Refresh the three-mode overall comparison page

**Files:**
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/platform-capabilities.html`
- Modify: `deliverables/ai-sdlc-2.0-offline-product-site/assets/css/pages.css`
- Modify: `tests/unit/test_offline_product_site.py`

**Interfaces:**
- Consumes: `overall-comparison-data.json` from Task 3.
- Produces: a static `[data-benefit-dataset="overall"]` region with five headline comparisons and the full fourteen-metric table.

- [ ] **Step 1: Write RED tests that reject the historical GPT-5.4 values**

```python
def test_platform_page_uses_refreshed_current_overall_comparison() -> None:
    section = benefit_section("platform-capabilities.html", "overall")
    text = node_text(section)
    assert "gpt-5.6-sol" in text
    assert "Superpowers 6.3.0" in text
    assert "动态专家" in text
    assert "GPT-5.4" not in text
    assert len(find_nodes(section, attribute="data-overall-metric-row")) == 14
```

- [ ] **Step 2: Run RED and render the current comparison**

Place the five headline metrics before the existing capability tabs. Render every overall metric row with all three arm values and a direction label. Include the synthetic benchmark and selected-scenario disclosure adjacent to the chart, not in a remote footnote.

- [ ] **Step 3: Preserve the historical system narrative without historical numbers**

Keep the explanation that AI-SDLC turns a coding assistant into a governed delivery system. Replace all copied GPT-5.4 numeric claims with the generated current values.

- [ ] **Step 4: Run page tests and commit**

Run: `uv run pytest -q tests/unit/test_offline_product_site.py -k platform`

Commit with message `feat: refresh current three-mode AI-SDLC comparison`.

### Task 7: Verify offline rendering, evidence identity, and publication boundaries

**Files:**
- Modify: `scripts/validate_offline_product_site.py`
- Modify: `tests/unit/test_offline_product_site.py`
- Modify: `docs/product-site/research/2026-08-21-current-benefit-data-report.md`

**Interfaces:**
- Consumes: all three data assets and updated pages.
- Produces: a validation rule that rejects data/page drift and a final evidence report suitable for product-site handoff.

- [ ] **Step 1: Add RED cross-asset/page parity tests**

```python
@pytest.mark.parametrize(
    ("page", "dataset"),
    [
        ("loop-engineering.html", "loop"),
        ("dynamic-expert-review.html", "expert-review"),
        ("platform-capabilities.html", "overall"),
    ],
)
def test_page_numbers_match_the_canonical_dataset(page: str, dataset: str) -> None:
    assert extract_benefit_values(page, dataset) == load_expected_values(dataset)
```

- [ ] **Step 2: Extend the offline validator**

Reject missing assets, unknown dataset fields, stale source commits, page/JSON numeric drift, remote chart dependencies, hidden disclosures, or a page that removes tied/regressed metrics from the full table.

- [ ] **Step 3: Run complete local verification**

Run:

```bash
uv run pytest -q tests/unit/test_product_site_benefit_builder.py tests/unit/test_offline_product_site.py
uv run python scripts/validate_offline_product_site.py deliverables/ai-sdlc-2.0-offline-product-site
uv run ruff check scripts/build_product_site_benefit_data.py tests/unit/test_product_site_benefit_builder.py tests/unit/test_offline_product_site.py
uv run ruff format --check scripts/build_product_site_benefit_data.py tests/unit/test_product_site_benefit_builder.py tests/unit/test_offline_product_site.py
git diff --check
```

Expected: all commands PASS.

- [ ] **Step 4: Run the existing browser acceptance once**

Run the repository's existing offline browser acceptance command. Verify desktop and 1024×768 layouts for the three data modules, no clipped tables, no console errors, and no network dependency. Do not introduce a second browser harness.

- [ ] **Step 5: Finalize the evidence report and commit**

Record final page screenshot hashes, dataset hashes, validation commands, source commit, benchmark limitations, and any tied/regressed metrics. Commit with message `test: verify product benefit data presentation`.

## Self-Review

- Spec coverage: Tasks 1-3 cover the reproducible data contract and latest capability refresh; Tasks 4-6 map one dataset to each requested page; Task 7 covers offline/no-JavaScript/evidence/publication boundaries.
- Placeholder scan: every implementation and verification step contains exact content; no placeholder work remains.
- Type consistency: `BenchmarkInputs`, `load_benchmark_inputs`, `build_datasets`, `canonical_json_bytes`, and `write_datasets` are defined in Task 2 and referenced consistently afterward.
- Scope control: Provider execution, unfinished directional benchmark state, and unrelated product-site worktree changes remain outside this plan.
