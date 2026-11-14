"""Launch the fixed sequence in background; all computation remains in Colab."""
import pathlib, subprocess, sys, json
root = pathlib.Path('/content/route-study')
driver = root / 'driver.py'
driver.write_text('''import subprocess,sys,pathlib,json
root=pathlib.Path('/content/route-study')
jobs=[['route_study_colab.py','regression'],['route_study_colab.py','development'],['route_fit_colab.py'],['route_study_colab.py','validation']]
for args in jobs:
    (root/'current-phase.json').write_text(json.dumps(dict(command=args)))
    p=subprocess.run([sys.executable,'-u','/content/'+args[0]]+args[1:])
    if p.returncode:
        (root/'failed.json').write_text(json.dumps(dict(command=args,returncode=p.returncode)))
        sys.exit(p.returncode)
(root/'complete.json').write_text(json.dumps(dict(all_phases_complete=True)))
''')
with (root / 'running.log').open('w') as log:
    p = subprocess.Popen([sys.executable, '-u', str(driver)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
(root / 'running.json').write_text(json.dumps(dict(pid=p.pid)))
print('STUDY_STARTED', p.pid)
