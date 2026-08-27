import Testing
@testable import AegisAudioCore

@Test func ownerPresenceLeaseIsBoundedAndMonotonic() {
    var lease = OwnerPresenceLease()
    #expect(!lease.isAuthorized(at: 1))
    let authorized = lease.authorize(at: 10)
    #expect(authorized)
    #expect(lease.isAuthorized(at: 10))
    #expect(lease.isAuthorized(at: 10 + OwnerPresenceLease.durationSeconds - 0.001))
    #expect(!lease.isAuthorized(at: 10 + OwnerPresenceLease.durationSeconds))
    #expect(!lease.isAuthorized(at: 9))
}

@Test func ownerPresenceLeaseRevokesAndRejectsInvalidClockValues() {
    var lease = OwnerPresenceLease(authorizedAt: 5)
    lease.revoke()
    #expect(!lease.isAuthorized(at: 6))
    let invalidAuthorization = lease.authorize(at: .nan)
    #expect(!invalidAuthorization)
    #expect(!lease.isAuthorized(at: 7))
}
