import SwiftUI

enum AtlasTheme {
    static let background  = Color(red: 0x09 / 255.0, green: 0x11 / 255.0, blue: 0x1C / 255.0)
    static let surface     = Color(red: 0x16 / 255.0, green: 0x24 / 255.0, blue: 0x38 / 255.0)
    static let surface2    = Color(red: 0x1E / 255.0, green: 0x32 / 255.0, blue: 0x48 / 255.0)
    static let primary     = Color(red: 0x5A / 255.0, green: 0xA7 / 255.0, blue: 0xFF / 255.0)
    static let primaryDeep = Color(red: 0x1A / 255.0, green: 0x3A / 255.0, blue: 0x60 / 255.0)
    static let secondary   = Color(red: 0x5E / 255.0, green: 0xF2 / 255.0, blue: 0xC2 / 255.0)
    static let tertiary    = Color(red: 0xFF / 255.0, green: 0xBE / 255.0, blue: 0x5C / 255.0)
    static let onBackground = Color(red: 0xF3 / 255.0, green: 0xF7 / 255.0, blue: 0xFB / 255.0)
    static let onSurface   = Color(red: 0xCB / 255.0, green: 0xD9 / 255.0, blue: 0xEC / 255.0)
    static let muted       = Color(red: 0x5A / 255.0, green: 0x7A / 255.0, blue: 0x9E / 255.0)
    static let error       = Color(red: 0xFF / 255.0, green: 0x5C / 255.0, blue: 0x72 / 255.0)
    static let warning     = Color(red: 0xFF / 255.0, green: 0xBE / 255.0, blue: 0x5C / 255.0)
    static let success     = Color(red: 0x5E / 255.0, green: 0xF2 / 255.0, blue: 0xC2 / 255.0)
}

struct AtlasCardModifier: ViewModifier {
    var corner: CGFloat = 14
    func body(content: Content) -> some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(AtlasTheme.surface2)
            .clipShape(RoundedRectangle(cornerRadius: corner, style: .continuous))
    }
}

extension View {
    func atlasCard(corner: CGFloat = 14) -> some View {
        modifier(AtlasCardModifier(corner: corner))
    }
}
