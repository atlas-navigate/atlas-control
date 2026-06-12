import Foundation
import CoreBluetooth

private let atlasServiceUUID        = CBUUID(string: "7d2ea28a-3d2b-4f7b-9f10-9e4d4d6a0001")
private let atlasCharacteristicUUID = CBUUID(string: "7d2ea28a-3d2b-4f7b-9f10-9e4d4d6a0002")

/// CoreBluetooth port of the Android `AtlasBleScanner`.
///
/// Workflow: scan → discover → connect → discover service → discover
/// characteristic → read value → parse JSON manifest → return.
final class AtlasBleScanner: NSObject {

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var continuation: CheckedContinuation<AtlasBlePairing?, Never>?
    private var timeoutTask: Task<Void, Never>?
    private let lock = NSLock()        // serializes finish() against the
                                       // CoreBluetooth callback queue and
                                       // the timeout task — without it the
                                       // race could double-resume the
                                       // continuation and crash.

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    /// True once Bluetooth has reported `poweredOn`.  The setup wizard uses
    /// this to disable the scan button when BT is off and surface a hint.
    var isAvailable: Bool { central.state == .poweredOn }

    /// Scan for the Atlas advertising service for at most [timeout] seconds.
    /// Returns the parsed BLE manifest (URLs + hotspot creds) or `nil` on
    /// timeout or error.
    func scan(timeout: TimeInterval = 15) async -> AtlasBlePairing? {
        await withCheckedContinuation { (cont: CheckedContinuation<AtlasBlePairing?, Never>) in
            self.continuation = cont
            self.timeoutTask?.cancel()
            self.timeoutTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                self?.finish(with: nil)
            }
            tryStartScanning()
        }
    }

    private func tryStartScanning() {
        switch central.state {
        case .poweredOn:
            central.scanForPeripherals(withServices: [atlasServiceUUID], options: nil)
        case .unsupported, .unauthorized, .poweredOff:
            finish(with: nil)
        default:
            // .unknown / .resetting — wait for centralManagerDidUpdateState
            break
        }
    }

    private func finish(with pairing: AtlasBlePairing?) {
        lock.lock()
        let cont = continuation
        continuation = nil
        timeoutTask?.cancel()
        timeoutTask = nil
        if central.isScanning { central.stopScan() }
        if let peripheral, peripheral.state != .disconnected {
            central.cancelPeripheralConnection(peripheral)
        }
        peripheral = nil
        lock.unlock()
        cont?.resume(returning: pairing)
    }

    private func parseManifest(_ data: Data) -> AtlasBlePairing? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        let name      = (object["device_name"] as? String) ?? "Atlas"
        let preferred = (object["preferred_url"] as? String) ?? ""
        var candidates: [String] = (object["urls"] as? [String]) ?? []
        if !preferred.isEmpty && !candidates.contains(preferred) {
            candidates.insert(preferred, at: 0)
        }
        let ssid = (object["hotspot_ssid"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        let pw   = (object["hotspot_password"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        return AtlasBlePairing(
            deviceName:      name,
            preferredUrl:    preferred.isEmpty ? (candidates.first ?? "") : preferred,
            candidateUrls:   candidates,
            hotspotSsid:     ssid,
            hotspotPassword: pw
        )
    }
}

// MARK: - CoreBluetooth delegate

extension AtlasBleScanner: CBCentralManagerDelegate, CBPeripheralDelegate {

    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if continuation == nil { return }
        tryStartScanning()
    }

    func centralManager(
        _ central: CBCentralManager,
        didDiscover peripheral: CBPeripheral,
        advertisementData: [String: Any],
        rssi RSSI: NSNumber
    ) {
        central.stopScan()
        self.peripheral = peripheral
        peripheral.delegate = self
        central.connect(peripheral, options: nil)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        peripheral.discoverServices([atlasServiceUUID])
    }

    func centralManager(
        _ central: CBCentralManager,
        didFailToConnect peripheral: CBPeripheral,
        error: Error?
    ) {
        finish(with: nil)
    }

    func centralManager(
        _ central: CBCentralManager,
        didDisconnectPeripheral peripheral: CBPeripheral,
        error: Error?
    ) {
        // If we've already resolved the manifest the continuation is nil and
        // this is a normal post-read disconnect — no-op.  Otherwise treat it
        // as a failure.
        if continuation != nil { finish(with: nil) }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard error == nil,
              let service = peripheral.services?.first(where: { $0.uuid == atlasServiceUUID }) else {
            finish(with: nil); return
        }
        peripheral.discoverCharacteristics([atlasCharacteristicUUID], for: service)
    }

    func peripheral(
        _ peripheral: CBPeripheral,
        didDiscoverCharacteristicsFor service: CBService,
        error: Error?
    ) {
        guard error == nil,
              let characteristic = service.characteristics?.first(where: { $0.uuid == atlasCharacteristicUUID }) else {
            finish(with: nil); return
        }
        peripheral.readValue(for: characteristic)
    }

    func peripheral(
        _ peripheral: CBPeripheral,
        didUpdateValueFor characteristic: CBCharacteristic,
        error: Error?
    ) {
        let pairing = (characteristic.value).flatMap { parseManifest($0) }
        finish(with: pairing)
    }
}
