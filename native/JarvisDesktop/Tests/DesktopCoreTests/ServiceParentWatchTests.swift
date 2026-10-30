import Darwin
import Foundation
import XCTest
@testable import DesktopCore

final class ServiceParentWatchTests: XCTestCase {
    func testWriterIsNotInheritedAndEOFMeansWriterClosedWithoutAnyHeartbeat() throws {
        let watch = try ServiceParentWatch()
        let writer = watch.pipe.fileHandleForWriting.fileDescriptor
        XCTAssertNotEqual(fcntl(writer, F_GETFD) & FD_CLOEXEC, 0)
        let reader = dup(watch.pipe.fileHandleForReading.fileDescriptor)
        XCTAssertGreaterThanOrEqual(reader, 0)
        defer { Darwin.close(reader) }
        XCTAssertGreaterThanOrEqual(fcntl(reader, F_SETFL, O_NONBLOCK), 0)
        var byte: UInt8 = 0
        XCTAssertEqual(Darwin.read(reader, &byte, 1), -1)
        XCTAssertEqual(errno, EAGAIN, "A live desktop contributes no data to the pipe")
        watch.didLaunch()
        watch.close(); watch.close()
        XCTAssertEqual(Darwin.read(reader, &byte, 1), 0, "Closing only the owned writer makes EOF observable")
    }

    func testDroppingAnUnlaunchedWatchClosesItsWriterAsWell() throws {
        var watch: ServiceParentWatch? = try ServiceParentWatch()
        let reader = dup(try XCTUnwrap(watch).pipe.fileHandleForReading.fileDescriptor)
        XCTAssertGreaterThanOrEqual(reader, 0)
        defer { Darwin.close(reader) }
        watch = nil
        var byte: UInt8 = 0
        XCTAssertEqual(Darwin.read(reader, &byte, 1), 0)
    }
}
