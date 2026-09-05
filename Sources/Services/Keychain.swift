import Foundation
import Security

/// 存放 Spotify 的 client ID 與 refresh token。
/// 這些東西不該進 UserDefaults，所以走 Keychain。
///
/// 模擬器上有個例外：`simctl install` 覆蓋安裝會被 iOS 當成「重新安裝」，
/// 連帶清掉這個 App 的 Keychain 項目，於是每改一次程式重裝就要重登一次。
/// 所以只在模擬器另外鏡一份到 UserDefaults 當退路 —— 模擬器只存在開發機上，
/// 實機（包含 CI 打包出來的 ipa）走的還是純 Keychain。
enum Keychain {

    #if targetEnvironment(simulator)
    private static let mirrorPrefix = "kikitori.sim_keychain_mirror."

    private static func mirror(_ value: String?, for key: String) {
        let defaults = UserDefaults.standard
        if let value {
            defaults.set(value, forKey: mirrorPrefix + key)
        } else {
            defaults.removeObject(forKey: mirrorPrefix + key)
        }
    }

    private static func mirrored(_ key: String) -> String? {
        UserDefaults.standard.string(forKey: mirrorPrefix + key)
    }
    #endif

    static func set(_ value: String?, for key: String) {
        guard let value, !value.isEmpty else {
            remove(key)
            return
        }
        #if targetEnvironment(simulator)
        mirror(value, for: key)
        #endif
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
              let data = item as? Data else {
            #if targetEnvironment(simulator)
            // Keychain 被重裝清掉了，從鏡像救回來並寫回去
            if let saved = mirrored(key) {
                set(saved, for: key)
                return saved
            }
            #endif
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    static func remove(_ key: String) {
        #if targetEnvironment(simulator)
        mirror(nil, for: key)
        #endif
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "com.o19750120.kikitori",
            kSecAttrAccount as String: key
        ]
        SecItemDelete(query as CFDictionary)
    }
}
