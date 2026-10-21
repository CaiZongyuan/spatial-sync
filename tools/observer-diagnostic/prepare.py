"""Apply the identical diagnostic delta to the selected immutable source tree."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

packet = Path(__file__).resolve().parent
if sys.argv[1] == 'apply':
    patch = packet / 'instrument.patch'
    subprocess.run(['git', 'apply', '--check', str(patch)], check=True)
    subprocess.run(['git', 'apply', str(patch)], check=True)
    shutil.copyfile(packet / 'fixture.ts', 'tests/e2e/observer-diagnostic.ts')
    shutil.copyfile(packet / 'mark.ts', 'apps/web/src/lib/stream/observer-diagnostic.ts')
    print(json.dumps({'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                      'patchSha256': hashlib.sha256(patch.read_bytes()).hexdigest()}))
else:
    report = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    if report.get('errors'):
        raise SystemExit('READINESS FAILURE: collection errors')
    actual = []
    def visit(suites):
        for suite in suites:
            for spec in suite.get('specs', []):
                actual.append({'file': spec['file'], 'title': spec['title']})
            visit(suite.get('suites', []))
    visit(report['suites'])
    expected = json.loads((packet / 'fixed17.json').read_text(encoding='utf-8'))
    if actual != expected:
        raise SystemExit('READINESS FAILURE: case IDs/order differ from original shard4')
    print('Exact original 17 case IDs/order verified')
