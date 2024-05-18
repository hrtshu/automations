import Foundation
import IOKit.usb

class USBWatcher {
    private var ioKitNotificationPort: IONotificationPortRef?
    private var ioKitRunLoopSource: CFRunLoopSource?
    private var addedIter: io_iterator_t = 0

    init() {
        setupUSBMatching()
    }

    deinit {
        IOObjectRelease(addedIter)
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

        let callback: IOServiceMatchingCallback = { (userData, iterator) in
            var deviceInfoList: [String] = []
            while case let device = IOIteratorNext(iterator), device != IO_OBJECT_NULL {
                // Get the device's vendor ID
                var vendorID: Int32 = 0
                let vendorIDResult = IORegistryEntryGetParentEntry(device, kIOServicePlane, &vendorID)
                if vendorIDResult == KERN_SUCCESS {
                    let vendorIDCFNumber = IORegistryEntryCreateCFProperty(device, String(kUSBVendorID) as CFString, kCFAllocatorDefault, 0)
                    if let vendorIDNumber = vendorIDCFNumber?.takeUnretainedValue() as? NSNumber {
                        vendorID = vendorIDNumber.int32Value
                    }
                }

                // Get the device's product ID
                var productID: Int32 = 0
                let productIDResult = IORegistryEntryGetParentEntry(device, kIOServicePlane, &productID)
                if productIDResult == KERN_SUCCESS {
                    let productIDCFNumber = IORegistryEntryCreateCFProperty(device, String(kUSBProductID) as CFString, kCFAllocatorDefault, 0)
                    if let productIDNumber = productIDCFNumber?.takeUnretainedValue() as? NSNumber {
                        productID = productIDNumber.int32Value
                    }
                }

                let deviceInfo = "\(String(format: "%04X", vendorID)):\(String(format: "%04X", productID))"
                deviceInfoList.append(deviceInfo)

                IOObjectRelease(device)
            }
            let joinedDeviceInfo = deviceInfoList.joined(separator: ",")
            NSLog("added \(joinedDeviceInfo)")

            DispatchQueue.global().async {
                let fileManager = FileManager.default
                let homeDirectory = FileManager.default.homeDirectoryForCurrentUser
                // TODO: scriptPathをコマンドライン引数で指定できるようにする
                let scriptPath = homeDirectory.appendingPathComponent("automations/device_event_hooks/usb_add.sh").path

                if fileManager.isExecutableFile(atPath: scriptPath) {
                    let task = Process()
                    task.launchPath = scriptPath
                    task.arguments = [joinedDeviceInfo]

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

        let result = IOServiceAddMatchingNotification(
            ioKitNotificationPort,
            kIOFirstMatchNotification,
            matchingDict,
            callback,
            nil,
            &addedIter
        )

        if result == kIOReturnSuccess {
            callback(nil, addedIter) // Handle existing devices.
        }
    }
}

NSLog("started usb_watcher")
let watcher = USBWatcher()
RunLoop.current.run()