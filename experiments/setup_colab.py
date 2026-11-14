"""Execute ONLY in Colab: pinned source checkout and Python binding build."""
import pathlib, subprocess, sys, json, platform, os
ROOT = pathlib.Path('/content/cut-research')
ROOT.mkdir(exist_ok=True)
COMMIT = '28ac801df472e68736410411d70bfdb15f06e281'
def run(args):
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as proc:
        for line in proc.stdout:
            print(line, end='', flush=True)
        if proc.wait():
            raise subprocess.CalledProcessError(proc.returncode, args)
repo = ROOT / 'packingsolver'
if not repo.exists():
    run(['git', 'clone', 'https://github.com/fontanf/packingsolver.git', str(repo)])
run(['git', '-C', str(repo), 'checkout', COMMIT])
run([sys.executable, '-m', 'pip', 'install', 'cmake>=3.28', 'ninja', 'pytest', 'plotly'])
os.environ['CMAKE_BUILD_PARALLEL_LEVEL'] = '2'
run([sys.executable, '-m', 'pip', 'install', str(repo)])
(ROOT / 'environment.json').write_text(json.dumps({'commit': COMMIT, 'python': sys.version, 'platform': platform.platform(), 'cpu': subprocess.check_output(['lscpu'], text=True)}, indent=2))
tests = subprocess.run([sys.executable, '-m', 'pytest', str(repo / 'python/tests/test_rectangleguillotine.py'), '-q'], capture_output=True, text=True)
print(tests.stdout + tests.stderr, flush=True)
(ROOT / 'setup-tests.txt').write_text(tests.stdout + tests.stderr)
if tests.returncode and not (
    "set_bin_type_resource_tolerance: no keyword 'resource_tolerance'" in tests.stdout
    and '1 failed, 30 passed' in tests.stdout
):
    tests.check_returncode()
print('SETUP_COMPLETE', flush=True)
