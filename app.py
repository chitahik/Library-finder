# LIBRARY FINDER V4.7 VERIFIED-R2
import os
import re
import time
import json
from pathlib import Path
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
    복본 권수 합계가 아니라 '입력한 서로 다른 제목 중 몇 종을 오늘 빌릴 수 있는가'로 계산.
    동시에 큰글자책 판본이 있는 경우 별도 메타데이터로 보존한다.
    """
    summaries = []

    for lib in LIBRARIES:
        key = lib["key"]
        available_titles = []
        owned_not_now = []
        not_owned = []
        failed = []
        large_print_titles = []
        large_print_available_titles = []
        large_print_total_copies = 0
        large_print_available_copies = 0

        for title in titles:
            result = detail[title][key]

            try:
                large_total = int(result.get("large_total") or 0)
            except Exception:
                large_total = 0
            try:
                large_available = int(result.get("large_available") or 0)
            except Exception:
                large_available = 0

            if large_total > 0:
                large_print_titles.append(title)
                large_print_total_copies += large_total
            if large_available > 0:
                large_print_available_titles.append(title)
                large_print_available_copies += large_available

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
            "large_print_titles": large_print_titles,
            "large_print_available_titles": large_print_available_titles,
            "large_print_total_copies": large_print_total_copies,
            "large_print_available_copies": large_print_available_copies,
        })

    summaries.sort(
        key=lambda x: (
            -x["available_count"],
            -x["confirmed_owned_count"],
            len(x["failed"]),
            x["name"],
        )
    )
    return summaries

def render_priority_summary(titles, detail):
    summaries = build_library_summary(titles, detail)
    total = len(titles)

    st.markdown("## 📍 오늘 어디로 갈까?")
    best = summaries[0] if summaries else None

    if best:
        st.success(
            f"**1순위: {best['name']}** — 입력한 {total}권 중 "
            f"**{best['available_count']}권을 오늘 바로 빌릴 수 있어요.**"
        )

    for rank, item in enumerate(summaries, start=1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}위")
        extra = ""
        if item["failed"]:
            extra = f" · 확인 실패 {len(item['failed'])}권"
        large_note = ""
        if item.get("large_print_total_copies", 0):
            if item.get("large_print_available_copies", 0):
                large_note = (
                    f" · **큰글자책 {item['large_print_total_copies']}권 "
                    f"(즉시대출 {item['large_print_available_copies']}권)**"
                )
            else:
                large_note = (
                    f" · 큰글자책 {item['large_print_total_copies']}권 "
                    f"(즉시대출 없음)"
                )

        st.markdown(
            f"**{medal} {item['name']}** — "
            f"즉시대출 **{item['available_count']}/{total}권**"
            f" · 소장 확인 {item['confirmed_owned_count']}권"
            f"{large_note}{extra}"
        )

    st.markdown("---")
    st.markdown("## 📚 도서관별 오늘 빌릴 수 있는 책")

    for rank, selected in enumerate(summaries, start=1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "4위")
        title_line = (
            f"{medal} {selected['name']} — "
            f"오늘 바로 {len(selected['available_titles'])}/{total}권"
        )

        with st.expander(title_line, expanded=(rank == 1)):
            if selected["available_titles"]:
                st.markdown(
                    f"**🟢 오늘 바로 빌릴 수 있는 책 "
                    f"{len(selected['available_titles'])}권**"
                )
                for t in selected["available_titles"]:
                    result = detail[t][selected["key"]]
                    try:
                        large_available = int(result.get("large_available") or 0)
                    except Exception:
                        large_available = 0

                    if large_available > 0:
                        st.markdown(f"- {t} **(큰글자책 즉시대출 가능)**")
                    else:
                        st.markdown(f"- {t}")
            else:
                st.info("오늘 바로 빌릴 수 있다고 확인된 책이 없어요.")

            if selected["owned_not_now"]:
                st.markdown(
                    f"**🟠 소장은 있지만 지금 바로 못 빌리거나 "
                    f"상태 확인이 필요한 책 {len(selected['owned_not_now'])}권**"
                )
                for t in selected["owned_not_now"]:
                    st.markdown(f"- {t}")

            if selected["not_owned"]:
                st.caption(f"⚪ 소장 없음 {len(selected['not_owned'])}권")

            if selected["failed"]:
                st.warning(f"⚠️ 조회 실패 {len(selected['failed'])}권")
                for t in selected["failed"]:
                    st.markdown(f"- {t}")

    st.caption(
        "※ 순위는 복본 수가 아니라, 입력한 책 제목 중 "
        "'오늘 즉시대출 가능한 제목 수'를 기준으로 계산합니다."
    )

    st.markdown("---")
    st.markdown("## 🔎 책별 상세 결과")


# -----------------------------
# V3.6 검색 안정성: 체크포인트 / 부분 결과 보존
# -----------------------------
CHECKPOINT_VERSION = "v4.7-r2-record-boundary-20260902"
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


def search_one_library(lib, title):
    try:
        if lib["type"] == "sen":
            return sen_result(lib, title)
        if lib["type"] == "jongno":
            return jongno_result(lib, title)
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
    previous_completed=None,
    only_failed=False,
):
    detail = previous_detail or {}
    completed = set(previous_completed or set())

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

            result = search_one_library(lib, title)
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
    clean = re.sub(r"[!！?？:：·ㆍ]", " ", title)
    clean = re.sub(r"\s+", " ", clean).strip()

    variants = [title.strip(), clean]
    words = clean.split()

    if len(words) >= 4:
        variants.append(" ".join(words[:4]))
    if len(words) >= 3:
        variants.append(" ".join(words[:3]))
        # 예: "바깥은 여름 김애란" → "바깥은 여름"
        variants.append(" ".join(words[:2]))

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


def sen_result(lib, title):
    """
    서울시교육청 도서관:
    1) 입력값 그대로 먼저 검색
    2) '제목 + 저자'처럼 보이면 제목만 검색
    3) 결과 카드 안에서 제목과 저자 힌트를 다시 검증

    즉, 넓게 검색하더라도 다른 책을 소장으로 오인하지 않는다.
    """
    last_err = None
    had_successful_response = False

    title_only, author_hint = _split_title_author_hint(title)

    queries = []
    for q in (str(title).strip(), title_only):
        if q and q not in queries:
            queries.append(q)

    for q in queries:
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
            requested_title=title_only or title,
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
    V4.6 진단 버전.
    종로구 공식 검색 form을 제출하고,
    실제 선택된 form/입력 name/결과 건수/큰글자 문자열 개수를 함께 반환한다.
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
    last_diag = {}

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

            if method == "post":
                r = session.post(
                    action,
                    data=payload,
                    headers={**headers, "Referer": first.url},
                    timeout=25,
                )
            else:
                r = session.get(
                    action,
                    params=payload,
                    headers={**headers, "Referer": first.url},
                    timeout=25,
                )

            r.raise_for_status()

            result_soup = BeautifulSoup(r.text, "html.parser")
            result_text = re.sub(
                r"\s+", " ", result_soup.get_text(" ", strip=True)
            )

            count_match = re.search(
                r"검색결과\s*[:：]\s*([0-9,]+)\s*건",
                result_text,
            )
            result_count = (
                int(count_match.group(1).replace(",", ""))
                if count_match else None
            )

            diag = {
                "query": title,
                "form_score": picked.get("score"),
                "form_index": picked.get("form_index"),
                "form_action": str(form.get("action") or ""),
                "resolved_action": action,
                "method": method.upper(),
                "search_input_name": str(search_input.get("name") or ""),
                "search_input_id": str(search_input.get("id") or ""),
                "search_placeholder": str(search_input.get("placeholder") or ""),
                "payload_keys": sorted(str(k) for k in payload.keys()),
                "result_url": r.url,
                "result_count": result_count,
                "large_text_count": result_text.count("큰글자"),
                "large_alt_text_count": result_text.count("대활자"),
                "cheongun_text_count": result_text.count("청운문학도서관"),
                "bookcafe_text_count": (
                    result_text.count("청운효자동 북카페")
                    + result_text.count("청운효자동북카페")
                ),
                "html_length": len(r.text),
            }

            return r.text, r.url, diag

        except Exception as e:
            last_err = e
            last_diag = {
                "query": title,
                "attempt": attempt + 1,
                "error": f"{type(e).__name__}: {str(e)[:300]}",
            }
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))

    err = last_err or RuntimeError("종로구 공식검색 실패")
    setattr(err, "_jongno_diag", last_diag)
    raise err

def _jongno_names(library_key):
    if library_key == "cheongun":
        return ["청운문학도서관", "종로구립청운문학도서관"]
    return ["청운효자동 북카페", "청운 효자동 북카페", "청운효자동북카페"]


def _jongno_parse(html, title, library_key):
    """
    V4.7: 종로구 검색결과를 '제목 링크 1개 = 자료 1건'으로 파싱한다.
    큰 부모 div의 합쳐진 텍스트를 세지 않고, 각 제목 <a>에서 시작해
    해당 결과의 저자/청구기호/도서관/대출상태가 들어 있는 최소 컨테이너만 사용한다.
    """
    soup = BeautifulSoup(html, "html.parser")

    lib_aliases = {
        "cheongun": ["청운문학도서관"],
        "bookcafe": ["청운효자동 북카페", "청운효자동북카페"],
    }
    aliases = lib_aliases.get(library_key, [])
    if not aliases:
        return {
            "status": "⚪ 소장 없음",
            "available": 0,
            "copies": 0,
            "regular_total": 0,
            "regular_available": 0,
            "large_total": 0,
            "large_available": 0,
        }

    def compact(s):
        return re.sub(r"\s+", " ", str(s or "")).strip()

    def nt(s):
        return normalize_text(s)

    # 사용자 입력에서 저자 힌트를 떼고 제목 후보를 만든다.
    requested_title, author_hint = _split_title_author_hint(title)
    requested_title = requested_title or title
    title_words = compact(requested_title).split()

    title_variants = []
    for n in range(len(title_words), 1, -1):
        title_variants.append(" ".join(title_words[:n]))
    if requested_title:
        title_variants.append(requested_title)
    title_variants = [nt(x) for x in title_variants if x]
    title_variants = list(dict.fromkeys(title_variants))

    records = []
    seen = set()

    # 공식 검색결과의 제목 링크를 기준점으로 삼는다.
    for a in soup.find_all("a"):
        link_title = compact(a.get_text(" ", strip=True))
        if not link_title:
            continue

        nlink = nt(link_title)
        if not any(v and (v in nlink or nlink in v) for v in title_variants):
            continue

        # 제목 링크에서 위로 올라가며 '이 자료 한 건'의 경계를 먼저 찾는다.
        # li/tr/article 같은 반복 레코드 태그를 최우선으로 사용해야
        # 상위 ul/div의 다른 도서관/큰글자책 결과가 섞이지 않는다.
        card = a.find_parent(["li", "tr", "article"])

        if card is None:
            node = a
            for _ in range(8):
                node = getattr(node, "parent", None)
                if node is None:
                    break

                txt = compact(node.get_text(" ", strip=True))
                ntx = nt(txt)

                has_library = any(nt(alias) in ntx for alias in aliases)
                has_callno = ("청구기호" in txt)
                has_state = ("대출가능" in txt or "대출불가" in txt or
                             "대출중" in txt or "비치중" in txt)

                # 여러 제목 링크가 들어있는 상위 컨테이너는 자료 한 건이 아니다.
                title_link_count = sum(
                    1 for aa in node.find_all("a")
                    if any(v and (v in nt(aa.get_text(" ", strip=True)) or
                                  nt(aa.get_text(" ", strip=True)) in v)
                           for v in title_variants)
                )

                if has_library and has_callno and has_state and title_link_count <= 1:
                    card = node
                    break

        if card is None:
            continue

        text = compact(card.get_text(" ", strip=True))
        ntext = nt(text)

        # 같은 최소 컨테이너 안에서 목표 도서관인지 다시 확인.
        matched_alias = next(
            (alias for alias in aliases if nt(alias) in ntext),
            None
        )
        if not matched_alias:
            continue

        # 제목이 같아도 저자가 명시된 입력이면 다른 저자 결과를 배제.
        if author_hint and nt(author_hint) not in ntext:
            continue

        is_large = ("큰글자" in text or "대활자" in text)

        # '대출불가'에는 '대출가능'이 들어있지 않지만 상태를 명시적으로 우선 판정.
        unavailable = any(x in text for x in (
            "대출불가", "대출중", "예약중", "이용불가", "관외대출불가"
        ))
        available = (not unavailable) and any(
            x in text for x in ("대출가능", "비치중")
        )

        # 같은 자료가 중첩 DOM/중복 링크로 두 번 잡히는 것을 막는다.
        call_match = re.search(
            r"청구기호\s*[:：]?\s*(.+?)(?=\s*도서관\s*[:：]|\s*대출(?:가능|불가|중)|$)",
            text
        )
        callno = compact(call_match.group(1)) if call_match else ""

        key = (
            nt(link_title),
            nt(callno),
            nt(matched_alias),
            "L" if is_large else "R",
        )
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "title": link_title,
            "callno": callno,
            "library": matched_alias,
            "is_large": is_large,
            "available": bool(available),
            "text": text,
        })

    regular = [r for r in records if not r["is_large"]]
    large = [r for r in records if r["is_large"]]

    regular_total = len(regular)
    regular_available = sum(1 for r in regular if r["available"])
    large_total = len(large)
    large_available = sum(1 for r in large if r["available"])
    copies = regular_total + large_total
    available = regular_available + large_available

    if copies == 0:
        status = "⚪ 소장 없음"
    else:
        pieces = []
        if regular_total:
            pieces.append(
                f"일반판 {regular_total} / "
                + (f"즉시대출 {regular_available}" if regular_available else "즉시대출 없음")
            )
        if large_total:
            pieces.append(
                f"큰글자책 {large_total} / "
                + (f"즉시대출 {large_available}" if large_available else "즉시대출 없음")
            )

        dot = "🟢" if available > 0 else "🟡"
        status = dot + " " + " · ".join(pieces)

    return {
        "status": status,
        "available": available,
        "copies": copies,
        "regular_total": regular_total,
        "regular_available": regular_available,
        "large_total": large_total,
        "large_available": large_available,
        "_records": records,
    }

def jongno_result(lib, title):
    """
    종로구 공식검색을 여러 검색어 변형으로 끝까지 확인한 뒤
    가장 많은 실제 판본/복본을 잡아낸 결과를 채택한다.

    핵심:
    - 예: "바깥은 여름 김애란" 검색은 일반판 2권만 잡힐 수 있음
    - "바깥은 여름" 검색은 일반판 2 + 큰글자책 1을 잡을 수 있음
    - V4.2는 첫 번째 성공 결과에서 멈추지 않고 둘 다 본다.
    """
    web_errors = []
    web_succeeded = False
    candidates = []

    for q in query_variants(title):
        try:
            html, result_url, fetch_diag = fetch_jongno(q)
            web_succeeded = True

            # 현재 검색어 q를 기준으로 파싱해서 너무 넓은 제목 매칭을 줄인다.
            parsed = _jongno_parse(html, q, lib["key"])
            status = parsed.get("status", "")

            if "소장 없음" not in status:
                candidates.append({
                    **parsed,
                    "url": result_url,
                    "source": f"종로구립도서관 공식검색(requests) · 검색어: {q}",
                    "_query": q,
                    "_fetch_diag": fetch_diag,
                })

        except Exception as e:
            web_errors.append(f"{type(e).__name__}: {str(e)[:180]}")

    if candidates:
        # 가장 많은 실제 복본을 찾은 결과를 우선.
        # 동률이면 즉시대출 가능 수, 큰글자책 포착 수가 많은 결과 우선.
        candidates.sort(
            key=lambda r: (
                int(r.get("copies") or 0),
                int(r.get("available") or 0),
                int(r.get("large_total") or 0),
                len(str(r.get("_query", ""))),
            ),
            reverse=True,
        )
        best = candidates[0].copy()
        chosen_query = best.pop("_query", None)
        chosen_diag = best.pop("_fetch_diag", {}) or {}

        best["diagnostic"] = {
            **chosen_diag,
            "chosen_query": chosen_query,
            "parsed_copies": int(best.get("copies") or 0),
            "parsed_available": int(best.get("available") or 0),
            "parsed_large_total": int(best.get("large_total") or 0),
            "parsed_large_available": int(best.get("large_available") or 0),
        }
        return best

    # 공식 웹검색에서 하나도 못 잡았을 때만 정보나루 fallback
    try:
        api_result = data4library_result(lib, title)
        api_status = api_result.get("status", "")
        if "소장 판본" in api_status:
            api_result["source"] = "도서관정보나루 API fallback"
            return api_result
        api_note = api_status or "API 결과 없음"
    except Exception as e:
        api_note = f"{type(e).__name__}: {str(e)[:180]}"

    if web_succeeded:
        return {
            "status": "⚪ 소장 없음",
            "available": 0,
            "copies": 0,
            "url": official_url(lib, title),
            "source": f"종로구 공식검색 정상응답 / API: {api_note}",
        }

    err = " | ".join(web_errors[-2:]) if web_errors else "원인 미상"
    return {
        "status": "⚠️ 자동조회 실패 · 진단정보 확인",
        "available": 0,
        "copies": None,
        "url": official_url(lib, title),
        "source": f"공식검색: {err} / API: {api_note}",
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

    sample = (
        "우리 가족의 보물을 찾아라!\n"
        "목화씨\n"
        "도슨트 이창용의 미술대모험 2"
    )

    txt = st.text_area(
        "책 제목",
        height=210,
        placeholder=sample,
        help="한 줄에 한 권씩 입력하세요.",
    )

    if not st.button(
        "🔎 한꺼번에 검색",
        type="primary",
        width="stretch",
    ):
        return

    titles = []

    for line in txt.splitlines():
        t = line.strip(" \t•·-–—")

        if t and t not in titles:
            titles.append(t)

    if not titles:
        st.warning(
            "책 제목을 한 권 이상 입력해주세요."
        )
        return

    if len(titles) > 15:
        st.info(
            "한 번에 최대 15권까지 검색합니다. "
            "안정적인 사용은 10권 안팎을 권장해요."
        )

    titles = titles[:15]

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

    for title in titles:
        st.markdown(
            f"### 📖 {title}"
        )

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

    with st.expander("🧪 종로구 검색 진단 (V4.7)"):
        st.caption(
            "이 영역은 큰글자책 누락 원인을 찾기 위한 임시 진단입니다. "
            "검색 폼과 서버가 실제로 돌려준 결과를 보여줍니다."
        )

        for title in titles:
            st.markdown(f"**{title}**")

            for lib in LIBRARIES:
                if lib["type"] != "jongno":
                    continue

                result = detail[title][lib["key"]]
                diag = result.get("diagnostic") or {}

                st.markdown(f"**{lib['label']}**")

                if not diag:
                    st.code("진단 데이터 없음", language=None)
                    continue

                lines = [
                    f"선택 검색어: {diag.get('chosen_query')}",
                    f"검색 method: {diag.get('method')}",
                    f"form action: {diag.get('form_action')}",
                    f"검색 input name: {diag.get('search_input_name')}",
                    f"검색 placeholder: {diag.get('search_placeholder')}",
                    f"서버 검색결과 건수: {diag.get('result_count')}",
                    f"응답의 '큰글자' 문자열 수: {diag.get('large_text_count')}",
                    f"응답의 '대활자' 문자열 수: {diag.get('large_alt_text_count')}",
                    f"응답의 청운문학도서관 문자열 수: {diag.get('cheongun_text_count')}",
                    f"응답의 청운효자동 북카페 문자열 수: {diag.get('bookcafe_text_count')}",
                    f"파서가 잡은 전체 복본: {diag.get('parsed_copies')}",
                    f"파서가 잡은 즉시대출: {diag.get('parsed_available')}",
                    f"파서가 잡은 큰글자책: {diag.get('parsed_large_total')}",
                    f"파서가 잡은 큰글자책 즉시대출: {diag.get('parsed_large_available')}",
                ]
                st.code("\n".join(lines), language=None)

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
        "※ V4.7 VERIFIED · 종로구는 검색결과의 제목 링크 하나를 자료 한 건의 기준점으로 삼아 일반판/큰글자책과 대출상태를 분리 판독합니다. "
        "'소장 없음'과 '조회 실패'는 서로 다르게 표시합니다."
    )


if __name__ == "__main__":
    main()
