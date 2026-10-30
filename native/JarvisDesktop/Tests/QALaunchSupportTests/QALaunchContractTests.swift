import Darwin
import Foundation
import XCTest
@testable import QALaunchSupport

final class QALaunchContractTests: XCTestCase {
    func testArgumentFreeQualificationRequiresPinnedPrivateConfigurationAndRechecksIt() throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        let config = try fixture.writeQualification()
        try fixture.writeRequest(arguments: [])
        let admitted = try fixture.admit()
        XCTAssertEqual(admitted.request.arguments, [])
        try admitted.revalidate()
        var data = try Data(contentsOf: config); data.append(10)
        try data.write(to: config)
        XCTAssertThrowsError(try admitted.revalidate())
    }

    func testProductAndQualificationNeverBorrowDevelopmentOverrides() throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        try fixture.writeInfo(additional: ["JarvisDistributionChannel": "self-contained"])
        try fixture.writeRequest(arguments: [])
        XCTAssertThrowsError(try fixture.admit())
        try fixture.writeRequest()
        XCTAssertThrowsError(try fixture.admit())
        _ = try fixture.writeQualification()
        try fixture.writeRequest()
        XCTAssertThrowsError(try fixture.admit(), "Qualification uses its pinned profile, never runtime flags")
    }

    func testQualificationProfileRejectsOutsideScopeUnsafePermissionsAndChangedRuntimePin() throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        _ = try fixture.writeQualification(state: "/Users/not-opened/private-profile")
        try fixture.writeRequest(arguments: [])
        XCTAssertThrowsError(try fixture.admit())
        _ = try fixture.writeQualification()
        XCTAssertEqual(chmod(fixture.root.appendingPathComponent("state").path, 0o755), 0)
        XCTAssertThrowsError(try fixture.admit())
        XCTAssertEqual(chmod(fixture.root.appendingPathComponent("state").path, 0o700), 0)
        _ = try fixture.writeQualification()
        try Data("changed".utf8).write(to: fixture.app.appendingPathComponent("Contents/Resources/JarvisRuntime/runtime-manifest.json"))
        XCTAssertThrowsError(try fixture.admit())
    }

    func testLexicalPrivateTmpAdmissionDoesNotCollapseItsPrefixOrAcceptAliasesAndDotComponents() throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        let request = try QALaunchAdmission.fileURL(from: fixture.requestURL.path, isDirectory: false)
        XCTAssertEqual(request.path, fixture.requestURL.path)
        XCTAssertNoThrow(try fixture.admit())
        for invalid in [fixture.root.path + "/./request.json", fixture.root.path + "/../request.json",
                        fixture.root.path + "//request.json", fixture.requestURL.path.replacingOccurrences(of: "/private/tmp/", with: "/tmp/")] {
            XCTAssertThrowsError(try QALaunchAdmission.fileURL(from: invalid, isDirectory: false)) {
                XCTAssertEqual($0 as? QALaunchIssue, .path)
            }
        }
        XCTAssertEqual(try QALaunchAdmission.fileURL(from: fixture.root.path + "/", isDirectory: true).path, fixture.root.path)
    }

    func testAdmissionRequiresRealNativeMainAndVoiceUsageDescriptions() throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        let admission = try fixture.admit()
        XCTAssertEqual(admission.executableURL, fixture.executable)
        XCTAssertEqual(admission.request.arguments.count, 12)
        try admission.revalidate()
        try fixture.writeInfo(executable: "JarvisQA")
        XCTAssertThrowsError(try fixture.admit()) { XCTAssertEqual($0 as? QALaunchIssue, .executable) }
        try fixture.writeInfo(speech: false)
        XCTAssertThrowsError(try fixture.admit()) { XCTAssertEqual($0 as? QALaunchIssue, .usageDescription) }
        try fixture.writeInfo()
        try fixture.writeBinary(Data("#!/bin/bash\nexec /usr/bin/true\n".utf8))
        try fixture.writeRequest()
        XCTAssertThrowsError(try fixture.admit()) { XCTAssertEqual($0 as? QALaunchIssue, .executable) }
    }

    func testBundleIdentityPathAndReceiptCannotEscapeSyntheticScope() throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        try fixture.writeInfo(bundleID: "com.jarvis.desktop.baseline")
        XCTAssertThrowsError(try fixture.admit()) { XCTAssertEqual($0 as? QALaunchIssue, .bundle) }
        try fixture.writeInfo()
        XCTAssertThrowsError(try QALaunchAdmission.admit(requestURL: fixture.requestURL,
            receiptURL: URL(fileURLWithPath: "/Users/not-opened/receipt.json")))
        let alias = fixture.root.appendingPathComponent("alias.app")
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: fixture.app)
        try fixture.writeRequest(appPath: alias.path)
        XCTAssertThrowsError(try fixture.admit()) { XCTAssertEqual($0 as? QALaunchIssue, .path) }
    }

    func testExecutableHashAndRequestCASRejectChangesWithoutLaunching() throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        let admission = try fixture.admit()
        var changed = try Data(contentsOf: fixture.executable); changed.append(42)
        try fixture.writeBinary(changed)
        XCTAssertThrowsError(try admission.revalidate())
        XCTAssertThrowsError(try fixture.admit()) { XCTAssertEqual($0 as? QALaunchIssue, .executable) }
        try fixture.writeRequest()
        let current = try fixture.admit()
        var request = try JSONSerialization.jsonObject(with: Data(contentsOf: fixture.requestURL)) as! [String: Any]
        request["schema"] = "untrusted"
        try JSONSerialization.data(withJSONObject: request).write(to: fixture.requestURL)
        XCTAssertThrowsError(try current.revalidate()) { XCTAssertEqual($0 as? QALaunchIssue, .changed) }
    }

    func testUnknownSecretsDuplicateFlagsAndNonSyntheticProfileAreRejected() throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        try fixture.writeRequest(environment: fixture.environment.merging(["OPENAI_API_KEY": "do-not-log-test-marker"]) { _, right in right })
        XCTAssertThrowsError(try fixture.admit()) { XCTAssertEqual($0 as? QALaunchIssue, .arguments) }
        var arguments = fixture.arguments; arguments[0] = "--token"
        try fixture.writeRequest(arguments: arguments)
        XCTAssertThrowsError(try fixture.admit())
        arguments = fixture.arguments; arguments[2] = "--python"
        try fixture.writeRequest(arguments: arguments)
        XCTAssertThrowsError(try fixture.admit())
        arguments = fixture.arguments; arguments[7] = "/Users/not-opened/personal-profile"
        try fixture.writeRequest(arguments: arguments)
        XCTAssertThrowsError(try fixture.admit())
    }

    @MainActor func testSuccessfulReturnedApplicationGetsMetadataOnlyReceiptAndCannotReplay() async throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        let moment = Date(timeIntervalSince1970: 1_793_300_000)
        let app = fixture.application(at: moment)
        let process = fixture.process(at: moment)
        let driver = QALaunchFakeDriver(result: .success(app))
        let coordinator = QALaunchCoordinator(driver: driver, observe: { _ in process }, now: { moment })
        let receipt = try await coordinator.launch(requestURL: fixture.requestURL, receiptURL: fixture.receiptURL)
        XCTAssertEqual(driver.calls, 1); XCTAssertEqual(receipt.status, "launched")
        XCTAssertEqual(receipt.launchEffectStatus, "known"); XCTAssertEqual(receipt.process, process)
        XCTAssertFalse(receipt.normalAppQuitClaimed); XCTAssertFalse(receipt.credentialsOrArgumentsLogged)
        let object = try JSONSerialization.jsonObject(with: Data(contentsOf: fixture.receiptURL)) as! [String: Any]
        XCTAssertNil(object["arguments"]); XCTAssertNil(object["environment"]); XCTAssertNil(object["exit_code"])
        XCTAssertNotNil(object["arguments_sha256"]); XCTAssertNotNil(object["environment_sha256"])
        XCTAssertEqual(object["creates_new_application_instance"] as? Bool, true)
        XCTAssertEqual(object["allows_running_application_substitution"] as? Bool, false)
        do {
            _ = try await coordinator.launch(requestURL: fixture.requestURL, receiptURL: fixture.receiptURL)
            XCTFail("El recibo existente no debe autorizar otro arranque")
        } catch { XCTAssertEqual(error as? QALaunchIssue, .receiptExists) }
        XCTAssertEqual(driver.calls, 1)
    }

    @MainActor func testLaunchTimeoutPersistsUnknownAndNeverFallsBackOrRetries() async throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        let driver = QALaunchFakeDriver(result: .failure(QALaunchIssue.timeout))
        let coordinator = QALaunchCoordinator(driver: driver, observe: { _ in nil })
        do {
            _ = try await coordinator.launch(requestURL: fixture.requestURL, receiptURL: fixture.receiptURL)
            XCTFail("La falta de confirmación debe conservar incertidumbre")
        } catch { XCTAssertEqual(error as? QALaunchIssue, .timeout) }
        let receipt = try JSONDecoder().decode(QALaunchReceipt.self, from: Data(contentsOf: fixture.receiptURL))
        XCTAssertEqual(receipt.status, "unknown"); XCTAssertEqual(receipt.errorCode, "timeout")
        XCTAssertNil(receipt.process)
        do { _ = try await coordinator.launch(requestURL: fixture.requestURL, receiptURL: fixture.receiptURL) }
        catch { XCTAssertEqual(error as? QALaunchIssue, .receiptExists) }
        XCTAssertEqual(driver.calls, 1)
    }

    @MainActor func testForeignOrReusedReturnedPIDCannotBePromotedToKnown() async throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        let moment = Date()
        let driver = QALaunchFakeDriver(result: .success(fixture.application(at: moment)))
        let different = QALaunchProcessIdentity(pid: 4321, parentPID: 1, startSeconds: 1, startMicroseconds: 0,
                                               executablePath: "/not-opened/other-program")
        let coordinator = QALaunchCoordinator(driver: driver, observe: { _ in different }, now: { moment })
        do { _ = try await coordinator.launch(requestURL: fixture.requestURL, receiptURL: fixture.receiptURL) }
        catch { XCTAssertEqual(error as? QALaunchIssue, .processIdentity) }
        let receipt = try JSONDecoder().decode(QALaunchReceipt.self, from: Data(contentsOf: fixture.receiptURL))
        XCTAssertEqual(receipt.status, "unknown"); XCTAssertNil(receipt.process)
        XCTAssertEqual(driver.calls, 1)
    }

    @MainActor func testScriptAdmissionFailureDoesNotCallDriverOrPublishReceipt() async throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        try fixture.writeInfo(executable: "JarvisQA")
        let driver = QALaunchFakeDriver(result: .failure(QALaunchIssue.launchServices))
        let coordinator = QALaunchCoordinator(driver: driver)
        do { _ = try await coordinator.launch(requestURL: fixture.requestURL, receiptURL: fixture.receiptURL) }
        catch { XCTAssertEqual(error as? QALaunchIssue, .executable) }
        XCTAssertEqual(driver.calls, 0)
        XCTAssertFalse(FileManager.default.fileExists(atPath: fixture.receiptURL.path))
    }

    @MainActor func testRequestChangedWhileLaunchServicesOpensRetainsUnconfirmedCandidate() async throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        let moment = Date()
        let driver = QALaunchFakeDriver(result: .success(fixture.application(at: moment)), onOpen: {
            var data = try Data(contentsOf: fixture.requestURL); data.append(0x20)
            try data.write(to: fixture.requestURL)
        })
        let coordinator = QALaunchCoordinator(driver: driver, observe: { _ in fixture.process(at: moment) }, now: { moment })
        do {
            _ = try await coordinator.launch(requestURL: fixture.requestURL, receiptURL: fixture.receiptURL)
            XCTFail("Un cambio durante el arranque no puede producir éxito")
        } catch { XCTAssertEqual(error as? QALaunchIssue, .changed) }
        let receipt = try JSONDecoder().decode(QALaunchReceipt.self, from: Data(contentsOf: fixture.receiptURL))
        XCTAssertEqual(receipt.status, "unknown"); XCTAssertNil(receipt.process)
        XCTAssertEqual(receipt.returnedCandidate?.pid, 4321)
        XCTAssertEqual(receipt.returnedCandidate?.executablePath, fixture.executable.path)
        XCTAssertEqual(receipt.returnedCandidate?.independentlyConfirmed, false)
        XCTAssertEqual(driver.calls, 1)
    }

    @MainActor func testReceiptSubstitutionCannotPublishKnownOrLoseReturnedCandidate() async throws {
        let fixture = try QALaunchFixture(); defer { fixture.remove() }
        let moment = Date(), marker = Data("RECIBO SUSTITUIDO SINTÉTICO".utf8)
        let held = fixture.root.appendingPathComponent("reserved-original.json")
        let driver = QALaunchFakeDriver(result: .success(fixture.application(at: moment)), onOpen: {
            try FileManager.default.moveItem(at: fixture.receiptURL, to: held)
            try marker.write(to: fixture.receiptURL)
        })
        let coordinator = QALaunchCoordinator(driver: driver, observe: { _ in fixture.process(at: moment) }, now: { moment })
        do {
            _ = try await coordinator.launch(requestURL: fixture.requestURL, receiptURL: fixture.receiptURL)
            XCTFail("El descriptor antiguo no acredita la ruta sustituida")
        } catch let failure as QALaunchUncertainFailure {
            XCTAssertEqual(failure.issue, .receiptWrite)
            XCTAssertEqual(failure.receipt.status, "unknown")
            XCTAssertEqual(failure.receipt.returnedCandidate?.pid, 4321)
            XCTAssertEqual(failure.receipt.returnedCandidate?.independentlyConfirmed, false)
        }
        XCTAssertEqual(try Data(contentsOf: fixture.receiptURL), marker)
        let reserved = try JSONDecoder().decode(QALaunchReceipt.self, from: Data(contentsOf: held))
        XCTAssertEqual(reserved.status, "requested")
        XCTAssertEqual(driver.calls, 1)
    }

    func testProcessObservationReadsOnlyThisTestProcessKernelIdentity() throws {
        let identity = try XCTUnwrap(QALaunchProcessIdentity.observe(getpid()))
        XCTAssertEqual(identity.pid, getpid()); XCTAssertEqual(identity.parentPID, getppid())
        XCTAssertGreaterThan(identity.startSeconds, 0)
        XCTAssertTrue(identity.executablePath.hasPrefix("/"))
        XCTAssertEqual(QALaunchProcessIdentity.observe(getpid()), identity)
    }
}

@MainActor private final class QALaunchFakeDriver: QALaunchDriver {
    let result: Result<QALaunchApplication, QALaunchIssue>
    let onOpen: (() throws -> Void)?
    private(set) var calls = 0
    init(result: Result<QALaunchApplication, QALaunchIssue>, onOpen: (() throws -> Void)? = nil) {
        self.result = result; self.onOpen = onOpen
    }
    func open(_ admission: QALaunchAdmission) async throws -> QALaunchApplication {
        calls += 1; try onOpen?(); return try result.get()
    }
}

private struct QALaunchFixture {
    let root: URL
    let app: URL
    let executable: URL
    let requestURL: URL
    let receiptURL: URL
    let bundleID = "local.jarvis.qa.synthetic"
    var environment: [String: String] {
        ["HOME": root.appendingPathComponent("home").path, "TMPDIR": root.appendingPathComponent("tmp").path,
         "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"]
    }
    var arguments: [String] {
        ["--python", "/usr/bin/true", "--source", root.appendingPathComponent("source").path,
         "--dependencies", root.appendingPathComponent("dependencies").path,
         "--state", root.appendingPathComponent("state").path, "--codex", "/usr/bin/true",
         "--provider-state", root.appendingPathComponent("provider").path]
    }
    init() throws {
        root = URL(fileURLWithPath: "/private/tmp/jarvis-qa-contract-\(UUID().uuidString)", isDirectory: true)
        app = root.appendingPathComponent("Jarvis.app", isDirectory: true)
        executable = app.appendingPathComponent("Contents/MacOS/Jarvis")
        requestURL = root.appendingPathComponent("request.json"); receiptURL = root.appendingPathComponent("receipt.json")
        for directory in [executable.deletingLastPathComponent()] + ["home", "tmp", "source", "dependencies", "state", "provider"].map({ root.appendingPathComponent($0) }) {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        }
        // Header fixture only: never executed or used to claim native code signing/runtime coverage.
        var header = Data(repeating: 0, count: 32)
        header.replaceSubrange(0..<4, with: [0xcf, 0xfa, 0xed, 0xfe])
        header.replaceSubrange(4..<8, with: [0x0c, 0x00, 0x00, 0x01])
        header.replaceSubrange(12..<16, with: [0x02, 0x00, 0x00, 0x00])
        try writeBinary(header); try writeInfo(); try writeRequest()
    }
    func writeBinary(_ data: Data) throws {
        try data.write(to: executable)
        guard chmod(executable.path, 0o700) == 0 else { throw QALaunchIssue.file }
    }
    func writeInfo(executable: String = "Jarvis", bundleID: String? = nil, speech: Bool = true, additional: [String: Any] = [:]) throws {
        var value: [String: Any] = ["CFBundleIdentifier": bundleID ?? self.bundleID, "CFBundlePackageType": "APPL",
            "CFBundleExecutable": executable, "NSMicrophoneUsageDescription": "Micrófono sintético revisado."]
        if speech { value["NSSpeechRecognitionUsageDescription"] = "Reconocimiento local sintético revisado." }
        value.merge(additional) { _, new in new }
        try PropertyListSerialization.data(fromPropertyList: value, format: .xml, options: 0)
            .write(to: app.appendingPathComponent("Contents/Info.plist"))
    }
    func writeQualification(state: String? = nil) throws -> URL {
        let resources = app.appendingPathComponent("Contents/Resources", isDirectory: true)
        let runtime = resources.appendingPathComponent("JarvisRuntime", isDirectory: true)
        try FileManager.default.createDirectory(at: runtime, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let config = resources.appendingPathComponent("JarvisQualification.json")
        let raw: [String: String] = ["format": "jarvis.qualification-profile.v1", "state": state ?? root.appendingPathComponent("state").path,
                                     "provider_state": root.appendingPathComponent("provider").path]
        let data = try JSONSerialization.data(withJSONObject: raw, options: [.sortedKeys, .withoutEscapingSlashes])
        try data.write(to: config)
        // Contract fixture only, not a complete product manifest or executable.
        let manifest = Data("{}".utf8)
        try manifest.write(to: runtime.appendingPathComponent("runtime-manifest.json"))
        try writeInfo(additional: ["JarvisDistributionChannel": "self-contained-qualification",
                                   "JarvisQualificationConfigurationSHA256": QALaunchAdmission.digest(data),
                                   "JarvisRuntimeManifestSHA256": QALaunchAdmission.digest(manifest)])
        return config
    }
    func writeRequest(appPath: String? = nil, arguments: [String]? = nil, environment: [String: String]? = nil) throws {
        let value = QALaunchRequest(appPath: appPath ?? app.path, expectedBundleID: bundleID,
            expectedExecutableSHA256: QALaunchAdmission.digest(try Data(contentsOf: executable)),
            arguments: arguments ?? self.arguments, environment: environment ?? self.environment)
        try JSONEncoder().encode(value).write(to: requestURL)
    }
    func admit() throws -> QALaunchAdmission { try QALaunchAdmission.admit(requestURL: requestURL, receiptURL: receiptURL) }
    func application(at date: Date) -> QALaunchApplication {
        .init(pid: 4321, bundleID: bundleID, bundleURL: app, executableURL: executable, launchDate: date)
    }
    func process(at date: Date) -> QALaunchProcessIdentity {
        .init(pid: 4321, parentPID: 1, startSeconds: UInt64(date.timeIntervalSince1970), startMicroseconds: 1234, executablePath: executable.path)
    }
    func remove() { try? FileManager.default.removeItem(at: root) }
}
