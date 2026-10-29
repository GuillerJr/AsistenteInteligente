import DesktopCore
import Foundation
import Darwin
import XCTest
@testable import JarvisApp

final class SelectedMediaFileTests: XCTestCase {
    private func fixture(_ body: (URL) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("jarvis-selected-media-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: directory) }
        try body(directory)
    }
    func testChosenRegularSyntheticFileRetainsExactBytes() throws {
        try fixture { root in
            let url = root.appendingPathComponent("prueba.txt"), data = Data("DOCUMENTO LOCAL ÁRBOL 42".utf8)
            try data.write(to: url)
            XCTAssertEqual(try SelectedMediaFile.read(url), data)
        }
    }
    func testPrivateSyntheticPathIsRejectedEvenWhenExplicitlySelected() throws {
        try fixture { root in
            for name in ["auth.json", ".env", ".env.test", ".zsh_history"] {
                let url = root.appendingPathComponent(name)
                try Data("SÓLO FIXTURE".utf8).write(to: url)
                XCTAssertThrowsError(try SelectedMediaFile.read(url))
            }
        }
    }
    func testSymlinkAndHardlinkDoNotEnterTheAttachmentPipeline() throws {
        try fixture { root in
            let original = root.appendingPathComponent("original.txt")
            try Data("ORIGINAL".utf8).write(to: original)
            let symlink = root.appendingPathComponent("alias.txt")
            try FileManager.default.createSymbolicLink(at: symlink, withDestinationURL: original)
            XCTAssertThrowsError(try SelectedMediaFile.read(symlink))
            let linked = root.appendingPathComponent("enlace.txt")
            try FileManager.default.linkItem(at: original, to: linked)
            XCTAssertThrowsError(try SelectedMediaFile.read(linked))
        }
    }
    func testPrivateParentAndOversizedFilesFailBeforePayloadUse() throws {
        try fixture { root in
            let privateRoot = root.appendingPathComponent(".codex", isDirectory: true)
            try FileManager.default.createDirectory(at: privateRoot, withIntermediateDirectories: false)
            let hidden = privateRoot.appendingPathComponent("dato.txt")
            try Data("SINTÉTICO".utf8).write(to: hidden)
            XCTAssertThrowsError(try SelectedMediaFile.read(hidden))
            let large = root.appendingPathComponent("limite.bin")
            XCTAssertTrue(FileManager.default.createFile(atPath: large.path, contents: nil))
            let handle = try FileHandle(forWritingTo: large)
            try handle.truncate(atOffset: 16 * 1024 * 1024 + 1); try handle.close()
            XCTAssertThrowsError(try SelectedMediaFile.read(large))
        }
    }
    func testSelectedFIFOIsRejectedWithoutWaitingForAWriter() throws {
        try fixture { root in
            let fifo = root.appendingPathComponent("entrada.txt")
            XCTAssertEqual(mkfifo(fifo.path, 0o600), 0)
            XCTAssertThrowsError(try SelectedMediaFile.read(fifo))
        }
    }
    func testPreviewDimensionsAreBoundedBeforeImageOrPageRendering() {
        XCTAssertTrue(NativeMediaPreviewPolicy.image(width: 8000, height: 5000, count: 1))
        for dimensions in [(8001, 5000, 1), (Int.max, Int.max, 1), (0, 1, 1), (1, 1, 0), (1, 1, 10001)] {
            XCTAssertFalse(NativeMediaPreviewPolicy.image(width: dimensions.0, height: dimensions.1, count: dimensions.2))
        }
        XCTAssertTrue(NativeMediaPreviewPolicy.page(CGRect(x: 0, y: 0, width: 612, height: 792)))
        for box in [CGRect(x: 0, y: 0, width: 20001, height: 1), CGRect(x: 0, y: 0, width: 1, height: 0),
                    CGRect(x: CGFloat.infinity, y: 0, width: 1, height: 1), CGRect(x: 0, y: 0, width: CGFloat.nan, height: 1)] {
            XCTAssertFalse(NativeMediaPreviewPolicy.page(box))
        }
    }
}
