add_library(probe SHARED probe.cpp)
target_link_libraries(probe PRIVATE)
set_target_properties(probe PROPERTIES OUTPUT_NAME "tds_probe2")
