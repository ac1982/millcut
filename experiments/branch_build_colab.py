"""Build branch continuation harness in Colab, original pinned solver only."""
import pathlib, shutil, subprocess
root=pathlib.Path('/content/native-study')
shutil.copy('/content/branch_labels.cpp',root/'build-src/branch_labels.cpp')
p=root/'build-src/CMakeLists.txt'
s=p.read_text()
if 'add_executable(branch_labels' not in s:
    s+='\nadd_executable(branch_labels branch_labels.cpp)\ntarget_link_libraries(branch_labels PRIVATE native_core)\n'
    p.write_text(s)
for cmd in (['cmake','-S',str(root/'build-src'),'-B',str(root/'build'),'-G','Ninja','-DCMAKE_BUILD_TYPE=Release'],['cmake','--build',str(root/'build'),'--target','branch_labels','-j2']):
    p=subprocess.run(cmd,capture_output=True,text=True);print(p.stdout[-2000:],p.stderr[-3000:]);p.check_returncode()
print('BRANCH_BUILD_COMPLETE')
