
import os
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import pandas as pd
import requests
import streamlit as st

BASE = "https://data4library.kr/api"

TARGET_LIBRARIES = [
    {
        "key": "jeongdok",
        "label": "정독도서관",
        "aliases": ["정독도서관", "서울특별시교육청정독도서관"],
        "known_code": "111020",
        "official_search": "https://jdlib.sen.go.kr/jdlib/intro/search/index.do?menu_idx=213&search_type=titlecollquery&search_text={q}&locExquery=111020",
    },
    {
        "key": "child",
        "label": "교육청 어린이도서관",
        "aliases": ["어린이도서관", "서울특별시교육청어린이도서관"],
        "known_code": "111017",
        "official_search": "https://childlib.sen.go.kr/childlib/intro/search/index.do?menu_idx=213&search_type=titlecollquery&search_text={q}&locExquery=111017",
    },
    {
        "key": "cheongun",
        "label": "청운문학도서관",
        "aliases": ["청운문학도서관", "종로구립청운문학도서관"],
        "known_code": "",
        "official_search": "https://lib.jongno.go.kr/menu/subpage/subpage_02/sub01.php",
    },
    {
        "key": "bookcafe",
        "label": "청운효자동 북카페",
        "aliases": ["청운효자동 북카페", "청운효자동북카페", "청운효자동 북카페 작은도서관"],
        "known_code": "",
        "official_search": "https://lib.jongno.go.kr/menu/subpage/subpage_02/sub01.php",
    },
]

st.set_page_config(page_title="우리 동네 도서관 책 찾기", page_icon="📚", layout="wide")

CUSTOM_CSS = """
<style>
.block-container {max-width: 1100px; padding-top: 2rem;}
.small-note {font-size:0.88rem; opacity:.72;}
.status-available {font-weight:700;}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return os.environ.get(name, default)


AUTH_KEY = secret("DATA4LIBRARY_AUTH_KEY", "")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).lower().strip()
    s = re.sub(r"[\s·ㆍ:：,，.。\-_/()\[\]{}'\"“”‘’!?！？]+", "", s)
    return s


def similarity(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        shorter, longer = min(len(a), len(b)), max(len(a), len(b))
        return 0.88 + 0.1 * (shorter / longer)
    return SequenceMatcher(None, a, b).ratio()


@st.cache_data(ttl=300, show_spinner=False)
def api_get(path, params_tuple):
    params = dict(params_tuple)
    params["authKey"] = AUTH_KEY
    params["format"] = "json"
    r = requests.get(f"{BASE}/{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def extract_docs(data):
    response = data.get("response", data)
    docs = response.get("docs", [])
    out = []
    for x in docs:
        if isinstance(x, dict):
            out.append(x.get("doc", x))
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def search_book_candidates(title):
    data = api_get("srchBooks", tuple({
        "keyword": title,
        "pageNo": 1,
        "pageSize": 20,
    }.items()))
    docs = extract_docs(data)
    candidates = []
    for d in docs:
        t = d.get("bookname") or d.get("bookName") or d.get("title") or ""
        author = d.get("authors") or d.get("author") or ""
        publisher = d.get("publisher") or d.get("publication") or ""
        isbn = d.get("isbn13") or d.get("isbn") or ""
        year = d.get("publication_year") or d.get("publicationYear") or d.get("pubYear") or ""
        score = similarity(title, t)
        if isbn:
            candidates.append({
                "title": t,
                "author": author,
                "publisher": publisher,
                "isbn13": str(isbn).replace("-", ""),
                "year": year,
                "score": score,
            })
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def choose_best_book(title):
    candidates = search_book_candidates(title)
    if not candidates:
        return None, []
    best = candidates[0]
    # 너무 엉뚱한 자동매칭을 피함
    if best["score"] < 0.58:
        return None, candidates[:5]
    return best, candidates[:5]


def extract_libs(data):
    response = data.get("response", data)
    libs = response.get("libs", [])
    out = []
    for x in libs:
        if isinstance(x, dict):
            out.append(x.get("lib", x))
    return out


@st.cache_data(ttl=86400, show_spinner=False)
def discover_seoul_libraries():
    # 서울 전체를 여러 페이지로 조회. API가 페이지 크기를 제한해도 동작하도록 함.
    found = []
    for page in range(1, 11):
        try:
            data = api_get("libSrch", tuple({
                "region": "11",
                "pageNo": page,
                "pageSize": 100,
            }.items()))
        except Exception:
            break
        libs = extract_libs(data)
        if not libs:
            break
        found.extend(libs)
        if len(libs) < 100:
            break
    return found


def resolve_library_codes():
    libs = discover_seoul_libraries()
    result = {}
    for target in TARGET_LIBRARIES:
        if target["known_code"]:
            result[target["key"]] = target["known_code"]
            continue

        best_code, best_score, best_name = "", 0.0, ""
        for lib in libs:
            name = lib.get("libName") or lib.get("libname") or lib.get("name") or ""
            code = lib.get("libCode") or lib.get("libcode") or lib.get("code") or ""
            for alias in target["aliases"]:
                s = similarity(alias, name)
                if s > best_score:
                    best_code, best_score, best_name = str(code), s, name

        # 안전장치: 이름이 충분히 유사할 때만 사용
        if best_score >= 0.72:
            result[target["key"]] = best_code
        else:
            result[target["key"]] = ""
    return result


@st.cache_data(ttl=120, show_spinner=False)
def check_book(lib_code, isbn13):
    data = api_get("bookExist", tuple({
        "libCode": lib_code,
        "isbn13": isbn13,
    }.items()))
    response = data.get("response", data)
    result = response.get("result", response.get("results", response))
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        result = {}

    has_book = result.get("hasBook")
    if has_book is None:
        has_book = result.get("result")
    loan = result.get("loanAvailable")

    def yes(v):
        return str(v).strip().upper() in {"Y", "YES", "TRUE", "1", "가능", "대출가능"}

    has = yes(has_book)
    available = yes(loan)

    if not has:
        return "⚪ 소장 없음"
    if available:
        return "🟢 대출 가능"
    return "🔴 대출 중/불가"


def official_link(target, title):
    template = target["official_search"]
    if "{q}" in template:
        return template.format(q=quote_plus(title))
    return template


def main():
    st.title("📚 우리 동네 도서관 책 찾기")
    st.caption("책 제목을 한 줄에 하나씩 넣으면 네 곳의 소장·대출 가능 여부를 한꺼번에 확인합니다.")

    if not AUTH_KEY:
        st.error("도서관 정보나루 API 인증키가 설정되지 않았습니다.")
        st.info("배포할 때 Streamlit Secrets에 DATA4LIBRARY_AUTH_KEY를 한 번만 넣으면, 다른 사용자는 키 없이 링크만 열어 사용할 수 있습니다.")
        st.stop()

    with st.expander("검색 대상 도서관", expanded=False):
        st.write("정독도서관 · 서울특별시교육청 어린이도서관 · 청운문학도서관 · 청운효자동 북카페")

    sample = "우리 가족의 보물을 찾아라!\n목화씨\n도슨트 이창용의 미술대모험 2"
    titles_text = st.text_area(
        "책 제목",
        height=210,
        placeholder=sample,
        help="한 줄에 한 권씩 입력하세요. 20권 정도까지 한 번에 검색하는 것을 권장합니다.",
    )

    col1, col2 = st.columns([1, 3])
    with col1:
        run = st.button("🔎 한꺼번에 검색", type="primary", use_container_width=True)
    with col2:
        st.caption("검색 결과는 도서관 정보나루의 수집 시점과 실제 서가 상황 사이에 차이가 있을 수 있습니다. 중요한 책은 결과의 '공식 확인' 링크로 최종 확인하세요.")

    if not run:
        return

    titles = []
    for line in titles_text.splitlines():
        t = line.strip(" \t•·-–—")
        if t and t not in titles:
            titles.append(t)

    if not titles:
        st.warning("책 제목을 한 권 이상 입력해주세요.")
        return
    if len(titles) > 30:
        st.warning("한 번에 30권까지만 검색합니다.")
        titles = titles[:30]

    with st.spinner("도서관 네 곳을 확인하고 있어요…"):
        lib_codes = resolve_library_codes()

        unresolved = [
            x["label"] for x in TARGET_LIBRARIES
            if not lib_codes.get(x["key"])
        ]

        rows = []
        match_details = []

        progress = st.progress(0)
        total = len(titles)

        for idx, title in enumerate(titles):
            best, candidates = choose_best_book(title)

            row = {"입력한 책": title}
            if not best:
                row["확인된 판본"] = "⚠️ 책 판본 확인 필요"
                for target in TARGET_LIBRARIES:
                    row[target["label"]] = "❓ 확인 필요"
                rows.append(row)
                match_details.append((title, None, candidates))
                progress.progress((idx + 1) / total)
                continue

            row["확인된 판본"] = f'{best["title"]} / {best["author"]} / ISBN {best["isbn13"]}'
            match_details.append((title, best, candidates))

            for target in TARGET_LIBRARIES:
                code = lib_codes.get(target["key"], "")
                if not code:
                    row[target["label"]] = "❓ 도서관 코드 확인 필요"
                    continue
                try:
                    row[target["label"]] = check_book(code, best["isbn13"])
                except Exception:
                    row[target["label"]] = "⚠️ 조회 오류"

            rows.append(row)
            progress.progress((idx + 1) / total)

        progress.empty()

    df = pd.DataFrame(rows)

    st.subheader("검색 결과")
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "입력한 책": st.column_config.TextColumn(width="medium"),
            "확인된 판본": st.column_config.TextColumn(width="large"),
        }
    )

    # 어느 도서관이 가장 많이 '대출 가능'인지 계산
    counts = {}
    for target in TARGET_LIBRARIES:
        col = target["label"]
        if col in df:
            counts[col] = int(df[col].astype(str).str.startswith("🟢").sum())

    if counts:
        best_lib = max(counts, key=counts.get)
        best_count = counts[best_lib]
        st.success(f"오늘 가장 효율적인 곳: **{best_lib}** — 입력한 책 중 **{best_count}권**이 대출 가능으로 조회됐어요.")

    if unresolved:
        st.warning("자동으로 도서관 코드를 찾지 못한 곳: " + ", ".join(unresolved) + " — 아래 README의 수동 코드 설정 방법을 사용하면 됩니다.")

    with st.expander("🔗 공식 도서관 사이트에서 최종 확인"):
        st.caption("특히 꼭 빌려야 하는 책은 출발 직전에 공식 사이트에서 한 번 더 확인하는 것을 권장합니다.")
        for title in titles:
            st.markdown(f"**{title}**")
            links = []
            for target in TARGET_LIBRARIES:
                url = official_link(target, title)
                links.append(f'[{target["label"]}]({url})')
            st.markdown(" · ".join(links))

    with st.expander("책 판본 매칭 보기"):
        st.caption("같은 제목의 여러 판본이 있을 수 있어 자동으로 가장 가까운 판본을 골랐습니다.")
        for original, best, candidates in match_details:
            st.markdown(f"**{original}**")
            if best:
                st.write(f'선택: {best["title"]} — {best["author"]} / {best["publisher"]} / ISBN {best["isbn13"]}')
            else:
                st.write("자동 선택하지 않음")
            if candidates:
                st.write("후보:")
                for c in candidates[:3]:
                    st.write(f'- {c["title"]} — {c["author"]} / {c["publisher"]} / {c["isbn13"]} (유사도 {c["score"]:.2f})')

    st.markdown(
        '<div class="small-note">데이터 출처: 국립중앙도서관 도서관 정보나루 Open API. '
        '소장·대출 상태는 데이터 수집 주기 때문에 실제 도서관 현황과 시차가 있을 수 있습니다.</div>',
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
