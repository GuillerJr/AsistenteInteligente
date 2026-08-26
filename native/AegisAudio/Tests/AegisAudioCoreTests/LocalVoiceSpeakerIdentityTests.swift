import Testing
@testable import AegisAudioCore

@Suite("Local voice speaker identity")
struct LocalVoiceSpeakerIdentityTests {
    @Test("Parses only exact speaker identity questions")
    func commands() {
        for transcript in [
            "¿Quién soy?",
            "Jarvis, ¿me reconoces?",
            "Sabes quién soy",
            "Who am I?",
            "Jarvis, do you recognize me?",
        ] {
            #expect(
                LocalVoiceSpeakerIdentityCommand.parse(transcript) == .identifySpeaker
            )
        }
        for transcript in [
            "Quién soy y qué hora es",
            "Reconoce a la persona de la foto",
            "Quién está hablando en el video",
            "Dime si me reconoces y abre Mail",
        ] {
            #expect(LocalVoiceSpeakerIdentityCommand.parse(transcript) == nil)
        }
    }

    @Test("Formats only an already validated speaker identifier")
    func identifier() {
        #expect(
            LocalVoiceSpeakerIdentityCommand.spokenIdentifier("guillermo_jr")
                == "guillermo jr"
        )
        #expect(LocalVoiceSpeakerIdentityCommand.spokenIdentifier("invitado-1") == "invitado 1")
        #expect(LocalVoiceSpeakerIdentityCommand.spokenIdentifier(nil) == nil)
        #expect(LocalVoiceSpeakerIdentityCommand.spokenIdentifier("background") == nil)
        #expect(LocalVoiceSpeakerIdentityCommand.spokenIdentifier("../owner") == nil)
        #expect(LocalVoiceSpeakerIdentityCommand.spokenIdentifier("Admin Root") == nil)
    }
}
