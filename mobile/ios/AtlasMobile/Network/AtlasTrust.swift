import CryptoKit
import Foundation
import Security

/// How the app trusts an Atlas.
///
/// Every Atlas has its own self-signed TLS key, made on the production line.
/// The app pins SHA-256 of that key's SubjectPublicKeyInfo — the value Atlas
/// publishes as `tls_spki_sha256` and prints (inside the QR code) on its label.
/// After pairing, a connection succeeds only if the server presents exactly
/// that key; the address is irrelevant, so DHCP changes and hotspot/LAN
/// switches never matter, and a different key is a security warning.
enum AtlasTrust {
    /// DER prefix of a SubjectPublicKeyInfo holding an EC P-256 public key.
    private static let p256SpkiHeader: [UInt8] = [
        0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x02, 0x01,
        0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
    ]

    /// base64url(no padding) of SHA-256(SubjectPublicKeyInfo) of the leaf certificate.
    static func spkiPin(_ trust: SecTrust) -> String? {
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first,
              let key = SecCertificateCopyKey(leaf),
              let raw = SecKeyCopyExternalRepresentation(key, nil) as Data?,
              let attrs = SecKeyCopyAttributes(key) as? [CFString: Any],
              (attrs[kSecAttrKeyType] as? String) == (kSecAttrKeyTypeECSECPrimeRandom as String),
              (attrs[kSecAttrKeySizeInBits] as? Int) == 256,
              raw.count == 65
        else { return nil }
        let digest = SHA256.hash(data: Data(p256SpkiHeader) + raw)
        return base64url(Data(digest))
    }

    static func base64url(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// URLSession delegate that accepts only the pinned key (or, with
/// `expectedPin == nil`, records the key of an Atlas we have not met yet —
/// used only to read its public /api/device/info).
final class PinnedSessionDelegate: NSObject, URLSessionDelegate {
    let expectedPin: String?
    private let lock = NSLock()
    private var observed: String?
    private var mismatch = false

    var observedPin: String? { lock.lock(); defer { lock.unlock() }; return observed }
    var pinMismatch: Bool { lock.lock(); defer { lock.unlock() }; return mismatch }

    init(expectedPin: String?) { self.expectedPin = expectedPin }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let pin = AtlasTrust.spkiPin(trust)
        else { completionHandler(.cancelAuthenticationChallenge, nil); return }
        lock.lock(); observed = pin; lock.unlock()
        if let expected = expectedPin, expected != pin {
            lock.lock(); mismatch = true; lock.unlock()
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }
}
