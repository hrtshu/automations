import Cocoa

func displayReconfigurationCallBack(display: CGDirectDisplayID, flags: CGDisplayChangeSummaryFlags, userInfo: UnsafeMutableRawPointer?) {
    let scriptName: String
    if flags.contains(CGDisplayChangeSummaryFlags.addFlag) {
        NSLog("added display \(display)")
        scriptName = "display_added.sh"
    } else if flags.contains(CGDisplayChangeSummaryFlags.removeFlag) {
        NSLog("removed display \(display)")
        scriptName = "display_removed.sh"
    } else {
        return
    }

    DispatchQueue.global().async {
        let fileManager = FileManager.default
        let homeDirectory = FileManager.default.homeDirectoryForCurrentUser
        // TODO: scriptPathをコマンドライン引数で指定できるようにする
        let scriptPath = homeDirectory.appendingPathComponent("automations/device_event_hooks/\(scriptName)").path

        if fileManager.isExecutableFile(atPath: scriptPath) {
            let task = Process()
            task.launchPath = scriptPath

            let pipe: Pipe = Pipe()
            task.standardOutput = pipe
            task.standardError = pipe

            task.launch()
            task.waitUntilExit()
            let exitCode = task.terminationStatus

            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            if let output = String(data: data, encoding: .utf8) {
                NSLog("script executed (exit code: \(exitCode)): \(scriptPath)\n\(output)")
            }
        } else {
            NSLog("Error: script is not executable: \(scriptPath)")
        }
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        CGDisplayRegisterReconfigurationCallback(displayReconfigurationCallBack, nil)
    }

    func applicationWillTerminate(_ notification: Notification) {
        CGDisplayRemoveReconfigurationCallback(displayReconfigurationCallBack, nil)
    }
}

NSLog("started display_watcher")

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
