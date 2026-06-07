"""
Google Sheets 연동 (streamlit_gsheets.GSheetsConnection 공식 API 기반).
세션 TTL 캐시로 읽기 비용을 줄이고, 쓰기 시에는 read→merge→update 패턴으로 누적 기록을 유지한다.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Optional

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

try:
    from streamlit_gsheets import GSheetsConnection
except ImportError:
    GSheetsConnection = None

SHEET_USERS = "users"
SHEET_HISTORY = "history"
SHEET_FINAL = "final_assessments"

USERS_COLS = ["student_id", "name", "password_hash"]
HISTORY_COLS = [
    "datetime",
    "student_id",
    "name",
    "subject",
    "unit",
    "diagnosis_result",
    "ncs_score",
    "mode",
    "record_id",
    "symptom",
    "reasoning",
    "teacher_feedback",
    "teacher_feedback_updated_at",
    "reflection",
    "image_b64",
    "mission_step_photos_json",
    "ai_chance_used_steps",
]
FINAL_COLS = [
    "student_id",
    "student_name",
    "final_score",
    "teacher_overall_comment",
    "subject_specialty_notes",
    "updated_at",
    "updated_by",
]

_CACHE_TTL_SEC = 60.0

# 구글 시트는 셀당 최대 50,000자를 허용한다. 약간의 안전 마진을 두고 자른다.
_GSHEETS_CELL_LIMIT = 49500
# 이미지/JSON처럼 잘라 쓰면 깨지는 필드는 한도 초과 시 통째로 비운다.
_BINARY_LIKE_FIELDS = {"image_b64", "mission_step_photos_json"}


def _clip_value_for_sheet_cell(field: str, value: Any) -> str:
    """구글 시트 셀 한도(50,000자)에 안전하게 들어가도록 값을 정리한다.

    - 이미지/JSON 같은 바이너리성 필드는 잘라내면 깨지므로 비운다.
    - 일반 텍스트 필드는 한도 직전까지 자르고 잘림 안내 마커를 덧붙인다.
    """
    s = "" if value is None else str(value)
    if len(s) <= _GSHEETS_CELL_LIMIT:
        return s
    if field in _BINARY_LIKE_FIELDS:
        logger.warning(
            "필드 %s가 셀 한도 %d자를 초과(%d자)하여 비웠습니다.",
            field, _GSHEETS_CELL_LIMIT, len(s),
        )
        return ""
    marker = f"\n…(이하 생략 — 셀 한도 초과로 {len(s) - _GSHEETS_CELL_LIMIT}자 잘림)"
    cut = max(0, _GSHEETS_CELL_LIMIT - len(marker))
    logger.warning(
        "필드 %s가 셀 한도 %d자를 초과(%d자)하여 잘랐습니다.",
        field, _GSHEETS_CELL_LIMIT, len(s),
    )
    return s[:cut] + marker


def _normalize_sheet_student_id(raw: Any) -> str:
    """시트·입력 학번을 매칭용 문자열로 통일 (int/float/str, '202601.0' 형태 방어)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    if isinstance(raw, bool):
        return str(int(raw))
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        try:
            if raw == int(raw):
                return str(int(raw))
        except (ValueError, OverflowError):
            pass
        raw = str(raw).strip()
    s = str(raw).strip()
    if not s:
        return ""
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    return s


# ---------------------------------------------------------------------------
# 비밀번호 해시 유틸
# ---------------------------------------------------------------------------
def _pepper() -> str:
    try:
        p = st.secrets.get("GSHEETS_PASSWORD_PEPPER")
        if p:
            return str(p)
    except Exception:
        pass
    return "dev-only-pepper-yongsan-rr"


def hash_student_password(student_id: str, plain_password: str) -> str:
    sid = _normalize_sheet_student_id(student_id)
    raw = f"{_pepper()}|{sid}|{plain_password}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_sha256_hex(s: str) -> bool:
    s = (s or "").strip()
    if len(s) != 64:
        return False
    try:
        int(s, 16)
        return True
    except ValueError:
        return False


def verify_student_password(student_id: str, plain_password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    stored_hash = str(stored_hash).strip()
    if _is_sha256_hex(stored_hash):
        return hash_student_password(student_id, plain_password) == stored_hash.lower()
    return plain_password == stored_hash


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def gsheets_available() -> bool:
    return GSheetsConnection is not None


def get_gsheets_connection() -> Any:
    if not gsheets_available():
        raise RuntimeError("st-gsheets-connection 패키지가 없습니다.")
    return st.connection("gsheets", type=GSheetsConnection)


def _ensure_private_mode_for_write() -> Any:
    try:
        conf = st.secrets["connections"]["gsheets"]
        if str(conf.get("type")).strip() != "service_account":
            raise RuntimeError("Secrets 설정에서 type = 'service_account'가 아니면 쓰기가 불가능합니다.")
    except Exception:
        raise RuntimeError("Secrets에 구글 서비스 계정 정보가 설정되지 않았습니다.")
    return get_gsheets_connection()


# ---------------------------------------------------------------------------
# 읽기 / 쓰기
# ---------------------------------------------------------------------------
def _normalize_df(df: Optional[pd.DataFrame], cols: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
    df.columns = [str(c).strip() for c in df.columns]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    out = df[cols].copy()
    for c in cols:
        out[c] = (
            out[c]
            .apply(
                lambda v: ""
                if (v is None or (isinstance(v, float) and pd.isna(v)))
                else str(v)
            )
            .astype(str)
            .str.strip()
        )
    return out


def read_users_df() -> pd.DataFrame:
    now = time.time()
    if (
        st.session_state.get("_gs_users_df") is not None
        and now - st.session_state.get("_gs_users_ts", 0) < _CACHE_TTL_SEC
    ):
        return st.session_state._gs_users_df
    conn = get_gsheets_connection()
    try:
        df = conn.read(worksheet=SHEET_USERS, ttl=0)
    except Exception as e:
        logger.error("Users 시트 읽기 실패: %s", e)
        df = pd.DataFrame(columns=USERS_COLS)
    df = _normalize_df(df, USERS_COLS)
    df["student_id"] = df["student_id"].map(_normalize_sheet_student_id)
    st.session_state._gs_users_df = df
    st.session_state._gs_users_ts = now
    return df


def read_history_df() -> pd.DataFrame:
    now = time.time()
    if (
        st.session_state.get("_gs_history_df") is not None
        and now - st.session_state.get("_gs_history_ts", 0) < _CACHE_TTL_SEC
    ):
        return st.session_state._gs_history_df
    conn = get_gsheets_connection()
    try:
        df = conn.read(worksheet=SHEET_HISTORY, ttl=0)
    except Exception as e:
        logger.error("History 시트 읽기 실패: %s", e)
        df = pd.DataFrame(columns=HISTORY_COLS)
    df = _normalize_df(df, HISTORY_COLS)
    df["student_id"] = df["student_id"].map(_normalize_sheet_student_id)
    st.session_state._gs_history_df = df
    st.session_state._gs_history_ts = now
    return df


def get_user_row(student_id: str) -> Optional[dict[str, Any]]:
    sid = _normalize_sheet_student_id(student_id)
    df = read_users_df()
    if df.empty:
        logger.info("찾는 학번: %s, 시트 내 학번 목록: [] (빈 users)", sid)
        return None
    ids = df["student_id"].tolist()
    logger.info("찾는 학번: %s, 시트 내 학번 목록: %s", sid, ids)
    m = df[df["student_id"] == sid]
    if m.empty:
        return None
    row = m.iloc[0]
    return {k: str(row.get(k, "")).strip() for k in USERS_COLS}


def _append_rows_via_update(worksheet: str, cols: list[str], new_rows: list[dict[str, Any]]) -> None:
    conn = _ensure_private_mode_for_write()
    try:
        current_df = conn.read(worksheet=worksheet, ttl=0)
    except Exception:
        current_df = pd.DataFrame(columns=cols)
    current_df = _normalize_df(current_df, cols)
    if "student_id" in cols:
        current_df["student_id"] = current_df["student_id"].map(_normalize_sheet_student_id)
    new_df = pd.DataFrame(new_rows, columns=cols)
    if "student_id" in cols:
        new_df["student_id"] = new_df["student_id"].map(_normalize_sheet_student_id)
    combined = pd.concat([current_df.astype(str), new_df.astype(str)], ignore_index=True)
    try:
        conn.update(worksheet=worksheet, data=combined)
    except Exception as e:
        raise RuntimeError(f"시트 업데이트 중 오류 발생: {e}") from e


def append_user_row(student_id: str, name: str, plain_password: str) -> None:
    sid = _normalize_sheet_student_id(student_id)
    h = hash_student_password(sid, plain_password)
    _append_rows_via_update(
        SHEET_USERS, USERS_COLS, [{"student_id": sid, "name": name, "password_hash": h}]
    )
    st.session_state.pop("_gs_users_df", None)
    st.session_state.pop("_gs_users_ts", None)


def append_history_from_record(record: dict[str, Any], ncs_score: float) -> None:
    sid = _normalize_sheet_student_id(record.get("student_id", ""))
    row = {
        "datetime": record.get("submitted_at", ""),
        "student_id": sid,
        "name": record.get("student_display_name", ""),
        "subject": record.get("subject", ""),
        "unit": record.get("unit", ""),
        "diagnosis_result": record.get("result", ""),
        "ncs_score": str(round(float(ncs_score), 2)),
        "mode": record.get("mode", ""),
        "record_id": record.get("record_id", ""),
        "symptom": record.get("symptom", ""),
        "reasoning": record.get("reasoning", ""),
        "teacher_feedback": record.get("teacher_feedback", ""),
        "teacher_feedback_updated_at": record.get("teacher_feedback_updated_at", ""),
        "reflection": record.get("reflection", ""),
        "image_b64": record.get("image_b64", ""),
        "mission_step_photos_json": record.get("mission_step_photos_json", ""),
        "ai_chance_used_steps": record.get("ai_chance_used_steps", ""),
    }
    # 셀 한도(50,000자) 초과로 인한 시트 거절을 방지하는 마지막 안전망.
    row = {k: _clip_value_for_sheet_cell(k, v) for k, v in row.items()}
    _append_rows_via_update(SHEET_HISTORY, HISTORY_COLS, [row])
    st.session_state.pop("_gs_history_df", None)
    st.session_state.pop("_gs_history_ts", None)


_HISTORY_SHEET_TO_APP_KEY = {
    "datetime": "submitted_at",
    "name": "student_display_name",
    "diagnosis_result": "result",
}


def _adapt_history_row_to_app(row: dict[str, Any]) -> dict[str, Any]:
    """시트 컬럼명을 app.py 표준 record 키로 번역(이중 키)."""
    out = dict(row)
    for sheet_key, app_key in _HISTORY_SHEET_TO_APP_KEY.items():
        val = row.get(sheet_key, "")
        if app_key not in out or not out.get(app_key):
            out[app_key] = val
    out["student_id"] = _normalize_sheet_student_id(row.get("student_id", ""))
    return out


def history_df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    return [_adapt_history_row_to_app(r) for r in df.to_dict("records")]


def invalidate_all_sheet_caches() -> None:
    st.session_state.pop("_gs_users_df", None)
    st.session_state.pop("_gs_users_ts", None)
    st.session_state.pop("_gs_history_df", None)
    st.session_state.pop("_gs_history_ts", None)


def force_refresh_history() -> pd.DataFrame:
    st.session_state.pop("_gs_history_df", None)
    st.session_state.pop("_gs_history_ts", None)
    return read_history_df()


def force_refresh_users() -> pd.DataFrame:
    st.session_state.pop("_gs_users_df", None)
    st.session_state.pop("_gs_users_ts", None)
    return read_users_df()


def filter_history_records_by_student(student_id: Any) -> list[dict[str, Any]]:
    sid_target = _normalize_sheet_student_id(student_id)
    if not sid_target:
        return []
    df = force_refresh_history()
    if df is None or df.empty:
        return []
    mask = df["student_id"] == sid_target
    rows = df.loc[mask].to_dict("records")
    return [_adapt_history_row_to_app(r) for r in rows]


def update_teacher_feedback_in_sheet(record_id: str, feedback: str, updated_at: str) -> None:
    conn = _ensure_private_mode_for_write()
    df = conn.read(worksheet=SHEET_HISTORY, ttl=0)
    df = _normalize_df(df, HISTORY_COLS)
    rid = str(record_id).strip()
    mask = df["record_id"].astype(str).str.strip() == rid
    if mask.any():
        df.loc[mask, "teacher_feedback"] = feedback
        df.loc[mask, "teacher_feedback_updated_at"] = updated_at
        conn.update(worksheet=SHEET_HISTORY, data=df)
        st.session_state.pop("_gs_history_df", None)
        st.session_state.pop("_gs_history_ts", None)


def clear_history_worksheet() -> None:
    conn = _ensure_private_mode_for_write()
    empty_df = pd.DataFrame(columns=HISTORY_COLS)
    conn.update(worksheet=SHEET_HISTORY, data=empty_df)
    st.session_state.pop("_gs_history_df", None)
    st.session_state.pop("_gs_history_ts", None)


# ---------------------------------------------------------------------------
# 학기말 최종 평가 (final_assessments 시트) — 학생별 1행
# ---------------------------------------------------------------------------
def read_final_df() -> pd.DataFrame:
    now = time.time()
    if (
        st.session_state.get("_gs_final_df") is not None
        and now - st.session_state.get("_gs_final_ts", 0) < _CACHE_TTL_SEC
    ):
        return st.session_state._gs_final_df
    try:
        conn = get_gsheets_connection()
        df = conn.read(worksheet=SHEET_FINAL, ttl=0)
    except Exception as e:
        # 시트가 아직 없거나 권한·네트워크 문제일 때 — 빈 테이블로 폴백.
        logger.info("final_assessments 시트 읽기 실패(빈 테이블로 폴백): %s", e)
        df = pd.DataFrame(columns=FINAL_COLS)
    df = _normalize_df(df, FINAL_COLS)
    df["student_id"] = df["student_id"].map(_normalize_sheet_student_id)
    st.session_state._gs_final_df = df
    st.session_state._gs_final_ts = now
    return df


def get_final_assessment(student_id: Any) -> Optional[dict[str, Any]]:
    """해당 학생의 최종 평가 row를 반환. 없으면 None."""
    sid = _normalize_sheet_student_id(student_id)
    if not sid:
        return None
    df = read_final_df()
    if df.empty:
        return None
    m = df[df["student_id"] == sid]
    if m.empty:
        return None
    row = m.iloc[0]
    return {k: str(row.get(k, "")).strip() for k in FINAL_COLS}


def upsert_final_assessment(
    student_id: str,
    student_name: str,
    final_score: str,
    teacher_overall_comment: str,
    subject_specialty_notes: str,
    updated_at: str,
    updated_by: str,
) -> None:
    """학생 1명의 최종 평가 row를 삽입 또는 업데이트(upsert)."""
    conn = _ensure_private_mode_for_write()
    try:
        df = conn.read(worksheet=SHEET_FINAL, ttl=0)
    except Exception:
        df = pd.DataFrame(columns=FINAL_COLS)
    df = _normalize_df(df, FINAL_COLS)
    df["student_id"] = df["student_id"].map(_normalize_sheet_student_id)

    sid = _normalize_sheet_student_id(student_id)
    new_row = {
        "student_id": sid,
        "student_name": student_name,
        "final_score": final_score,
        "teacher_overall_comment": teacher_overall_comment,
        "subject_specialty_notes": subject_specialty_notes,
        "updated_at": updated_at,
        "updated_by": updated_by,
    }
    # 셀 한도 안전망
    new_row = {k: _clip_value_for_sheet_cell(k, v) for k, v in new_row.items()}

    mask = df["student_id"] == sid
    if mask.any():
        for k, v in new_row.items():
            df.loc[mask, k] = v
    else:
        df = pd.concat(
            [df.astype(str), pd.DataFrame([new_row], columns=FINAL_COLS)],
            ignore_index=True,
        )

    try:
        conn.update(worksheet=SHEET_FINAL, data=df)
    except Exception as e_update:
        # 시트 탭이 아직 없는 경우 — 자동 생성 시도
        logger.info("final_assessments update 실패, create 시도: %s", e_update)
        try:
            conn.create(worksheet=SHEET_FINAL, data=df)
        except Exception as e_create:
            raise RuntimeError(
                "final_assessments 시트를 갱신하지 못했습니다. "
                "구글 시트 권한·이름·연결 설정을 확인해 주세요. "
                f"(update 오류: {e_update} / create 오류: {e_create})"
            ) from e_create

    st.session_state.pop("_gs_final_df", None)
    st.session_state.pop("_gs_final_ts", None)


def maybe_upgrade_plaintext_password(student_id: str, plain_password: str, stored_hash: str) -> None:
    """시트에 평문 비밀번호가 남아 있으면 로그인 성공 후 해시로 덮어쓴다."""
    if not stored_hash or _is_sha256_hex(str(stored_hash)):
        return
    sid = _normalize_sheet_student_id(student_id)
    if not sid:
        return
    conn = _ensure_private_mode_for_write()
    df = conn.read(worksheet=SHEET_USERS, ttl=0)
    df = _normalize_df(df, USERS_COLS)
    df["student_id"] = df["student_id"].map(_normalize_sheet_student_id)
    mask = df["student_id"] == sid
    if not mask.any():
        return
    df.loc[mask, "password_hash"] = hash_student_password(sid, plain_password)
    conn.update(worksheet=SHEET_USERS, data=df)
    st.session_state.pop("_gs_users_df", None)
    st.session_state.pop("_gs_users_ts", None)
