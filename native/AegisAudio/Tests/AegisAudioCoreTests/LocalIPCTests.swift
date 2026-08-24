import Foundation
import Testing
@testable import AegisAudioCore

@Test func ipcCanonicalJSONAndHMACMatchPythonProtocolVector() throws {
    let transcript: [String: Any] = [
        "schema_version": "1.0",
        "type": "speech.transcript",
        "capture_id": "fedcba98-7654-3210-fedc-ba9876543210",
        "sequence": 4,
        "text": "Analiza el sistema",
        "locale_identifier": "es-US",
        "duration_milliseconds": 1_500,
        "is_final": true,
        "on_device": true,
        "confidence": 0.9,
    ]
    let body: [String: Any] = [
        "protocol_version": "1.0",
        "request_id": "01234567-89ab-cdef-0123-456789abcdef",
        "method": "voice.submit",
        "timestamp": "2026-08-18T15:00:00.123456+00:00",
        "nonce": "abababababababababababababababab",
        "payload": ["transcript": transcript],
    ]

    let canonical = try LocalIPCClient.canonicalJSON(body)
    let canonicalText = try #require(String(data: canonical, encoding: .utf8))
    #expect(
        canonicalText
            == #"{"method":"voice.submit","nonce":"abababababababababababababababab","payload":{"transcript":{"capture_id":"fedcba98-7654-3210-fedc-ba9876543210","confidence":0.9,"duration_milliseconds":1500,"is_final":true,"locale_identifier":"es-US","on_device":true,"schema_version":"1.0","sequence":4,"text":"Analiza el sistema","type":"speech.transcript"}},"protocol_version":"1.0","request_id":"01234567-89ab-cdef-0123-456789abcdef","timestamp":"2026-08-18T15:00:00.123456+00:00"}"#
    )
    let tag = try LocalIPCClient.authenticationTag(
        body: body,
        secret: Data(repeating: 0x11, count: 32)
    )
    #expect(tag == "3639f66a0b2b33bbd2066f31cbb6871a96e5c2d35d7b7f1c2984582e5cf4140d")
}

@Test func ipcSecretParserRejectsNonCanonicalKeychainValues() {
    #expect(MacOSIPCSecretStore.decodeCanonicalSecret(String(repeating: "11", count: 32)) != nil)
    #expect(MacOSIPCSecretStore.decodeCanonicalSecret(String(repeating: "AA", count: 32)) == nil)
    #expect(MacOSIPCSecretStore.decodeCanonicalSecret("11\n" + String(repeating: "11", count: 31)) == nil)
    #expect(MacOSIPCSecretStore.decodeCanonicalSecret("short") == nil)
}

@Test func ipcClientRejectsUnsafeConfigurationBeforeOpeningSocket() {
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try LocalIPCClient(socketPath: "relative.sock", secret: Data(repeating: 0x11, count: 32))
    }
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try LocalIPCClient(socketPath: "/tmp/aegis.sock", secret: Data(repeating: 0x11, count: 31))
    }
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try LocalIPCClient(
            socketPath: "/tmp/aegis.sock",
            secret: Data(repeating: 0x11, count: 32),
            maxFrameBytes: 2_048
        )
    }
}

@Test func ipcTimestampMatchesPythonAwareDatetimeCanonicalization() {
    let date = Date(timeIntervalSince1970: 1_776_526_400.123456)
    #expect(LocalIPCClient.formatTimestamp(date).hasSuffix(".123456+00:00"))
    let wholeSecond = Date(timeIntervalSince1970: 0)
    #expect(LocalIPCClient.formatTimestamp(wholeSecond) == "1970-01-01T00:00:00+00:00")
}

@Test func ipcPublicEventsExposeOnlyBoundedOperationalFields() throws {
    let requestID = UUID(uuidString: "01234567-89ab-cdef-0123-456789abcdef")!
    let health = try #require(
        IPCHealthEvent(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: ["status": "ok", "ignored": "private"],
                errorCode: nil
            )
        )
    )
    let submission = try #require(
        VoiceSubmissionEvent(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: [
                    "job_id": "fedcba98-7654-3210-fedc-ba9876543210",
                    "status": "queued",
                    "result": "must-not-be-forwarded",
                ],
                errorCode: nil
            )
        )
    )
    let healthJSON = try #require(
        JSONSerialization.jsonObject(with: JSONEncoder().encode(health)) as? [String: Any]
    )
    let submissionJSON = try #require(
        JSONSerialization.jsonObject(with: JSONEncoder().encode(submission)) as? [String: Any]
    )

    #expect(healthJSON["ignored"] == nil)
    #expect(submissionJSON["result"] == nil)
    #expect(submissionJSON["job_id"] as? String == "FEDCBA98-7654-3210-FEDC-BA9876543210")
}

@Test func ipcConversationEventAcceptsOnlyAValidCreatedConversation() throws {
    let requestID = UUID(uuidString: "01234567-89ab-cdef-0123-456789abcdef")!
    let conversationID = UUID(uuidString: "fedcba98-7654-3210-fedc-ba9876543210")!
    let event = try #require(
        IPCConversationEvent(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: [
                    "conversation_id": conversationID.uuidString.lowercased(),
                    "title": "ignored",
                ],
                errorCode: nil
            )
        )
    )
    #expect(event.conversationID == conversationID)

    let invalid = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: ["conversation_id": "not-a-uuid"],
        errorCode: nil
    )
    let rejected = LocalIPCResponse(
        requestID: requestID,
        ok: false,
        payload: ["conversation_id": conversationID.uuidString],
        errorCode: "conversation_capacity_reached"
    )
    #expect(IPCConversationEvent(response: invalid) == nil)
    #expect(IPCConversationEvent(response: rejected) == nil)
}

@Test func ipcSecurityStatusAcceptsOnlyKnownIntegrityStates() throws {
    let requestID = UUID(uuidString: "01234567-89ab-cdef-0123-456789abcdef")!
    let intact = try #require(
        IPCSecurityStatusEvent(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: ["state": "intact", "details": "must-not-be-forwarded"],
                errorCode: nil
            )
        )
    )
    #expect(intact.integrity == .intact)

    let compromised = try #require(
        IPCSecurityStatusEvent(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: ["state": "compromised"],
                errorCode: nil
            )
        )
    )
    #expect(compromised.integrity == .compromised)

    let unknown = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: ["state": "degraded"],
        errorCode: nil
    )
    #expect(IPCSecurityStatusEvent(response: unknown) == nil)
}

@Test func ipcSwarmActivityAcceptsOnlyBoundedUniqueRoles() throws {
    let requestID = UUID(uuidString: "01234567-89ab-cdef-0123-456789abcdef")!
    let event = try #require(
        IPCSwarmActivityEvent(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: [
                    "agents": [
                        ["role": "router", "active_jobs": 1],
                        ["role": "code_security", "active_jobs": 2],
                    ],
                    "request_id": "must-not-be-forwarded",
                ],
                errorCode: nil
            )
        )
    )
    #expect(event.agents.map(\.role) == [.router, .codeSecurity])
    #expect(event.agents.map(\.activeJobs) == [1, 2])

    let duplicate = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: [
            "agents": [
                ["role": "vision", "active_jobs": 1],
                ["role": "vision", "active_jobs": 1],
            ],
        ],
        errorCode: nil
    )
    let invalidCount = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: ["agents": [["role": "omni", "active_jobs": 0]]],
        errorCode: nil
    )
    #expect(IPCSwarmActivityEvent(response: duplicate) == nil)
    #expect(IPCSwarmActivityEvent(response: invalidCount) == nil)
}

@Test func ipcSwarmUpdateRequiresVersionedBoundedPayload() throws {
    let requestID = UUID(uuidString: "01234567-89ab-cdef-0123-456789abcdef")!
    let update = try #require(
        IPCSwarmActivityUpdate(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: [
                    "version": 7,
                    "changed": true,
                    "agents": [["role": "planner", "active_jobs": 1]],
                    "job_id": "must-not-be-forwarded",
                ],
                errorCode: nil
            )
        )
    )
    #expect(update.version == 7)
    #expect(update.changed)
    #expect(update.agents.map(\.role) == [.planner])

    let invalidVersion = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: ["version": -1, "changed": false, "agents": []],
        errorCode: nil
    )
    let missingChange = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: ["version": 1, "agents": []],
        errorCode: nil
    )
    #expect(IPCSwarmActivityUpdate(response: invalidVersion) == nil)
    #expect(IPCSwarmActivityUpdate(response: missingChange) == nil)
}

@Test func ipcSwarmWaitRejectsInvalidBoundsBeforeSocketAccess() throws {
    let client = try LocalIPCClient(
        socketPath: "/tmp/does-not-exist.sock",
        secret: Data(repeating: 0x11, count: 32)
    )
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try client.waitForSwarmActivity(afterVersion: -1)
    }
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try client.waitForSwarmActivity(afterVersion: 0, timeoutMilliseconds: 20_001)
    }
    #expect(throws: LocalIPCError.socketUnavailable) {
        try client.waitForSwarmActivity(afterVersion: 0, timeoutMilliseconds: 100)
    }
}

@Test func ipcSpeechArtifactAcceptsOnlyBoundedDigestBoundFiles() throws {
    let requestID = UUID(uuidString: "01234567-89ab-cdef-0123-456789abcdef")!
    let token = String(repeating: "a", count: 32)
    let valid = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: [
            "token": token,
            "file_name": "jarvis-tts-\(token).wav",
            "sha256": String(repeating: "b", count: 64),
            "byte_count": 44,
            "text": "must-not-be-forwarded",
        ],
        errorCode: nil
    )
    let event = try #require(IPCSpeechArtifactEvent(response: valid))
    #expect(event.token == token)
    #expect(event.fileName == "jarvis-tts-\(token).wav")
    #expect(event.byteCount == 44)

    var wrongFile = valid.payload
    wrongFile["file_name"] = "jarvis-tts-\(String(repeating: "c", count: 32)).wav"
    var wrongDigest = valid.payload
    wrongDigest["sha256"] = "short"
    var oversized = valid.payload
    oversized["byte_count"] = IPCSpeechArtifactEvent.maximumAudioBytes + 1

    #expect(IPCSpeechArtifactEvent(response: LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: wrongFile,
        errorCode: nil
    )) == nil)
    #expect(IPCSpeechArtifactEvent(response: LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: wrongDigest,
        errorCode: nil
    )) == nil)
    #expect(IPCSpeechArtifactEvent(response: LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: oversized,
        errorCode: nil
    )) == nil)
}

@Test func ipcSpeechMethodsRejectInvalidInputBeforeSocketAccess() throws {
    let client = try LocalIPCClient(
        socketPath: "/tmp/does-not-exist.sock",
        secret: Data(repeating: 0x11, count: 32)
    )
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try client.synthesizeSpeech("   ")
    }
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try client.synthesizeSpeech(String(repeating: "x", count: 2_001))
    }
    #expect(throws: LocalIPCError.socketUnavailable) {
        try client.synthesizeSpeech("Sistemas en línea.")
    }
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try client.releaseSpeechArtifact("short")
    }
}

@Test func ipcJobStatusRequiresConsistentBoundedTerminalFields() throws {
    let requestID = UUID(uuidString: "01234567-89ab-cdef-0123-456789abcdef")!
    let jobID = UUID(uuidString: "fedcba98-7654-3210-fedc-ba9876543210")!
    let completed = try #require(
        IPCJobStatusEvent(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: [
                    "job_id": jobID.uuidString,
                    "status": "completed",
                    "result": "respuesta:nativa",
                ],
                errorCode: nil
            )
        )
    )
    #expect(completed.jobID == jobID)
    #expect(completed.state == .completed)
    #expect(completed.result == "respuesta:nativa")

    let queued = try #require(
        IPCJobStatusEvent(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: [
                    "job_id": jobID.uuidString,
                    "status": "queued",
                    "result": NSNull(),
                    "error_code": NSNull(),
                ],
                errorCode: nil
            )
        )
    )
    #expect(queued.state == .queued)
    #expect(queued.result == nil)

    let oversized = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: [
            "job_id": jobID.uuidString,
            "status": "completed",
            "result": String(repeating: "x", count: IPCJobStatusEvent.maximumResultBytes + 1),
        ],
        errorCode: nil
    )
    let inconsistent = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: [
            "job_id": jobID.uuidString,
            "status": "running",
            "result": "prematuro",
        ],
        errorCode: nil
    )
    #expect(IPCJobStatusEvent(response: oversized) == nil)
    #expect(IPCJobStatusEvent(response: inconsistent) == nil)
}

@Test func ipcJobStatusParsesOnlyExactPendingConfirmation() throws {
    let requestID = UUID(uuidString: "01234567-89ab-cdef-0123-456789abcdef")!
    let jobID = UUID(uuidString: "fedcba98-7654-3210-fedc-ba9876543210")!
    let confirmation: [String: Any] = [
        "call_digest": String(repeating: "a", count: 64),
        "tool_name": "network_discover_hosts",
        "summary": "Sondeo TCP en 127.0.0.1/32; puertos 443",
        "expires_at": "2026-08-19T12:02:00Z",
    ]
    let pending = try #require(
        IPCJobStatusEvent(
            response: LocalIPCResponse(
                requestID: requestID,
                ok: true,
                payload: [
                    "job_id": jobID.uuidString,
                    "status": "awaiting_confirmation",
                    "confirmation": confirmation,
                ],
                errorCode: nil
            )
        )
    )

    #expect(pending.state == .awaitingConfirmation)
    #expect(pending.confirmation?.callDigest == String(repeating: "a", count: 64))
    #expect(pending.confirmation?.toolName == "network_discover_hosts")
    #expect(pending.confirmation?.summary == confirmation["summary"] as? String)

    let missing = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: ["job_id": jobID.uuidString, "status": "awaiting_confirmation"],
        errorCode: nil
    )
    var invalidConfirmation = confirmation
    invalidConfirmation["call_digest"] = "short"
    let invalid = LocalIPCResponse(
        requestID: requestID,
        ok: true,
        payload: [
            "job_id": jobID.uuidString,
            "status": "awaiting_confirmation",
            "confirmation": invalidConfirmation,
        ],
        errorCode: nil
    )
    #expect(IPCJobStatusEvent(response: missing) == nil)
    #expect(IPCJobStatusEvent(response: invalid) == nil)
}

@Test func ipcApprovalRejectsInvalidDigestBeforeSocketAccess() throws {
    let client = try LocalIPCClient(
        socketPath: "/tmp/does-not-exist.sock",
        secret: Data(repeating: 0x11, count: 32)
    )
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try client.approveJob(UUID(), callDigest: "short")
    }
}

@Test func strictTranscriptDecoderRejectsRemoteAndExtraFields() throws {
    let valid = Data(
        #"{"schema_version":"1.0","type":"speech.transcript","capture_id":"01234567-89ab-cdef-0123-456789abcdef","sequence":1,"text":"Revisa el sistema","locale_identifier":"es-US","duration_milliseconds":900,"is_final":true,"on_device":true}"#.utf8
    )
    let decoded = try SpeechTranscriptEvent.decodeStrictJSON(valid)
    #expect(decoded.isFinal)
    #expect(decoded.onDevice)

    let remote = Data(String(data: valid, encoding: .utf8)!.replacingOccurrences(
        of: #""on_device":true"#,
        with: #""on_device":false"#
    ).utf8)
    #expect(throws: DecodingError.self) {
        try SpeechTranscriptEvent.decodeStrictJSON(remote)
    }

    let extra = Data(String(data: valid, encoding: .utf8)!.dropLast().utf8)
        + Data(#", "audio":"forbidden"}"#.utf8)
    #expect(throws: DecodingError.self) {
        try SpeechTranscriptEvent.decodeStrictJSON(extra)
    }
}

@Test func ipcClientRefusesPartialTranscriptBeforeSocketAccess() throws {
    let partial = try #require(
        SpeechTranscriptEvent(
            captureID: UUID(),
            sequence: 1,
            text: "Revisa",
            localeIdentifier: "es-US",
            durationMilliseconds: 200,
            isFinal: false,
            confidence: nil
        )
    )
    let client = try LocalIPCClient(
        socketPath: "/tmp/does-not-exist.sock",
        secret: Data(repeating: 0x11, count: 32)
    )
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try client.submitVoiceTranscript(partial)
    }
}

@Test func ipcClientRejectsInvalidImagePromptBeforeSocketAccess() throws {
    let client = try LocalIPCClient(
        socketPath: "/tmp/does-not-exist.sock",
        secret: Data(repeating: 0x11, count: 32)
    )
    let image = try LocalImageAttachment(
        mediaType: "image/jpeg",
        data: Data([0xFF, 0xD8, 0xFF, 0xD9])
    )

    #expect(throws: LocalIPCError.invalidConfiguration) {
        try client.submitImage(text: "   ", image: image)
    }
    #expect(throws: LocalIPCError.invalidConfiguration) {
        try client.submitImage(text: String(repeating: "x", count: 4_097), image: image)
    }
    #expect(throws: LocalIPCError.socketUnavailable) {
        try client.submitImage(text: "¿Qué riesgos ves?", image: image)
    }
}
