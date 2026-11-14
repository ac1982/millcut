"""Restore the frozen solver and start the sequential development pilot."""
import pathlib, subprocess, sys, shutil, json
history = pathlib.Path('/content/data-expansion/history/experiments')
p = subprocess.run([sys.executable, str(history / 'routing_setup_colab.py')], capture_output=True, text=True)
print(p.stdout[-1800:], p.stderr[-1000:])
p.check_returncode()
shutil.copy(history / 'routing_core_colab.py', '/content/routing_core_colab.py')
root = pathlib.Path('/content/data-expansion/corpus')
with (root / 'labels' / 'pilot-running.log').open('w') as log:
    p = subprocess.Popen([sys.executable, '-u', '/content/data_label_colab.py'], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
(root / 'labels' / 'running.json').write_text(json.dumps(dict(pid=p.pid)))
print('PILOT_STARTED', p.pid)
