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
from playwright.sync_api import sync_playwright

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
CHECKPOINT_VERSION = "v3.6-jongno-single-library-20260902"
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
    payload = {
        "version": CHECKPOINT_VERSION,
        "titles": titles,
        "detail": {
            title: {
                libkey: _json_safe_result(result)
                for libkey, result in libs.items()
            }
            for title, libs in detail.items()
        },
        "completed_pairs": [list(x) for x in completed_pairs],
        "saved_at": time.time(),
    }
    try:
        _checkpoint_path(titles).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_checkpoint(titles, max_age_seconds=60 * 60 * 6):
    p = _checkpoint_path(titles)
    if not p.exists():
        return {}, set()
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
        if payload.get("version") != CHECKPOINT_VERSION:
            return {}, set()
        if payload.get("titles") != titles:
            return {}, set()
        if time.time() - float(payload.get("saved_at", 0)) > max_age_seconds:
            return {}, set()

        detail = payload.get("detail", {})
        completed = {
            (x[0], x[1])
            for x in payload.get("completed_pairs", [])
            if isinstance(x, list) and len(x) == 2
        }
        return detail, completed
    except Exception:
        return {}, set()


def clear_checkpoint(titles):
    try:
        _checkpoint_path(titles).unlink(missing_ok=True)
    except Exception:
        pass


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
# 종로구립도서관 브라우저 검색
# -----------------------------
def _all_checkbox_info(page):
    """
    체크박스와 주변 텍스트를 읽어서 디버깅/선택에 활용.
    """
    out = []
    boxes = page.locator('input[type="checkbox"]')

    for i in range(boxes.count()):
        box = boxes.nth(i)

        try:
            box_id = box.get_attribute("id") or ""
            name = box.get_attribute("name") or ""
            value = box.get_attribute("value") or ""
            checked = box.is_checked()

            nearby = ""
            try:
                nearby = box.locator(
                    "xpath=ancestor::*[self::label or self::li or self::div or self::td][1]"
                ).inner_text()
            except Exception:
                try:
                    nearby = box.locator("xpath=..").inner_text()
                except Exception:
                    nearby = ""

            out.append({
                "box": box,
                "id": box_id,
                "name": name,
                "value": value,
                "checked": checked,
                "text": nearby.strip(),
            })

        except Exception:
            continue

    return out


def _set_only_target_library(page, target_names):
    """
    종로구 검색에서 청운문학/북카페를 동시에 선택하지 않고,
    현재 조회하려는 도서관 하나만 선택한다.

    이렇게 해야 다른 도서관의 '대출가능' 상태가 섞이지 않는다.
    """
    target_norms = [norm(x) for x in target_names]

    found_target = False
    infos = _all_checkbox_info(page)

    for info in infos:
        combined = " ".join([
            info["id"],
            info["name"],
            info["value"],
            info["text"],
        ])
        combined_n = norm(combined)

        is_target = any(
            t and t in combined_n
            for t in target_norms
        )

        try:
            if is_target:
                if not info["box"].is_checked():
                    info["box"].check(force=True)
                found_target = True
            else:
                # 도서관 선택 체크박스만 해제해야 하지만
                # 사이트 구조를 알 수 없으므로 텍스트가 '도서관'처럼 보이는 것만 해제.
                text_n = norm(info["text"])
                looks_like_library_filter = (
                    "도서관" in info["text"]
                    or "북카페" in info["text"]
                    or "문학" in info["text"]
                )
                if looks_like_library_filter and info["box"].is_checked():
                    info["box"].uncheck(force=True)
        except Exception:
            continue

    if found_target:
        return True

    # label 기반 fallback
    labels = page.locator("label")
    for i in range(labels.count()):
        lab = labels.nth(i)

        try:
            txt = lab.inner_text().strip()
        except Exception:
            continue

        txt_n = norm(txt)

        if not any(
            t and t in txt_n
            for t in target_norms
        ):
            continue

        try:
            for_id = lab.get_attribute("for")

            if for_id:
                box = page.locator(f'#{for_id}')

                if box.count():
                    box = box.first

                    if not box.is_checked():
                        box.check(force=True)

                    return True
        except Exception:
            pass

        try:
            box = lab.locator('input[type="checkbox"]')

            if box.count():
                box = box.first

                if not box.is_checked():
                    box.check(force=True)

                return True
        except Exception:
            pass

    return False


def _find_search_input(page):
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


def _click_search(page, inp):
    clicked = False

    for role in ["button", "link"]:
        loc = page.get_by_role(
            role,
            name=re.compile(r"^\s*검색\s*$"),
        )

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
        submits = page.locator(
            'input[type="submit"], button[type="submit"]'
        )

        for i in range(submits.count()):
            x = submits.nth(i)

            try:
                val = (
                    (x.get_attribute("value") or "")
                    + " "
                    + x.inner_text()
                )

                if "검색" in val and x.is_visible():
                    x.click()
                    clicked = True
                    break

            except Exception:
                continue

    if not clicked:
        inp.press("Enter")


@st.cache_data(ttl=90, show_spinner=False)
def _jongno_browser_search(title, library_key):
    """
    V3.6:
    한 번에 종로 도서관 하나만 선택해서 검색한다.
    다른 도서관 상태가 섞이는 문제를 제거한다.
    """
    url = "https://lib.jongno.go.kr/plus_m/search_list_klas.php"

    target_names = (
        ["청운문학도서관", "종로구립청운문학도서관"]
        if library_key == "cheongun"
        else ["청운효자동 북카페", "청운 효자동 북카페", "청운효자동북카페"]
    )

    with sync_playwright() as pw:
        launch_kwargs = dict(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
            ],
        )

        chromium_paths = [
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]

        executable = next(
            (
                p
                for p in chromium_paths
                if os.path.exists(p)
            ),
            None,
        )

        if executable:
            launch_kwargs["executable_path"] = executable

        browser = pw.chromium.launch(**launch_kwargs)

        context = browser.new_context(
            viewport={
                "width": 1280,
                "height": 1400,
            },
            locale="ko-KR",
        )

        page = context.new_page()

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=25000,
            )

            page.wait_for_timeout(800)

            selected = _set_only_target_library(
                page,
                target_names,
            )

            if not selected:
                # 체크박스 선택에 실패해도 바로 '소장 없음'으로 만들지 않는다.
                raise RuntimeError(
                    f"{library_key} 도서관 선택 체크박스를 찾지 못했습니다."
                )

            inp = _find_search_input(page)

            if inp is None:
                raise RuntimeError(
                    "종로구 검색 입력창을 찾지 못했습니다."
                )

            inp.fill(title)
            _click_search(page, inp)

            try:
                page.wait_for_load_state(
                    "networkidle",
                    timeout=12000,
                )
            except Exception:
                page.wait_for_timeout(2500)

            body = page.locator("body").inner_text(
                timeout=10000
            )

            result_url = page.url

            return body, result_url

        finally:
            browser.close()


def _title_found_in_text(text, title):
    """
    제목 전체가 아니라 일부만 노출되는 검색결과도 고려.
    """
    text_n = norm(text)
    title_n = norm(title)

    if not title_n:
        return False

    variants = [title_n]

    if len(title_n) >= 6:
        variants.append(
            title_n[:max(5, int(len(title_n) * 0.8))]
        )

    if len(title_n) >= 10:
        variants.append(
            title_n[:max(6, int(len(title_n) * 0.65))]
        )

    return any(
        v and v in text_n
        for v in variants
    )


def _jongno_library_status(body, title, library_key):
    """
    V3.6 종로구립도서관 판정.

    중요한 원칙:
    1) 이 함수로 넘어온 페이지는 해당 도서관 하나만 선택된 검색결과다.
    2) 그래서 다른 도서관의 대출상태가 섞일 가능성을 줄였다.
    3) 자동 파싱이 불확실할 때는 '소장 없음'으로 단정하지 않는다.
    """
    text = re.sub(r"\s+", " ", body or "").strip()
    compact = re.sub(r"\s+", "", text)

    if not text:
        return "⚠️ 자동조회 실패 · 결과 화면 없음", 0, None

    # 사이트가 명시적으로 검색 결과 없음이라고 말하는 경우
    no_result_markers = (
        "검색결과가없습니다",
        "검색결과없음",
        "자료가없습니다",
        "검색된자료가없습니다",
        "찾으시는자료가없습니다",
        "총0건",
    )

    compact_n = norm(compact)

    if any(
        norm(marker) in compact_n
        for marker in no_result_markers
    ):
        return "⚪ 소장 없음", 0, 0

    title_found = _title_found_in_text(
        text,
        title,
    )

    # 검색 결과 화면에 제목이 전혀 없다면 없는 것으로 판단.
    # 단, 결과 화면 구조가 바뀌어 제목 파싱이 실패했을 수 있으므로
    # 도서관명/대출상태가 동시에 보이면 '확인 필요'로 보수적으로 처리.
    if library_key == "cheongun":
        library_names = [
            "청운문학도서관",
            "종로구립청운문학도서관",
        ]
    else:
        library_names = [
            "청운효자동 북카페",
            "청운 효자동 북카페",
            "청운효자동북카페",
        ]

    library_found = any(
        norm(name) in compact_n
        for name in library_names
    )

    if not title_found:
        if library_found and any(
            marker in compact
            for marker in (
                "대출가능",
                "비치중",
                "대출중",
                "예약중",
                "대출불가",
            )
        ):
            return "📚 결과 있음 / 제목 매칭 확인 필요", 0, 1

        return "⚪ 소장 없음", 0, 0

    # 제목이 검색결과에 있으면 최소한 검색 결과는 존재.
    # 도서관을 하나만 선택해서 검색했으므로, 해당 자료는 그 도서관 결과로 간주.
    available_markers = (
        "대출가능",
        "비치중",
        "대출 가능",
        "대출가능(비치중)",
    )

    unavailable_markers = (
        "대출중",
        "예약중",
        "대출불가",
        "관외대출불가",
        "이용불가",
        "대출예약중",
    )

    # '대출가능 0' 같은 표현은 가능으로 오인하지 않게 막는다.
    zero_available_patterns = (
        "대출가능0",
        "대출가능:0",
        "대출가능：0",
    )

    has_available = (
        any(
            marker.replace(" ", "") in compact
            for marker in available_markers
        )
        and not any(
            marker in compact
            for marker in zero_available_patterns
        )
    )

    if has_available:
        return "🟢 소장 있음 / 즉시대출 가능", 1, 1

    has_unavailable = any(
        marker in compact
        for marker in unavailable_markers
    )

    if has_unavailable:
        return "🟡 소장 있음 / 즉시대출 없음", 0, 1

    # 제목은 찾았지만 대출 상태 문구가 사이트 개편 등으로 달라졌다면
    # 없는 책으로 잘못 판정하지 않는다.
    return "📚 소장 있음 / 대출상태 확인 필요", 0, 1


def jongno_result(lib, title):
    last_err = None

    # 제목 전체 검색이 실패할 때 부제/문장부호 문제를 대비해 변형 검색.
    for q in query_variants(title):
        for attempt in range(2):
            try:
                body, result_url = _jongno_browser_search(
                    q,
                    lib["key"],
                )

                status, available, copies = _jongno_library_status(
                    body,
                    title,
                    lib["key"],
                )

                # 변형검색에서 확실한 결과를 찾으면 즉시 반환
                if "소장 없음" not in status:
                    return {
                        "status": status,
                        "available": available,
                        "copies": copies,
                        "url": result_url,
                        "source": "종로구립도서관 공식검색(도서관별 개별 조회)",
                    }

                # 원 제목 검색에서 명확히 없으면 다른 변형도 시도
                if attempt == 0:
                    break

            except Exception as e:
                last_err = e

                if attempt == 0:
                    time.sleep(1.0)

    # 브라우저 자동화 자체가 실패했다면 '소장 없음'으로 내려버리지 않는다.
    if last_err:
        return {
            "status": "⚠️ 자동조회 실패 · 잠시 후 재검색",
            "available": 0,
            "copies": None,
            "url": official_url(lib, title),
            "source": (
                "종로구립도서관 공식검색(도서관별 개별 조회): "
                f"{type(last_err).__name__}"
            ),
        }

    return {
        "status": "⚪ 소장 없음",
        "available": 0,
        "copies": 0,
        "url": official_url(lib, title),
        "source": "종로구립도서관 공식검색(도서관별 개별 조회)",
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
            "⏱️ 10권 안팎 검색은 공식 웹검색과 브라우저 자동화를 함께 사용해 "
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
        "※ 종로구 두 도서관은 각각 따로 검색해 다른 도서관의 "
        "대출상태가 섞이지 않도록 했습니다. "
        "'소장 없음'과 '조회 실패'는 서로 다르게 표시합니다."
    )


if __name__ == "__main__":
    main()
