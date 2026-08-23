
import os
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

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
    r = requests.get(base, params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text, r.url


def sen_result(lib, title):
    last_err = None
    for q in query_variants(title):
        try:
            html, url = fetch_sen(lib["base"], lib["menu_idx"], lib["loc"], q)
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

    if last_err:
        return {
            "status": "⚠️ 공식 조회 오류",
            "available": 0,
            "copies": 0,
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



def jongno_result(lib, title):
    """
    종로구 공식 통합검색은 브라우저 폼 상태/선택 도서관 값을 이용해 검색하며,
    단순 query GET 호출만으로는 동일 결과가 재현되지 않는 경우가 있다.
    잘못된 '소장 없음' 판정을 하지 않기 위해 자동 판정 대신 공식확인 상태를 반환한다.
    """
    return {
        "status": "🔵 공식 검색에서 확인",
        "available": 0,
        "copies": None,
        "url": official_url(lib, title),
        "source": "종로구립도서관 공식검색",
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

    if not st.button("🔎 한꺼번에 검색", type="primary", use_container_width=True):
        return

    titles = []
    for line in txt.splitlines():
        t = line.strip(" \t•·-–—")
        if t and t not in titles:
            titles.append(t)

    if not titles:
        st.warning("책 제목을 한 권 이상 입력해주세요.")
        return
    titles = titles[:30]

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
    progress.empty()

    st.subheader("검색 결과")

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

    st.caption("※ 정독·교육청 어린이도서관은 자동 조회합니다. 청운문학·청운효자동은 종로구 공식 검색 폼의 세션/선택값 때문에 현재 자동 판정을 보류하고 공식 검색으로 연결합니다.")


if __name__ == "__main__":
    main()
