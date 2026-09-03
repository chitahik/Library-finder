# LIBRARY FINDER V5.7 FINAL TITLE-AUTHOR
import os
import re
import time
import json
from pathlib import Path
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
import math

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
        "aliases": ["청운효자동 북카페", "청운효자동북카페", "청운 효자동 북카페"],
        "official": "https://lib.jongno.go.kr/plus_m/search_list_klas.php",
        "match_names": ["청운효자동 북카페", "청운 효자동 북카페", "청운효자동북카페"],
    },
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; LibraryFinder/3.6; +https://streamlit.io)"
}


# -----------------------------
# 오늘 어디로 갈지 / 도서관별 빌릴 책 요약
# -----------------------------
def _is_available_now(result):
    """
    화면 문구가 아니라 result['available'] 숫자를 기준으로 판단.
    V4.0의 '일반판 1 / 즉시대출 1' 같은 새 문구도 정확히 반영한다.
    """
    result = result or {}
    status = str(result.get("status", ""))

    if any(x in status for x in ("조회 오류", "자동조회 실패", "조회 실패")):
        return False

    try:
        return int(result.get("available") or 0) > 0
    except Exception:
        return False


def _is_owned(result):
    """
    화면 문구가 아니라 result['copies'] 숫자를 우선 사용.
    copies=0 → 소장 없음, copies>0 → 소장 있음.
    """
    result = result or {}
    status = str(result.get("status", ""))

    if any(x in status for x in ("조회 오류", "자동조회 실패", "조회 실패")):
        return None

    copies = result.get("copies")
    if copies is not None:
        try:
            return int(copies) > 0
        except Exception:
            pass

    if "소장 없음" in status:
        return False

    return any(x in status for x in (
        "소장 있음", "소장 ", "대출중", "예약가능",
        "소장 확인", "일반판", "큰글자책", "소장 판본"
    ))

def build_library_summary(titles, detail):
    """
    복본 수가 아니라 '입력한 서로 다른 제목 중 몇 종을 오늘 빌릴 수 있는가'로 계산한다.
    큰글자책/대활자본은 최종 버전에서 의도적으로 제외한다.
    """
    summaries = []

    for lib in LIBRARIES:
        key = lib["key"]
        available_titles = []
        owned_not_now = []
        not_owned = []
        failed = []

        for title in titles:
            result = detail[title][key]

            if _is_available_now(result):
                available_titles.append(title)
                continue

            owned = _is_owned(result)
            if owned is None:
                failed.append(title)
            elif owned:
                owned_not_now.append(title)
            else:
                not_owned.append(title)

        summaries.append({
            "key": key,
            "name": lib["label"],
            "available_titles": available_titles,
            "available_count": len(available_titles),
            "owned_not_now": owned_not_now,
            "not_owned": not_owned,
            "failed": failed,
            "confirmed_owned_count": len(available_titles) + len(owned_not_now),
        })

    summaries.sort(
        key=lambda x: (
            -x["available_count"],
            len(x["failed"]),
            x["name"],
        )
    )
    return summaries


def render_priority_summary(titles, detail):
    """
    V5.5 UI
    - 상단 순위의 (x/y)는 '즉시 빌릴 수 있는 제목 수 / 내가 입력한 전체 제목 수'
    - 복본/소장 권수는 상단에서 보여주지 않는다.
    - 도서관별 영역에는 즉시대출 가능한 제목만 목록으로 보여준다.
    """
    summaries = build_library_summary(titles, detail)
    total = len(titles)

    st.markdown("## 📍 오늘 어디로 갈까?")

    for rank, item in enumerate(summaries, start=1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}위")
        failed_note = ""
        if item["failed"]:
            failed_note = f" · 확인 실패 {len(item['failed'])}권"

        st.markdown(
            f"**{medal} {item['name']} — ({item['available_count']}/{total})**"
            f"{failed_note}"
        )

    st.caption(
        "※ (즉시 빌릴 수 있는 제목 수 / 내가 입력한 전체 제목 수)"
    )

    st.markdown("---")
    st.markdown("## 📚 도서관별 오늘 빌릴 수 있는 책")

    for rank, selected in enumerate(summaries, start=1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "4위")

        title_line = (
            f"{medal} {selected['name']} — "
            f"({len(selected['available_titles'])}/{total})"
        )

        with st.expander(title_line, expanded=(rank == 1)):
            if selected["available_titles"]:
                st.markdown(
                    f"**● 오늘 바로 빌릴 수 있는 책 "
                    f"{len(selected['available_titles'])}권**"
                )
                for t in selected["available_titles"]:
                    st.markdown(f"- {t}")
            else:
                st.info("오늘 바로 빌릴 수 있는 책이 없어요.")

            if selected["failed"]:
                st.warning(
                    f"⚠️ 조회 실패 {len(selected['failed'])}권"
                )
                for t in selected["failed"]:
                    st.markdown(f"- {t}")

    st.caption(
        "※ 위 숫자는 복본 수가 아닙니다. "
        "실제 소장 권수와 대출 상태는 아래 책별 상세 결과에서 확인합니다."
    )

    st.markdown("---")
    st.markdown("## 🔎 책별 상세 결과")


# -----------------------------
# V3.6 검색 안정성: 체크포인트 / 부분 결과 보존
# -----------------------------
CHECKPOINT_VERSION = "v5.7-one-tap-author-only-20260903"
CHECKPOINT_DIR = Path("/tmp/library_finder_checkpoints")
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


def _checkpoint_key(titles):
    raw = (CHECKPOINT_VERSION + "\n" + "\n".join(titles)).encode("utf-8")
    import hashlib
    return hashlib.sha256(raw).hexdigest()[:20]


def _checkpoint_path(titles):
    return CHECKPOINT_DIR / f"{_checkpoint_key(titles)}.json"


def _json_safe_result(result):
    if not isinstance(result, dict):
        return {}
    safe = {}
    for k, v in result.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            safe[k] = v
        else:
            safe[k] = str(v)
    return safe


def save_checkpoint(titles, detail, completed_pairs):
    # V3.9 검증 중에는 오래된 결과를 재사용하지 않음
    return


def load_checkpoint(titles, max_age_seconds=0):
    return {}, set()


def clear_checkpoint(titles):
    return


def make_failure_result(lib, exc=None):
    return {
        "status": "⚠️ 조회 실패 · 다시 시도 가능",
        "available": 0,
        "copies": None,
        "url": official_url(lib, ""),
        "source": f"일시적 조회 실패: {type(exc).__name__ if exc else 'Error'}",
    }


def search_one_library(lib, book):
    title = book["title"] if isinstance(book, dict) else str(book)
    author = book.get("author", "") if isinstance(book, dict) else ""

    try:
        if lib["type"] == "sen":
            return sen_result(lib, title, author=author)
        if lib["type"] == "jongno":
            return jongno_result(lib, title, author=author)
        return data4library_result(lib, title)
    except Exception as e:
        return {
            "status": "⚠️ 조회 실패 · 다시 시도 가능",
            "available": 0,
            "copies": None,
            "url": official_url(lib, title),
            "source": f"일시적 조회 실패: {type(e).__name__}",
        }


def run_resilient_search(
    titles,
    previous_detail=None,
    books=None,
    previous_completed=None,
    only_failed=False,
):
    detail = previous_detail or {}
    completed = set(previous_completed or set())
    books = books or [{"title": t, "author": ""} for t in titles]
    book_map = {b["title"]: b for b in books}

    total_pairs = len(titles) * len(LIBRARIES)
    done_pairs = 0

    for title in titles:
        for lib in LIBRARIES:
            if (title, lib["key"]) in completed:
                done_pairs += 1

    progress = st.progress(done_pairs / total_pairs if total_pairs else 0)
    status_box = st.empty()

    for ti, title in enumerate(titles, start=1):
        detail.setdefault(title, {})

        for lib in LIBRARIES:
            pair = (title, lib["key"])

            if pair in completed:
                continue

            if only_failed:
                old = detail.get(title, {}).get(lib["key"], {})
                old_status = old.get("status", "")
                if not any(
                    marker in old_status
                    for marker in ("조회 실패", "조회 오류", "자동조회 실패")
                ):
                    completed.add(pair)
                    continue

            status_box.info(
                f"검색 중 {ti}/{len(titles)}권 · {lib['label']} "
                f"({done_pairs + 1}/{total_pairs})"
            )

            result = search_one_library(lib, book_map.get(title, {"title": title, "author": ""}))
            detail[title][lib["key"]] = result
            completed.add(pair)
            done_pairs += 1

            save_checkpoint(titles, detail, completed)
            progress.progress(min(done_pairs / total_pairs, 1.0))
            time.sleep(0.2)

    status_box.empty()
    progress.progress(1.0)
    save_checkpoint(titles, detail, completed)

    return detail, completed


def has_failed_items(titles, detail):
    failed = []

    for title in titles:
        for lib in LIBRARIES:
            result = detail.get(title, {}).get(lib["key"], {})
            status = result.get("status", "")

            if any(
                marker in status
                for marker in ("조회 실패", "조회 오류", "자동조회 실패")
            ):
                failed.append((title, lib["key"]))

    return failed


st.set_page_config(
    page_title="우리 동네 도서관 책 찾기",
    page_icon="📚",
    layout="wide",
)

st.markdown(
    """
<style>
.block-container {max-width: 1120px; padding-top: 1.5rem;}
.small {font-size:.86rem; opacity:.72;}
div[data-testid="stDataFrame"] {font-size: .95rem;}
</style>
""",
    unsafe_allow_html=True,
)


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
    """
    제목 전용 검색어 변형.
    V5.6부터 저자는 별도 입력값으로 받으므로 제목의 마지막 단어를
    저자로 추측하거나 제목을 임의로 잘라내지 않는다.
    """
    clean = re.sub(r"[!！?？:：·ㆍ]", " ", str(title or ""))
    clean = re.sub(r"\s+", " ", clean).strip()

    out = []
    for v in (str(title or "").strip(), clean):
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

    for attempt in range(3):
        try:
            r = requests.get(
                base,
                params=params,
                headers=HEADERS,
                timeout=20,
            )

            if r.status_code == 429 or 500 <= r.status_code < 600:
                raise requests.HTTPError(
                    f"temporary HTTP {r.status_code}",
                    response=r,
                )

            r.raise_for_status()
            return r.text, r.url

        except (requests.RequestException, requests.Timeout) as e:
            last_err = e

            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))

    raise last_err or RuntimeError("서울시교육청 공식 조회 실패")


def _split_title_author_hint(user_text):
    """
    사용자가 '바깥은 여름 김애란'처럼 입력한 경우
    마지막 단어를 저자 힌트로 보고 제목 후보도 함께 만든다.
    완벽한 저자 판별은 아니므로, 결과 카드 검증에만 보조적으로 사용한다.
    """
    clean = re.sub(r"[!！?？:：·ㆍ]", " ", str(user_text or ""))
    clean = re.sub(r"\s+", " ", clean).strip()
    words = clean.split()

    if len(words) >= 3:
        return " ".join(words[:-1]), words[-1]
    return clean, ""


def _sen_parse_cards(html, requested_title, author_hint=""):
    """
    서울시교육청 도서관 검색결과를 페이지 전체 숫자가 아니라
    실제 '책 결과 카드' 단위로 판독한다.

    이렇게 해야 검색어가 페이지 어딘가에 반복되어 나타나거나,
    페이지의 다른 숫자를 '검색건수'로 잘못 읽는 오탐을 막을 수 있다.
    """
    soup = BeautifulSoup(html, "html.parser")
    whole_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    whole_norm = norm(whole_text)

    if "찾으시는자료가없습니다" in whole_norm:
        return {
            "status": "⚪ 소장 없음",
            "available": 0,
            "copies": 0,
        }

    title_norm = norm(requested_title)
    author_norm = norm(author_hint)

    # 결과 카드가 보통 li/tr/div/article 중 하나이므로
    # '제목 + 대출상태'를 모두 포함하는 가장 안쪽 요소만 선택.
    state_terms = (
        "자료상태", "대출가능", "대출중", "예약가능",
        "대출불가", "비치중"
    )

    def qualifies(tag):
        txt = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
        if not txt or len(txt) > 1800:
            return False

        n = norm(txt)
        if title_norm and title_norm not in n:
            return False

        if author_norm and author_norm not in n:
            return False

        if not any(term in txt for term in state_terms):
            return False

        return True

    cards = []
    for tag in soup.find_all(["li", "tr", "article", "div"]):
        if not qualifies(tag):
            continue

        # 자식 중 더 작은 qualifying block이 있으면 부모는 제외
        has_child = False
        for child in tag.find_all(["li", "tr", "article", "div"]):
            if child is tag:
                continue
            if qualifies(child):
                has_child = True
                break

        if not has_child:
            cards.append(re.sub(r"\s+", " ", tag.get_text(" ", strip=True)))

    # 완전 동일한 중복 DOM만 제거
    unique_cards = []
    seen = set()
    for txt in cards:
        key = re.sub(r"\s+", "", txt)
        if key not in seen:
            seen.add(key)
            unique_cards.append(txt)

    if not unique_cards:
        return {
            "status": "⚪ 소장 없음",
            "available": 0,
            "copies": 0,
        }

    total = len(unique_cards)
    available = 0
    loaned = 0
    reserved = 0

    for txt in unique_cards:
        compact = re.sub(r"\s+", "", txt)

        is_unavailable = any(x in compact for x in (
            "대출불가", "대출중", "관외대출불가", "이용불가"
        ))
        is_available = (not is_unavailable) and (
            "대출가능" in compact or "비치중" in compact
        )

        if is_available:
            available += 1
        if "대출중" in compact or "대출불가" in compact:
            loaned += 1
        if "예약가능" in compact:
            reserved += 1

    if available:
        status = f"🟢 소장 {total} / 즉시대출 가능"
    else:
        status = f"🟡 소장 {total} / 즉시대출 없음"
        extras = []
        if loaned:
            extras.append("대출중 자료 있음")
        if reserved:
            extras.append("예약가능 자료 있음")
        if extras:
            status += " · " + " · ".join(extras)

    return {
        "status": status,
        "available": available,
        "copies": total,
    }


def sen_result(lib, title, author=""):
    """
    서울시교육청 도서관.
    - 제목은 제목 검색으로 조회한다.
    - 저자 입력 시 결과 카드 안에 저자명이 함께 있는 자료만 인정한다.
    - 저자 미입력 시 저자 검증을 완전히 생략한다.
    """
    last_err = None
    had_successful_response = False
    author_hint = str(author or "").strip()

    for q in query_variants(title):
        try:
            html, url = fetch_sen(
                lib["base"],
                lib["menu_idx"],
                lib["loc"],
                q,
            )
            had_successful_response = True
        except Exception as e:
            last_err = e
            continue

        parsed = _sen_parse_cards(
            html,
            requested_title=title,
            author_hint=author_hint,
        )

        if parsed["copies"] > 0:
            return {
                **parsed,
                "url": url,
                "source": "공식 도서관 카드검증",
            }

    if had_successful_response:
        return {
            "status": "⚪ 소장 없음",
            "available": 0,
            "copies": 0,
            "url": official_url(lib, title),
            "source": "공식 도서관 카드검증",
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
        "source": "공식 도서관 카드검증",
    }


def api_get(path, **params):
    params["authKey"] = AUTH_KEY
    params["format"] = "json"

    r = requests.get(
        f"{DATA4LIBRARY}/{path}",
        params=params,
        timeout=15,
    )
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
            docs = extract_docs(
                api_get(
                    "srchBooks",
                    keyword=q,
                    pageNo=1,
                    pageSize=30,
                )
            )
        except Exception:
            continue

        for d in docs:
            isbn = str(
                d.get("isbn13")
                or d.get("isbn")
                or ""
            ).replace("-", "")

            name = (
                d.get("bookname")
                or d.get("bookName")
                or d.get("title")
                or ""
            )

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

    all_docs.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    return all_docs


def extract_libs(data):
    res = data.get("response", data)
    libs = res.get("libs", [])
    return [
        x.get("lib", x)
        for x in libs
        if isinstance(x, dict)
    ]


@st.cache_data(ttl=86400, show_spinner=False)
def seoul_libraries():
    out = []

    for page in range(1, 11):
        try:
            libs = extract_libs(
                api_get(
                    "libSrch",
                    region="11",
                    pageNo=page,
                    pageSize=100,
                )
            )
        except Exception:
            break

        if not libs:
            break

        out.extend(libs)

        if len(libs) < 100:
            break

    return out



@st.cache_data(ttl=3600, show_spinner=False)
def author_book_candidates(author, max_books=60):
    """
    저자명만 입력했을 때 정보나루에서 저자 필드가 실제로 일치하는 책을 찾는다.
    동일 제목의 여러 판본은 뒤에서 제목 단위로 합친다.
    """
    author = str(author or "").strip()
    if not author or not AUTH_KEY:
        return []

    author_norm = norm(author)
    found = []
    seen_isbn = set()

    # 보통 저자 검색은 1~3페이지면 충분하지만 넉넉히 5페이지까지 확인.
    for page in range(1, 6):
        try:
            docs = extract_docs(
                api_get(
                    "srchBooks",
                    keyword=author,
                    pageNo=page,
                    pageSize=100,
                )
            )
        except Exception:
            break

        if not docs:
            break

        for d in docs:
            authors = d.get("authors") or d.get("author") or ""
            if author_norm not in norm(authors):
                continue

            isbn = str(
                d.get("isbn13")
                or d.get("isbn")
                or ""
            ).replace("-", "")
            title = (
                d.get("bookname")
                or d.get("bookName")
                or d.get("title")
                or ""
            ).strip()

            if not isbn or not title or isbn in seen_isbn:
                continue

            seen_isbn.add(isbn)
            found.append({
                "title": title,
                "author": authors,
                "isbn": isbn,
            })

            if len(found) >= max_books:
                return found

        if len(docs) < 100:
            break

    return found


def author_only_library_results(author):
    """
    저자만 입력한 경우:
    정보나루의 저자 검색 결과를 ISBN별로 각 도서관에 대입해
    '서로 다른 제목 수'와 현재 대출가능 제목 수를 집계한다.
    """
    candidates = author_book_candidates(author)

    results = {
        lib["key"]: {
            "name": lib["label"],
            "owned_titles": [],
            "available_titles": [],
            "error": "",
        }
        for lib in LIBRARIES
    }

    if not AUTH_KEY:
        for lib in LIBRARIES:
            results[lib["key"]]["error"] = "정보나루 API 키가 필요합니다."
        return results

    if not candidates:
        return results

    lib_codes = {}
    for lib in LIBRARIES:
        try:
            lib_codes[lib["key"]] = resolve_lib_code(lib)
        except Exception:
            lib_codes[lib["key"]] = ""

    # API 호출 수가 많아질 수 있으므로 병렬 처리.
    jobs = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        for lib in LIBRARIES:
            code = lib_codes.get(lib["key"], "")
            if not code:
                results[lib["key"]]["error"] = "도서관 코드 확인 실패"
                continue

            for c in candidates:
                fut = executor.submit(book_exist, code, c["isbn"])
                jobs[fut] = (lib["key"], c)

        raw_hits = {lib["key"]: [] for lib in LIBRARIES}

        for fut in as_completed(jobs):
            lib_key, c = jobs[fut]
            try:
                has, loan = fut.result()
            except Exception:
                continue

            if has:
                raw_hits[lib_key].append({
                    "title": c["title"],
                    "available": bool(loan),
                })

    # 여러 ISBN/판본이 같은 제목이면 제목 1종으로 합친다.
    for lib in LIBRARIES:
        by_title = {}
        for hit in raw_hits.get(lib["key"], []):
            k = norm(hit["title"])
            if not k:
                continue
            if k not in by_title:
                by_title[k] = {
                    "title": hit["title"],
                    "available": bool(hit["available"]),
                }
            else:
                by_title[k]["available"] = (
                    by_title[k]["available"] or bool(hit["available"])
                )

        items = sorted(
            by_title.values(),
            key=lambda x: norm(x["title"])
        )
        results[lib["key"]]["owned_titles"] = [x["title"] for x in items]
        results[lib["key"]]["available_titles"] = [
            x["title"] for x in items if x["available"]
        ]

    return results


def render_author_only_results(author_queries):
    if not author_queries:
        return

    st.markdown("---")
    st.markdown("## ✍️ 저자별 도서관 검색")

    st.caption(
        "도서명을 비우고 저자명만 입력한 경우, "
        "해당 저자의 책을 도서관별로 찾아 서로 다른 제목 수를 비교합니다."
    )

    for author in author_queries:
        st.markdown(f"### {author}")

        results = author_only_library_results(author)

        ranking = []
        for lib in LIBRARIES:
            r = results[lib["key"]]
            ranking.append({
                "name": lib["label"],
                "key": lib["key"],
                "owned": len(r["owned_titles"]),
                "available": len(r["available_titles"]),
                "error": r.get("error", ""),
            })

        ranking.sort(
            key=lambda x: (-x["owned"], -x["available"], x["name"])
        )

        for rank, item in enumerate(ranking, start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "4위")
            if item["error"]:
                st.markdown(
                    f"**{medal} {item['name']}** — 확인 실패"
                )
            else:
                st.markdown(
                    f"**{medal} {item['name']}** — "
                    f"소장 **{item['owned']}종** · "
                    f"지금 대출가능 **{item['available']}종**"
                )

        for item in ranking:
            r = results[item["key"]]
            with st.expander(
                f"{item['name']} — 소장 {item['owned']}종 / "
                f"대출가능 {item['available']}종"
            ):
                if r.get("error"):
                    st.warning(r["error"])
                    continue

                if r["available_titles"]:
                    st.markdown("**🟢 지금 빌릴 수 있는 책**")
                    for t in r["available_titles"]:
                        st.markdown(f"- {t}")

                unavailable = [
                    t for t in r["owned_titles"]
                    if t not in set(r["available_titles"])
                ]
                if unavailable:
                    st.markdown("**🟡 소장은 있지만 지금 바로 못 빌리는 책**")
                    for t in unavailable:
                        st.markdown(f"- {t}")

                if not r["owned_titles"]:
                    st.info("소장 도서가 확인되지 않았어요.")


def resolve_lib_code(lib):
    best = ("", 0, "")

    for row in seoul_libraries():
        name = (
            row.get("libName")
            or row.get("libname")
            or row.get("name")
            or ""
        )

        code = str(
            row.get("libCode")
            or row.get("libcode")
            or row.get("code")
            or ""
        )

        for alias in lib["aliases"]:
            s = score(alias, name)
            if s > best[1]:
                best = (code, s, name)

    return best[0] if best[1] >= 0.70 else ""


@st.cache_data(ttl=120, show_spinner=False)
def book_exist(lib_code, isbn):
    data = api_get(
        "bookExist",
        libCode=lib_code,
        isbn13=isbn,
    )

    res = data.get("response", data)
    result = res.get(
        "result",
        res.get("results", res),
    )

    if isinstance(result, list):
        result = result[0] if result else {}

    if not isinstance(result, dict):
        result = {}

    def yes(v):
        return str(v).strip().upper() in {
            "Y",
            "YES",
            "TRUE",
            "1",
            "가능",
            "대출가능",
        }

    return (
        yes(result.get("hasBook")),
        yes(result.get("loanAvailable")),
    )


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
    cands = [
        c
        for c in cands
        if c["score"] >= 0.62
    ][:8]

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
            has, loan = book_exist(
                code,
                c["isbn"],
            )
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
            "status": (
                f"🟢 소장 판본 {held} / "
                f"대출가능 판본 {available}"
            ),
            "available": available,
            "copies": held,
            "url": lib["official"],
            "source": "정보나루",
        }

    return {
        "status": (
            f"🟡 소장 판본 {held} / "
            f"현재 대출가능 0"
        ),
        "available": 0,
        "copies": held,
        "url": lib["official"],
        "source": "정보나루",
    }


# -----------------------------
# 종로구립도서관 검색 - requests + 정보나루 fallback
# -----------------------------
JONGNO_SEARCH_URL = "https://lib.jongno.go.kr/plus_m/search_list_klas.php"


def _jongno_pick_form(soup):
    """
    V4.6: 종로구 '자료검색' form을 우선 선택.
    헤더 통합검색 form을 잘못 고르는 일을 막기 위해
    action/search_list_klas.php, 검색어 placeholder, 입력 name을 함께 점수화한다.
    """
    candidates = []

    for form_index, form in enumerate(soup.find_all("form")):
        action = str(form.get("action") or "")
        method = str(form.get("method") or "get").lower()
        form_text = form.get_text(" ", strip=True)

        for inp_index, inp in enumerate(form.find_all("input")):
            typ = (inp.get("type") or "text").lower()
            if typ not in ("text", "search", ""):
                continue

            name = str(inp.get("name") or "")
            placeholder = str(inp.get("placeholder") or "")
            iid = str(inp.get("id") or "")
            hint = f"{name} {placeholder} {iid}".lower()

            pts = 0

            # 자료검색 페이지 자체를 제출하는 form이면 가장 강하게 우선.
            if "search_list_klas.php" in action:
                pts += 500
            if "klas" in action.lower():
                pts += 150

            if "검색어" in placeholder:
                pts += 120
            if "도서" in placeholder or "자료" in placeholder:
                pts += 40

            for token, score in (
                ("search", 45),
                ("keyword", 40),
                ("query", 35),
                ("word", 25),
                ("title", 20),
            ):
                if token in hint:
                    pts += score

            if "자료검색" in form_text:
                pts += 80
            elif "검색" in form_text:
                pts += 15

            # 너무 짧은 사이트 헤더용 form보다 여러 검색 제어가 있는 form을 우대.
            pts += min(30, len(form.find_all(["input", "select"])) * 2)

            candidates.append({
                "score": pts,
                "form": form,
                "input": inp,
                "form_index": form_index,
                "input_index": inp_index,
                "action": action,
                "method": method,
                "input_name": name,
                "placeholder": placeholder,
            })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[0]

def _jongno_form_payload(form, search_input, title):
    payload = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "text").lower()
        if typ in ("submit", "button", "image", "file"):
            continue
        if typ in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        payload[name] = inp.get("value", "")

    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        opt = sel.find("option", selected=True) or sel.find("option")
        if opt is not None:
            payload[name] = opt.get("value", "")

    search_name = search_input.get("name")
    if not search_name:
        raise RuntimeError("검색 입력창 name 속성 없음")
    payload[search_name] = title
    return payload


@st.cache_data(ttl=90, show_spinner=False)
def fetch_jongno(title):
    """
    V5.4:
    종로구 통합 자료검색 form을 제출하고, 검색결과가 여러 페이지면
    뒤 페이지까지 함께 가져온다.

    종로구 통합검색은 한 페이지에 일부 결과만 보여주므로,
    목표 도서관 자료가 2페이지 이후에 있으면 1페이지만 파싱할 때
    '소장 없음'으로 잘못 판정될 수 있다.
    """
    from urllib.parse import urljoin

    session = requests.Session()
    headers = {
        **HEADERS,
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }

    last_err = None

    for attempt in range(3):
        try:
            first = session.get(JONGNO_SEARCH_URL, headers=headers, timeout=20)
            first.raise_for_status()

            soup = BeautifulSoup(first.text, "html.parser")
            picked = _jongno_pick_form(soup)
            if not picked:
                raise RuntimeError("종로구 검색 form을 찾지 못함")

            form = picked["form"]
            search_input = picked["input"]
            payload = _jongno_form_payload(form, search_input, title)
            action = urljoin(first.url, form.get("action") or first.url)
            method = (form.get("method") or "get").lower()

            def submit(page=None):
                page_payload = dict(payload)
                if page is not None and page > 1:
                    # 종로구 검색 페이지의 페이지 번호 파라미터
                    page_payload["Page"] = page

                if method == "post":
                    rr = session.post(
                        action,
                        data=page_payload,
                        headers={**headers, "Referer": first.url},
                        timeout=25,
                    )
                else:
                    rr = session.get(
                        action,
                        params=page_payload,
                        headers={**headers, "Referer": first.url},
                        timeout=25,
                    )

                rr.raise_for_status()
                return rr

            r = submit()
            page_htmls = [r.text]
            result_url = r.url

            # 검색결과 건수 파악. 종로구 통합검색은 현재 페이지당 약 15건.
            result_soup = BeautifulSoup(r.text, "html.parser")
            result_text = re.sub(
                r"\s+", " ", result_soup.get_text(" ", strip=True)
            )
            count_match = re.search(
                r"검색(?:결과|건수)\s*[:：]\s*([0-9,]+)\s*건",
                result_text,
            )
            result_count = (
                int(count_match.group(1).replace(",", ""))
                if count_match else 0
            )

            # 실제 첫 페이지의 결과 카드 수를 페이지 크기로 우선 사용.
            first_cards = result_soup.select(".book-container")
            page_size = len(first_cards) if len(first_cards) >= 5 else 15

            total_pages = (
                max(1, math.ceil(result_count / page_size))
                if result_count else 1
            )

            # 일반적인 제목 검색은 몇 페이지 안쪽이다.
            # 폭넓은 검색어가 들어와도 무한 요청하지 않도록 최대 10페이지.
            total_pages = min(total_pages, 10)

            for page in range(2, total_pages + 1):
                try:
                    rr = submit(page)
                    page_htmls.append(rr.text)
                except Exception:
                    # 한 페이지가 실패해도 이미 받은 결과는 사용한다.
                    continue

            # 여러 HTML 문서를 하나의 wrapper 아래 합쳐 파서가 전 페이지를 보게 한다.
            combined = "<div id='jongno-pages'>" + "\n".join(page_htmls) + "</div>"
            return combined, result_url

        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))

    raise last_err or RuntimeError("종로구 공식검색 실패")


def _jongno_names(library_key):
    if library_key == "cheongun":
        return ["청운문학도서관", "종로구립청운문학도서관"]
    return ["청운효자동 북카페", "청운 효자동 북카페", "청운효자동북카페"]


def _jongno_parse(html, title, library_key, author=''):
    """
    V5.3:
    종로구 통합검색의 실제 책 결과 카드를 1건씩 판독한다.

    - .book-container 태그 종류(article/div/li 등)에 의존하지 않는다.
    - 목표 도서관 이름이 카드 안에 있는 결과만 사용한다.
    - 큰글자책/대활자본은 제외한다.
    - 기본 카드 선택이 실패하면 제목 텍스트에서 가장 가까운 결과 컨테이너를 다시 찾는다.
    """
    soup = BeautifulSoup(html, "html.parser")
    target_names = [norm(x) for x in _jongno_names(library_key)]

    requested_title = str(title or "").strip()
    requested_norm = norm(requested_title)
    author_norm = norm(author)

    large_markers = ("큰글자", "큰글자도서", "큰글자책", "대활자")

    records = []
    seen = set()

    def add_card(card):
        text = re.sub(r"\s+", " ", card.get_text(" ", strip=True)).strip()
        if not text:
            return

        ntext = norm(text)

        if not any(name and name in ntext for name in target_names):
            return

        if any(marker in text for marker in large_markers):
            return

        if requested_norm and requested_norm not in ntext:
            return

        if author_norm and author_norm not in ntext:
            return

        call_match = re.search(
            r"청구기호\s*[:：]?\s*(.+?)(?=\s*도서관\s*[:：]|\s*대출(?:가능|불가|중)|$)",
            text,
        )
        call_no = (
            re.sub(r"\s+", " ", call_match.group(1)).strip()
            if call_match else ""
        )

        href = ""
        a = card.find("a", href=True)
        if a is not None:
            href = str(a.get("href") or "")

        unavailable = any(x in text for x in (
            "대출불가", "대출중", "예약중", "이용불가", "관외대출불가"
        ))
        is_available = (not unavailable) and any(
            x in text for x in ("대출가능", "비치중")
        )

        if call_no:
            key = (library_key, norm(call_no))
        elif href:
            key = (library_key, href, norm(text))
        else:
            key = (library_key, norm(text))

        if key in seen:
            return

        seen.add(key)
        records.append({
            "call_no": call_no,
            "available": bool(is_available),
        })

    # 1차: 사이트의 반복 결과 카드 class
    cards = soup.select(".book-container")
    for card in cards:
        add_card(card)

    # 2차 fallback:
    # .book-container 구조가 달라도 제목이 보이는 노드에서 가장 가까운
    # '도서관 + 대출상태' 컨테이너를 찾아 다시 판독한다.
    if not records and requested_norm:
        candidate_nodes = []

        for node in soup.find_all(
            ["a", "strong", "b", "h2", "h3", "h4", "dt", "span", "div"]
        ):
            node_text = re.sub(
                r"\s+", " ", node.get_text(" ", strip=True)
            ).strip()

            if not node_text:
                continue

            if requested_norm not in norm(node_text):
                continue

            parent = node

            for _ in range(7):
                parent = getattr(parent, "parent", None)
                if parent is None:
                    break

                ptext = re.sub(
                    r"\s+", " ", parent.get_text(" ", strip=True)
                ).strip()

                if not ptext or len(ptext) > 2500:
                    continue

                pn = norm(ptext)

                has_target_library = any(
                    name and name in pn for name in target_names
                )
                has_state = any(
                    x in ptext
                    for x in ("대출가능", "대출불가", "대출중", "비치중")
                )

                if (
                    has_target_library
                    and has_state
                    and requested_norm in pn
                ):
                    candidate_nodes.append(parent)
                    break

        candidate_nodes.sort(
            key=lambda node: len(
                re.sub(
                    r"\s+",
                    " ",
                    node.get_text(" ", strip=True),
                )
            )
        )

        for card in candidate_nodes:
            add_card(card)

    copies = len(records)
    available = sum(1 for r in records if r["available"])

    if copies == 0:
        status = "⚪ 소장 없음"
    elif available > 0:
        status = f"🟢 소장 {copies} / 즉시대출 {available}"
    else:
        status = f"🟡 소장 {copies} / 즉시대출 없음"

    return {
        "status": status,
        "available": available,
        "copies": copies,
    }


def jongno_result(lib, title, author=''):
    """
    종로구 공식 통합검색만 사용한다.
    정보나루 fallback으로 소장 여부를 추정하지 않아 잘못된 판정이 섞이지 않게 한다.
    """
    errors = []
    successful_response = False
    candidates = []

    # 넓은 검색어가 필요한 경우를 대비해 변형 검색도 하되,
    # 판독은 항상 사용자가 입력한 원래 제목/저자 기준으로 검증한다.
    for q in query_variants(title):
        try:
            html, result_url = fetch_jongno(q)
            successful_response = True
            parsed = _jongno_parse(html, title, lib["key"], author=author)

            if int(parsed.get("copies") or 0) > 0:
                candidates.append({
                    **parsed,
                    "url": result_url,
                    "source": f"종로구립도서관 공식검색 · 검색어: {q}",
                    "_query_len": len(q),
                })

        except Exception as e:
            errors.append(f"{type(e).__name__}: {str(e)[:180]}")

    if candidates:
        # 같은 책이 여러 검색어 변형에서 잡히면 복본 수가 가장 풍부한 결과를 사용한다.
        candidates.sort(
            key=lambda r: (
                int(r.get("copies") or 0),
                int(r.get("available") or 0),
                r.get("_query_len", 0),
            ),
            reverse=True,
        )
        best = candidates[0].copy()
        best.pop("_query_len", None)
        return best

    if successful_response:
        return {
            "status": "⚪ 소장 없음",
            "available": 0,
            "copies": 0,
            "url": official_url(lib, title),
            "source": "종로구립도서관 공식검색",
        }

    err = " | ".join(errors[-2:]) if errors else "원인 미상"
    return {
        "status": "⚠️ 조회 실패 · 다시 시도 가능",
        "available": 0,
        "copies": None,
        "url": official_url(lib, title),
        "source": f"종로구 공식검색 실패: {err}",
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

    st.caption(
        "책 목록을 넣으면 네 도서관을 한 번에 확인하고, "
        "오늘 가장 많이 빌릴 수 있는 곳을 알려줍니다."
    )

    with st.expander("검색 대상 도서관"):
        st.write(
            "정독도서관 · 서울특별시교육청 어린이도서관 · "
            "청운문학도서관 · 청운효자동 북카페"
        )

    st.markdown(
        "※ 저자명은 선택사항입니다. 동일·유사 제목의 도서가 있는 경우 "
        "저자명을 함께 입력하면 더 정확하게 검색할 수 있습니다."
    )
    st.caption(
        "도서명을 비우고 저자명만 입력하면, 해당 저자의 책을 도서관별로 검색합니다."
    )

    header_left, header_right = st.columns([1.45, 1])
    with header_left:
        st.markdown("**도서명**")
    with header_right:
        st.markdown("**저자명 (선택)**")

    input_rows = []
    for i in range(10):
        col_title, col_author = st.columns([1.45, 1])
        with col_title:
            title_value = st.text_input(
                f"도서명 {i + 1}",
                key=f"book_title_{i}",
                placeholder="도서명",
                label_visibility="collapsed",
            )
        with col_author:
            author_value = st.text_input(
                f"저자명 {i + 1}",
                key=f"book_author_{i}",
                placeholder="저자명",
                label_visibility="collapsed",
            )

        input_rows.append({
            "title": title_value.strip(),
            "author": author_value.strip(),
        })

    if not st.button(
        "🔎 한꺼번에 검색",
        type="primary",
        width="stretch",
    ):
        return

    books = []
    author_queries = []
    seen_books = set()
    seen_authors = set()

    for row in input_rows:
        title = str(row.get("title", "") or "").strip()
        author = str(row.get("author", "") or "").strip()

        # 둘 다 빈 행은 무시
        if not title and not author:
            continue

        # 저자만 입력한 행은 '저자별 검색'으로 분리
        if not title and author:
            ak = norm(author)
            if ak and ak not in seen_authors:
                seen_authors.add(ak)
                author_queries.append(author)
            continue

        # 제목이 있는 행은 기존 책 검색
        key = (norm(title), norm(author))
        if key in seen_books:
            continue

        seen_books.add(key)
        books.append({"title": title, "author": author})

    if not books and not author_queries:
        st.warning("도서명 또는 저자명을 한 개 이상 입력해주세요.")
        return

    if len(books) > 15:
        st.info(
            "도서명 검색은 한 번에 최대 15권까지 처리합니다. "
            "안정적인 사용은 10권 안팎을 권장해요."
        )

    books = books[:15]
    titles = [b["title"] for b in books]

    # 저자만 입력한 검색은 일반 제목 검색과 별도로 먼저 보여준다.
    if author_queries:
        with st.spinner("저자별 도서관 소장 도서를 확인하는 중..."):
            render_author_only_results(author_queries)

    # 제목 검색이 하나도 없으면 저자 검색 결과만 보여주고 종료.
    if not titles:
        st.caption(
            "※ 저자만 입력한 검색은 정보나루의 서지/소장 정보를 기준으로 집계합니다."
        )
        return

    restored_detail, restored_completed = load_checkpoint(
        titles
    )

    if restored_completed:
        st.info(
            f"이전 검색에서 완료된 "
            f"{len(restored_completed)}개 조회를 불러왔어요. "
            "이어서 검색합니다."
        )

    detail, completed_pairs = run_resilient_search(
        titles,
        previous_detail=restored_detail,
        previous_completed=restored_completed,
        books=books,
    )

    failed_pairs = has_failed_items(
        titles,
        detail,
    )

    if failed_pairs:
        st.warning(
            f"⚠️ 일시적으로 실패한 조회가 "
            f"{len(failed_pairs)}개 있어요. "
            "나머지 결과는 그대로 보존했습니다."
        )

        if st.button(
            "🔁 실패한 항목만 다시 검색",
            use_container_width=True,
        ):
            retry_completed = set(
                completed_pairs
            )

            for pair in failed_pairs:
                retry_completed.discard(pair)

            with st.spinner(
                "실패한 항목만 다시 확인하는 중..."
            ):
                detail, completed_pairs = run_resilient_search(
                    titles,
                    previous_detail=detail,
                    previous_completed=retry_completed,
                    books=books,
                    only_failed=True,
                )

            st.rerun()

    render_priority_summary(
        titles,
        detail,
    )

    if len(titles) >= 8:
        st.caption(
            "⏱️ 10권 안팎 검색은 여러 공식 검색을 함께 사용해 "
            "시간이 걸릴 수 있어요. 공식 사이트 응답이 늦으면 자동 재시도합니다."
        )

    st.subheader("검색 결과")

    failed_checks = 0

    for title in titles:
        for lib in LIBRARIES:
            status = detail[title][lib["key"]]["status"]

            if any(
                marker in status
                for marker in (
                    "조회 오류",
                    "자동조회 실패",
                    "조회 실패",
                )
            ):
                failed_checks += 1

    if failed_checks:
        st.warning(
            f"총 {len(titles)}권 검색은 완료했지만 "
            f"{failed_checks}개 도서관 조회가 일시적으로 실패했어요. "
            "해당 항목만 잠시 후 다시 검색하면 됩니다."
        )
    else:
        st.success(
            f"{len(titles)}권 × 4개 도서관 조회 완료"
        )

    author_by_title = {b["title"]: b.get("author", "") for b in books}

    for title in titles:
        author = author_by_title.get(title, "")
        heading = f"### 📖 {title}"
        if author:
            heading += f" · {author}"
        st.markdown(heading)

        for lib in LIBRARIES:
            result = detail[title][lib["key"]]

            st.markdown(
                f"**{lib['label']}**"
            )

            st.write(
                result["status"]
            )

            if any(
                marker in result.get("status", "")
                for marker in ("조회 실패", "자동조회 실패", "조회 오류")
            ):
                st.caption("진단: " + result.get("source", "원인 정보 없음"))

        if title != titles[-1]:
            st.divider()

    with st.expander(
        "🔗 공식 검색으로 최종 확인"
    ):
        for title in titles:
            st.markdown(
                f"**{title}**"
            )

            for lib in LIBRARIES:
                url = detail[title][lib["key"]]["url"]

                st.markdown(
                    f'[{lib["label"]} 공식 확인]({url})'
                )

    st.caption(
        "※ V5.7 FINAL · 위쪽 숫자는 '오늘 바로 빌릴 수 있는 제목 수 / 내가 입력한 전체 제목 수'입니다. 실제 소장 복본 수는 아래 책별 상세 결과에서만 표시합니다. "
        "큰글자책/대활자본은 제외하며, 소장 없음과 조회 실패를 구분합니다."
    )


if __name__ == "__main__":
    main()
