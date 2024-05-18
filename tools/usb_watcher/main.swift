import Foundation
import IOKit.usb

func getDeviceInfoProperty(device: io_object_t, property: String) -> Int32 {
    var value: Int32 = 0
    let result = IORegistryEntryGetParentEntry(device, kIOServicePlane, &value)
    if result == KERN_SUCCESS {
        let cfNumber = IORegistryEntryCreateCFProperty(device, String(property) as CFString, kCFAllocatorDefault, 0)
        if let number = cfNumber?.takeUnretainedValue() as? NSNumber {
            value = number.int32Value
        }
    }
    return value
}

func getDeviceInfo(device: io_object_t) -> String {
    let vendorID = getDeviceInfoProperty(device: device, property: kUSBVendorID)
    let productID = getDeviceInfoProperty(device: device, property: kUSBProductID)
    return "\(String(format: "%04X", vendorID)):\(String(format: "%04X", productID))"
}

func getAllConnectedDevices() -> [String] {
    var deviceInfoList: [String] = []
    let matchingDict: CFMutableDictionary? = IOServiceMatching(kIOUSBDeviceClassName)
    var iter: io_iterator_t = 0
    let result = IOServiceGetMatchingServices(kIOMainPortDefault, matchingDict, &iter)
    if result == KERN_SUCCESS {
        while case let device = IOIteratorNext(iter), device != IO_OBJECT_NULL {
            let deviceInfo = getDeviceInfo(device: device)
            deviceInfoList.append(deviceInfo)
            IOObjectRelease(device)
        }
    }
    return deviceInfoList
}

func handleUSBEvent(iterator: io_iterator_t, eventType: String, previousDeviceInfoList: inout [String]) {
    var changedDeviceInfoList: [String] = []
    while case let device = IOIteratorNext(iterator), device != IO_OBJECT_NULL {
        let deviceInfo = getDeviceInfo(device: device)
        changedDeviceInfoList.append(deviceInfo)
        IOObjectRelease(device)
    }

    // changedDeviceInfoList を補正
    var currentAllDeviceInfoList: [String] = []
    let containZeroDevice = eventType == "removed" && changedDeviceInfoList.contains("0000:0000")
    if containZeroDevice {
        currentAllDeviceInfoList = getAllConnectedDevices()
        let newDeviceInfoList = Array(Set(previousDeviceInfoList).subtracting(Set(currentAllDeviceInfoList)))
        print("\(newDeviceInfoList.joined(separator: ","))")
        changedDeviceInfoList = newDeviceInfoList
    }

    if changedDeviceInfoList.isEmpty {
        return
    }

    executeScript(eventType: eventType, changedDeviceInfoList: changedDeviceInfoList)

    // previousDeviceInfoList を更新
    if containZeroDevice {
        previousDeviceInfoList = currentAllDeviceInfoList
    } else {
        if eventType == "added" {
            previousDeviceInfoList.append(contentsOf: changedDeviceInfoList)
        } else if eventType == "removed" {
            previousDeviceInfoList = Array(Set(previousDeviceInfoList).subtracting(Set(changedDeviceInfoList)))
        }
    }
}

func executeScript(eventType: String, changedDeviceInfoList: [String]) {
    let joinedDeviceInfoList = changedDeviceInfoList.joined(separator: ",")
    NSLog("\(eventType) \(joinedDeviceInfoList)")

    let homeDirectory = FileManager.default.homeDirectoryForCurrentUser
    // TODO: scriptPathをコマンドライン引数で指定できるようにする
    let scriptPath = homeDirectory.appendingPathComponent("automations/device_event_hooks/usb_\(eventType).sh").path

    if FileManager.default.isExecutableFile(atPath: scriptPath) {
        let task = Process()
        task.launchPath = scriptPath
        task.arguments = [joinedDeviceInfoList]

        let pipe: Pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = pipe

        task.launch()

        DispatchQueue.global().async {
            task.waitUntilExit()
            let exitCode = task.terminationStatus

            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            if let output = String(data: data, encoding: .utf8) {
                NSLog("script executed (exit code: \(exitCode)): \(scriptPath)\n\(output)")
            }
        }
    } else {
        NSLog("Error: script is not executable: \(scriptPath)")
    }
}

let serialQueue = DispatchQueue(label: "serialQueue")
var previousDeviceInfoList: [String] = []

class USBWatcher {
    private var ioKitNotificationPort: IONotificationPortRef?
    private var ioKitRunLoopSource: CFRunLoopSource?
    private var addedIter: io_iterator_t = 0
    private var removedIter: io_iterator_t = 0

    init() {
        setupUSBMatching()
    }

    deinit {
        IOObjectRelease(addedIter)
        IOObjectRelease(removedIter)
        if let runLoopSource = ioKitRunLoopSource {
            CFRunLoopRemoveSource(CFRunLoopGetCurrent(), runLoopSource, .commonModes)
        }
        if let notificationPort = ioKitNotificationPort {
            IONotificationPortDestroy(notificationPort)
        }
    }

    private func setupUSBMatching() {
        let matchingDict: CFMutableDictionary? = IOServiceMatching(kIOUSBDeviceClassName)

        ioKitNotificationPort = IONotificationPortCreate(kIOMainPortDefault)
        ioKitRunLoopSource = IONotificationPortGetRunLoopSource(ioKitNotificationPort).takeRetainedValue()

        CFRunLoopAddSource(CFRunLoopGetCurrent(), ioKitRunLoopSource, .commonModes)

        let addedCallback: IOServiceMatchingCallback = { (userData, iterator) in
            serialQueue.async {
                handleUSBEvent(iterator: iterator, eventType: "added", previousDeviceInfoList: &previousDeviceInfoList)
            }
        }

        let removedCallback: IOServiceMatchingCallback = { (userData, iterator) in
            serialQueue.async {
                handleUSBEvent(iterator: iterator, eventType: "removed", previousDeviceInfoList: &previousDeviceInfoList)
            }
        }

        let addedResult = IOServiceAddMatchingNotification(
            ioKitNotificationPort,
            kIOFirstMatchNotification,
            matchingDict,
            addedCallback,
            nil,
            &addedIter
        )

        if addedResult == kIOReturnSuccess {
            addedCallback(nil, addedIter) // Handle existing devices.
        }

        let removedResult = IOServiceAddMatchingNotification(
            ioKitNotificationPort,
            kIOTerminatedNotification,
            matchingDict,
            removedCallback,
            nil,
            &removedIter
        )

        if removedResult == kIOReturnSuccess {
            removedCallback(nil, removedIter) // Handle existing devices.
        }
    }

}

NSLog("started usb_watcher")
let watcher = USBWatcher()
RunLoop.current.run()