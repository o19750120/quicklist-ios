import Foundation
import AuthenticationServices
import CryptoKit
import UIKit

/// Spotify 的 PKCE 授權流程。
///
/// 手機 App 沒辦法安全保存 client secret，所以走 PKCE：
/// 先產一組隨機字串（verifier），把它的 SHA256（challenge）送去授權頁；
/// 之後拿授權碼換 token 時再附上原始的 verifier，
/// Spotify 比對得起來才給 token。這樣就算授權碼被攔截也換不到東西。
@MainActor
final class SpotifyAuth: ObservableObject {

    enum AuthError: LocalizedError {
        case missingClientID
        case cancelled
        case badResponse(String)

        var errorDescription: String? {
            switch self {
            case .missingClientID:
                return "還沒有填 Spotify Client ID"
            case .cancelled:
                return "授權被取消"
            case .badResponse(let detail):
                return detail
            }
        }
    }

    @Published private(set) var isAuthorized = false
    @Published private(set) var lastError: String?
    @Published var clientID: String {
        didSet { Keychain.set(clientID, for: Self.clientIDKey) }
    }

    private static let clientIDKey = "spotify_client_id"
    private static let refreshTokenKey = "spotify_refresh_token"

    private let redirectURI = "kikitori://spotify-callback"
    private let scopes = [
        "user-read-currently-playing",
        "user-read-playback-state",
        "user-modify-playback-state"
    ].joined(separator: " ")

    private var accessToken: String?
    private var accessTokenExpiry = Date.distantPast
    private var authSession: ASWebAuthenticationSession?
    private let anchorProvider = AuthAnchorProvider()

    init() {
        // 優先用使用者自己填過的，沒有的話用建置時注入的那組
        let stored = Keychain.get(Self.clientIDKey) ?? ""
        let resolved = stored.isEmpty ? BuildSecrets.spotifyClientID : stored
        self.clientID = resolved
        self.isAuthorized = Keychain.get(Self.refreshTokenKey) != nil

        if stored.isEmpty && !resolved.isEmpty {
            Keychain.set(resolved, for: Self.clientIDKey)
        }
    }

    var hasClientID: Bool {
        !clientID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: - 登入 / 登出

    func signIn() async {
        lastError = nil
        guard hasClientID else {
            lastError = AuthError.missingClientID.localizedDescription
            return
        }

        let verifier = Self.makeCodeVerifier()

        var components = URLComponents(string: "https://accounts.spotify.com/authorize")!
        components.queryItems = [
            URLQueryItem(name: "client_id", value: clientID.trimmingCharacters(in: .whitespacesAndNewlines)),
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "code_challenge", value: Self.makeCodeChallenge(from: verifier)),
            URLQueryItem(name: "scope", value: scopes),
            URLQueryItem(name: "show_dialog", value: "true")
        ]

        guard let url = components.url else {
            lastError = "授權網址組不出來"
            return
        }

        do {
            let callback = try await presentAuthSession(url: url)
            let items = URLComponents(url: callback, resolvingAgainstBaseURL: false)?.queryItems ?? []

            guard let code = items.first(where: { $0.name == "code" })?.value else {
                let reason = items.first(where: { $0.name == "error" })?.value ?? "沒有拿到授權碼"
                throw AuthError.badResponse(reason)
            }

            try await exchange(code: code, verifier: verifier)
            isAuthorized = true
            logInfo("Spotify", "授權成功")
        } catch AuthError.cancelled {
            logInfo("Spotify", "使用者取消授權")
            return
        } catch {
            lastError = error.localizedDescription
            logError("Spotify", "授權失敗：\(error.localizedDescription)")
        }
    }

    func signOut() {
        Keychain.remove(Self.refreshTokenKey)
        accessToken = nil
        accessTokenExpiry = .distantPast
        isAuthorized = false
    }

    // MARK: - Token

    /// 取得一個保證有效的 access token，過期就自動用 refresh token 換新的。
    func validAccessToken() async throws -> String {
        if let token = accessToken, accessTokenExpiry > Date().addingTimeInterval(30) {
            return token
        }
        guard let refresh = Keychain.get(Self.refreshTokenKey) else {
            isAuthorized = false
            throw AuthError.badResponse("尚未登入 Spotify")
        }
        try await postToken(body: [
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": clientID.trimmingCharacters(in: .whitespacesAndNewlines)
        ])
        guard let token = accessToken else {
            throw AuthError.badResponse("換不到 access token")
        }
        return token
    }

    private func exchange(code: String, verifier: String) async throws {
        try await postToken(body: [
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirectURI,
            "client_id": clientID.trimmingCharacters(in: .whitespacesAndNewlines),
            "code_verifier": verifier
        ])
    }

    private func postToken(body: [String: String]) async throws {
        var request = URLRequest(url: URL(string: "https://accounts.spotify.com/api/token")!)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = Self.formEncode(body).data(using: .utf8)

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw AuthError.badResponse("沒有收到 HTTP 回應")
        }

        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]

        guard http.statusCode == 200 else {
            let detail = (json["error_description"] as? String)
                ?? (json["error"] as? String)
                ?? "HTTP \(http.statusCode)"
            throw AuthError.badResponse("Spotify 回應：\(detail)")
        }

        accessToken = json["access_token"] as? String
        let expiresIn = json["expires_in"] as? Double ?? 3600
        accessTokenExpiry = Date().addingTimeInterval(expiresIn)

        // refresh token 只有授權碼交換時一定會給，
        // 之後用 refresh token 換新 token 時不一定回傳，沒回傳就沿用舊的。
        if let newRefresh = json["refresh_token"] as? String {
            Keychain.set(newRefresh, for: Self.refreshTokenKey)
        }
    }

    private static func formEncode(_ body: [String: String]) -> String {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~")
        return body.map { key, value in
            let encoded = value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
            return "\(key)=\(encoded)"
        }.joined(separator: "&")
    }

    // MARK: - ASWebAuthenticationSession 包裝成 async

    private func presentAuthSession(url: URL) async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: url,
                callbackURLScheme: "kikitori"
            ) { callbackURL, error in
                if let error {
                    let nsError = error as NSError
                    if nsError.domain == ASWebAuthenticationSessionErrorDomain,
                       nsError.code == ASWebAuthenticationSessionError.canceledLogin.rawValue {
                        continuation.resume(throwing: AuthError.cancelled)
                    } else {
                        continuation.resume(throwing: AuthError.badResponse(error.localizedDescription))
                    }
                    return
                }
                guard let callbackURL else {
                    continuation.resume(throwing: AuthError.badResponse("沒有收到回調網址"))
                    return
                }
                continuation.resume(returning: callbackURL)
            }
            session.presentationContextProvider = anchorProvider
            session.prefersEphemeralWebBrowserSession = false
            authSession = session
            session.start()
        }
    }

    // MARK: - PKCE 產生器

    private static func makeCodeVerifier() -> String {
        let allowed = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        return String((0..<96).map { _ in allowed.randomElement()! })
    }

    private static func makeCodeChallenge(from verifier: String) -> String {
        let digest = SHA256.hash(data: Data(verifier.utf8))
        return Data(digest).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// ASWebAuthenticationSession 需要知道要把授權視窗掛在哪個 window 上。
/// 獨立成一個沒有 actor 隔離的小類別，避免主執行緒隔離的麻煩。
final class AuthAnchorProvider: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        let activeScene = scenes.first { $0.activationState == .foregroundActive } ?? scenes.first
        let window = activeScene?.windows.first { $0.isKeyWindow } ?? activeScene?.windows.first
        return window ?? ASPresentationAnchor()
    }
}
