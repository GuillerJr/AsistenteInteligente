import CryptoKit
import Darwin
import Foundation
import XCTest
@testable import DesktopCore

private final class SyntheticRuntime {
    let root: URL
    let contents: [String: Data] = [
        "python/bin/python3.11": Data([0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0, 0, 1, 0, 0, 0, 0, 2, 0, 0, 0]),
        "codex/bin/codex": Data([0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0, 0, 1, 0, 0, 0, 0, 2, 0, 0, 0]),
        "core/jarvis_core/__init__.py": Data("# own synthetic module".utf8),
        "site-packages/synthetic_dependency.py": Data("# synthetic locked dependency".utf8),
        "certs/cacert.pem": Data("synthetic certificate fixture; never used for TLS".utf8),
        "licenses/NOTICE.txt": Data("synthetic license".utf8),
        "models/minilm/manifest.json": Data("{}".utf8),
        "models/minilm/tokenizer.json": Data("{}".utf8),
        "models/minilm/onnx/model_qint8_arm64.onnx": Data("synthetic model; never executed".utf8),
        "models/minilm/LICENSE.Apache-2.0.txt": Data("synthetic license slot".utf8),
    ]
    init(suffix: String = "") throws {
        root = URL(fileURLWithPath: "/private/tmp/jarvis-runtime-fixture-" + UUID().uuidString + suffix, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        for (relative, data) in contents {
            let target = root.appendingPathComponent(relative)
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            try data.write(to: target, options: .withoutOverwriting)
            XCTAssertEqual(chmod(target.path, relative.hasSuffix("python3.11") || relative == "codex/bin/codex" ? 0o755 : 0o644), 0)
        }
    }
    deinit { try? FileManager.default.removeItem(at: root) }
    func manifest() -> [String: RPCValue] {
        let entries: [String: RPCValue] = ["python": .string("python/bin/python3.11"), "source": .string("core"),
            "dependencies": .string("site-packages"), "codex": .string("codex/bin/codex"), "model": .string("models/minilm")]
        let rows = contents.keys.sorted().map { path in RPCValue.object([
            "path": .string(path), "bytes": .number(Double(contents[path]!.count)),
            "sha256": .string(BundledRuntime.digest(contents[path]!)),
            "executable": .bool(path.hasSuffix("python3.11") || path == "codex/bin/codex")]) }
        return ["schema": .string("jarvis.bundled-runtime.v1"), "source_commit": .string(String(repeating: "a", count: 40)),
            "target": .string("aarch64-apple-darwin"), "minimum_macos": .string("14.0"), "python_version": .string("3.11.18"),
            "codex_version": .string("0.163.0"), "entrypoints": .object(entries), "files": .array(rows), "components": .array(
                ["python", "codex", "jarvis-core"].map { id in .object([
                    "id": .string(id), "version": .string("1"), "origin": .string("https://example.invalid/synthetic.zip"),
                    "archive_sha256": .string(String(repeating: "b", count: 64)), "license_paths": .array([.string("licenses/NOTICE.txt")])]) })]
    }
    static func encode(_ object: [String: RPCValue]) throws -> Data {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(RPCValue.object(object))
    }
    @discardableResult func write(_ object: [String: RPCValue]? = nil) throws -> String {
        let data = try Self.encode(object ?? manifest())
        try data.write(to: root.appendingPathComponent("runtime-manifest.json"))
        return BundledRuntime.digest(data)
    }
    func resolve() throws -> BundledRuntime { try BundledRuntime.resolve(root: root, manifestSHA256: write()) }
}

final class BundledRuntimeTests: XCTestCase {
    func testClosedInventoryResolvesExactSlotsAndRevalidates() throws {
        let fixture = try SyntheticRuntime(suffix: " con espacios")
        let runtime = try fixture.resolve()
        XCTAssertEqual(runtime.python, fixture.root.appendingPathComponent("python/bin/python3.11").path)
        XCTAssertEqual(runtime.source, fixture.root.appendingPathComponent("core").path)
        XCTAssertEqual(runtime.dependencies, fixture.root.appendingPathComponent("site-packages").path)
        XCTAssertEqual(runtime.codex, fixture.root.appendingPathComponent("codex/bin/codex").path)
        XCTAssertEqual(runtime.model, fixture.root.appendingPathComponent("models/minilm").path)
        XCTAssertEqual(runtime.certificateBundle, fixture.root.appendingPathComponent("certs/cacert.pem").path)
        try runtime.revalidate()
        try Data("altered".utf8).write(to: fixture.root.appendingPathComponent("core/jarvis_core/__init__.py"))
        XCTAssertThrowsError(try runtime.revalidate())
    }

    func testMissingExtraAndEmptyUninventoriedDirectoryFailClosed() throws {
        do {
            let fixture = try SyntheticRuntime(), pin = try fixture.write()
            try FileManager.default.removeItem(at: fixture.root.appendingPathComponent("python/bin/python3.11"))
            XCTAssertThrowsError(try BundledRuntime.resolve(root: fixture.root, manifestSHA256: pin))
        }
        do {
            let fixture = try SyntheticRuntime(), pin = try fixture.write()
            try Data("unlisted".utf8).write(to: fixture.root.appendingPathComponent("unlisted.py"))
            XCTAssertThrowsError(try BundledRuntime.resolve(root: fixture.root, manifestSHA256: pin))
        }
        do {
            let fixture = try SyntheticRuntime(), pin = try fixture.write()
            try FileManager.default.createDirectory(at: fixture.root.appendingPathComponent("unlisted-empty"), withIntermediateDirectories: false)
            XCTAssertThrowsError(try BundledRuntime.resolve(root: fixture.root, manifestSHA256: pin))
        }
    }

    func testSymlinkAndHardlinkCannotBorrowAnInventoriedHash() throws {
        for hard in [false, true] {
            let fixture = try SyntheticRuntime(), pin = try fixture.write()
            let original = fixture.root.appendingPathComponent("certs/cacert.pem")
            let external = fixture.root.deletingLastPathComponent().appendingPathComponent("jarvis-runtime-external-" + UUID().uuidString)
            try fixture.contents["certs/cacert.pem"]!.write(to: external, options: .withoutOverwriting)
            defer { try? FileManager.default.removeItem(at: external) }
            try FileManager.default.removeItem(at: original)
            if hard { XCTAssertEqual(link(external.path, original.path), 0) }
            else { try FileManager.default.createSymbolicLink(at: original, withDestinationURL: external) }
            XCTAssertThrowsError(try BundledRuntime.resolve(root: fixture.root, manifestSHA256: pin))
        }
    }

    func testExecutablePermissionOrReplacedPhysicalRootInvalidatesPriorAdmission() throws {
        let fixture = try SyntheticRuntime(), runtime = try fixture.resolve()
        XCTAssertEqual(chmod(runtime.python, 0o644), 0)
        XCTAssertThrowsError(try runtime.revalidate())
        XCTAssertEqual(chmod(runtime.python, 0o755), 0)
        try runtime.revalidate()
        let retired = fixture.root.deletingLastPathComponent().appendingPathComponent("jarvis-runtime-retired-" + UUID().uuidString)
        try FileManager.default.moveItem(at: fixture.root, to: retired)
        defer { try? FileManager.default.removeItem(at: retired) }
        try FileManager.default.copyItem(at: retired, to: fixture.root)
        _ = try BundledRuntime.resolve(root: fixture.root, manifestSHA256: runtime.manifestSHA256)
        XCTAssertThrowsError(try runtime.revalidate(), "Identical bytes in a different physical root need a fresh admission")
    }

    func testManifestIsCanonicalAndRejectsDuplicateKeysInsteadOfChoosingLast() throws {
        let fixture = try SyntheticRuntime()
        let canonical = try SyntheticRuntime.encode(fixture.manifest())
        _ = try BundledRuntimeManifest(canonical)
        XCTAssertThrowsError(try BundledRuntimeManifest(canonical + Data([10])))
        let text = try XCTUnwrap(String(data: canonical, encoding: .utf8))
        let duplicated = text.replacingOccurrences(of: "{\"codex_version\":", with: "{\"schema\":\"ignored\",\"codex_version\":")
        XCTAssertNotEqual(duplicated, text)
        XCTAssertThrowsError(try BundledRuntimeManifest(Data(duplicated.utf8)))
    }

    func testMalformedEntriesInventoriesAndComponentProvenanceAreRejected() throws {
        let fixture = try SyntheticRuntime()
        let base = fixture.manifest()
        for key in ["target", "minimum_macos", "python_version", "codex_version", "source_commit", "schema"] {
            var bad = base; bad[key] = .string("different")
            XCTAssertThrowsError(try BundledRuntimeManifest(SyntheticRuntime.encode(bad)), key)
        }
        for path in ["../python", "/usr/bin/python3", "python//bin/python3.11", "python/bin/../python3.11"] {
            var bad = base, entries = try XCTUnwrap(base["entrypoints"]?.object)
            entries["python"] = .string(path); bad["entrypoints"] = .object(entries)
            XCTAssertThrowsError(try BundledRuntimeManifest(SyntheticRuntime.encode(bad)), path)
        }
        var duplicate = base, rows = try XCTUnwrap(base["files"]?.array)
        rows.append(try XCTUnwrap(rows.first)); duplicate["files"] = .array(rows)
        XCTAssertThrowsError(try BundledRuntimeManifest(SyntheticRuntime.encode(duplicate)))
        for invalid in [RPCValue.bool(true), .number(-1), .number(1.5), .number(Double(512 * 1024 * 1024 + 1))] {
            var bad = base, values = try XCTUnwrap(base["files"]?.array), row = try XCTUnwrap(values[0].object)
            row["bytes"] = invalid; values[0] = .object(row); bad["files"] = .array(values)
            XCTAssertThrowsError(try BundledRuntimeManifest(SyntheticRuntime.encode(bad)))
        }
        for origin in ["http://example.invalid/archive", "https://person@example.invalid/archive", "https://example.invalid/archive?secret=no", "https://example.invalid/archive#fragment"] {
            var bad = base, components = try XCTUnwrap(base["components"]?.array), component = try XCTUnwrap(components[0].object)
            component["origin"] = .string(origin); components[0] = .object(component); bad["components"] = .array(components)
            XCTAssertThrowsError(try BundledRuntimeManifest(SyntheticRuntime.encode(bad)))
        }
        var caseConflict = base, conflictingRows = try XCTUnwrap(base["files"]?.array)
        let original = try XCTUnwrap(conflictingRows.first?.object)
        var conflicting = original; conflicting["path"] = .string(try XCTUnwrap(original["path"]?.string).uppercased())
        conflictingRows.append(.object(conflicting))
        conflictingRows.sort { ($0["path"].string ?? "") < ($1["path"].string ?? "") }
        caseConflict["files"] = .array(conflictingRows)
        XCTAssertThrowsError(try BundledRuntimeManifest(SyntheticRuntime.encode(caseConflict)))
    }

    func testBundledPlanCannotSwapRuntimePathsAndUsesPrivateEnvironment() throws {
        let fixture = try SyntheticRuntime(), runtime = try fixture.resolve()
        let state = fixture.root.deletingLastPathComponent().appendingPathComponent("jarvis-bundled-state-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: state, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: state) }
        func options(python: String? = nil, source: String? = nil, codex: String? = nil) -> LaunchOptions {
            LaunchOptions(python: python ?? runtime.python, source: source ?? runtime.source, dependencies: runtime.dependencies,
                          state: state.path, codex: codex ?? runtime.codex, bundledRuntime: runtime, distributionMode: .selfContained)
        }
        let plan = try ServiceLaunchPlan(options(), inheritedEnvironment: ["HOME": "/synthetic-owner", "PATH": "/synthetic-external/bin", "PYTHONPATH": "/synthetic-injection", "DYLD_LIBRARY_PATH": "/synthetic-injection", "SSL_CERT_FILE": "/synthetic-certificate"])
        XCTAssertEqual(plan.executable.path, runtime.python)
        XCTAssertEqual(plan.environment["HOME"], state.appendingPathComponent("runtime-home").path)
        XCTAssertEqual(plan.environment["TMPDIR"], state.appendingPathComponent("tmp").path + "/")
        XCTAssertEqual(plan.environment["SSL_CERT_FILE"], runtime.certificateBundle)
        XCTAssertNil(plan.environment["PYTHONPATH"]); XCTAssertNil(plan.environment["DYLD_LIBRARY_PATH"])
        XCTAssertFalse(plan.environment["PATH"]?.contains("synthetic-external") == true)
        XCTAssertTrue(plan.arguments.contains("--bundled-runtime-root")); XCTAssertTrue(plan.arguments.contains(runtime.manifestSHA256))
        XCTAssertFalse(plan.arguments.contains("--parent-watch-stdin"), "Only the actual owned child creates a lifetime pipe")
        XCTAssertThrowsError(try ServiceLaunchPlan(options(python: "/usr/bin/python3")))
        XCTAssertThrowsError(try ServiceLaunchPlan(options(source: "/synthetic-external/core")))
        XCTAssertThrowsError(try ServiceLaunchPlan(options(codex: "/synthetic-external/codex")))
    }

    func testProductSelectionNeverFallsBackToExplicitDevelopmentArguments() throws {
        let dev = ["--python", "/synthetic-python", "--source", "/synthetic-core", "--state", "/private/tmp/synthetic-state", "--codex", "/synthetic-codex"]
        for channel in ["self-contained", "self-contained-qualification"] {
            XCTAssertThrowsError(try LaunchOptions.resolve(dev, channel: channel, runtimePresent: true, hasManifestPin: true,
                runtime: { XCTFail("No runtime read before rejecting overrides"); throw BundledRuntimeFailure.missing },
                profile: { _ in XCTFail("No profile access"); throw BundledRuntimeFailure.missing }))
            XCTAssertThrowsError(try LaunchOptions.resolve([], channel: channel, runtimePresent: false, hasManifestPin: false,
                runtime: { throw BundledRuntimeFailure.missing }, profile: { _ in XCTFail("No fallback profile"); throw RPCFailure.configuration }))
        }
        XCTAssertThrowsError(try LaunchOptions.resolve(dev, channel: "development", runtimePresent: true, hasManifestPin: true,
            runtime: { throw RPCFailure.configuration }, profile: { _ in throw RPCFailure.configuration }))
        let parsed = try LaunchOptions.resolve(dev, channel: "development", runtimePresent: false, hasManifestPin: false,
            runtime: { XCTFail(); throw RPCFailure.configuration }, profile: { _ in XCTFail(); throw RPCFailure.configuration })
        XCTAssertEqual(parsed.distributionMode, .development)
        XCTAssertNil(parsed.bundledRuntime)
    }

    func testQualificationProfileOnlyUsesExactPrivateSyntheticDirectories() throws {
        let fixture = try SyntheticRuntime()
        let state = fixture.root.appendingPathComponent("state"), provider = fixture.root.appendingPathComponent("provider")
        for path in [state, provider] { try FileManager.default.createDirectory(at: path, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700]) }
        let raw: [String: RPCValue] = ["format": .string("jarvis.qualification-profile.v1"), "state": .string(state.path), "provider_state": .string(provider.path)]
        let admitted = try LaunchOptions.qualificationProfile(SyntheticRuntime.encode(raw))
        XCTAssertEqual(admitted.state, state.path); XCTAssertEqual(admitted.provider, provider.path)
        for path in ["/synthetic-owner/profile", fixture.root.path + "/../state", state.path + "/", state.path.replacingOccurrences(of: "/private/tmp/", with: "/tmp/")] {
            var bad = raw; bad["state"] = .string(path)
            XCTAssertThrowsError(try LaunchOptions.qualificationProfile(SyntheticRuntime.encode(bad)))
        }
        var bad = raw; bad["python"] = .string("/external/python")
        XCTAssertThrowsError(try LaunchOptions.qualificationProfile(SyntheticRuntime.encode(bad)))
        XCTAssertEqual(chmod(state.path, 0o755), 0)
        XCTAssertThrowsError(try LaunchOptions.qualificationProfile(SyntheticRuntime.encode(raw)))
    }

    func testPrincipalProfileIsDistinctAndDoesNotChangePermissionsOfExistingUnsafeDirectory() throws {
        let fixture = try SyntheticRuntime()
        let paths = try LaunchOptions.principalProfile(applicationSupport: fixture.root)
        XCTAssertEqual(paths.state, fixture.root.appendingPathComponent("Jarvis Desktop/Perfiles/Principal").path)
        XCTAssertEqual(paths.provider, URL(fileURLWithPath: paths.state).appendingPathComponent("provider").path)
        XCTAssertEqual(chmod(paths.state, 0o755), 0)
        XCTAssertThrowsError(try LaunchOptions.principalProfile(applicationSupport: fixture.root))
        var info = stat(); XCTAssertEqual(lstat(paths.state, &info), 0)
        XCTAssertEqual(info.st_mode & 0o777, 0o755, "No automatic chmod of an existing, unadmitted profile")
    }
}
