#include "rtx/TransferStore.hpp"

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <system_error>

#include "rtx/Ids.hpp"
#include "rtx/Json.hpp"
#include "rtx/Platform.hpp"

namespace rtx {
namespace {

std::string Text (const JsonPtr& node, const char* key)
{
	if (node == nullptr) return {};
	const JsonPtr field = node->Get (key);
	return field != nullptr ? field->StringOr ("") : std::string ();
}

} // namespace

bool EnsureDirectory (const std::string& path)
{
	std::error_code code;
	std::filesystem::create_directories (FsPath (path), code);
	return !code && std::filesystem::is_directory (FsPath (path), code);
}

bool RemoveDirectory (const std::string& path)
{
	std::error_code code;
	std::filesystem::remove_all (FsPath (path), code);
	return !code;
}

bool WriteTextFile (const std::string& path, const std::string& content)
{
	std::ofstream stream (FsPath (path), std::ios::binary | std::ios::trunc);
	if (!stream) return false;
	stream.write (content.data (), static_cast<std::streamsize> (content.size ()));
	return stream.good ();
}

bool ReadTextFile (const std::string& path, std::string& content)
{
	std::ifstream stream (FsPath (path), std::ios::binary);
	if (!stream) return false;
	std::ostringstream buffer;
	buffer << stream.rdbuf ();
	content = buffer.str ();
	return true;
}

long long FileSize (const std::string& path)
{
	std::error_code code;
	const auto size = std::filesystem::file_size (FsPath (path), code);
	if (code) return -1;
	return static_cast<long long> (size);
}

std::string TransferStore::DefaultPath ()
{
	return AppDataDirectory () + "/transfers.json";
}

std::string TransferStore::DefaultWorkDirectory ()
{
	return AppDataDirectory () + "/captures";
}

TransferStore::TransferStore (std::string filePath) : path (std::move (filePath))
{
}

Status TransferStore::Load ()
{
	pending.clear ();
	assignments.clear ();
	std::string content;
	if (!ReadTextFile (path, content)) return Status::Ok ();   // Noch nichts da ist kein Fehler.
	const JsonPtr root = Json::Parse (content);
	if (root == nullptr || root->GetKind () != Json::Kind::Object)
		return Status::Fail (errc::IoFailed, "Der Zustandsspeicher ist unlesbar: " + path);

	if (const JsonPtr items = root->Get ("pending")) {
		for (const JsonPtr& item : items->Items ()) {
			PendingTransfer transfer;
			transfer.sourceProjectKey = Text (item, "sourceProjectKey");
			transfer.sourceViewKey = Text (item, "sourceViewKey");
			transfer.idempotencyKey = Text (item, "idempotencyKey");
			transfer.captureId = Text (item, "captureId");
			transfer.manifestCaptureId = Text (item, "manifestCaptureId");
			transfer.manifestCreatedAt = Text (item, "manifestCreatedAt");
			transfer.manifestSha256 = Text (item, "manifestSha256");
			transfer.directory = Text (item, "directory");
			transfer.targetProjectId = Text (item, "targetProjectId");
			transfer.targetMode = Text (item, "targetMode");
			transfer.targetViewpointId = Text (item, "targetViewpointId");
			transfer.targetViewpointName = Text (item, "targetViewpointName");
			transfer.targetFrame = Text (item, "targetFrame");
			transfer.targetSize = Text (item, "targetSize");
			transfer.serverUrl = Text (item, "serverUrl");
			transfer.createdAt = Text (item, "createdAt");
			if (!transfer.idempotencyKey.empty ()) pending.push_back (transfer);
		}
	}
	if (const JsonPtr items = root->Get ("assignments")) {
		for (const JsonPtr& item : items->Items ()) {
			LastAssignment assignment;
			assignment.sourceProjectKey = Text (item, "sourceProjectKey");
			assignment.sourceViewKey = Text (item, "sourceViewKey");
			assignment.projectId = Text (item, "projectId");
			assignment.projectName = Text (item, "projectName");
			assignment.viewpointId = Text (item, "viewpointId");
			assignment.viewpointName = Text (item, "viewpointName");
			if (!assignment.projectId.empty ()) assignments.push_back (assignment);
		}
	}
	return Status::Ok ();
}

Status TransferStore::Save () const
{
	const std::size_t slash = path.find_last_of ('/');
	if (slash != std::string::npos && !EnsureDirectory (path.substr (0, slash)))
		return Status::Fail (errc::IoFailed, "Das Zustandsverzeichnis ließ sich nicht anlegen.");

	JsonPtr root = Json::MakeObject ();
	JsonPtr items = Json::MakeArray ();
	for (const PendingTransfer& transfer : pending) {
		JsonPtr node = Json::MakeObject ();
		node->Set ("sourceProjectKey", Json::MakeString (transfer.sourceProjectKey));
		node->Set ("sourceViewKey", Json::MakeString (transfer.sourceViewKey));
		node->Set ("idempotencyKey", Json::MakeString (transfer.idempotencyKey));
		node->Set ("captureId", Json::MakeString (transfer.captureId));
		node->Set ("manifestCaptureId", Json::MakeString (transfer.manifestCaptureId));
		node->Set ("manifestCreatedAt", Json::MakeString (transfer.manifestCreatedAt));
		node->Set ("manifestSha256", Json::MakeString (transfer.manifestSha256));
		node->Set ("directory", Json::MakeString (transfer.directory));
		node->Set ("targetProjectId", Json::MakeString (transfer.targetProjectId));
		node->Set ("targetMode", Json::MakeString (transfer.targetMode));
		node->Set ("targetViewpointId", Json::MakeString (transfer.targetViewpointId));
		node->Set ("targetViewpointName", Json::MakeString (transfer.targetViewpointName));
		node->Set ("targetFrame", Json::MakeString (transfer.targetFrame));
		node->Set ("targetSize", Json::MakeString (transfer.targetSize));
		node->Set ("serverUrl", Json::MakeString (transfer.serverUrl));
		node->Set ("createdAt", Json::MakeString (transfer.createdAt));
		items->Append (node);
	}
	root->Set ("pending", items);

	JsonPtr assignmentItems = Json::MakeArray ();
	for (const LastAssignment& assignment : assignments) {
		JsonPtr node = Json::MakeObject ();
		node->Set ("sourceProjectKey", Json::MakeString (assignment.sourceProjectKey));
		node->Set ("sourceViewKey", Json::MakeString (assignment.sourceViewKey));
		node->Set ("projectId", Json::MakeString (assignment.projectId));
		node->Set ("projectName", Json::MakeString (assignment.projectName));
		node->Set ("viewpointId", Json::MakeString (assignment.viewpointId));
		node->Set ("viewpointName", Json::MakeString (assignment.viewpointName));
		assignmentItems->Append (node);
	}
	root->Set ("assignments", assignmentItems);

	// Erst neben die Zieldatei schreiben, dann umbenennen: ein Absturz mitten
	// im Schreiben darf keinen halben Zustand hinterlassen.
	const std::string temporary = path + ".tmp";
	if (!WriteTextFile (temporary, root->SerializePretty ()))
		return Status::Fail (errc::IoFailed, "Der Zustand ließ sich nicht schreiben.");
	std::error_code code;
	std::filesystem::rename (FsPath (temporary), FsPath (path), code);
	if (code) return Status::Fail (errc::IoFailed, "Der Zustand ließ sich nicht ersetzen.");
	return Status::Ok ();
}

PendingTransfer TransferStore::FindPending (const std::string& sourceProjectKey,
											const std::string& sourceViewKey) const
{
	for (const PendingTransfer& transfer : pending) {
		if (transfer.sourceProjectKey == sourceProjectKey && transfer.sourceViewKey == sourceViewKey)
			return transfer;
	}
	return {};
}

void TransferStore::PutPending (const PendingTransfer& transfer)
{
	for (PendingTransfer& existing : pending) {
		if (existing.sourceProjectKey == transfer.sourceProjectKey &&
			existing.sourceViewKey == transfer.sourceViewKey) {
			existing = transfer;
			return;
		}
	}
	pending.push_back (transfer);
}

void TransferStore::RemovePending (const std::string& sourceProjectKey,
								   const std::string& sourceViewKey)
{
	for (auto it = pending.begin (); it != pending.end (); ++it) {
		if (it->sourceProjectKey == sourceProjectKey && it->sourceViewKey == sourceViewKey) {
			pending.erase (it);
			return;
		}
	}
}

LastAssignment TransferStore::FindAssignment (const std::string& sourceProjectKey,
											  const std::string& sourceViewKey) const
{
	for (const LastAssignment& assignment : assignments) {
		if (assignment.sourceProjectKey == sourceProjectKey &&
			assignment.sourceViewKey == sourceViewKey)
			return assignment;
	}
	return {};
}

void TransferStore::PutAssignment (const LastAssignment& assignment)
{
	for (LastAssignment& existing : assignments) {
		if (existing.sourceProjectKey == assignment.sourceProjectKey &&
			existing.sourceViewKey == assignment.sourceViewKey) {
			existing = assignment;
			return;
		}
	}
	assignments.push_back (assignment);
}

} // namespace rtx
