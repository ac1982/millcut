import pathlib,shutil,subprocess
root=pathlib.Path('/content/native-study')
shutil.copy('/content/ejection.cpp',root/'build-src/ejection.cpp')
p=root/'build-src/CMakeLists.txt';s=p.read_text()
if 'add_executable(ejection' not in s:s+='\nadd_executable(ejection ejection.cpp)\ntarget_link_libraries(ejection PRIVATE native_core)\n';p.write_text(s)
for cmd in (['cmake','-S',str(root/'build-src'),'-B',str(root/'build'),'-G','Ninja','-DCMAKE_BUILD_TYPE=Release'],['cmake','--build',str(root/'build'),'--target','ejection','-j2']):
    p=subprocess.run(cmd,capture_output=True,text=True);print(p.stdout[-1500:],p.stderr[-2500:]);p.check_returncode()
print('EJECTION_BUILD_COMPLETE')
