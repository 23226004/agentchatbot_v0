# db-admin/src
DB 관리 소스 루트. 설치형 패키지 **`db_admin/`** 하나만 둔다(editable 설치 시 `src` 가 sys.path
루트가 되므로, 패키지 밖 top-level 디렉터리를 두면 `import pipeline` 류로 누수된다 — 두지 말 것).
계층(lawgo_client·pipeline)은 `db_admin/` **안에** 있다(automation/inspector 는 향후 분리 시 추가).
