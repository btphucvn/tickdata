# Module 5C — SigScanner: thư viện tĩnh + tool dòng lệnh.
add_library(sigscan_lib STATIC sigscan.cpp)
target_include_directories(sigscan_lib PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})

add_executable(sigscan sigscan_main.cpp)
target_link_libraries(sigscan PRIVATE sigscan_lib)
