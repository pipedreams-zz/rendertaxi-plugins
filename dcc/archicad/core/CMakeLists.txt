# Der DevKit-freie Kern des Add-Ons.
#
# `development-environments.md`, Abschnitt 4 verlangt, dass Manifest, Uploader,
# Tokenablage und HTTP ohne DevKit bau- und testbar bleiben. Dieses Ziel hängt
# deshalb ausschließlich von der C++-Standardbibliothek, der libcurl des
# Systems und — für die Keychain — von Security.framework ab.

add_library (rtxcore STATIC
	src/Canonical.cpp
	src/CaptureManifest.cpp
	src/CaptureTransfer.cpp
	src/DeviceLogin.cpp
	src/HttpCurl.cpp
	src/Ids.cpp
	src/ImageCrop.cpp
	src/ImageFile.cpp
	src/Json.cpp
	src/Log.cpp
	src/PaletteText.cpp
	src/PluginApi.cpp
	src/Sha256.cpp
	src/TokenStore.cpp
	src/TokenStoreKeychain.cpp
	src/TransferStore.cpp
	src/UrlHost.cpp
)

target_include_directories (rtxcore PUBLIC "${CMAKE_CURRENT_LIST_DIR}/include")
target_compile_features (rtxcore PUBLIC cxx_std_17)
set_target_properties (rtxcore PROPERTIES POSITION_INDEPENDENT_CODE ON)

if (NOT WIN32)
	target_compile_options (rtxcore PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter)
else ()
	# Quelltexte sind UTF-8; ohne `/utf-8` liest MSVC sie in der Codepage des
	# Build-Rechners.
	target_compile_options (rtxcore PRIVATE /utf-8)
endif ()

# Unter Windows kommt libcurl nicht mit dem System, sondern aus vcpkg
# (Triplet `x64-windows-static-md`, statisch gegen die DLL-Laufzeit gelinkt);
# siehe `.github/workflows/archicad-windows.yml`.
find_package (CURL REQUIRED)
target_link_libraries (rtxcore PUBLIC CURL::libcurl)

if (APPLE)
	find_library (SecurityFramework Security)
	find_library (CoreFoundationFramework CoreFoundation)
	# ImageIO und CoreGraphics für den Zuschnitt auf das Zielformat. Beide sind
	# Systemframeworks von macOS, also keine neu aufgenommene Abhängigkeit im
	# Sinne der Reiferichtlinie aus AGENTS.md.
	find_library (ImageIOFramework ImageIO)
	find_library (CoreGraphicsFramework CoreGraphics)
	target_link_libraries (rtxcore PUBLIC ${SecurityFramework} ${CoreFoundationFramework}
		${ImageIOFramework} ${CoreGraphicsFramework})
endif ()

if (RTX_BUILD_TESTS)
	add_subdirectory (tests)
endif ()
