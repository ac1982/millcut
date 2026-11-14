import pathlib,subprocess,shutil
root=pathlib.Path('/content/native-study')
shutil.copy('/content/branch_policy.cpp',root/'build-src/branch_policy.cpp')
p=root/'build-src/CMakeLists.txt';s=p.read_text()
if 'add_executable(branch_policy' not in s:
    s+='\nadd_executable(branch_policy branch_policy.cpp)\ntarget_include_directories(branch_policy BEFORE PRIVATE ${FORK}/src)\ntarget_link_libraries(branch_policy PRIVATE native_core)\n'
    p.write_text(s)
for cmd in (['cmake','-S',str(root/'build-src'),'-B',str(root/'build'),'-G','Ninja','-DCMAKE_BUILD_TYPE=Release'],['cmake','--build',str(root/'build'),'--target','branch_policy','-j2']):
    p=subprocess.run(cmd,capture_output=True,text=True);print(p.stdout[-2000:],p.stderr[-3000:]);p.check_returncode()
print('POLICY_BUILD_COMPLETE')
