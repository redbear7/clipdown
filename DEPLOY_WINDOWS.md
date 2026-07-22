# 유튜브 다운로더 — Windows 배포 가이드

Tauri v2 + GitHub Actions로 Windows용 인스톨러(.exe)를 빌드하고 자동 업데이트를 배포합니다.

## 개요

```
[태그 push]  →  [GitHub Actions Windows 러너]  →  [NSIS 인스톨러 + 서명 + latest.json]
                                                          ↓
                                                    [GitHub Releases]
                                                          ↓
                       [설치된 앱이 latest.json 폴링 → 자동 업데이트 알림]
```

- **인스톨러**: NSIS `.exe` (사용자 폴더 설치, 관리자 권한 불필요)
- **번들 내용**: Python 3.11 embeddable + Flask + yt-dlp + ffmpeg + Deno
- **자동 업데이트**: Tauri updater plugin, minisign 서명 검증
- **UI**: Perplexity 스타일 시니어 화면 (`/simple`)

---

## 1. GitHub 저장소 준비

### 새 저장소 생성 (권장)

GitHub에서 새 저장소 `redbear7/clipdown` 을 만드세요 (Private 가능).

로컬에서 origin을 교체:

```bash
cd /Users/redbear7/clipdown
git remote set-url origin https://github.com/redbear7/clipdown.git
```

> 저장소 이름을 다르게 쓸 경우 `tauri-app/src-tauri/tauri.conf.json`의 `plugins.updater.endpoints` URL과 `.github/workflows/release.yml`의 경로도 함께 바꿔주세요.

---

## 2. GitHub Secrets 등록

저장소 **Settings → Secrets and variables → Actions → New repository secret** 에서 아래 2개 등록:

### `TAURI_SIGNING_PRIVATE_KEY`

로컬에 생성된 개인키 파일 내용 전체를 붙여넣습니다.

```bash
cat /Users/redbear7/clipdown/tauri-app/.keys/clipdown-updater.key
```

출력 전체(주석·개행 포함)를 secret 값으로 등록.

### `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`

키 생성 시 `--ci` 옵션으로 무비밀번호 생성했으므로 **빈 문자열**로 등록하거나 아예 생성하지 않아도 됩니다. (워크플로우가 optional로 처리)

> ⚠️ **개인키는 절대 커밋하지 마세요.** `.gitignore`에 `tauri-app/.keys/`가 추가되어 있습니다. 잃어버리면 향후 업데이트 서명이 불가능해지므로 안전한 곳(1Password 등)에 백업 필수.

---

## 3. 첫 릴리스

```bash
# 1) 코드 커밋
cd /Users/redbear7/clipdown
git add -A
git commit -m "feat: Tauri Windows app with auto-updater"
git push origin main

# 2) 태그 push → 워크플로우 자동 실행
git tag v0.1.1
git push origin v0.1.1
```

- GitHub Actions 탭에서 진행 상황 확인 (약 15~25분 소요)
- 완료되면 Releases 탭에 `유튜브 다운로더 v0.1.1` 릴리스가 생성되고 3개 파일 첨부됨:
  - `유튜브-다운로더_0.1.1_x64-setup.exe` (인스톨러, ~130MB)
  - `유튜브-다운로더_0.1.1_x64-setup.exe.sig` (서명)
  - `latest.json` (updater 매니페스트)

---

## 4. 사용자 설치

Windows 11 사용자는 `-setup.exe` 다운로드 후 더블클릭 → 자동으로 설치 → 시작 메뉴에 "유튜브 다운로더" 등록.

첫 실행 시 SmartScreen 경고가 뜰 수 있음 ("추가 정보 → 실행" 버튼). 이는 코드 서명 인증서 미보유 상태이며, 향후 EV 인증서 구매 시 사라짐.

---

## 5. 신규 버전 배포 (자동 업데이트)

```bash
# 1) 버전 번호 두 곳 동시 수정 (스크립트로 자동화 가능)
#    - tauri-app/src-tauri/tauri.conf.json  ("version": "0.1.2")
#    - tauri-app/package.json                ("version": "0.1.2")

# 2) 커밋 + 태그
git add -A
git commit -m "chore: bump v0.1.2"
git tag v0.1.2
git push && git push origin v0.1.2
```

기존 설치 사용자는 앱 실행 시 자동으로 `latest.json` 확인 → 새 버전이 있으면 다이얼로그 표시 → 승인하면 다운로드·설치·재시작.

---

## 6. 로컬에서 확인 (선택)

macOS에선 Windows 앱을 직접 빌드할 수 없지만, Rust/설정 컴파일 에러는 잡을 수 있습니다:

```bash
cd /Users/redbear7/clipdown/tauri-app
npm run tauri -- info      # Tauri/Rust/Node 환경 확인
npm run tauri -- dev       # macOS에서 개발 실행 (기존과 동일)
```

Windows 빌드 자체 확인이 필요하면:
- Windows 11 머신에서 `Rust + Node + WebView2 + Visual Studio Build Tools` 설치 후
- `npm run tauri -- build --bundles nsis` 실행

---

## 7. 트러블슈팅

| 증상 | 원인/해결 |
|---|---|
| GitHub Actions 빌드 실패: `TAURI_SIGNING_PRIVATE_KEY not set` | Secrets 등록 확인 |
| 인스톨러는 만들어졌는데 `latest.json` 서명 매치 안 됨 | 개인키가 로컬에서 등록된 pubkey와 다름. 재생성 후 secret+config 모두 업데이트 |
| 설치된 앱이 시작 화면에서 흰 창 | 서버 포트 8899가 다른 프로그램에 점유. `netstat -ano \| findstr :8899`로 확인 |
| 자동 업데이트 알림이 안 뜸 | `plugins.updater.endpoints` URL이 실제 릴리스 URL과 일치하는지 확인. `curl` 로 latest.json 접근 가능한지 테스트 |

---

## 파일 위치 참고

| 파일 | 역할 |
|---|---|
| `tauri-app/src-tauri/tauri.conf.json` | Tauri 앱 설정, updater endpoint, pubkey |
| `tauri-app/src-tauri/Cargo.toml` | Rust 의존성 (updater plugin 포함) |
| `tauri-app/src-tauri/src/lib.rs` | Python 서버 실행 로직 |
| `tauri-app/src-tauri/capabilities/default.json` | updater/process 권한 |
| `tauri-app/.keys/clipdown-updater.key` | **개인 서명키 (비공개, 백업 필수)** |
| `tauri-app/.keys/clipdown-updater.key.pub` | 공개 서명키 (config에 삽입됨) |
| `.github/workflows/release.yml` | Windows 빌드 워크플로우 |
