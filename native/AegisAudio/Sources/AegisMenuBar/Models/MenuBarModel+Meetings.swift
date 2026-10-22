import AegisAudioCore
import AppKit
@preconcurrency import AVFoundation
import Foundation
import Observation
import OSLog
@preconcurrency import Speech
import SwiftUI
import UserNotifications

// Behavior for Meetings; observable storage remains in MenuBarModel.
extension MenuBarModel {
    func toggleMeetingNotes() async {
        if let transcriber = meetingTranscriber {
            meetingTranscriber = nil
            let transcript = await transcriber.stop()
            meetingRecording = false
            let started = meetingStartedAt ?? Date()
            meetingStartedAt = nil
            scheduleWakeWordResume(if: meetingResumesWakeWord)
            guard transcript.count >= 20 else {
                chat.notice = String(localized: "No escuché nada que guardar de la reunión.", bundle: .module)
                return
            }
            let formatter = DateFormatter()
            formatter.dateFormat = "dd/MM HH:mm"
            let stamp = formatter.string(from: started)
            chat.newConversation()
            chat.attachments = ChatAttachment.meetingTranscript(transcript, title: String(localized: "Reunión \(stamp)", bundle: .module))
            logger.info("meeting_notes_submitted")
            _ = await sendChatMessage(
                String(localized: "Estas son las notas de mi reunión del \(stamp). Resúmela: temas, decisiones, ", bundle: .module)
                + String(localized: "tareas con su responsable y fecha si aparecen, y lo que quedó pendiente.", bundle: .module)
            )
            return
        }
        guard userSessionAvailable, !voiceState.isBusy, !chat.isBusy else { return }
        guard let transcriber = MeetingTranscriber() else {
            chat.error = String(localized: "No puedo transcribir en este Mac: revisa los permisos de micrófono y ", bundle: .module)
                + "reconocimiento de voz."
            return
        }
        meetingResumesWakeWord = pauseWakeWordListening()
        do {
            try transcriber.start()
        } catch {
            scheduleWakeWordResume(if: meetingResumesWakeWord)
            chat.error = String(localized: "No pude empezar a grabar la reunión.", bundle: .module)
            return
        }
        meetingTranscriber = transcriber
        meetingStartedAt = Date()
        meetingRecording = true
        logger.info("meeting_notes_started")
    }

    var meetingLiveText: String { meetingTranscriber?.text ?? "" }
}
