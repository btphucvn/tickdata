# Module 5B — Hook engine DLL (tdshook.dll). Cần MinHook + puff (giải nén .tkd windowed).
add_library(tdshook SHARED tdshook.cpp gui_inject.cpp slippage.cpp fxt_patch.cpp fxt_virtual.cpp
    ${CMAKE_SOURCE_DIR}/third_party/puff/puff.c)
set_target_properties(tdshook PROPERTIES PREFIX "" OUTPUT_NAME "tdshook")
target_link_libraries(tdshook PRIVATE minhook sigscan_lib)
target_include_directories(tdshook PRIVATE
    ${CMAKE_SOURCE_DIR}/sigscan
    ${CMAKE_SOURCE_DIR}/third_party/puff
    ${CMAKE_SOURCE_DIR}/third_party/minhook/include)
