cmake_minimum_required(VERSION 3.28)
project(NativePackingResearch LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 14)
include(FetchContent)
set(OPTIMIZATIONTOOLS_BUILD_TEST OFF CACHE BOOL "" FORCE)
FetchContent_Declare(optimizationtools GIT_REPOSITORY https://github.com/fontanf/optimizationtools.git GIT_TAG 7e29ad3e2dab11a44887fbcc9382188c5d0ead12 EXCLUDE_FROM_ALL)
FetchContent_MakeAvailable(optimizationtools)
set(SHAPE_BUILD_TEST OFF CACHE BOOL "" FORCE)
FetchContent_Declare(shape GIT_REPOSITORY https://github.com/fontanf/shape.git GIT_TAG 841836162e377c46c7d65d216ec8e6e4bbe58e5a EXCLUDE_FROM_ALL)
FetchContent_MakeAvailable(shape)
find_package(Threads REQUIRED)
set(REPO /content/native-study/pristine)
set(FORK /content/native-study/patched)
set(DEPS /content/native-study/deps)
add_library(native_core STATIC
 ${REPO}/src/algorithms/common.cpp ${REPO}/src/algorithms/resource.cpp
 ${REPO}/src/rectangleguillotine/block.cpp
 ${REPO}/src/rectangleguillotine/instance.cpp
 ${REPO}/src/rectangleguillotine/instance_builder.cpp
 ${REPO}/src/rectangleguillotine/solution.cpp
 ${REPO}/src/rectangleguillotine/solution_builder.cpp
 ${REPO}/src/rectangleguillotine/instance_flipper.cpp
 ${REPO}/src/rectangleguillotine/algorithm_formatter.cpp)
target_include_directories(native_core PUBLIC ${REPO}/include ${REPO}/src ${DEPS}/columngenerationsolver/include ${DEPS}/treesearchsolver/include)
target_link_libraries(native_core PUBLIC OptimizationTools_utils Shape::shape Threads::Threads)
add_executable(native_baseline harness.cpp)
target_link_libraries(native_baseline PRIVATE native_core)
add_executable(native_patched harness.cpp)
target_include_directories(native_patched BEFORE PRIVATE ${FORK}/src)
target_compile_definitions(native_patched PRIVATE NATIVE_RESEARCH=1)
target_link_libraries(native_patched PRIVATE native_core)
