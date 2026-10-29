import Foundation

enum MacAccessPolicy {
    static func permits(bundleID: String, ownBundleID: String?) -> Bool {
        let id=bundleID.lowercased()
        let blocked: Set<String>=["com.apple.terminal","com.googlecode.iterm2","dev.warp.warp-stable","com.apple.keychainaccess","com.apple.passwords","com.apple.systempreferences","com.apple.systemsettings","com.apple.securityagent","com.apple.systemuiserver","com.apple.loginwindow","com.agilebits.onepassword7","com.1password.1password","com.bitwarden.desktop","com.apple.findmy"]
        return !id.isEmpty && bundleID != ownBundleID && !blocked.contains(id) && !id.contains("squipi") && !id.contains("password")
    }
    static func secure(role: String, subrole: String, protected: Bool) -> Bool {
        protected || role.lowercased().contains("secure") || subrole.lowercased().contains("secure")
    }
    static let pressRoles: Set<String>=["AXButton","AXCheckBox","AXRadioButton","AXMenuItem","AXPopUpButton","AXDisclosureTriangle","AXLink"]
    static let textRoles: Set<String>=["AXTextField","AXTextArea","AXComboBox"]
}
