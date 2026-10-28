# Cleanup report — 2026-10-25

Removed runtime-unreferenced legacy modules, standalone tests, examples, historical snapshots, obsolete documentation, and test/cache output.

Preserved: active .venv, app modules used by the GUI, prompts, configuration, operational database, existing projects/artifacts, automation, and GUI offline self-test.

## Removed targets

- archive
- tests
- scripts
- agents
- .pytest_cache
- .test_tmp
- __pycache__
- data\temp\pytest-dropdown
- data\temp\pytest-dropdown-unified
- data\temp\pytest-final
- data\temp\pytest-no-scroll
- data\temp\pytest-popup-frame
- data\temp\pytest-run
- data\database\ollama_e2e.db
- data\files
- data\vector_db
- data\backups
- knowledge
- 예제.txt
- pytest.ini
- requirements-dev.txt
- README_V1.0.md
- README_V1.2.md
- app\gui\dashboard.py
- app\agents\core
- app\orchestrator\request_planner.py
- app\models\model_router.py
- docs\baseline_spec
- docs\STABILITY_HARDENING_REPORT.md
- docs\V1.0_RUN_AND_INTEGRATION_TEST.md
- docs\V1.0_STRUCTURE.md
- docs\V1.2_E2E_USER_GUIDE.md
- docs\V1.2_TEST_REPORT.md
- docs\V1.3_E2E_USER_GUIDE.md
- docs\V1.3_IMPLEMENTATION_PLAN.md
- docs\V1.3_TEST_REPORT.md
- logs\streamlit.stdout.log
- logs\streamlit.stderr.log
- app\__pycache__
- app\agents\__pycache__
- app\agents\core\__pycache__
- app\agents\specialists\__pycache__
- app\approval\__pycache__
- app\automations\__pycache__
- app\chief\__pycache__
- app\context\__pycache__
- app\errors\__pycache__
- app\gui\__pycache__
- app\knowledge\__pycache__
- app\models\__pycache__
- app\orchestrator\__pycache__
- app\organization\__pycache__
- app\prompts\__pycache__
- app\qa\__pycache__
- app\runtime\__pycache__
- app\storage\__pycache__
- app\task_manager\__pycache__
- app\tools\__pycache__
- app\utils\__pycache__
- app\workflow\__pycache__

553
16729413

## Verification

PASS: all 54 remaining application modules parsed and imported; offline workflow completed with QA failure, rework, final approval and result files; Qt dashboard created, rendered and refreshed against a temporary DB; operational DB passed read-only PRAGMA quick_check. Real Ollama/Gemini inference and external automation were not executed.

Additional cleanup: temporary verification script, superseded Korean usage document (replaced by README.md), and generated Python caches inside existing projects.

Package fix: app/gui/__init__.py now exports the current Qt Dashboard instead of the removed legacy Tk Dashboard.

Before cleanup: component test and both retry-chain standalone checks passed. pytest was unavailable in the active environment; no full pytest run is claimed.

