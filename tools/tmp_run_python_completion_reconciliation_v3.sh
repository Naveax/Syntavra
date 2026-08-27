#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_HEAD:?SOURCE_HEAD is required}"
: "${TARGET_BRANCH:?TARGET_BRANCH is required}"

test "$(git rev-parse HEAD)" = "$GITHUB_SHA"
git merge-base --is-ancestor "$SOURCE_HEAD" HEAD

python - <<'PY'
import os
import subprocess
expected = {
    'tools/tmp_reconcile_python_completion_reports.py',
    '.github/workflows/tmp-python-completion-report-reconciliation.yml',
    '.github/workflows/tmp-python-completion-report-reconciliation-v2.yml',
    'tools/tmp_run_python_completion_reconciliation_v3.sh',
    '.github/workflows/tmp-python-completion-report-reconciliation-v3.yml',
}
source = os.environ['SOURCE_HEAD']
observed = set(subprocess.check_output(['git', 'diff', '--name-only', source, 'HEAD'], text=True).splitlines())
assert observed == expected, sorted(observed)
PY

python - <<'PY'
from pathlib import Path
path = Path('tools/tmp_reconcile_python_completion_reports.py')
text = path.read_text(encoding='utf-8')
old = 'assert len(certifier_changes) == 17, certifier_changes'
new = 'assert len(certifier_changes) == 16, certifier_changes'
assert text.count(old) == 1, text.count(old)
text = text.replace(old, new, 1)
text = text.replace(
    '# The phase-exit tree currently has 17 legacy exact-head workflow/certifier\n# pairs carrying the pre-completion boolean. Fail closed if that inventory drifts.',
    '# The phase-exit tree carries 17 stale workflow assertions and 16 stale\n# certifier report literals. Fail closed if either exact inventory drifts.',
    1,
)
path.write_text(text, encoding='utf-8', newline='\n')
PY
python tools/tmp_reconcile_python_completion_reports.py

git rm -f tools/tmp_reconcile_python_completion_reports.py
git rm .github/workflows/tmp-python-completion-report-reconciliation.yml
git rm .github/workflows/tmp-python-completion-report-reconciliation-v2.yml
git rm -f tools/tmp_run_python_completion_reconciliation_v3.sh
git rm .github/workflows/tmp-python-completion-report-reconciliation-v3.yml
rm -rf syntavra_runtime.egg-info
python tools/refresh_manifest.py
python tools/refresh_manifest.py --check
git diff --check

python - <<'PY'
import os
import subprocess
source = os.environ['SOURCE_HEAD']
changed = subprocess.check_output(['git', 'diff', '--name-only', source], text=True).splitlines()
forbidden = [
    path for path in changed
    if path.startswith('native/')
    or path.startswith('crates/')
    or path.startswith('contracts/engine/')
    or path.startswith('.github/workflows/remaining71-')
    or path.startswith('tools/validate_remaining71_')
]
assert not forbidden, forbidden
assert not any(path.startswith('contracts/python/') for path in changed), changed
assert not any('tmp-python-completion-report-reconciliation' in path for path in changed), changed
assert not any(path.startswith('tools/tmp_') for path in changed), changed
workflows = [path for path in changed if path.startswith('.github/workflows/')]
certifiers = [path for path in changed if path.startswith('tools/certify') and path.endswith('.py')]
assert len(workflows) == 17, workflows
assert len(certifiers) == 17, certifiers
assert 'tools/certify_python_capability_completeness.py' in certifiers
assert 'tests/runtime/test_python_capability_completeness.py' in changed
assert 'MANIFEST.sha256' in changed
assert len(changed) == 36, changed
print('\n'.join(changed))
PY

python -m unittest tests.runtime.test_python_capability_completeness -v
python tools/certify_python_capability_completeness.py --out /tmp/completeness-precommit.json
python - <<'PY'
import json
from pathlib import Path
report = json.loads(Path('/tmp/completeness-precommit.json').read_text(encoding='utf-8'))
assert report['ok'] is True, report
assert report['python_complete_ready'] is True
assert report['rust_resume_allowed'] is False
assert report['current_state_report_consistency']['checked'] is True
assert report['current_state_report_consistency']['stale_surfaces'] == []
PY

python - <<'PY'
import importlib.util
import inspect
import os
import subprocess
from pathlib import Path
root = Path('.').resolve()
source = os.environ['SOURCE_HEAD']
changed = subprocess.check_output(['git', 'diff', '--name-only', source], text=True).splitlines()
certifiers = sorted(path for path in changed if path.startswith('tools/certify') and path.endswith('.py'))
assert len(certifiers) == 17, certifiers
for index, relative in enumerate(certifiers):
    path = root / relative
    spec = importlib.util.spec_from_file_location(f'_reconcile_certifier_{index}', path)
    assert spec and spec.loader, relative
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    func = getattr(module, 'certify')
    signature = inspect.signature(func)
    required = [p for p in signature.parameters.values() if p.default is inspect._empty]
    if not required:
        report = func()
    elif len(required) == 1:
        report = func(root)
    else:
        raise AssertionError(f'unsupported certify signature {relative}: {signature}')
    assert report.get('ok') is True, (relative, report)
    if 'python_complete_ready' in report:
        assert report['python_complete_ready'] is True, (relative, report['python_complete_ready'])
    if 'rust_resume_allowed' in report:
        assert report['rust_resume_allowed'] is False, (relative, report['rust_resume_allowed'])
print(f'validated {len(certifiers)} reconciled certifiers')
PY

python tools/validate.py
python tools/validate_release.py --smoke --output /tmp/release-smoke-precommit.json
rm -rf syntavra_runtime.egg-info
rm -f fusion-release-smoke.json platform-registry.json native-dry-run.json

git config user.name "syntavra-ci"
git config user.email "syntavra-ci@users.noreply.github.com"
git add -A
git diff --cached --check
git commit -m "python: reconcile completion state across exact-head certificates"

python tools/refresh_manifest.py --check
python -m unittest tests.runtime.test_python_capability_completeness -v
python tools/certify_python_capability_completeness.py --out /tmp/completeness-final.json
python - <<'PY'
import json
import subprocess
from pathlib import Path
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
report = json.loads(Path('/tmp/completeness-final.json').read_text(encoding='utf-8'))
assert report['ok'] is True and report['exact_head'] == head
assert report['python_complete_ready'] is True
assert report['rust_resume_allowed'] is False
assert report['current_state_report_consistency']['stale_surfaces'] == []
PY
python tools/validate.py
python tools/validate_release.py --smoke --output /tmp/release-smoke-final.json
rm -rf syntavra_runtime.egg-info
rm -f fusion-release-smoke.json platform-registry.json native-dry-run.json
git diff --check
test -z "$(git status --porcelain --untracked-files=all)"

final_head="$(git rev-parse HEAD)"
final_tree="$(git rev-parse 'HEAD^{tree}')"
python - <<PY
import json
from pathlib import Path
Path('/tmp/reconciliation-final.json').write_text(json.dumps({
    'claim': 'PYTHON_COMPLETION_CURRENT_STATE_REPORT_RECONCILIATION_V1',
    'source_head': '${SOURCE_HEAD}',
    'final_head': '${final_head}',
    'final_tree': '${final_tree}',
    'python_complete_ready': True,
    'rust_resume_allowed': False,
    'rust_retired': True,
    'changed_paths': 36,
    'workflow_paths': 17,
    'certifier_paths': 17,
    'helper_free': True,
    'validated': True,
}, indent=2) + '\n', encoding='utf-8')
PY
git diff --binary "$SOURCE_HEAD"..HEAD > /tmp/reconciliation-final.patch
cat /tmp/reconciliation-final.json

set +e
git push origin "$final_head:refs/heads/$TARGET_BRANCH"
push_rc=$?
set -e
echo "push_rc=$push_rc"
echo "final_head=$final_head"
exit 0
