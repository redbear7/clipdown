# macOS 코드 서명 + 공증 설정 가이드

Apple Developer 계정 등록 완료 상태 기준. 이 절차를 마치면 사용자가 "확인되지 않은 개발자" 경고 없이 앱을 열 수 있습니다.

---

## 1. Developer ID Application 인증서 생성

1. https://developer.apple.com/account/resources/certificates/list 접속
2. **+** 버튼 → **Software → Developer ID Application** 선택 → Continue
3. Keychain Access에서 CSR 생성 후 업로드:
   - Keychain Access 앱 → 상단 메뉴 `Certificate Assistant → Request a Certificate from a Certificate Authority`
   - Email 입력, `Saved to disk` 선택 → 저장
4. 저장한 `.certSigningRequest` 파일 업로드 → 인증서 생성
5. 다운로드 후 더블클릭하여 Keychain에 설치

## 2. .p12로 내보내기

1. Keychain Access → **My Certificates** 카테고리
2. `Developer ID Application: Your Name (TEAMID)` 항목 우클릭 → **Export**
3. 형식 `.p12`, 임의 비밀번호 설정 → 저장 (예: `~/Desktop/clipdown-cert.p12`)

## 3. GitHub Secrets 등록

**https://github.com/redbear7/clipdown/settings/secrets/actions** 에서 아래 6개 등록.

### `APPLE_CERTIFICATE`
`.p12` 파일을 base64로 변환한 문자열:

```bash
base64 -i ~/Desktop/clipdown-cert.p12 | pbcopy
```

붙여넣기.

### `APPLE_CERTIFICATE_PASSWORD`
.p12 내보낼 때 설정한 비밀번호.

### `APPLE_SIGNING_IDENTITY`
정확한 문자열 확인:

```bash
security find-identity -v -p codesigning | grep "Developer ID Application"
```

출력 예:
```
1) ABC123... "Developer ID Application: 홍길동 (ABCDE12345)"
```

큰따옴표 안 내용만 복사 → secret 값으로 등록:
`Developer ID Application: 홍길동 (ABCDE12345)`

### `APPLE_TEAM_ID`
위의 괄호 안 10자리 (예: `ABCDE12345`)
또는 https://developer.apple.com/account 상단 우측 Team ID 표시란에서 확인.

### `APPLE_ID`
Apple Developer 로그인 이메일.

### `APPLE_PASSWORD`  ⚠️ 앱 전용 비밀번호 (일반 비번 아님)
1. https://appleid.apple.com/account/manage 로그인
2. **Sign-In and Security → App-Specific Passwords → Generate Password**
3. Label 아무거나 (예: "GitHub Actions ClipDown")
4. 생성된 `xxxx-xxxx-xxxx-xxxx` 형식 문자열 복사 → secret으로 등록

---

## 4. 배포 및 확인

Secrets 등록 후 새 태그 push:

```bash
cd /Users/bangju/clipdown
# 예: v0.1.4 (다음 릴리스 버전)
# tauri.conf.json, updater.py, simple.html 3곳의 버전 문자열 수정 후
git commit -am "chore: bump v0.1.4"
git tag v0.1.4 && git push origin main v0.1.4
```

빌드 완료 후 macOS 사용자가 DMG 다운로드·설치·실행 → **첫 실행 시 경고 없음**.

---

## 5. 서명 확인 (선택)

DMG 열어서 앱을 Application에 복사 후:

```bash
codesign -dv --verbose=4 /Applications/ClipDown.app 2>&1 | grep -E "Authority|Identifier|TeamIdentifier|Timestamp"
```

`Authority=Developer ID Application: 홍길동 (ABCDE12345)` 표시되면 성공.

공증 확인:
```bash
spctl -a -vvv -t exec /Applications/ClipDown.app
```

`accepted` + `source=Notarized Developer ID` 표시되면 공증까지 성공.

---

## 6. 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| `errSecInternalComponent` | Keychain에 인증서 없음 → 워크플로우가 자동 import하므로 GitHub Secret 확인 |
| `Notarization failed: Invalid credentials` | `APPLE_PASSWORD`가 일반 Apple ID 비밀번호 → 앱 전용 비밀번호로 교체 |
| `hardenedRuntime not enabled` | tauri.conf.json의 `bundle.macOS.hardenedRuntime: true` 확인 |
| `Python was rejected because a component signature failed` | entitlements.plist 누락 → `bundle.macOS.entitlements` 경로 확인 |
| 공증 30분 이상 지연 | Apple 서버 큐 지연. 워크플로우 timeout 늘리거나 재시도 |
