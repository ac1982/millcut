"""Colab setup & environment verification for Paper Manufactory study (R51+)."""
import pathlib, zipfile, shutil, subprocess, sys, json, os
import numpy as np

ROOT = pathlib.Path('/content/mill-study')
ROOT.mkdir(parents=True, exist_ok=True)
for sub in ['certificates', 'packages', 'source', 'logs']:
    (ROOT / sub).mkdir(exist_ok=True)

CORPUS = pathlib.Path('/content/data-expansion/corpus')
CORPUS.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile('/content/routing-data-v2.zip') as z:
    z.extractall(CORPUS)

BASE = pathlib.Path('/content/portfolio-study')
for sub in ['planted-quality', 'routing']:
    (BASE / sub).mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile('/content/compiled-package.zip') as z:
    z.extractall(BASE)

NATIVE = pathlib.Path('/content/native-check')
NATIVE.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile('/content/ejection-binaries.zip') as z:
    z.extractall(NATIVE)
for p in NATIVE.rglob('*'):
    if p.is_file():
        os.chmod(p, 0o755)

pip_proc = subprocess.run(
    [sys.executable, '-m', 'pip', 'install', '-q', 'ortools==9.15.6755', 'scipy'],
    capture_output=True, text=True
)
pip_proc.check_returncode()

sys.path.insert(0, str(BASE))
import packingsolver.rectangleguillotine as ps
from ortools.sat.python import cp_model
import scipy
from scipy.optimize import linprog

# Verify HiGHS equality dual sign convention:
# Minimize 10 * x0 + 6 * x1 subject to x0 == 1, x1 == 1, 0 <= x <= 1
# Optimal objective = 16. Shadow price d(obj)/d(b) should be +10 and +6.
lp = linprog(
    c=np.array([10.0, 6.0]),
    A_eq=np.eye(2),
    b_eq=np.array([1.0, 1.0]),
    bounds=(0, 1),
    method='highs'
)
marginals = lp.eqlin.marginals.tolist()

gpp = subprocess.check_output(['g++', '--version'], text=True).splitlines()[0]
cpu = subprocess.check_output(['lscpu'], text=True)
native_files = [str(p.relative_to(NATIVE)) for p in NATIVE.rglob('*') if p.is_file()]
manifest = json.loads((CORPUS / 'manifest.json').read_text())

env_info = {
    'python': sys.version,
    'gpp': gpp,
    'scipy': scipy.__version__,
    'ortools_ok': True,
    'packingsolver_ok': hasattr(ps, 'InstanceBuilder'),
    'highs_eq_marginals_for_10_6': marginals,
    'native_binaries': native_files,
    'corpus_count': len(manifest),
    'cpu_summary': [line for line in cpu.splitlines() if any(k in line for k in ('Model name', 'CPU(s):', 'Thread(s)'))],
}
(ROOT / 'environment.json').write_text(json.dumps(env_info, indent=2))
print('MILL_SETUP_OK', json.dumps(env_info))
