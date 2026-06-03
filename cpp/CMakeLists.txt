cmake_minimum_required(VERSION 3.10)
project(rknn_yolo_infer)

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O2 -ffast-math")
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64")
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -march=armv8-a")
endif()

find_path(RKNN_INCLUDE_DIR rknn_api.h
    PATHS
        /usr/include
        /usr/local/include
        $ENV{HOME}/rknpu2/runtime/RK3568/Linux/librknn_api/include
        $ENV{HOME}/rknpu2/runtime/RK3576/Linux/librknn_api/include
        $ENV{HOME}/rknpu2/runtime/RK3588/Linux/librknn_api/include
    DOC "RKNN API include directory"
)

find_library(RKNN_LIB rknnrt
    PATHS
        /usr/lib
        /usr/local/lib
        $ENV{HOME}/rknpu2/runtime/RK3568/Linux/librknn_api/aarch64
        $ENV{HOME}/rknpu2/runtime/RK3576/Linux/librknn_api/aarch64
        $ENV{HOME}/rknpu2/runtime/RK3588/Linux/librknn_api/aarch64
    DOC "RKNN runtime library"
)

if(NOT RKNN_INCLUDE_DIR)
    message(FATAL_ERROR
        "Cannot find rknn_api.h.\n"
        "Pass it explicitly with:\n"
        "  cmake .. -DRKNN_INCLUDE_DIR=<path>")
endif()

if(NOT RKNN_LIB)
    message(FATAL_ERROR
        "Cannot find librknnrt.so.\n"
        "Pass it explicitly with:\n"
        "  cmake .. -DRKNN_LIB=<path>/librknnrt.so")
endif()

message(STATUS "RKNN include: ${RKNN_INCLUDE_DIR}")
message(STATUS "RKNN lib: ${RKNN_LIB}")

add_library(rknn_yolo SHARED
    rknn_yolo_infer.cpp
)

target_include_directories(rknn_yolo PRIVATE
    ${RKNN_INCLUDE_DIR}
)

target_link_libraries(rknn_yolo PRIVATE
    ${RKNN_LIB}
    m
    pthread
)

set_target_properties(rknn_yolo PROPERTIES
    OUTPUT_NAME "rknn_yolo"
    SOVERSION 1
)

set(PROJECT_ROOT_DIR ${CMAKE_CURRENT_SOURCE_DIR}/..)

add_custom_command(TARGET rknn_yolo POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_FILE:rknn_yolo>
            ${PROJECT_ROOT_DIR}/$<TARGET_FILE_NAME:rknn_yolo>
    COMMENT "Copying librknn_yolo.so to project root"
)

install(TARGETS rknn_yolo
    LIBRARY DESTINATION ${CMAKE_INSTALL_PREFIX}
)

message(STATUS
    "\n"
    "Build commands:\n"
    "  mkdir build && cd build\n"
    "  cmake .. -DCMAKE_BUILD_TYPE=Release\n"
    "  make -j4\n"
    "  # The build also copies librknn_yolo.so to the project root.\n"
)
