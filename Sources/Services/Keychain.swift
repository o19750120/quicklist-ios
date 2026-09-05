import Foundation
import Security

/// 存放 Spotify 的 client ID 與 refresh token。
/// 這些東西不該進 UserDefaults，所以走 Keychain。
enum Keychain {
    static func set(_ value: String?, for key: String) {
        guard let value, !value.isEmpty else {
            remove(key)
            return
        }
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.o19750120.kikitori",
            kSecAttrAccount as String: key
        ]
        SecItemDelete(query as CFDictionary)

        var insert = query
        insert[kSecValueData as String] = Data(value.utf8)
        insert[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(insert as CFDictionary, nil)
    }

    static func get(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.o19750120.kikitori",
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func remove(_ key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.o19750120.kikitori",
            kSecAttrAccount as String: key
        ]
        SecItemDelete(query as CFDictionary)
    }
}
