# Library Finder V3.1

Streamlit 설치 오류 수정판.

로그에서 확인된 원인:
- `packages.txt`에 `libasound2`를 직접 설치하려 했고,
  Streamlit의 Debian trixie 환경에서 Chromium이 요구하는 `libasound2t64`와 충돌함.
- 그 결과 apt 설치가 실패하면서 앱 시작 시 Playwright import도 실패함.

수정:
- `packages.txt`에는 `chromium` 하나만 둠.
- Chromium이 필요한 시스템 의존성은 apt가 현재 Debian 버전에 맞게 자동 해결하게 함.
- `requirements.txt`의 Playwright 유지.
- 기존 정독/어린이 검색 로직과 종로구 Playwright 브라우저 자동화 로직은 유지.

테스트:
- `목화씨` → 청운문학도서관 `소장 있음 / 즉시대출 가능`
