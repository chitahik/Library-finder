# LIBRARY FINDER V3.9 VERIFIED
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
    status = (result or {}).get("status", "")
    if "조회 오류" in status or "자동조회 실패" in status or "조회 실패" in status:
        return False
    if "즉시대출 없음" in status:
        return False
    return "즉시대출 가능" in status


def _is_owned(result):
    status = (result or {}).get("status", "")
    if "조회 오류" in status or "자동조회 실패" in status or "조회 실패" in status:
        return None
    if "소장 없음" in status:
        return False
    return (
        "소장 있음" in status
        or "소장 " in status
        or "대출중" in status
        or "예약가능" in status
        or "소장 확인" in status
    )


def build_library_summary(titles, detail):
    """
    복본 권수 합계가 아니라 '입력한 서로 다른 제목 중 몇 종을 오늘 빌릴 수 있는가'로 계산.
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
        st.markdown(
            f"**{medal} {item['name']}** — "
            f"즉시대출 **{item['available_count']}/{total}권**"
            f" · 소장 확인 {item['confirmed_owned_count']}권{extra}"
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
CHECKPOINT_VERSION = "v3.9-verified-20260902"
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


def sen_result(lib, title):
    last_err = None
    had_successful_response = False

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

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text("\n", strip=True)
        nt = norm(text)
        nq = norm(title)

        if "찾으시는자료가없습니다" in nt:
            continue

        m = re.search(r"총\s*([0-9,]+)\s*건", text)
        total = int(m.group(1).replace(",", "")) if m else None

        if nq not in nt:
            short = nq[:max(5, int(len(nq) * 0.7))]
            if short not in nt:
                continue

        if total == 0:
            continue

        copies = total if total is not None else 1

        has_available = (
            re.search(r"자료상태\s*:\s*대출가능", text) is not None
        )
        has_loaned = (
            re.search(r"자료상태\s*:\s*대출중", text) is not None
        )
        has_reserved = (
            re.search(
                r"자료상태\s*:\s*예약가능(?:\([^)]*\))?",
                text,
            )
            is not None
        )

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
    """검색어 입력창이 들어있는 form을 HTML에서 자동 탐색."""
    candidates = []
    for form in soup.find_all("form"):
        for inp in form.find_all("input"):
            typ = (inp.get("type") or "text").lower()
            if typ not in ("text", "search", ""):
                continue
            name = str(inp.get("name") or "")
            placeholder = str(inp.get("placeholder") or "")
            iid = str(inp.get("id") or "")
            hint = f"{name} {placeholder} {iid}".lower()
            pts = 0
            if "검색어" in placeholder:
                pts += 100
            if "search" in hint:
                pts += 40
            if "keyword" in hint:
                pts += 35
            if "query" in hint:
                pts += 30
            if "word" in hint:
                pts += 20
            if "검색" in form.get_text(" ", strip=True):
                pts += 10
            candidates.append((pts, form, inp))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1], candidates[0][2]


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
    """브라우저 자동화 없이 종로구 공식 검색 form을 그대로 제출."""
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

            form, search_input = picked
            payload = _jongno_form_payload(form, search_input, title)
            action = urljoin(first.url, form.get("action") or first.url)
            method = (form.get("method") or "get").lower()

            if method == "post":
                r = session.post(
                    action, data=payload,
                    headers={**headers, "Referer": first.url}, timeout=25
                )
            else:
                r = session.get(
                    action, params=payload,
                    headers={**headers, "Referer": first.url}, timeout=25
                )

            r.raise_for_status()
            return r.text, r.url

        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))

    raise last_err or RuntimeError("종로구 공식검색 실패")


def _jongno_names(library_key):
    if library_key == "cheongun":
        return ["청운문학도서관", "종로구립청운문학도서관"]
    return ["청운효자동 북카페", "청운 효자동 북카페", "청운효자동북카페"]


def _jongno_parse(html, title, library_key):
    soup = BeautifulSoup(html, "html.parser")
    title_n = norm(title)
    title_variants = [title_n]
    if len(title_n) >= 6:
        title_variants.append(title_n[:max(5, int(len(title_n) * 0.75))])

    lib_norms = [norm(x) for x in _jongno_names(library_key)]

    # 작은 결과 블록부터 검사해 다른 도서관 상태가 섞이지 않게 함.
    matched = []
    for tag in soup.find_all(["li", "tr", "article", "div", "a"]):
        txt = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
        if not txt or len(txt) > 1800:
            continue
        n = norm(txt)
        if not any(v and v in n for v in title_variants):
            continue
        if not any(v and v in n for v in lib_norms):
            continue
        matched.append(txt)

    # 중복 컨테이너 제거: 가장 짧은 블록을 우선
    matched = sorted(set(matched), key=len)
    if not matched:
        whole = norm(soup.get_text(" ", strip=True))
        if any(v and v in whole for v in title_variants) and any(
            v and v in whole for v in lib_norms
        ):
            return "📚 소장 있음 / 대출상태 확인 필요", 0, 1
        return "⚪ 소장 없음", 0, 0

    # 가장 작은 관련 블록들에서 상태 확인
    relevant = matched[:6]
    available = 0
    unavailable = 0
    for txt in relevant:
        compact = re.sub(r"\s+", "", txt)
        if any(x in compact for x in (
            "대출불가", "대출중", "예약중", "관외대출불가", "이용불가"
        )):
            unavailable += 1
        elif "대출가능" in compact or "비치중" in compact:
            available += 1

    copies = max(1, available + unavailable)
    if available:
        return f"🟢 소장 {copies} / 즉시대출 가능", available, copies
    if unavailable:
        return f"🟡 소장 {copies} / 즉시대출 없음", 0, copies
    return f"📚 소장 {copies} / 대출상태 확인 필요", 0, copies


def jongno_result(lib, title):
    """
    1차 종로구 공식 웹검색.
    실패하거나 웹검색에서 못 찾으면 정보나루 API도 교차 확인.
    실패 원인은 source에 남겨 앱 화면에서 확인 가능.
    """
    web_errors = []
    web_succeeded = False

    for q in query_variants(title):
        try:
            html, result_url = fetch_jongno(q)
            web_succeeded = True
            status, available, copies = _jongno_parse(html, title, lib["key"])
            if "소장 없음" not in status:
                return {
                    "status": status,
                    "available": available,
                    "copies": copies,
                    "url": result_url,
                    "source": "종로구립도서관 공식검색(requests)",
                }
        except Exception as e:
            web_errors.append(f"{type(e).__name__}: {str(e)[:180]}")

    # 정보나루 API fallback
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
        "※ V3.9 VERIFIED · 종로구는 공식 웹검색을 우선 사용하고 정보나루 API를 교차 확인합니다. "
        "'소장 없음'과 '조회 실패'는 서로 다르게 표시합니다."
    )


if __name__ == "__main__":
    main()
