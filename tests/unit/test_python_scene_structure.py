"""Registered Python scenes remain structurally inspectable without either physics engine."""

import subprocess
import sys


def test_builtin_arm_structure_and_real_configs_load_with_engine_imports_rejected():
    script = """
import importlib.abc
import sys
class RejectEngines(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('newton', 'warp'):
            raise RuntimeError('Engine import attempted: ' + fullname)
sys.meta_path.insert(0, RejectEngines())
from apps.api.app.library_validation import program_schemas
from packages.library.common import TYPE_ROOT, document
from packages.library.scenes import SceneLibrary
from packages.library.structural import validate
from packages.library.validation import StructuralValidator
types = {v['type']: v for f in TYPE_ROOT.glob('*.yaml') if (v := document(f))}
library = SceneLibrary(user_root=sys.argv[1], state_root=sys.argv[1])
for selector in ('lab-demo@1', 'lab-demo@2', 'robot-and-tubes@1'):
    scene = library.resolve(selector)
    report = validate({'kind': 'scene', 'subject': 'scene:' + scene.id,
                       'metadata': scene.metadata, 'bindings': scene.bindings, 'locations': scene.locations,
                       'package': StructuralValidator.scope(scene.path), 'assets': [],
                       'types': types, 'programs': program_schemas()})
    checks = {c['id']: c for c in report['checks']}
    assert checks['S5']['status'] == checks['S8']['status'] == 'pass', report
assert not any(name.split('.')[0] in ('newton', 'warp') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, "nonexistent-structure-fixture"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
