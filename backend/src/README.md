# backend/src
백엔드 소스 루트. 설치형 패키지 **`backend_app/`** 하나만 둔다(editable 설치 시 `src` 가 sys.path
루트가 되므로, 패키지 밖 top-level 디렉터리를 두면 `import core` 류로 누수된다 — 두지 말 것).
SoC 계층(api/core/services/repositories)은 `backend_app/` **안에** 있다. schemas 는 `legal_core` 공유.
