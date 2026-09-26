import Foundation

/// The setup code on an Atlas label: three groups of four capitals and digits
/// (H7QD-M3XR-9KTW). The Wi-Fi password on the same label is four lowercase
/// groups, so the two can't be mixed up. Atlas ignores dashes and case;
/// formatting here only helps the owner type it. (Android: SetupCode.kt.)
enum SetupCode {
    static let length = 12

    /// Uppercase letters and digits only, grouped in fours as the owner types.
    static func format(_ input: String) -> String {
        let raw = input.uppercased().filter { ("A"..."Z").contains($0) || ("0"..."9").contains($0) }.prefix(length)
        var groups: [String] = []
        var current = ""
        for ch in raw {
            current.append(ch)
            if current.count == 4 { groups.append(current); current = "" }
        }
        if !current.isEmpty { groups.append(current) }
        return groups.joined(separator: "-")
    }

    static func isComplete(_ input: String) -> Bool { format(input).filter { $0 != "-" }.count == length }

    /// Four lowercase groups: the label's Wi-Fi password, typed in the wrong place.
    static func looksLikeWifiPassword(_ input: String) -> Bool {
        input.trimmingCharacters(in: .whitespaces)
            .range(of: "^[a-z0-9]{4}(-[a-z0-9]{4}){3}$", options: .regularExpression) != nil
    }
}
