add_executable (rtxcore-tests
	Testing.cpp
	MockServer.cpp
	FakePlatform.cpp
	CoreTest.cpp
	TransferTest.cpp
	PaletteTextTest.cpp
	PlatformTest.cpp
	SavedViewsTest.cpp
	ProjectListTest.cpp
)

target_link_libraries (rtxcore-tests PRIVATE rtxcore)
# `../../src` nur wegen `Version.hpp`: die Kennung und die Fassung des Add-Ons
# stehen dort, und der Kerntest hält sie gegen `kPluginClientId` (§5.1). Die
# Datei ist reiner Text ohne DevKit — die Grenze aus `README.md` bleibt.
target_include_directories (rtxcore-tests PRIVATE "${CMAKE_CURRENT_LIST_DIR}"
	"${CMAKE_CURRENT_LIST_DIR}/../../src")
target_compile_features (rtxcore-tests PRIVATE cxx_std_17)
if (NOT WIN32)
	target_compile_options (rtxcore-tests PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter)
endif ()

# Derselbe Scheinserver als eigenständiges Programm — die Gegenstelle für die
# Vorführung mit echtem Archicad, solange die Serverhälfte nicht ausgeliefert
# ist. Siehe scripts/fake-server.sh.
add_executable (rtx-fake-platform
	MockServer.cpp
	FakePlatform.cpp
	FakePlatformMain.cpp
)
target_link_libraries (rtx-fake-platform PRIVATE rtxcore)
target_include_directories (rtx-fake-platform PRIVATE "${CMAKE_CURRENT_LIST_DIR}")
target_compile_features (rtx-fake-platform PRIVATE cxx_std_17)
if (NOT WIN32)
	target_compile_options (rtx-fake-platform PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter)
endif ()

enable_testing ()
add_test (NAME rtxcore COMMAND rtxcore-tests)
