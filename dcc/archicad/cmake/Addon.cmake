# Das Archicad-Bundle. Aufbau nach dem DevKit-Beispiel (`Examples/*/CMakeLists.txt`)
# und nach dem gisloader-Add-on des Nutzers — aber nur das Gerüst: kein
# eingebetteter Browser, kein Importweg.

set (AC_ADDON_NAME "rendertaxi" CACHE STRING "Add-On name.")
set (AC_ADDON_LANGUAGE "INT" CACHE STRING "Add-On language code.")

message (STATUS "Archicad DevKit: ${AC_API_DEVKIT_DIR}")

file (READ "${AC_API_DEVKIT_DIR}/Support/Inc/ACAPinc.h" ACAPIncContent)
string (REGEX MATCHALL "#define[ \t]+ServerMainVers_([0-9][0-9])" VersionList ${ACAPIncContent})
set (ARCHICAD_VERSION ${CMAKE_MATCH_1})
message (STATUS "Archicad-Hauptversion des DevKit: ${ARCHICAD_VERSION}")
if (NOT ARCHICAD_VERSION STREQUAL "28")
	message (FATAL_ERROR
		"Dieses Add-On ist für Archicad 28 (DevKit 28.4001). Gefunden wurde ${ARCHICAD_VERSION}. "
		"Graphisoft lädt kein Add-On, das gegen ein anderes DevKit gebaut wurde; die Unterstützung "
		"weiterer Versionen ist in Issue #20 ausdrücklich Nicht-Ziel.")
endif ()

if (WIN32)
	add_definitions (-DUNICODE -D_UNICODE -D_ITERATOR_DEBUG_LEVEL=0 -DGS_WIN)
else ()
	add_definitions (-Dmacintosh=1 -DGS_MAC)
endif ()
add_definitions (-DACExtension)

set (AddOnSourcesFolder "${CMAKE_CURRENT_LIST_DIR}/../src")
set (AddOnResourcesFolder "${CMAKE_CURRENT_LIST_DIR}/..")
set (ResourceObjectsDir "${CMAKE_BINARY_DIR}/ResourceObjects")

if (WIN32)
	file (GLOB AddOnResourceFiles
		${AddOnResourcesFolder}/R${AC_ADDON_LANGUAGE}/*.grc
		${AddOnResourcesFolder}/RFIX/*.grc
		${AddOnResourcesFolder}/RFIX.win/*.rc2
	)
else ()
	file (GLOB AddOnResourceFiles
		${AddOnResourcesFolder}/R${AC_ADDON_LANGUAGE}/*.grc
		${AddOnResourcesFolder}/RFIX/*.grc
		${AddOnResourcesFolder}/RFIX.mac/*.plist
	)
endif ()

add_custom_target (AddOnResources ALL
	DEPENDS "${ResourceObjectsDir}/AddOnResources.stamp"
	SOURCES ${AddOnResourceFiles}
)

get_filename_component (APIDevKitToolsFolder "${AC_API_DEVKIT_DIR}/Support/Tools" ABSOLUTE)
get_filename_component (AddOnSourcesFolderAbsolute "${AddOnSourcesFolder}" ABSOLUTE)
get_filename_component (AddOnResourcesFolderAbsolute "${AddOnResourcesFolder}" ABSOLUTE)

if (WIN32)
	# Unter Windows übersetzt `CompileResources.py` die .grc über ResConv zu
	# .rc2 und bindet sie mit `RFIX.win/rendertaxi.rc2` zu einer .res, die der
	# Linker in die .apx aufnimmt (Muster: gisloader-Add-on).
	add_custom_command (
		OUTPUT "${ResourceObjectsDir}/AddOnResources.stamp"
		DEPENDS ${AddOnResourceFiles}
		COMMENT "Ressourcen übersetzen…"
		COMMAND ${CMAKE_COMMAND} -E make_directory "${ResourceObjectsDir}"
		COMMAND python "${APIDevKitToolsFolder}/CompileResources.py" "${AC_ADDON_LANGUAGE}"
			"${AC_API_DEVKIT_DIR}" "${AddOnSourcesFolderAbsolute}" "${AddOnResourcesFolderAbsolute}"
			"${ResourceObjectsDir}" "${ResourceObjectsDir}/${AC_ADDON_NAME}.res"
		COMMAND ${CMAKE_COMMAND} -E touch "${ResourceObjectsDir}/AddOnResources.stamp"
	)
else ()
	add_custom_command (
		OUTPUT "${ResourceObjectsDir}/AddOnResources.stamp"
		DEPENDS ${AddOnResourceFiles}
		COMMENT "Ressourcen übersetzen…"
		COMMAND ${CMAKE_COMMAND} -E make_directory "${ResourceObjectsDir}"
		COMMAND python3 "${APIDevKitToolsFolder}/CompileResources.py" "${AC_ADDON_LANGUAGE}"
			"${AC_API_DEVKIT_DIR}" "${AddOnSourcesFolderAbsolute}" "${AddOnResourcesFolderAbsolute}"
			"${ResourceObjectsDir}"
			"${CMAKE_BINARY_DIR}/$<CONFIG>/${AC_ADDON_NAME}.bundle/Contents/Resources"
		COMMAND ${CMAKE_COMMAND} -E copy "${AC_API_DEVKIT_DIR}/Support/Inc/PkgInfo"
			"${CMAKE_BINARY_DIR}/$<CONFIG>/${AC_ADDON_NAME}.bundle/Contents/PkgInfo"
		COMMAND ${CMAKE_COMMAND} -E touch "${ResourceObjectsDir}/AddOnResources.stamp"
	)
endif ()

file (GLOB AddOnHeaderFiles ${AddOnSourcesFolder}/*.hpp ${AddOnSourcesFolder}/*.h)
file (GLOB AddOnSourceFiles ${AddOnSourcesFolder}/*.cpp)

# Messauftrag Archicad-Modellweg (RTX-A-010, #256): Prototyp eines GLB-Schreibers.
# Nur mit `-DRTX_SPIKE_MODEL_GLB=ON`; das veröffentlichte Add-on (build.sh, CI,
# dist.sh) enthält ihn nicht. Siehe `spike/model-glb/README.md`.
option (RTX_SPIKE_MODEL_GLB "Prototyp des GLB-Schreibers einbauen (nur für den Messauftrag #256)" OFF)
if (RTX_SPIKE_MODEL_GLB)
	file (GLOB RtxSpikeFiles
		${CMAKE_CURRENT_LIST_DIR}/../spike/model-glb/*.hpp
		${CMAKE_CURRENT_LIST_DIR}/../spike/model-glb/GlbWriter.cpp
		${CMAKE_CURRENT_LIST_DIR}/../spike/model-glb/ModelGlbSpike.cpp
	)
	list (APPEND AddOnSourceFiles ${RtxSpikeFiles})
	message (STATUS "Messauftrag #256: Prototyp des GLB-Schreibers ist eingebaut.")
endif ()
source_group ("Sources" FILES ${AddOnHeaderFiles} ${AddOnSourceFiles})

if (WIN32)
	add_library (AddOn SHARED ${AddOnHeaderFiles} ${AddOnSourceFiles})
	set_target_properties (AddOn PROPERTIES OUTPUT_NAME ${AC_ADDON_NAME})
	set_target_properties (AddOn PROPERTIES SUFFIX ".apx")
	target_link_options (AddOn PUBLIC "${ResourceObjectsDir}/${AC_ADDON_NAME}.res")
	target_link_options (AddOn PUBLIC /export:GetExportedFuncAddrs,@1 /export:SetImportedFuncAddrs,@2)
else ()
	add_library (AddOn MODULE ${AddOnHeaderFiles} ${AddOnSourceFiles})
	set_target_properties (AddOn PROPERTIES OUTPUT_NAME ${AC_ADDON_NAME})
	set_target_properties (AddOn PROPERTIES BUNDLE TRUE)
	set_target_properties (AddOn PROPERTIES
		MACOSX_BUNDLE_INFO_PLIST "${AddOnResourcesFolderAbsolute}/RFIX.mac/Info.plist")
	set_target_properties (AddOn PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/$<CONFIG>")
endif ()

target_include_directories (AddOn PUBLIC
	${AddOnSourcesFolder}
	${AC_API_DEVKIT_DIR}/Support/Inc
)

if (WIN32)
	target_link_libraries (AddOn
		"${AC_API_DEVKIT_DIR}/Support/Lib/ACAP_STAT.lib"
		rtxcore
	)
else ()
	find_library (CocoaFramework Cocoa)
	target_link_libraries (AddOn
		"${AC_API_DEVKIT_DIR}/Support/Lib/libACAP_STAT.a"
		${CocoaFramework}
		rtxcore
	)
endif ()

target_compile_features (AddOn PUBLIC cxx_std_17)
# Der Git-Stand für „Über" und den Fuß der Palette (#281). Leer heißt
# Entwicklungsbuild; `Version.hpp` setzt dann den Vorgabewert.
set (RTX_BUILD_COMMIT "" CACHE STRING "Git-Stand des Builds (CI und scripts/build.sh).")
if (RTX_SPIKE_MODEL_GLB)
	target_compile_definitions (AddOn PRIVATE RTX_SPIKE_MODEL_GLB=1)
endif ()
if (RTX_BUILD_COMMIT MATCHES "^[0-9a-f]+(-dirty)?$")
	target_compile_definitions (AddOn PRIVATE RTX_BUILD_COMMIT="${RTX_BUILD_COMMIT}")
	message (STATUS "Build-Kennung: ${RTX_BUILD_COMMIT}")
elseif (NOT RTX_BUILD_COMMIT STREQUAL "")
	message (WARNING "RTX_BUILD_COMMIT ist kein Git-Hash und wird nicht übernommen: ${RTX_BUILD_COMMIT}")
endif ()
target_compile_options (AddOn PUBLIC "$<$<CONFIG:Debug>:-DDEBUG>")
if (WIN32)
	# Warnungssatz des DevKit-Beispiels für MSVC; `/Zc:wchar_t-` verlangen die
	# Graphisoft-Header (GS::UniChar).
	target_compile_options (AddOn PUBLIC /W3 /WX /wd4996 /Zc:wchar_t- /utf-8)
else ()
	# Warnungssatz des DevKit-Beispiels: die Graphisoft-Header lösen ohne diese
	# Ausnahmen Warnungen aus, die mit -Werror den Build bräche.
	target_compile_options (AddOn PUBLIC -Wall -Werror -fvisibility=hidden
		-Wno-multichar -Wno-ctor-dtor-privacy -Wno-invalid-offsetof -Wno-ignored-qualifiers
		-Wno-reorder -Wno-overloaded-virtual -Wno-unused-parameter -Wno-missing-field-initializers
		-Wno-unknown-pragmas -Wno-missing-braces -Wno-unused-private-field -Wno-return-std-move
		-Wno-unused-value -Wno-switch -Wno-deprecated -Wno-shorten-64-to-32)
endif ()

add_dependencies (AddOn AddOnResources)

get_filename_component (APIDevKitModulesDir "${AC_API_DEVKIT_DIR}/Support/Modules" ABSOLUTE)
file (GLOB GSIncludeFolders ${APIDevKitModulesDir}/*)
foreach (includeFolder ${GSIncludeFolders})
	target_include_directories (AddOn PUBLIC "${includeFolder}")
endforeach ()

if (WIN32)
	# Unter Windows liegen die Importbibliotheken je Modul in `Modules/<Modul>/Win`.
	file (GLOB GSLinkLibs ${APIDevKitModulesDir}/*/Win/*.lib)
	foreach (linkLib ${GSLinkLibs})
		target_link_libraries (AddOn ${linkLib})
	endforeach ()
else ()
	get_filename_component (APIDevKitLinkLibDir "${AC_API_DEVKIT_DIR}/Support/Frameworks" ABSOLUTE)
	file (GLOB GSLinkLibFolders ${APIDevKitLinkLibDir}/*)
	foreach (linkLibFolder ${GSLinkLibFolders})
		target_link_libraries (AddOn ${linkLibFolder})
	endforeach ()
endif ()
