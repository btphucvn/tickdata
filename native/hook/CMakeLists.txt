# Module 5B — Hook engine DLL (tdshook.dll). Cần MinHook.
add_library(tdshook SHARED tdshook.cpp gui_inject.cpp slippage.cpp fxt_patch.cpp)
set_target_properties(tdshook PROPERTIES PREFIX "" OUTPUT_NAME "tdshook")
target_link_libraries(tdshook PRIVATE minhook sigscan_lib)
target_include_directories(tdshook PRIVATE
    ${CMAKE_SOURCE_DIR}/sigscan
    ${CMAKE_SOURCE_DIR}/third_party/minhook/include)
