import Testing
@testable import AegisAudioCore

@Test func buildIdentityAcceptsOnlyBoundedPublicRevisions() {
    #expect(JarvisBuildIdentity.isValid("development"))
    #expect(JarvisBuildIdentity.isValid(String(repeating: "a", count: 40)))
    #expect(!JarvisBuildIdentity.isValid(String(repeating: "A", count: 40)))
    #expect(!JarvisBuildIdentity.isValid(String(repeating: "g", count: 40)))
    #expect(!JarvisBuildIdentity.isValid(String(repeating: "a", count: 39)))
}
