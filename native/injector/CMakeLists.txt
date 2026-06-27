# Module 5A — DLL injector (injector.exe).
add_executable(injector injector.cpp)
# Cần quyền Administrator lúc chạy (OpenProcess PROCESS_ALL_ACCESS) — xem manifest.
target_link_libraries(injector PRIVATE)
