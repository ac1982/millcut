import pathlib,shutil,subprocess,runpy,sys
root=pathlib.Path('/content/native-study');out=root/'ejection';v1=out/'v1';v1.mkdir(exist_ok=True)
shutil.copy(root/'build/ejection',v1/'ejection')
shutil.copy(root/'build-src/ejection.cpp',v1/'ejection.cpp')
runpy.run_path('/content/ejection_build_colab.py',run_name='__main__')
p=subprocess.run([sys.executable,'-m','pip','install','ortools==9.15.6755'],capture_output=True,text=True);print(p.stdout[-1000:],p.stderr[-1000:]);p.check_returncode()
print('REFINEMENT_READY')
