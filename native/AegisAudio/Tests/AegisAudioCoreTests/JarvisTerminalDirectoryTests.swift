import Foundation
import Testing
@testable import AegisAudioCore

@Test func terminalDirectorySupportsConsecutiveLaunchesWithoutLosingExistingFiles() throws {
    let manager = FileManager.default
    let workspace = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    try manager.createDirectory(at: workspace, withIntermediateDirectories: false,
                                attributes: [.posixPermissions: 0o700])
    defer { try? manager.removeItem(at: workspace) }
    let first = try JarvisTerminalCommand.prepareDirectory(in: workspace)
    let pendingCommand = first.appendingPathComponent("pending.command")
    try Data("pending".utf8).write(to: pendingCommand)
    let second = try JarvisTerminalCommand.prepareDirectory(in: workspace)
    #expect(first == second)
    #expect(try Data(contentsOf: pendingCommand) == Data("pending".utf8))
}

@Test func terminalDirectoryRejectsUnsafeExistingPermissionsAndSymbolicLinks() throws {
    let manager = FileManager.default
    let workspace = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
    try manager.createDirectory(at: workspace, withIntermediateDirectories: false,
                                attributes: [.posixPermissions: 0o700])
    defer { try? manager.removeItem(at: workspace) }
    let directory = workspace.appendingPathComponent("Terminal", isDirectory: true)
    try manager.createDirectory(at: directory, withIntermediateDirectories: false,
                                attributes: [.posixPermissions: 0o755])
    #expect(throws: BundledDaemonSupervisorError.unsafeWorkspace) {
        try JarvisTerminalCommand.prepareDirectory(in: workspace)
    }
    let attributes = try manager.attributesOfItem(atPath: directory.path)
    #expect((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o755)
    try manager.removeItem(at: directory)
    let target = workspace.appendingPathComponent("other", isDirectory: true)
    try manager.createDirectory(at: target, withIntermediateDirectories: false,
                                attributes: [.posixPermissions: 0o700])
    try manager.createSymbolicLink(at: directory, withDestinationURL: target)
    #expect(throws: BundledDaemonSupervisorError.unsafeWorkspace) {
        try JarvisTerminalCommand.prepareDirectory(in: workspace)
    }
}
