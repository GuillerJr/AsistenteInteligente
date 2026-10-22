import AegisAudioCore
import Foundation

enum JarvisScheduledOperation {
    static func timeout(_ method: String, fallback: TimeInterval) -> TimeInterval {
        ["schedules.delete", "goals.stop", "goals.delete"].contains(method) ? 29 : fallback
    }

    static func errorMessage(_ code: String?) -> String? {
        switch code {
        case "goal_budget_exhausted": return JarvisScheduleLaunchReceipt.goalBudgetMessage
        case "goal_cancellation_failed":
            return String(localized: "No pude confirmar que el objetivo se haya detenido. Actualiza su estado antes de volver a intentarlo.", bundle: .module)
        case "schedule_cancel_pending":
            return String(localized: "La tarea sigue cancelándose. Aún no confirmé su eliminación. Actualiza su estado antes de reintentar.", bundle: .module)
        case "schedule_not_found":
            return String(localized: "Esa tarea ya no existe. Actualiza la lista.", bundle: .module)
        default: return nil
        }
    }
}

/// A successful IPC exchange does not necessarily mean a new run was admitted.
struct JarvisScheduleLaunchReceipt: Decodable {
    let started: Bool
    let start_reason: String?

    init(data: Data) throws {
        self = try JSONDecoder().decode(Self.self, from: data)
        if let start_reason, (start_reason == "started") != started {
            throw LocalIPCError.malformedResponse
        }
    }

    func message(title: String) -> String {
        if started {
            return String(localized: "Solicitud aceptada para «\(title)».", bundle: .module)
        }
        switch start_reason {
        case "already_running":
            return String(localized: "«\(title)» ya tiene una ejecución en curso. No inicié otra.", bundle: .module)
        case "capacity":
            return String(localized: "Jarvis ya está ejecutando el máximo de tareas a la vez. Inténtalo cuando termine alguna.", bundle: .module)
        case "execution_uncertain":
            return String(localized: "No inicié otra ejecución porque no se pudo verificar la anterior. Revisa su chat de resultados.", bundle: .module)
        case "unavailable":
            return String(localized: "El servicio de tareas no está disponible ahora. Vuelve a intentarlo cuando esté listo.", bundle: .module)
        case "not_found":
            return String(localized: "Esa tarea ya no existe. Actualiza la lista.", bundle: .module)
        default:
            return String(localized: "No se inició la tarea. Actualiza su estado antes de reintentar.", bundle: .module)
        }
    }

    static var unconfirmedMessage: String {
        String(localized: "No pude confirmar si la tarea se inició. Actualiza su estado antes de reintentar.", bundle: .module)
    }

    static var goalBudgetMessage: String {
        String(localized: "El objetivo agotó su límite de pasos y no puede reanudarse. Revisa el resultado y crea otro objetivo si necesitas continuar.", bundle: .module)
    }
}

struct JarvisScheduleSaveResult {
    let taskID: String
    let shouldDismiss: Bool
    let requiresReconciliation: Bool
    let message: String?

    init(data: Data, runNow: Bool, existingTaskID: String?) throws {
        struct Saved: Decodable {
            struct TaskID: Decodable { let task_id: UUID; let title: String? }
            let task: TaskID
        }
        let saved = try JSONDecoder().decode(Saved.self, from: data).task
        taskID = saved.task_id.uuidString.lowercased()
        guard existingTaskID == nil || existingTaskID?.lowercased() == taskID else {
            throw LocalIPCError.malformedResponse
        }
        guard runNow else {
            shouldDismiss = true; requiresReconciliation = false; message = nil; return
        }
        let receipt = try? JarvisScheduleLaunchReceipt(data: data)
        requiresReconciliation = receipt == nil
        shouldDismiss = receipt?.started == true
        if shouldDismiss { message = nil }
        else if let receipt {
            message = String(localized: "Tarea guardada.", bundle: .module) + " "
                + receipt.message(title: saved.title ?? String(localized: "Esta tarea", bundle: .module))
        } else {
            message = String(localized: "La tarea quedó guardada, pero no pude confirmar si se inició. Cierra este editor y actualiza las tareas antes de reintentar.", bundle: .module)
        }
    }
}
