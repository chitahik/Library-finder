
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

DATA4LIBRARY = "https://data4library.kr/api"

LIBRARIES = [
    {
        "key": "jeongdok",
        "label": "정독도서관",
        "type": "sen",
        "base": "https://jdlib.sen.go.kr/jdlib/intro/search/index.do",
        "menu_idx": "213",
        "loc": "111020",
        "aliases": ["정독도서관", "서울특별시교육청정독도서관"],
    },
    {
        "key": "child",
        "label": "교육청 어린이도서관",
        "type": "sen",
        "base": "https://childlib.sen.go.kr/childlib/intro/search/index.do",
        "menu_idx": "4",
        "loc": "111017",
        "aliases": ["어린이도서관", "서울특별시교육청어린이도서관"],
    },
    {
        "key": "cheongun",
        "label": "청운문학도서관",
        "type": "jongno",
        "aliases": ["청운문학도서관", "종로구립청운문학도서관"],
        "official": "https://lib.jongno.go.kr/plus_m/search_list_klas.php",
        "match_names": ["청운문학도서관"],
    },
    {
        "key": "bookcafe",
        "label": "청운효자동 북카페",
        "type": "jongno",
        "aliases": ["청운효자동 북카페", "청운효자동북카페"],
        "official": "https://lib.jongno.go.kr/plus_m/search_list_klas.php",
        "match_names": ["청운 효자동 북카페", "청운효자동 북카페"],
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LibraryFinder/2.0; +https://streamlit.io)"
}

st.set_page_config(page_title="우리 동네 도서관 책 찾기", page_icon="📚", layout="wide")

st.markdown("""
<style>
.block-container {max-width: 1120px; padding-top: 1.5rem;}
.small {font-size:.86rem; opacity:.72;}
div[data-testid="stDataFrame"] {font-size: .95rem;}
</style>
""", unsafe_allow_html=True)


def secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return os.environ.get(name, default)


AUTH_KEY = secret("DATA4LIBRARY_AUTH_KEY", "")


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", s)


def score(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    if a == b:
        return 1
    if a in b or b in a:
        return 0.9 + 0.09 * min(len(a), len(b)) / max(len(a), len(b))
    return SequenceMatcher(None, a, b).ratio()


def query_variants(title):
    clean = re.sub(r"[!！?？:：·ㆍ]", " ", title)
    clean = re.sub(r"\s+", " ", clean).strip()
    variants = [title.strip(), clean]
    # 부제 때문에 검색이 깨질 때를 대비해 앞부분도 시도
    words = clean.split()
    if len(words) >= 4:
        variants.append(" ".join(words[:4]))
    if len(words) >= 3:
        variants.append(" ".join(words[:3]))
    out = []
    for v in variants:
        if v and v not in out:
            out.append(v)
    return out


@st.cache_data(ttl=90, show_spinner=False)
def fetch_sen(base, menu_idx, loc, title):
    params = {
        "editMode": "normal",
        "locExquery": loc,
        "menu_idx": menu_idx,
        "search_text": title,
        "search_type": "titlecollquery",
    }

    last_err = None
    # 10권 이상 연속 조회 시 교육청 서버가 순간적으로 429/5xx/연결 오류를
    # 반환하는 경우를 대비해 짧은 backoff로 최대 3회 재시도한다.
    for attempt in range(3):
        try:
            r = requests.get(base, params=params, headers=HEADERS, timeout=20)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                raise requests.HTTPError(f"temporary HTTP {r.status_code}", response=r)
            r.raise_for_status()
            return r.text, r.url
        except (requests.RequestException, requests.Timeout) as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))

    raise last_err or RuntimeError("서울시교육청 공식 조회 실패")


def sen_result(lib, title):
    last_err = None
    had_successful_response = False

    for q in query_variants(title):
        try:
            html, url = fetch_sen(lib["base"], lib["menu_idx"], lib["loc"], q)
            had_successful_response = True
        except Exception as e:
            last_err = e
            continue

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        nt = norm(text)
        nq = norm(title)

        if "찾으시는자료가없습니다" in nt:
            continue

        # 검색결과 건수
        m = re.search(r"총\s*([0-9,]+)\s*건", text)
        total = int(m.group(1).replace(",", "")) if m else None

        # 실제 결과 영역에 입력 제목과 유사한 제목이 있는지 보수적으로 확인
        # 전체 텍스트에 입력 제목이 있고 결과 건수가 1건 이상이면 후보로 본다.
        if nq not in nt:
            # 부제가 붙은 경우 앞 70% 이상이 들어가도 허용
            short = nq[:max(5, int(len(nq) * 0.7))]
            if short not in nt:
                continue

        if total == 0:
            continue

        # 서울시교육청 검색 페이지는 동일한 결과를 모바일/데스크톱용 HTML로
        # 중복 렌더링하는 경우가 있어 "자료상태" 문자열 개수로 복본 수를 세면
        # 실제보다 2~4배 부풀려질 수 있다.
        # 따라서 실제 소장 건수는 화면에 표시되는 "총 N건"을 기준으로 사용한다.
        copies = total if total is not None else 1

        # 대출 상태는 중복 HTML에 안전하도록 '존재 여부'만 판정한다.
        has_available = re.search(r"자료상태\s*:\s*대출가능", text) is not None
        has_loaned = re.search(r"자료상태\s*:\s*대출중", text) is not None
        has_reserved = re.search(r"자료상태\s*:\s*예약가능(?:\([^)]*\))?", text) is not None

        if has_available:
            label = f"🟢 소장 {copies} / 즉시대출 가능"
            available = 1
        elif has_reserved or has_loaned:
            extra = []
            if has_reserved:
                extra.append("예약가능 자료 있음")
            if has_loaned:
                extra.append("대출중 자료 있음")
            label = f"🟡 소장 {copies} / 즉시대출 없음"
            if extra:
                label += " · " + " · ".join(extra)
            available = 0
        else:
            label = f"📚 소장 {copies} / 대출상태 확인"
            available = 0

        return {
            "status": label,
            "available": available,
            "copies": copies,
            "url": url,
            "source": "공식 도서관",
        }

    # 중요한 구분:
    # 어떤 검색 변형에서 한 번이라도 정상 응답을 받았다면,
    # 다른 변형 하나가 실패했더라도 최종 결과를 "조회 오류"로 만들지 않는다.
    if had_successful_response:
        return {
            "status": "⚪ 소장 없음",
            "available": 0,
            "copies": 0,
            "url": official_url(lib, title),
            "source": "공식 도서관",
        }

    if last_err:
        return {
            "status": "⚠️ 공식 조회 오류 · 잠시 후 재검색",
            "available": 0,
            "copies": None,
            "url": official_url(lib, title),
            "source": "공식 도서관",
        }

    return {
        "status": "⚪ 소장 없음",
        "available": 0,
        "copies": 0,
        "url": official_url(lib, title),
        "source": "공식 도서관",
    }


def api_get(path, **params):
    params["authKey"] = AUTH_KEY
    params["format"] = "json"
    r = requests.get(f"{DATA4LIBRARY}/{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def extract_docs(data):
    res = data.get("response", data)
    docs = res.get("docs", [])
    out = []
    for x in docs:
        if isinstance(x, dict):
            out.append(x.get("doc", x))
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def book_candidates(title):
    all_docs = []
    seen = set()
    for q in query_variants(title):
        try:
            docs = extract_docs(api_get("srchBooks", keyword=q, pageNo=1, pageSize=30))
        except Exception:
            continue
        for d in docs:
            isbn = str(d.get("isbn13") or d.get("isbn") or "").replace("-", "")
            name = d.get("bookname") or d.get("bookName") or d.get("title") or ""
            if not isbn or isbn in seen:
                continue
            seen.add(isbn)
            all_docs.append({
                "title": name,
                "author": d.get("authors") or d.get("author") or "",
                "publisher": d.get("publisher") or "",
                "isbn": isbn,
                "score": score(title, name),
            })
    all_docs.sort(key=lambda x: x["score"], reverse=True)
    return all_docs


def extract_libs(data):
    res = data.get("response", data)
    libs = res.get("libs", [])
    return [x.get("lib", x) for x in libs if isinstance(x, dict)]


@st.cache_data(ttl=86400, show_spinner=False)
def seoul_libraries():
    out = []
    for page in range(1, 11):
        try:
            libs = extract_libs(api_get("libSrch", region="11", pageNo=page, pageSize=100))
        except Exception:
            break
        if not libs:
            break
        out.extend(libs)
        if len(libs) < 100:
            break
    return out


def resolve_lib_code(lib):
    best = ("", 0, "")
    for row in seoul_libraries():
        name = row.get("libName") or row.get("libname") or row.get("name") or ""
        code = str(row.get("libCode") or row.get("libcode") or row.get("code") or "")
        for alias in lib["aliases"]:
            s = score(alias, name)
            if s > best[1]:
                best = (code, s, name)
    return best[0] if best[1] >= 0.70 else ""


@st.cache_data(ttl=120, show_spinner=False)
def book_exist(lib_code, isbn):
    data = api_get("bookExist", libCode=lib_code, isbn13=isbn)
    res = data.get("response", data)
    result = res.get("result", res.get("results", res))
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        result = {}

    def yes(v):
        return str(v).strip().upper() in {"Y", "YES", "TRUE", "1", "가능", "대출가능"}

    return yes(result.get("hasBook")), yes(result.get("loanAvailable"))


def data4library_result(lib, title):
    if not AUTH_KEY:
        return {
            "status": "❓ API 키 필요",
            "available": 0,
            "copies": 0,
            "url": lib["official"],
            "source": "정보나루",
        }

    code = resolve_lib_code(lib)
    if not code:
        return {
            "status": "❓ 공식 확인 필요",
            "available": 0,
            "copies": 0,
            "url": lib["official"],
            "source": "정보나루",
        }

    cands = book_candidates(title)
    # 제목 유사도가 충분한 후보 여러 판본을 검사한다.
    cands = [c for c in cands if c["score"] >= 0.62][:8]
    if not cands:
        return {
            "status": "❓ 판본 확인 필요",
            "available": 0,
            "copies": 0,
            "url": lib["official"],
            "source": "정보나루",
        }

    held = 0
    available = 0
    for c in cands:
        try:
            has, loan = book_exist(code, c["isbn"])
        except Exception:
            continue
        if has:
            held += 1
            available += 1 if loan else 0

    if held == 0:
        return {
            "status": "⚪ 소장 없음(정보나루)",
            "available": 0,
            "copies": 0,
            "url": lib["official"],
            "source": "정보나루",
        }
    if available:
        return {
            "status": f"🟢 소장 판본 {held} / 대출가능 판본 {available}",
            "available": available,
            "copies": held,
            "url": lib["official"],
            "source": "정보나루",
        }
    return {
        "status": f"🟡 소장 판본 {held} / 현재 대출가능 0",
        "available": 0,
        "copies": held,
        "url": lib["official"],
        "source": "정보나루",
    }



def _find_and_check_library(page, library_name):
    # 1) label 텍스트로 체크박스 클릭
    labels = page.locator("label")
    for i in range(labels.count()):
        lab = labels.nth(i)
        try:
            txt = lab.inner_text().strip()
        except Exception:
            continue
        if library_name.replace(" ", "") in txt.replace(" ", ""):
            try:
                lab.click()
                return True
            except Exception:
                pass

    # 2) 주변 텍스트를 가진 checkbox 찾기
    boxes = page.locator('input[type="checkbox"]')
    for i in range(boxes.count()):
        box = boxes.nth(i)
        try:
            parent_txt = box.locator("xpath=..").inner_text().strip()
        except Exception:
            parent_txt = ""
        if library_name.replace(" ", "") in parent_txt.replace(" ", ""):
            try:
                if not box.is_checked():
                    box.check(force=True)
                return True
            except Exception:
                pass
    return False


def _find_search_input(page):
    # 검색어처럼 보이는 visible input을 우선
    selectors = [
        'input[name*="query" i]',
        'input[name*="search" i]',
        'input[name*="keyword" i]',
        'input[name*="word" i]',
        'input[type="search"]',
        'input[type="text"]',
    ]
    for sel in selectors:
        loc = page.locator(sel)
        for i in range(loc.count()):
            x = loc.nth(i)
            try:
                if x.is_visible() and x.is_enabled():
                    return x
            except Exception:
                continue
    return None


@st.cache_data(ttl=90, show_spinner=False)
def _jongno_browser_search(title):
    """
    실제 Chromium을 띄워 종로구립도서관 검색 페이지에서 사람이 하듯 검색한다.
    Streamlit 서버에서는 headless 모드로 동작한다.
    """
    url = "https://lib.jongno.go.kr/plus_m/search_list_klas.php"

    with sync_playwright() as pw:
        # Streamlit Community Cloud의 packages.txt로 설치한 시스템 Chromium 사용.
        launch_kwargs = dict(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ],
        )
        # Debian Chromium 경로 후보
        chromium_paths = ["/usr/bin/chromium", "/usr/bin/chromium-browser"]
        executable = next((p for p in chromium_paths if os.path.exists(p)), None)
        if executable:
            launch_kwargs["executable_path"] = executable

        browser = pw.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            viewport={"width": 1280, "height": 1400},
            locale="ko-KR",
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=25000)

        # 두 도서관 선택. 이미 체크돼 있으면 건드리지 않아도 됨.
        _find_and_check_library(page, "청운문학도서관")
        # 표기는 페이지에 따라 공백 유무가 다름.
        if not _find_and_check_library(page, "청운효자동 북카페"):
            _find_and_check_library(page, "청운 효자동 북카페")

        inp = _find_search_input(page)
        if inp is None:
            raise RuntimeError("종로구 검색 입력창을 찾지 못했습니다.")
        inp.fill(title)

        # 검색 버튼: visible '검색' 버튼/링크 중 입력창과 가까운 것을 클릭
        clicked = False
        for role in ["button", "link"]:
            loc = page.get_by_role(role, name=re.compile(r"^\s*검색\s*$"))
            for i in range(loc.count()):
                x = loc.nth(i)
                try:
                    if x.is_visible():
                        x.click()
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break

        if not clicked:
            # submit input fallback
            submits = page.locator('input[type="submit"], button[type="submit"]')
            for i in range(submits.count()):
                x = submits.nth(i)
                try:
                    val = (x.get_attribute("value") or "") + " " + x.inner_text()
                    if "검색" in val and x.is_visible():
                        x.click()
                        clicked = True
                        break
                except Exception:
                    continue

        if not clicked:
            # Enter fallback
            inp.press("Enter")

        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            page.wait_for_timeout(2500)

        body = page.locator("body").inner_text(timeout=10000)
        result_url = page.url
        browser.close()
        return body, result_url


def _jongno_library_status(body, title, library_key):
    """
    종로구 검색 결과의 실제 화면 텍스트에서 도서관별 자료를 판정.
    화면 예시: [청운문학도서관] 목화씨 대출가능(비치중)
    """
    text = re.sub(r"\s+", " ", body)
    title_n = norm(title)
    title_short = title_n[:max(3, int(len(title_n) * 0.65))]

    if library_key == "cheongun":
        lib_patterns = [r"\[?\s*청운문학도서관\s*\]?"]
    else:
        lib_patterns = [
            r"\[?\s*청운효자동\s*북카페\s*\]?",
            r"\[?\s*청운\s*효자동\s*북카페\s*\]?",
        ]

    # 도서관명이 나오는 주변 700자를 개별 결과 후보로 본다.
    snippets = []
    for pat in lib_patterns:
        for m in re.finditer(pat, text):
            s = max(0, m.start() - 250)
            e = min(len(text), m.end() + 450)
            snip = text[s:e]
            sn = norm(snip)
            if title_n in sn or title_short in sn:
                snippets.append(snip)

    # 중복 카드 제거
    unique = []
    fingerprints = set()
    for snip in snippets:
        fp = norm(snip)
        if fp not in fingerprints:
            fingerprints.add(fp)
            unique.append(snip)

    if not unique:
        return "⚪ 소장 없음", 0, 0

    available = any(
        ("대출가능" in s or "비치중" in s) and "대출불가" not in s
        for s in unique
    )

    # 결과 카드가 중복 렌더링될 가능성이 있어 복본 수는 안전하게 '1+'로만 표시.
    # 종로구 화면에서 검색건수/카드 개수를 안정적으로 읽을 수 있을 때 추후 정확한 숫자로 확장 가능.
    if available:
        return "🟢 소장 있음 / 즉시대출 가능", 1, 1
    return "🟡 소장 있음 / 즉시대출 없음", 0, 1


def jongno_result(lib, title):
    last_err = None
    for attempt in range(2):
        try:
            body, result_url = _jongno_browser_search(title)
            status, available, copies = _jongno_library_status(body, title, lib["key"])
            return {
                "status": status,
                "available": available,
                "copies": copies,
                "url": result_url,
                "source": "종로구립도서관 공식검색(브라우저 자동화)",
            }
        except Exception as e:
            last_err = e
            if attempt == 0:
                time.sleep(1.0)

    return {
        "status": "⚠️ 자동조회 실패 · 잠시 후 재검색",
        "available": 0,
        "copies": None,
        "url": official_url(lib, title),
        "source": f"종로구립도서관 공식검색(브라우저 자동화): {type(last_err).__name__ if last_err else 'Error'}",
    }


def official_url(lib, title):
    if lib["type"] == "sen":
        return (
            f'{lib["base"]}?editMode=normal&locExquery={lib["loc"]}'
            f'&menu_idx={lib["menu_idx"]}&search_type=titlecollquery'
            f'&search_text={quote_plus(title)}'
        )
    if lib["type"] == "jongno":
        return lib["official"]
    return lib["official"]


def check_library(lib, title):
    if lib["type"] == "sen":
        return sen_result(lib, title)
    if lib["type"] == "jongno":
        return jongno_result(lib, title)
    return data4library_result(lib, title)


def main():
    st.title("📚 우리 동네 도서관 책 찾기")
    st.caption("책 제목 한 번으로 네 도서관의 소장·대출 상태를 함께 확인합니다.")

    with st.expander("검색 대상 도서관"):
        st.write("정독도서관 · 서울특별시교육청 어린이도서관 · 청운문학도서관 · 청운효자동 북카페")

    sample = "우리 가족의 보물을 찾아라!\n목화씨\n도슨트 이창용의 미술대모험 2"
    txt = st.text_area("책 제목", height=210, placeholder=sample, help="한 줄에 한 권씩 입력하세요.")

    if not st.button("🔎 한꺼번에 검색", type="primary", width="stretch"):
        return

    titles = []
    for line in txt.splitlines():
        t = line.strip(" \t•·-–—")
        if t and t not in titles:
            titles.append(t)

    if not titles:
        st.warning("책 제목을 한 권 이상 입력해주세요.")
        return
    if len(titles) > 15:
        st.info("한 번에 최대 15권까지 검색합니다. 안정적인 사용은 10권 안팎을 권장해요.")
    titles = titles[:15]

    rows = []
    detail = {}
    progress = st.progress(0)

    for i, title in enumerate(titles):
        row = {"책": title}
        detail[title] = {}
        for lib in LIBRARIES:
            result = check_library(lib, title)
            row[lib["label"]] = result["status"]
            detail[title][lib["key"]] = result
        rows.append(row)
        progress.progress((i + 1) / len(titles))

        # 공식 사이트에 짧은 시간 동안 요청이 몰려 조회 오류가 나는 것을 줄인다.
        if i < len(titles) - 1:
            time.sleep(0.35)
    progress.empty()

    st.subheader("검색 결과")

    failed_checks = 0
    for title in titles:
        for lib in LIBRARIES:
            status = detail[title][lib["key"]]["status"]
            if "조회 오류" in status or "자동조회 실패" in status:
                failed_checks += 1

    if failed_checks:
        st.warning(
            f"총 {len(titles)}권 검색은 완료했지만 {failed_checks}개 도서관 조회가 일시적으로 실패했어요. "
            "해당 항목만 잠시 후 다시 검색하면 됩니다."
        )
    else:
        st.success(f"{len(titles)}권 × 4개 도서관 조회 완료")

    # 모바일 우선: 가로 표 대신 책별로 도서관 4곳을 세로 표시한다.
    for title in titles:
        st.markdown(f"### 📖 {title}")
        for lib in LIBRARIES:
            result = detail[title][lib["key"]]
            st.markdown(f"**{lib['label']}**")
            st.write(result["status"])
        if title != titles[-1]:
            st.divider()

    with st.expander("🔗 공식 검색으로 최종 확인"):
        for title in titles:
            st.markdown(f"**{title}**")
            for lib in LIBRARIES:
                url = detail[title][lib["key"]]["url"]
                st.markdown(f'[{lib["label"]} 공식 확인]({url})')

    st.caption("※ 10권 안팎의 일괄검색을 안정적으로 처리하도록 재시도와 요청 간격을 적용했습니다. '소장 없음'과 '조회 오류'는 서로 다르게 표시합니다.")


if __name__ == "__main__":
    main()
