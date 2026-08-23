# Library Finder V3 — Browser Automation

정독/교육청 어린이도서관은 기존 공식검색 직접 조회를 유지합니다.

청운문학도서관/청운효자동 북카페는 Playwright + Chromium으로
종로구립도서관 검색 페이지를 실제 브라우저처럼 조작합니다.

배포에 필요한 파일:
- app.py
- requirements.txt
- packages.txt
- README.md

Streamlit Community Cloud는 packages.txt를 통해 Debian 외부 패키지를 설치합니다.
Chromium은 시스템 패키지로 설치하고 Playwright가 /usr/bin/chromium을 사용합니다.

회귀 테스트:
- 목화씨 조혜란 → 청운문학도서관: 소장 있음 / 즉시대출 가능
