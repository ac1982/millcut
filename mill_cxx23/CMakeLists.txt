cmake_minimum_required(VERSION 3.25)
project(mill_cxx23 VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(mill_cxx23_core STATIC
    src/mill_cxx23.cpp
)
target_include_directories(mill_cxx23_core PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

if(EMSCRIPTEN)
    add_executable(mill_cxx23_wasm src/mill_wasm.cpp)
    target_link_libraries(mill_cxx23_wasm PRIVATE mill_cxx23_core)
    target_compile_options(mill_cxx23_core PRIVATE -O3 -fno-exceptions)
    target_compile_options(mill_cxx23_wasm PRIVATE -O3 -fno-exceptions)
    target_link_options(mill_cxx23_wasm PRIVATE
        "-sWASM=1"
        "-sALLOW_MEMORY_GROWTH=1"
        "-sINITIAL_MEMORY=33554432"
        "-sMODULARIZE=1"
        "-sEXPORT_NAME=createMillCxx23"
        "-sEXPORTED_FUNCTIONS=['_wasm_solve_order','_wasm_verify_certificate','_wasm_engine_info','_malloc','_free']"
        "-sEXPORTED_RUNTIME_METHODS=['ccall','cwrap','UTF8ToString','stringToUTF8','lengthBytesUTF8']"
        "-sENVIRONMENT=web,worker,node"
    )
else()
    add_executable(mill_cxx23_cli src/mill_cli.cpp)
    target_link_libraries(mill_cxx23_cli PRIVATE mill_cxx23_core)
    target_compile_options(mill_cxx23_core PRIVATE -O3 -Wall -Wextra -Wpedantic)
    target_compile_options(mill_cxx23_cli PRIVATE -O3 -Wall -Wextra -Wpedantic)
endif()
