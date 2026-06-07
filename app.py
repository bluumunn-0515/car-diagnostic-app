import base64
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Optional
import streamlit as st

import sheets_backend as shb

logger = logging.getLogger(__name__)
try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None
try:
    import plotly.graph_objects as go
except ImportError:
    go = None
try:
    from fpdf import FPDF  # pyright: ignore[reportMissingModuleSource]
except ImportError:
    FPDF = None
try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

# ───────────────────────────────────────────────────────────────────────────
# 상수 및 설정
# ───────────────────────────────────────────────────────────────────────────
NCS_UNITS = [
    "자동차 전기전자장치 고장진단",
    "배터리 점검",
    "시동·충전장치 점검",
    "조명장치 점검",
    "편의장치 점검",
    "네트워크 장치 점검",
]
CURRICULUM = {"자동차 전기전자제어": list(NCS_UNITS)}
UNIT_ICONS = {
    "자동차 전기전자장치 고장진단": "🔧",
    "배터리 점검": "🔋",
    "시동·충전장치 점검": "🚗",
    "조명장치 점검": "💡",
    "편의장치 점검": "🪑",
    "네트워크 장치 점검": "🛰️",
}

NCS_RUBRIC = {
    "자동차 전기전자장치 고장진단": [
        ("안전·전원 차단 확인", ["안전", "전원 차단", "감전", "단락", "보호구", "규정 토크", "정비지침서"]),
        ("회로도/기호 분석", ["회로도", "전장 회로도", "커넥터", "하네스", "배선 색", "기호", "조인트", "DLC"]),
        ("회로시험기 측정 절차", ["멀티미터", "회로시험기", "0점 조정", "전압", "저항", "통전", "리드선", "COM", "VΩ"]),
        ("진단장비(스캐너) 활용", ["스캐너", "OBD-II", "OBD-Ⅱ", "DTC", "고장코드", "센서 데이터", "강제구동", "오실로스코프"]),
    ],
    "배터리 점검": [
        ("배터리 외관/상태 확인", ["배터리", "축전지", "단자", "비중", "전해액", "부식", "AGM", "EFB", "MF", "라벨"]),
        ("개방회로 전압(OCV) 측정", ["OCV", "개방회로", "정지 전압", "12.3", "12.9", "단자 전압", "DC V", "20℃"]),
        ("부하/CCA·SOC 판정", ["CCA", "RC", "부하 시험", "SOC", "충전 상태", "방전", "교체", "판정", "크랭킹 전압"]),
        ("암전류/배터리 센서 점검", ["암전류", "50mA", "배터리 센서", "퓨즈", "릴레이", "PWM"]),
    ],
    "시동·충전장치 점검": [
        ("시동회로 점검", ["시동 전동기", "스타터", "솔레노이드", "B단자", "ST단자", "M단자", "시동 릴레이", "인히비터", "크랭킹", "피니언", "오버러닝 클러치"]),
        ("발전기 출력 점검", ["발전기", "알터네이터", "충전 전압", "13.8", "14.9", "리플", "FR단자", "C단자", "레귤레이터", "OAD"]),
        ("회로 전압강하 측정", ["전압강하", "0.2V", "케이블", "B+", "접지", "굵기", "배선"]),
        ("점검 절차/예비점검", ["예비점검", "단계", "순서", "점프 스타트", "벨트장력", "정비지침서", "P/N", "관능검사"]),
    ],
    "조명장치 점검": [
        ("등화회로 분석", ["전조등", "미등", "방향지시등", "정지등", "번호판등", "퓨즈", "라이트 스위치", "플래셔", "다기능 스위치"]),
        ("광원/전구 점검", ["전구", "LED", "필라멘트", "단선", "소켓", "분당", "60~120회", "하이빔", "로우빔"]),
        ("회로 전압/접지 측정", ["입력 전압", "접지", "도통", "1Ω", "1MΩ", "1㏁", "단락", "어스", "릴레이"]),
        ("BCM/CAN 등화 제어", ["BCM", "IPS", "B-CAN", "C-CAN", "MICOM", "스캐너", "DTC", "Failsafe"]),
    ],
    "편의장치 점검": [
        ("편의장치 유형/회로 식별", ["BCM", "ETACS", "다기능 스위치", "와이퍼", "워셔", "도어록", "파워윈도우", "레인센서", "썬루프", "열선"]),
        ("모듈 전원·접지 점검", ["IGN2", "공급전압", "선간 전압", "0.3V", "접지", "0.2V", "탐침봉", "정상 전압"]),
        ("액추에이터/릴레이 점검", ["액추에이터", "모터", "릴레이", "85", "86", "30", "87", "와이퍼 25A", "85~110Ω", "구동"]),
        ("스캐너 자기진단/강제구동", ["스캐너", "DLC", "DTC", "고장코드", "센서 데이터", "강제구동", "VCU", "IMS", "자기진단"]),
    ],
    "네트워크 장치 점검": [
        ("통신 프로토콜 이해", ["CAN", "LIN", "K-LIN", "KWP2000", "프로토콜", "C-CAN", "B-CAN", "CRC", "트랜시버"]),
        ("종단저항/배선 점검", ["종단저항", "120Ω", "60Ω", "주선", "트위스트 페어", "조인트 커넥터", "배선"]),
        ("통신 신호/파형 측정", ["오실로스코프", "파형", "high", "low", "신호", "전압 레벨", "스코프"]),
        ("게이트웨이/모듈 진단", ["게이트웨이", "GW", "ECU", "DLC", "DTC", "bus-off", "time-out", "스캐너", "통신 가능"]),
    ],
}

MODE_RUBRIC_WEIGHTS = {
    "학습 모드": {
        "안전·전원 차단 확인": 1.3, "회로도/기호 분석": 1.2, "회로시험기 측정 절차": 1.1, "진단장비(스캐너) 활용": 1.0,
        "배터리 외관/상태 확인": 1.1, "개방회로 전압(OCV) 측정": 1.1, "부하/CCA·SOC 판정": 1.0, "암전류/배터리 센서 점검": 1.0,
        "시동회로 점검": 1.1, "발전기 출력 점검": 1.0, "회로 전압강하 측정": 1.0, "점검 절차/예비점검": 1.2,
        "등화회로 분석": 1.1, "광원/전구 점검": 1.0, "회로 전압/접지 측정": 1.0, "BCM/CAN 등화 제어": 1.0,
        "편의장치 유형/회로 식별": 1.0, "모듈 전원·접지 점검": 1.1, "액추에이터/릴레이 점검": 1.0, "스캐너 자기진단/강제구동": 1.1,
        "통신 프로토콜 이해": 1.2, "종단저항/배선 점검": 1.1, "통신 신호/파형 측정": 1.0, "게이트웨이/모듈 진단": 1.0,
    }
}

UNIT_INPUT_HINTS = {
    "자동차 전기전자장치 고장진단": {"target": "예: 운전석 도어 커넥터 E12", "state": "예: 멀티미터 전압 0V", "question": "예: 단선 위치 점검 순서"},
    "배터리 점검": {"target": "예: 12V 납축전지(MF)", "state": "예: OCV 12.0V 측정", "question": "예: CCA와 SOC 판정 순서"},
    "시동·충전장치 점검": {"target": "예: 알터네이터 B단자", "state": "예: 충전 전압 13.2V", "question": "예: 전압강하 문제 구분법"},
    "조명장치 점검": {"target": "예: 좌측 전조등(로우빔)", "state": "예: 퓨즈 도통되나 부점등", "question": "예: 접지 불량 확인법"},
    "편의장치 점검": {"target": "예: 파워윈도우 모터", "state": "예: 수동은 되나 AUTO 안됨", "question": "예: 강제구동 활용법"},
    "네트워크 장치 점검": {"target": "예: C-CAN 주선", "state": "예: ABS 모듈 통신불가", "question": "예: 종단저항 측정 포인트"},
}

UNIT_PHOTO_CHECKLISTS = {
    "자동차 전기전자장치 고장진단": ["회로도 분석용 커넥터 핀 번호가 보이나요?", "멀티미터 모드(DC V/Ω)가 보이나요?"],
    "배터리 점검": ["터미널 부식 상태가 보이나요?", "배터리 라벨(CCA/AGM 등)이 보이나요?"],
    "시동·충전장치 점검": ["솔레노이드 단자 위치가 보이나요?", "벨트 장력 상태가 보이나요?"],
}

GEMINI_MODEL_CANDIDATES = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-exp",
]
GEMINI_RETRY_DELAYS_SECONDS = [2.0, 4.0]
GEMINI_IMAGE_MAX_SIZE = (1024, 1024)
GEMINI_IMAGE_JPEG_QUALITY = 85
TEACHER_PASSWORD_DEFAULT = "0000"

# ───────────────────────────────────────────────────────────────────────────
# 유틸리티 함수
# ───────────────────────────────────────────────────────────────────────────
def now_kst_display() -> str:
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.now(ZoneInfo("Asia/Seoul"))
    except Exception:
        dt = datetime.now(timezone.utc) + timedelta(hours=9)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def _normalize_sid(student_id: Any) -> str:
    return str(student_id or "").strip()

def reset_student_session_soft() -> None:
    st.session_state.student_logged_in = False
    st.session_state.student_id = ""
    st.session_state.student_display_name = ""
    st.session_state["my_history_records"] = None
    reset_diagnosis_flow()

def reset_diagnosis_flow() -> None:
    st.session_state.diag_step = "input"
    st.session_state.latest_guidance = ""
    st.session_state.latest_evaluation = ""
    st.session_state.latest_execution_result = ""
    st.session_state.latest_result = ""
    st.session_state.latest_symptom = ""
    st.session_state.latest_reflection = ""
    st.session_state.latest_image_b64 = ""
    # 평가 검토·재수행 관련 보류 상태 초기화
    st.session_state["pending_record"] = None
    st.session_state["pending_ncs_score"] = 0.0
    st.session_state["previous_evaluation"] = ""
    st.session_state["redo_count"] = 0
    # 4단계 미션 카드 상태 초기화
    for i in range(1, 5):
        for k in (
            f"step_note_{i}", f"step_photo_{i}", f"step_done_{i}",
            f"step_photo_b64_{i}",
            f"ai_chance_used_{i}", f"ai_chance_text_{i}",
        ):
            st.session_state.pop(k, None)

def compose_structured_symptom(target_part: str, current_state: str, learning_question: str) -> str:
    target = (target_part or "").strip()
    state = (current_state or "").strip()
    question = (learning_question or "").strip()
    if not (target or state or question): return ""
    return f"[대상 부품]\n{target or '(미입력)'}\n[현재 상태]\n{state or '(미입력)'}\n[학습 질문]\n{question or '(미입력)'}"

# 구글 시트는 셀당 최대 50,000자만 허용한다.
# - 메인 사진 한 장은 한 셀(image_b64)을 단독으로 사용 → 넉넉히 45,000자까지 허용.
# - 4단계 미션 사진들은 mission_step_photos_json 한 셀에 묶여 들어가므로
#   장당 11,000자 정도로 제한해야 4장 + JSON 오버헤드가 50,000자 안쪽이 된다.
THUMB_B64_LIMIT_MAIN = 45000
THUMB_B64_LIMIT_STEP = 11000

# 점진적으로 크기/품질을 낮추며 base64 길이를 한도 이하로 맞추는 시도 시퀀스.
_THUMB_SHRINK_STEPS = [
    (480, 60), (400, 55), (340, 50), (280, 45),
    (240, 40), (200, 35), (170, 32), (140, 30),
    (110, 28), (90, 25),
]

def make_thumbnail_b64(image_file: Any, max_b64_chars: int = THUMB_B64_LIMIT_STEP) -> str:
    """업로드 사진을 base64 썸네일로 변환. 구글 시트 셀 한도(50,000자)에 안전하게 들어가도록
    크기·품질을 점진적으로 줄이며 max_b64_chars 이하가 될 때까지 재시도한다."""
    if image_file is None or PILImage is None:
        return ""
    try:
        raw = image_file.getvalue()
    except Exception:
        return ""
    last_b64 = ""
    try:
        with PILImage.open(BytesIO(raw)) as im:
            im.load()
            if im.mode != "RGB":
                im = im.convert("RGB")
            for size, q in _THUMB_SHRINK_STEPS:
                im_try = im.copy()
                im_try.thumbnail((size, size), PILImage.LANCZOS)
                buf = BytesIO()
                im_try.save(buf, format="JPEG", quality=q, optimize=True)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                last_b64 = b64
                if len(b64) <= max_b64_chars:
                    return b64
        # 가장 작은 시도가 여전히 한도를 넘으면 빈 문자열로 폴백(저장이 막히는 일을 막는다).
        if last_b64 and len(last_b64) > max_b64_chars:
            logger.warning(
                "썸네일이 한도(%d자)에 못 들어가 빈 값으로 폴백 — 마지막 시도 %d자",
                max_b64_chars, len(last_b64),
            )
            return ""
        return last_b64
    except Exception as e:
        logger.warning("썸네일 생성 실패: %s", e)
        return ""

def thumbnail_b64_to_bytes(b64: str) -> Optional[bytes]:
    if not b64: return None
    try: return base64.b64decode(str(b64).strip())
    except: return None

# ───────────────────────────────────────────────────────────────────────────
# AI 및 비즈니스 로직
# ───────────────────────────────────────────────────────────────────────────
STANDARD_PROCEDURE_BLOCK = """
[표준 절차 기준]
가. 멀티미터 사용 전 안전 점검 (전원 차단 확인, 레인지 선택)
나. 회로도 분석 (전원→퓨즈→스위치→부하→접지 흐름 추적)
다. 진단장비(스캐너) 사용 (DTC 확인, 센서 데이터 분석)
""".strip()

def build_learning_prompt(user_symptom: str, selected_unit: str) -> str:
    """NCS 능력단위 기반 4단계 스캐폴딩 미션 카드(간결형) 프롬프트."""
    sub_elements = NCS_RUBRIC.get(selected_unit, [])
    sub_text_lines = []
    for i, (name, keywords) in enumerate(sub_elements, 1):
        kw = ", ".join(keywords[:6])
        sub_text_lines.append(f"  {i}. {name} — {kw}")
    sub_text = "\n".join(sub_text_lines) or "  (세부 수행준거 없음)"

    return f"""
너는 '자동차 전기전자제어' NCS 기반 AI 코치다.
임무: 학생이 **스스로 답을 찾도록** 4단계 미션 카드를 짧고 직관적으로 만든다.

[필수 규칙]
1. 정답·결론·고장 원인을 절대 단정하지 말 것. 측정·관찰·비교 절차만 제시.
2. 표현은 짧은 동사구 위주, 한 줄당 30자 이내. 설명문/장황한 줄글 금지.
3. 각 단계는 **불릿 2개**만. 마지막에 학생이 직접 답해야 할 ✋ 질문 1줄.
4. ⚡ 3단계(측정)만 예외적으로 **정상 기준값**을 괄호로 함께 적는다. (예: 12.6V 이상 정상)
5. 사진이 있으면 1단계 첫 불릿에 "📷 사진의 ○○ 부위를 확인" 형태로 한 번만 짚는다.
6. 학생 입력에서 받은 [대상 부품]을 1단계 첫 불릿에 반드시 반영.

[단원] {selected_unit}

[학생 입력]
{user_symptom or '(미입력)'}

[NCS 수행준거 하위 요소]
{sub_text}

아래 형식만 그대로 따라 출력. 추가 머리말·맺음말 금지.

### 1️⃣ 준비 / 안전
• (점검 대상 + 안전 확인 1줄)
• (준비할 계측기/도구 1줄)
✋ 생각해볼 점: (스스로 확인해야 할 1줄 질문)

### 2️⃣ 점검 / 회로도
• (회로도에서 추적할 흐름 1줄)
• (커넥터·퓨즈·접지 등에서 살펴볼 핵심 1줄)
✋ 생각해볼 점: (스스로 확인해야 할 1줄 질문)

### 3️⃣ 측정 / 전압
• (측정 위치 + 멀티미터 모드/레인지 1줄)
• (또 다른 측정 포인트 + 정상 기준값 1줄)
✋ 생각해볼 점: (측정값과 기준을 비교해 스스로 판단할 1줄 질문)

### 4️⃣ 판정 / 조치
• (결과 해석 갈림길: 정상이면 / 이상이면 1줄)
• (추가 점검 또는 다음 단계 힌트 1줄, 정답 단정 X)
✋ 생각해볼 점: (다음 행동을 학생이 직접 결정하도록 유도하는 1줄 질문)
""".strip()

def build_evaluation_prompt(
    user_symptom: str,
    student_reasoning: str,
    selected_unit: str,
    guidance_text: str,
    photo_order_text: str = "",
) -> str:
    """학생이 오늘 입력한 4단계 메모와 첨부 사진을 NCS 수행준거와 비교해
    카테고리별로 구체적으로 분석하는 평가 프롬프트.
    한 카테고리당 "사실 → NCS 기준 → 보완 제안" 3줄로 일정한 형식만 출력하게 한다."""
    sub_elements = NCS_RUBRIC.get(selected_unit, [])
    sub_lines: list[str] = []
    for i, (name, keywords) in enumerate(sub_elements, 1):
        kw = ", ".join(keywords[:8])
        sub_lines.append(f"  {i}. {name} — {kw}")
    sub_text = "\n".join(sub_lines) or "  (세부 수행준거 없음)"
    photo_block = photo_order_text.strip() or "(첨부 사진 없음)"

    return f"""
너는 자동차 전기전자제어 NCS 수행준거 기반 평가 코치다.
학생이 4단계로 진행한 실습 결과를 NCS 수행준거·처음 제공된 AI 가이드·첨부 사진과
비교해 카테고리별로 구체적으로 평가한다.

[단원] {selected_unit}

[NCS 하위 수행준거]
{sub_text}

[학생이 처음 입력한 증상]
{user_symptom or '(없음)'}

[처음 제공된 AI 가이드]
{guidance_text or '(없음)'}

[학생이 4단계에서 작성한 진행 메모]
{student_reasoning}

[첨부 사진 안내]
{photo_block}
- 위 순서대로 사진이 첨부되었다. 사진을 참조해 학생이 실제로 어떤 부품·계측기·회로를
  확인했는지, 어떤 자세·도구로 측정했는지를 평가에 구체적으로 반영하라.
- 사진이 없거나 분석이 어려운 경우는 "사진으로 확인 어려움"이라고 명시.

[필수 규칙]
1. 정답·고장 원인을 단정하지 말 것. (예: "이건 단선입니다" 금지)
2. 한줄 요약은 70~110자 한 문장으로, 학생이 잘한 점과 보완점을 압축.
3. 카테고리별로 반드시 다음 3줄을 모두 포함:
   - 사실: 학생 메모/사진에서 확인한 구체 사실 1줄 (가능하면 짧게 인용)
   - NCS 기준: 해당 수행준거와 비교한 성취 수준 1줄
   - 보완 제안: 다음에 무엇을 더 측정/관찰해야 할지 1줄 (정답 단정 금지)
4. 각 줄은 50자 이내. 줄글·장황한 설명 금지.
5. 상태는 정확히 "통과" 또는 "보완" 둘 중 하나로만 표기.
6. 이모지·꾸밈문자 사용 금지.
7. 출력은 정확히 아래 형식만. 머리말·맺음말·추가 설명 금지.

## 한줄 요약
(70~110자 한 문장)

## 카테고리 평가

### 1. 준비/안전 — 통과
- 사실: (1줄)
- NCS 기준: (1줄)
- 보완 제안: (1줄)

### 2. 점검/회로도 — 보완
- 사실: (1줄)
- NCS 기준: (1줄)
- 보완 제안: (1줄)

### 3. 측정/전압 — 통과
- 사실: (1줄)
- NCS 기준: (1줄)
- 보완 제안: (1줄)

### 4. 판정/조치 — 보완
- 사실: (1줄)
- NCS 기준: (1줄)
- 보완 제안: (1줄)

## 종합 코멘트
(2~3줄: 학생의 강점과 개선 포인트, 다음 학습 방향)
""".strip()

_MISSION_STEP_META = [
    {"emoji": "🛡️", "title": "준비 / 안전",  "color": "#10B981"},
    {"emoji": "🔍", "title": "점검 / 회로도", "color": "#3B82F6"},
    {"emoji": "⚡", "title": "측정 / 전압",   "color": "#F59E0B"},
    {"emoji": "🛠️", "title": "판정 / 조치",  "color": "#EF4444"},
]

def _parse_mission_steps(guidance_text: str) -> list[dict]:
    """AI 가이드에서 ###으로 구분된 4단계를 추출. 형식이 어긋나도 가능한 만큼 파싱."""
    if not guidance_text:
        return []
    lines = guidance_text.splitlines()
    sections: list[dict] = []
    current: Optional[dict] = None
    for raw in lines:
        line = raw.rstrip()
        if line.lstrip().startswith("###"):
            if current is not None:
                sections.append(current)
            heading = line.lstrip("# ").strip()
            current = {"heading": heading, "body_lines": []}
        elif current is not None:
            current["body_lines"].append(line)
    if current is not None:
        sections.append(current)
    # 최대 4개만 사용
    return sections[:4]

def _compose_combined_result(guidance_text: str, evaluation_text: str) -> str:
    """가이드 텍스트와 평가 텍스트를 학생 포트폴리오·교사 대시보드에서 사용하기 좋은 형태로 합친다."""
    g = (guidance_text or "").strip()
    e = (evaluation_text or "").strip()
    if g and e:
        return f"## 🧭 AI 진단 가이드\n\n{g}\n\n---\n\n## 📝 AI 실습 평가\n\n{e}"
    return e or g

_STEP_FOCUS_BY_INDEX = {
    1: "안전·전원 차단·보호구·정비지침서·도구 준비",
    2: "회로도 분석·전원→퓨즈→스위치→부하→접지 흐름·커넥터/하네스 추적",
    3: "멀티미터/오실로스코프/스캐너 측정 절차·측정 위치·정상 기준값",
    4: "측정값 해석·정상/이상 판정·다음 점검 방향 결정",
}

def build_step_help_prompt(
    user_symptom: str, selected_unit: str, step_idx: int,
    step_title: str, step_body: str, student_step_note: str = "",
) -> str:
    """
    AI 찬스: 학생이 막혀 있을 때 NCS 능력단위·하위 수행준거를 바탕으로
    이 단계에서 어떻게 측정·진단·정비해야 하는지 더 구체적으로 도와주는 코칭 프롬프트.
    정답·고장 원인 단정은 여전히 금지하되, 측정 절차·기준값·관찰 포인트는 풍부하게 제공한다.
    """
    sub_elements = NCS_RUBRIC.get(selected_unit, [])
    sub_lines = []
    for i, (name, keywords) in enumerate(sub_elements, 1):
        kw = ", ".join(keywords[:8])
        sub_lines.append(f"  {i}. {name} — {kw}")
    sub_text = "\n".join(sub_lines) or "  (세부 수행준거 없음)"
    step_focus = _STEP_FOCUS_BY_INDEX.get(step_idx, "")

    return f"""
너는 '자동차 전기전자제어' NCS 기반 AI 코치다.
학생이 [{step_idx}단계 · {step_title}]에서 진행이 더디다고 판단해 **AI 찬스(심화 도움)**를 요청했다.
이 도움은 NCS 능력단위·하위 수행준거에 입각해 **어떻게 측정·진단·정비해야 하는지**를
구체적으로, 그러나 정답을 단정하지는 않는 형태로 제공한다.

[NCS 능력단위] {selected_unit}
[NCS 하위 수행준거]
{sub_text}
[현재 단계 핵심 영역] {step_focus}

[학생이 처음 입력한 증상]
{user_symptom or '(미입력)'}

[이 단계의 기존 가이드 원문]
{step_body or '(원문 없음)'}

[학생이 이 단계에서 적어둔 진행 메모]
{student_step_note or '(메모 없음)'}

[필수 규칙]
1. 정답·고장 원인을 단정하지 말 것. (예: "이건 배터리 불량입니다" 금지)
2. 대신 학생이 직접 측정·관찰·비교해야 할 **구체 절차와 기준값**을 풍부하게 제시.
3. 측정 항목마다 가능한 한 **정상 기준값/범위**를 괄호로 함께 적기.
   - 예: OCV 12.6V 이상 정상, 충전 전압 13.8~14.9V, 전압강하 0.2V 이하, 종단저항 약 60Ω 등.
4. 단계 핵심 영역에 어긋나는 내용(예: 1단계인데 측정 절차 위주)은 쓰지 말 것.
5. 학생이 진행 메모를 적어두었다면 그 내용에 응답하여 다음 행동을 안내할 것.
6. 결과 해석이 두 갈래로 갈리면 "이러면 정상 / 이러면 의심" 형태로 안내.
7. 글은 짧고 직관적으로. 한 줄 35자 이내. 줄글·장황한 설명문 금지.

[출력 형식 — 정확히 다음 4블록만 출력]

## 🆘 AI 찬스 — {step_idx}단계 심화 도움

### 🔧 무엇을 어떻게
• (가장 먼저 할 행동 1줄)
• (어떤 도구로 어떻게 1줄)
• (보조적으로 확인할 부분 1줄)

### 📏 측정 / 관찰 포인트와 기준값
• (측정 위치 또는 관찰 부위 — 정상 기준값 1줄)
• (또 다른 측정 위치 — 정상 기준값 1줄)
• (필요시 추가 포인트 — 정상 기준값 1줄)

### 🚦 결과 해석 갈림길
• ✅ 정상이면: (다음에 해야 할 행동 1줄)
• ⚠ 의심되면: (어디를 더 살펴볼지 1줄)

### ✋ 학생이 스스로 답해야 할 질문
• (이 단계 핵심을 스스로 정리하게 만드는 1줄 질문)
""".strip()

def ask_gemini_step_help(
    user_symptom: str, selected_unit: str, step_idx: int,
    step_title: str, step_body: str, key: str,
    student_step_note: str = "",
) -> str:
    """AI 찬스 호출. 실패한 모델은 모두 누적 기록해 학생/교사가 원인을 파악할 수 있게 한다."""
    if genai is None or types is None:
        return "❌ google-genai 패키지를 불러오지 못했습니다."
    if not (key and str(key).strip()):
        return "❌ Gemini API 키가 설정되어 있지 않습니다."
    try:
        client = genai.Client(api_key=str(key).strip())
    except Exception as e:
        return f"❌ Gemini 클라이언트 초기화 실패: {type(e).__name__}: {e}"

    prompt = build_step_help_prompt(
        user_symptom, selected_unit, step_idx, step_title, step_body, student_step_note,
    )
    parts = [types.Part.from_text(text=prompt)]
    error_log: list[str] = []
    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[types.Content(role="user", parts=parts)],
            )
            text = (getattr(response, "text", None) or "").strip()
            if text:
                logger.info("AI 찬스 성공: model=%s, chars=%d", model_name, len(text))
                return text
            error_log.append(f"{model_name}: 응답이 비어있음")
        except Exception as e:
            msg = f"{model_name}: {type(e).__name__}: {e}"
            error_log.append(msg)
            logger.error("AI 찬스 호출 에러 — %s", msg)
            continue

    return (
        "❌ AI 찬스 응답 실패\n\n"
        "사용 가능한 Gemini 모델을 찾지 못했습니다. 잠시 후 다시 시도하거나 선생님께 문의하세요.\n\n"
        "[시도한 모델별 오류]\n"
        + "\n".join(f"• {m}" for m in error_log)
    )

def build_subject_specialty_prompt(student_name: str, records: list[dict]) -> str:
    """학기말 누적 기록을 분석해 '과목별세부특기사항' 초안을 생성하는 프롬프트.
    학교생활기록부에 그대로 옮겨 적을 수 있는 공적·서술형 어조의 200~400자 문장을 요구한다."""
    rec_lines: list[str] = []
    for r in records:
        unit = (r.get("unit") or "").strip()
        score = _safe_float(r.get("ncs_score"))
        when = (r.get("submitted_at") or "")[:10]
        eval_only = _extract_evaluation_only(r.get("result", ""))
        det = _parse_evaluation_details(eval_only)
        summary = (det.get("summary") or "").strip()
        cats = det.get("categories") or []
        cats_status = ", ".join(
            f"{c.get('name')}={c.get('status')}" for c in cats
        ) or "(미평가)"
        rec_lines.append(
            f"- [{when}] 단원: {unit} | 점수 {score:.0f} | 한줄요약: {summary or '(없음)'}\n"
            f"  카테고리 상태: {cats_status}"
        )
    summary_text = "\n".join(rec_lines) or "(누적 기록 없음)"

    return f"""
너는 학생의 학교생활기록부 '과목별세부특기사항' 작성을 보조하는 평가 코치다.
교사가 그대로 옮겨 적을 수 있는 공적·서술형 초안을 작성한다.

[학생 이름] {student_name}
[과목] 자동차 전기전자제어 (NCS 수행준거 기반)

[학기 전체 실습 기록 요약]
{summary_text}

[필수 규칙]
1. 200~400자, 1~2문단의 한 덩어리 서술형 문장으로 작성.
2. 학교생활기록부에 어울리는 공적·관찰형 어조. 머리말·맺음말·이모지·꾸밈문자 금지.
3. 학생 실명을 직접 호명하지 말고 "본 학생", "이 학생"으로만 지칭.
4. 다음 네 가지를 자연스럽게 녹여라:
   - 어떤 NCS 능력단위에 대한 실습을 어떤 양상으로 수행했는지 (구체 단원명 포함)
   - 학습 태도·과정에서 관찰된 강점
   - 측정·진단 절차 수행에서 보여준 성장 또는 변화
   - 보완이 필요한 부분과 앞으로의 학습 방향
5. 단정적 평가어("최고", "최우수") 대신 관찰형 어구("~을 정확히 수행함",
   "~ 절차를 능숙히 적용함", "~에서 성장하는 모습을 보임")를 사용.
6. 정답·고장 원인을 단정하는 표현 금지.

[출력 형식]
(서술형 본문만 출력. 머리말·라벨·따옴표 없음.)
""".strip()


def ask_gemini_for_specialty_notes(
    student_name: str, records: list[dict], key: str,
) -> str:
    """과목별세부특기사항 초안을 Gemini로 생성. 실패 사유는 사람이 읽을 수 있게 반환."""
    if genai is None or types is None:
        return "❌ google-genai 패키지를 불러오지 못했습니다."
    if not (key and str(key).strip()):
        return "❌ Gemini API 키가 설정되어 있지 않습니다."
    try:
        client = genai.Client(api_key=str(key).strip())
    except Exception as e:
        return f"❌ Gemini 클라이언트 초기화 실패: {type(e).__name__}: {e}"

    prompt = build_subject_specialty_prompt(student_name, records)
    parts = [types.Part.from_text(text=prompt)]
    error_log: list[str] = []
    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[types.Content(role="user", parts=parts)],
            )
            text = (getattr(response, "text", None) or "").strip()
            if text:
                logger.info("과목별세부특기사항 생성 성공: model=%s, chars=%d",
                            model_name, len(text))
                return text
            error_log.append(f"{model_name}: 응답이 비어있음")
        except Exception as e:
            msg = f"{model_name}: {type(e).__name__}: {e}"
            error_log.append(msg)
            logger.error("과목별세부특기사항 생성 에러 — %s", msg)
            continue
    return (
        "❌ AI 응답 실패\n\n"
        "사용 가능한 Gemini 모델을 찾지 못했습니다. 잠시 후 다시 시도해 주세요.\n\n"
        "[시도한 모델별 오류]\n"
        + "\n".join(f"• {m}" for m in error_log)
    )


def _detect_image_mime(image_file: Any) -> str:
    """Streamlit file_uploader의 UploadedFile에서 mime 타입을 안전하게 추출."""
    mime = (getattr(image_file, "type", None) or "").strip().lower()
    if mime in ("image/jpeg", "image/jpg", "image/png", "image/webp"):
        if mime == "image/jpg":
            return "image/jpeg"
        return mime
    # 이름으로 추정
    name = (getattr(image_file, "name", None) or "").lower()
    if name.endswith(".png"):
        return "image/png"
    if name.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"

def _gather_evaluation_images() -> tuple[list[tuple[bytes, str]], list[str]]:
    """현재 진행 중인 실습의 메인/단계별 썸네일을 raw bytes로 풀어 반환.
    또한 사진의 의미(메인/1~4단계)를 설명하는 라벨도 함께 반환해 평가 프롬프트가
    AI에 사진 순서를 알려줄 수 있게 한다."""
    image_parts: list[tuple[bytes, str]] = []
    descriptions: list[str] = []
    main_b64 = (st.session_state.get("latest_image_b64") or "").strip()
    if main_b64:
        try:
            image_parts.append((base64.b64decode(main_b64), "image/jpeg"))
            descriptions.append("메인 증상 사진")
        except Exception as e:
            logger.warning("메인 사진 디코딩 실패: %s", e)
    for i in range(1, 5):
        b64 = (st.session_state.get(f"step_photo_b64_{i}") or "").strip()
        if b64:
            try:
                image_parts.append((base64.b64decode(b64), "image/jpeg"))
                descriptions.append(f"{i}단계 진행 사진")
            except Exception as e:
                logger.warning("%d단계 사진 디코딩 실패: %s", i, e)
    return image_parts, descriptions


def ask_gemini(
    user_symptom: str,
    student_reasoning: str,
    image_file: Any,
    key: str,
    selected_unit: str,
    step: str,
    guidance_text: str = "",
    extra_image_parts: Optional[list[tuple[bytes, str]]] = None,
    photo_order_text: str = "",
) -> str:
    """Gemini API 호출. 실패 사유를 사람이 읽을 수 있는 형식으로 반환한다.
    평가 단계에서는 extra_image_parts(메인+단계 사진들)와 photo_order_text를 함께 전달해
    AI가 사진을 보고 카테고리별로 분석할 수 있게 한다."""
    if genai is None or types is None:
        return ("❌ `google-genai` 패키지를 불러오지 못했습니다.\n"
                "requirements.txt에 `google-genai`가 있는지 확인하고 다시 배포해 주세요.")
    if not (key and str(key).strip()):
        return ("❌ Gemini API 키가 설정되어 있지 않습니다.\n"
                "`.streamlit/secrets.toml`의 `GEMINI_API_KEY`를 확인해 주세요.")

    try:
        client = genai.Client(api_key=str(key).strip())
    except Exception as e:
        logger.exception("Gemini Client 초기화 실패")
        return f"❌ Gemini 클라이언트 초기화 실패: {type(e).__name__}: {e}"

    if step == "evaluation":
        prompt = build_evaluation_prompt(
            user_symptom, student_reasoning, selected_unit, guidance_text,
            photo_order_text=photo_order_text,
        )
    else:
        prompt = build_learning_prompt(user_symptom, selected_unit)

    parts: list[Any] = [types.Part.from_text(text=prompt)]
    if image_file is not None:
        try:
            raw = image_file.getvalue() if hasattr(image_file, "getvalue") else image_file.read()
            if raw:
                mime = _detect_image_mime(image_file)
                parts.append(types.Part.from_bytes(data=raw, mime_type=mime))
        except Exception as e:
            logger.warning("이미지 첨부 실패(텍스트만 진행): %s", e)
    if extra_image_parts:
        for raw, mime in extra_image_parts:
            if not raw:
                continue
            try:
                parts.append(types.Part.from_bytes(
                    data=raw, mime_type=mime or "image/jpeg",
                ))
            except Exception as e:
                logger.warning("추가 이미지 첨부 실패(스킵): %s", e)

    last_error = "(원인 미상)"
    for model_name in GEMINI_MODEL_CANDIDATES:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[types.Content(role="user", parts=parts)],
            )
            text = (getattr(response, "text", None) or "").strip()
            if text:
                logger.info("Gemini 호출 성공: model=%s, chars=%d", model_name, len(text))
                return text
            last_error = "응답이 비어있음"
            logger.warning("Gemini 빈 응답: model=%s", model_name)
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            logger.error("AI 호출 에러 (%s): %s", model_name, e)
            continue

    return (
        "❌ AI 응답을 가져오지 못했습니다.\n\n"
        f"마지막 오류: `{last_error}`\n\n"
        "- 잠시 후 다시 시도해 주세요.\n"
        "- API 키 사용량/권한 문제일 수 있어요. 선생님께 문의하세요."
    )

# ───────────────────────────────────────────────────────────────────────────
# PDF 생성 로직 (수평 공간 부족 오류 해결 버전)
# ───────────────────────────────────────────────────────────────────────────
_CATEGORY_LABELS = [
    ("🛡️", "준비 / 안전"),
    ("🔍", "점검 / 회로도"),
    ("⚡", "측정 / 전압"),
    ("🛠️", "판정 / 조치"),
]
# 학생 포트폴리오에서 이모지 대신 보여줄 번호·짧은 이름.
_CATEGORY_DISPLAY = [
    ("1", "준비 / 안전"),
    ("2", "점검 / 회로도"),
    ("3", "측정 / 전압"),
    ("4", "판정 / 조치"),
]
# 라벨 매칭은 공백·슬래시 변형까지 모두 허용해야 한다.
_CATEGORY_LABEL_PATTERNS = {
    "준비 / 안전":  r"준비\s*[/／]\s*안전",
    "점검 / 회로도": r"점검\s*[/／]\s*회로도",
    "측정 / 전압":  r"측정\s*[/／]\s*전압",
    "판정 / 조치":  r"판정\s*[/／]\s*조치",
}

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _parse_category_scores(result_text: str) -> dict[str, int]:
    """평가 결과 텍스트에서 카테고리별 통과/보완 여부를 파싱해 점수(100/60/0)로 환산.
    구·신 형식 모두 지원: '통과'/'보완' 한국어 또는 ✅/⚠ 이모지."""
    scores: dict[str, int] = {}
    text = result_text or ""
    for _icon, label in _CATEGORY_LABELS:
        lab_pat = _CATEGORY_LABEL_PATTERNS.get(label, re.escape(label))
        # 라벨 뒤 한 줄 안에서 통과/보완 키워드(또는 ✅/⚠) 탐색
        pattern = rf"{lab_pat}[^\n]{{0,120}}?(통과|보완|✅|⚠)"
        m = re.search(pattern, text)
        if m:
            tok = m.group(1)
            scores[label] = 100 if (tok == "통과" or tok == "✅") else 60
        else:
            scores[label] = 0
    return scores


def _extract_labeled_bullet(body: str, key_pattern: str) -> str:
    """'- 사실: ...' 같은 라벨 불릿에서 값만 추출."""
    if not body:
        return ""
    m = re.search(
        rf"(?:^|\n)\s*[-•*]\s*{key_pattern}\s*[:：]\s*([^\n]+)",
        body, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()
    return ""


def _parse_evaluation_details(result_text: str) -> dict:
    """평가 텍스트에서 다음 항목들을 추출:
    - summary: 한줄 요약
    - overall: 종합 코멘트(있는 경우)
    - categories: 카테고리별 {num, name, label, status, fact, ncs, improve, desc}
    구·신 형식 모두 지원한다."""
    text = result_text or ""

    # 1) 한줄 요약
    summary = ""
    m = re.search(r"##\s*[^\n]*?한줄\s*요약[^\n]*", text)
    if m:
        after = text[m.end():]
        inline = re.match(r"\s*[:：]\s*([^\n]+)", after)
        if inline:
            summary = inline.group(1).strip()
        else:
            for ln in after.splitlines():
                s = ln.strip()
                if s and not s.startswith("#"):
                    summary = s
                    break
    if summary:
        summary = re.split(r"\n\s*##|\n\s*###", summary, maxsplit=1)[0].strip()
        summary = summary.lstrip("-•*·").strip()

    # 2) 종합 코멘트
    overall = ""
    m_overall = re.search(
        r"##\s*[^\n]*?(?:종합\s*코멘트|총평|총\s*평|마무리)[^\n]*\n+([\s\S]+?)(?=\n\s*##|\Z)",
        text,
    )
    if m_overall:
        overall = m_overall.group(1).strip()
        # 라인 시작의 잡문자 정리
        overall_lines = []
        for ln in overall.splitlines():
            ln_s = ln.rstrip()
            if ln_s.strip():
                overall_lines.append(ln_s.lstrip("-•*·").strip())
        overall = "\n".join(overall_lines).strip()

    # 3) 카테고리별 상태·세부 분석
    categories: list[dict] = []
    for (num, name), (_icon, label) in zip(_CATEGORY_DISPLAY, _CATEGORY_LABELS):
        lab_pat = _CATEGORY_LABEL_PATTERNS.get(label, re.escape(label))
        # ── (a) 새 포맷: ### 1. 준비/안전 — 통과 \n - 사실:... - NCS 기준:... - 보완 제안:...
        block_pat = (
            rf"###\s*\d*\.?\s*{lab_pat}[^\n]*?(통과|보완|✅|⚠)"
            rf"([\s\S]+?)(?=\n\s*###|\n\s*##|\Z)"
        )
        mb = re.search(block_pat, text)
        if mb:
            tok = mb.group(1)
            status = "통과" if (tok == "통과" or tok == "✅") else "보완"
            body = mb.group(2)
            fact = _extract_labeled_bullet(body, r"사실")
            ncs = _extract_labeled_bullet(body, r"NCS\s*기준")
            improve = _extract_labeled_bullet(body, r"보완\s*제안")
            # body에 라벨 불릿이 없으면 첫 한 줄을 desc로 보조 노출
            desc = ""
            if not (fact or ncs or improve):
                for ln in body.splitlines():
                    ln_s = ln.strip().lstrip("-•*·").strip()
                    if ln_s and not ln_s.startswith("#"):
                        desc = ln_s
                        break
            categories.append({
                "num": num, "name": name, "label": label, "status": status,
                "fact": fact, "ncs": ncs, "improve": improve, "desc": desc,
            })
            continue

        # ── (b) 구 포맷 호환: '준비/안전 ... 통과/보완 ... — 한줄 코멘트'
        pat_status = rf"{lab_pat}[^\n]{{0,120}}?(통과|보완|✅|⚠)"
        m2 = re.search(pat_status, text)
        if m2:
            tok = m2.group(1)
            status = "통과" if (tok == "통과" or tok == "✅") else "보완"
            tail = text[m2.end():]
            tail_line = tail.split("\n", 1)[0]
            mdesc = re.search(r"[—\-–|]\s*([^\n]+)$", tail_line)
            desc = (mdesc.group(1) if mdesc else "").strip()
            desc = re.sub(r"^[\[\(『「\"'·•\-—–\s]+", "", desc)
            desc = re.sub(r"[\]\)』」\"']+$", "", desc).strip()
            if re.fullmatch(r"[\s/✅⚠통과보완]+", desc or ""):
                desc = ""
            categories.append({
                "num": num, "name": name, "label": label, "status": status,
                "fact": "", "ncs": "", "improve": "", "desc": desc,
            })
            continue

        categories.append({
            "num": num, "name": name, "label": label, "status": "미평가",
            "fact": "", "ncs": "", "improve": "", "desc": "",
        })

    return {"summary": summary, "overall": overall, "categories": categories}

def _score_color(score: float) -> str:
    if score >= 85: return "#10B981"   # green
    if score >= 70: return "#3B82F6"   # blue
    if score >= 55: return "#F59E0B"   # amber
    return "#EF4444"                   # red

def _score_band(score: float) -> str:
    if score >= 85: return "우수"
    if score >= 70: return "양호"
    if score >= 55: return "보통"
    return "보완 필요"

def _aggregate_unit_scores(records: list[dict]) -> list[tuple[str, float, int]]:
    """단원별 평균 NCS 점수와 건수를 반환."""
    totals: dict[str, list[float]] = {}
    for r in records:
        unit = (r.get("unit") or "").strip()
        if not unit:
            continue
        totals.setdefault(unit, []).append(_safe_float(r.get("ncs_score"), 0))
    rows: list[tuple[str, float, int]] = []
    for unit in NCS_UNITS:
        if unit in totals:
            vs = totals[unit]
            rows.append((unit, sum(vs) / len(vs), len(vs)))
    # NCS_UNITS에 없는 단원도 뒤에 붙임
    for unit, vs in totals.items():
        if unit not in NCS_UNITS:
            rows.append((unit, sum(vs) / len(vs), len(vs)))
    return rows

def _aggregate_category_scores(records: list[dict]) -> dict[str, float]:
    """전체 기록의 카테고리별 평균 점수."""
    buckets: dict[str, list[int]] = {label: [] for _i, label in _CATEGORY_LABELS}
    for r in records:
        cs = _parse_category_scores(r.get("result", ""))
        for label, sc in cs.items():
            if sc > 0:
                buckets[label].append(sc)
    return {label: (sum(v) / len(v) if v else 0.0) for label, v in buckets.items()}

def _plotly_to_png_bytes(fig) -> Optional[bytes]:
    """Plotly 그래프를 PNG로 변환(가능할 때만). kaleido가 없으면 None."""
    if fig is None:
        return None
    try:
        return fig.to_image(format="png", width=900, height=420, scale=2)
    except Exception:
        return None

# fpdf2는 폰트에 없는 글자(이모지 등)를 만나면 글자 폭을 계산하지 못해
# "Not enough horizontal space to render a single character" 예외를 던진다.
# Malgun Gothic은 한글/한자/기호는 지원하지만 대부분의 컬러 이모지는 지원하지 않으므로
# PDF에 넣기 전에 미리 안전한 텍스트로 정리한다.
_EMOJI_REPLACEMENTS = {
    "🛡️": "[안전]", "🔍": "[점검]", "⚡": "[측정]", "🛠️": "[판정]",
    "🔧": "", "🔋": "", "🚗": "", "💡": "", "🪑": "", "🛰️": "",
    "📓": "", "📚": "", "📊": "", "📅": "", "📝": "", "📬": "", "📌": "",
    "🎯": "", "🎓": "", "🚀": "", "🤖": "", "🧭": "", "🧪": "",
    "✅": "[O]", "⚠": "[!]", "⚠️": "[!]", "❓": "?", "❗": "!",
    "🏷": "", "🏷️": "", "·": "·",
}

def _sanitize_pdf_text(text: Any) -> str:
    """PDF 출력 전에 폰트가 지원하지 않는 이모지/변형 선택자를 제거하거나 치환한다."""
    if text is None:
        return ""
    s = str(text)
    for emo, rep in _EMOJI_REPLACEMENTS.items():
        if emo in s:
            s = s.replace(emo, rep)
    # 변형 선택자(U+FE0E/U+FE0F), 0폭 결합자(U+200D), 영역 표시(U+20E3) 제거
    s = re.sub(r"[\ufe00-\ufe0f\u200d\u20e3]", "", s)
    # BMP 밖(서플리먼터리 평면)의 모든 코드포인트 = 거의 모든 이모지/픽토그램 제거
    s = "".join(ch for ch in s if ord(ch) <= 0xFFFF)
    # BMP 내 이모지/픽토그램 영역도 제거
    s = re.sub(
        r"[\u2300-\u23FF\u2460-\u24FF\u25A0-\u25FF\u2600-\u27BF\u2B00-\u2BFF]",
        "",
        s,
    )
    return s.strip()

def _pdf_safe_multicell(pdf, text: str, line_height: float = 7.0, width: float = 0.0) -> None:
    """fpdf2에서 폭 부족으로 인한 예외가 나도 PDF 생성이 중단되지 않도록 보호."""
    cleaned = _sanitize_pdf_text(text)
    if not cleaned:
        return
    # 좌측 마진으로 복귀하여 충분한 폭 확보
    try:
        pdf.set_x(pdf.l_margin)
    except Exception:
        pass
    try:
        pdf.multi_cell(width, line_height, cleaned)
    except Exception as e:
        logger.warning("PDF multi_cell 실패, 안전 모드로 재시도: %s", e)
        # 한 글자도 못 그릴 정도면 안전한 ASCII로 재시도
        ascii_safe = re.sub(r"[^\x20-\x7E\r\n\t]", "?", cleaned)
        try:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(width, line_height, ascii_safe)
        except Exception as e2:
            logger.error("PDF multi_cell 최종 실패: %s", e2)

def build_comprehensive_portfolio_pdf(student_id: str, student_name: str, records: list[dict]) -> bytes:
    """학기말 포트폴리오 PDF 생성. 어떤 예외가 발생해도 빈 bytes를 반환하여 UI 충돌을 방지한다."""
    if FPDF is None:
        return b""
    try:
        return _build_portfolio_pdf_inner(student_id, student_name, records)
    except Exception as e:
        logger.exception("학기말 포트폴리오 PDF 생성 중 예외: %s", e)
        return b""

def _build_portfolio_pdf_inner(student_id: str, student_name: str, records: list[dict]) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=15, top=15, right=15)

    font_path = Path(__file__).resolve().parent / "malgun.ttf"
    bold_path = Path(__file__).resolve().parent / "malgunbd.ttf"
    has_font = font_path.exists()
    if has_font:
        pdf.add_font("Malgun", "", str(font_path))
        if bold_path.exists():
            pdf.add_font("Malgun", "B", str(bold_path))
        base_font = "Malgun"
    else:
        base_font = "Helvetica"
    has_bold = has_font and bold_path.exists()
    pdf.set_font(base_font, size=11)

    student_id = _sanitize_pdf_text(student_id) or "-"
    student_name = _sanitize_pdf_text(student_name) or "학생"

    # ── 표지 ─────────────────────────────────────────────
    pdf.add_page()
    pdf.set_fill_color(30, 58, 138)
    pdf.rect(0, 0, 210, 40, "F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font(base_font, "B" if has_bold else "", 22)
    pdf.set_xy(0, 12)
    pdf.cell(210, 12, _sanitize_pdf_text("나의 자동차 실습 성장 일지"), align="C")
    pdf.set_font(base_font, size=12)
    pdf.set_xy(0, 26)
    pdf.cell(210, 8, _sanitize_pdf_text(f"{student_name}  ·  학번 {student_id}"), align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.set_xy(15, 50)

    # ── 요약 카드 ────────────────────────────────────────
    pdf.set_font(base_font, size=11)
    avg_score = (sum(_safe_float(r.get("ncs_score")) for r in records) / len(records)) if records else 0
    fb_count = sum(1 for r in records if (r.get("teacher_feedback") or "").strip())
    unit_count = len({(r.get("unit") or "").strip() for r in records if r.get("unit")})
    pdf.set_fill_color(243, 244, 246)
    pdf.set_draw_color(229, 231, 235)
    pdf.rect(15, pdf.get_y(), 180, 22, "DF")
    y0 = pdf.get_y()
    pdf.set_xy(20, y0 + 3); pdf.cell(55, 7, _sanitize_pdf_text("총 실습 건수"))
    pdf.set_xy(20, y0 + 11); pdf.set_font_size(14); pdf.cell(55, 7, f"{len(records)} 건"); pdf.set_font_size(11)
    pdf.set_xy(80, y0 + 3); pdf.cell(55, 7, _sanitize_pdf_text("평균 성취도"))
    pdf.set_xy(80, y0 + 11); pdf.set_font_size(14); pdf.cell(55, 7, f"{avg_score:.1f} 점"); pdf.set_font_size(11)
    pdf.set_xy(140, y0 + 3); pdf.cell(50, 7, _sanitize_pdf_text("참여 단원 / 피드백"))
    pdf.set_xy(140, y0 + 11); pdf.set_font_size(14); pdf.cell(50, 7, f"{unit_count}단원 · {fb_count}건"); pdf.set_font_size(11)
    pdf.set_xy(15, y0 + 28)

    # ── 교사 피드백 상단 강조 ────────────────────────────
    feedback_recs = [r for r in records if (r.get("teacher_feedback") or "").strip()]
    if feedback_recs:
        pdf.set_font_size(14); pdf.set_text_color(202, 138, 4)
        pdf.set_x(15)
        pdf.cell(0, 10, _sanitize_pdf_text("[ 선생님의 피드백 ]"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0); pdf.set_font_size(11)
        for r in feedback_recs:
            if pdf.get_y() > 250: pdf.add_page()
            pdf.set_fill_color(255, 247, 230)
            pdf.set_draw_color(250, 140, 22)
            head = f"  {r.get('unit', '')}  ({(r.get('submitted_at') or '')[:10]})"
            pdf.set_x(15); pdf.cell(180, 6, _sanitize_pdf_text(head), fill=True)
            pdf.ln(6)
            _pdf_safe_multicell(pdf, f"  {r.get('teacher_feedback', '')}", line_height=7, width=180)
            pdf.ln(3)
        pdf.ln(2)

    # ── 단원별 성취도 그래프 ─────────────────────────────
    if go is not None and records:
        try:
            unit_rows = _aggregate_unit_scores(records)
            if unit_rows:
                units = [u for u, _s, _n in unit_rows]
                scores = [s for _u, s, _n in unit_rows]
                colors = [_score_color(s) for s in scores]
                fig = go.Figure(data=[go.Bar(
                    x=units, y=scores, marker_color=colors,
                    text=[f"{s:.0f}" for s in scores], textposition="outside"
                )])
                fig.update_layout(
                    title="단원별 평균 성취도",
                    yaxis=dict(range=[0, 110]),
                    plot_bgcolor="white", paper_bgcolor="white",
                    margin=dict(l=40, r=20, t=40, b=80),
                )
                png = _plotly_to_png_bytes(fig)
                if png:
                    if pdf.get_y() > 200: pdf.add_page()
                    pdf.image(BytesIO(png), x=15, w=180)
                    pdf.ln(4)

            cat_avgs = _aggregate_category_scores(records)
            if any(cat_avgs.values()):
                labels = [lab for _ico, lab in _CATEGORY_LABELS]
                vals = [cat_avgs.get(lab, 0.0) for lab in labels]
                fig2 = go.Figure(data=go.Scatterpolar(
                    r=vals + [vals[0]], theta=labels + [labels[0]],
                    fill="toself", line_color="#1E40AF", fillcolor="rgba(59,130,246,0.35)"
                ))
                fig2.update_layout(
                    title="NCS 카테고리별 평균",
                    polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
                    paper_bgcolor="white", margin=dict(l=40, r=40, t=40, b=20),
                )
                png2 = _plotly_to_png_bytes(fig2)
                if png2:
                    if pdf.get_y() > 200: pdf.add_page()
                    pdf.image(BytesIO(png2), x=30, w=150)
                    pdf.ln(4)
        except Exception as e:
            logger.warning("그래프 PDF 임베드 실패(텍스트 본문은 계속 진행): %s", e)

    # ── 실습 기록 상세 ────────────────────────────────────
    pdf.add_page()
    pdf.set_font_size(14); pdf.set_text_color(30, 58, 138)
    pdf.set_x(15)
    pdf.cell(0, 10, _sanitize_pdf_text("실습 기록"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0); pdf.set_font_size(11)

    for idx, rec in enumerate(sorted(records, key=lambda x: x.get('submitted_at', '')), 1):
        if pdf.get_y() > 240: pdf.add_page()
        score = _safe_float(rec.get("ncs_score"))
        col = _score_color(score)
        r_, g_, b_ = int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16)
        y = pdf.get_y()
        pdf.set_fill_color(r_, g_, b_); pdf.rect(15, y, 4, 22, "F")
        pdf.set_xy(22, y + 2)
        pdf.set_font_size(13)
        title = f"{idx}. {rec.get('unit', '')}  ({(rec.get('submitted_at') or '')[:10]})"
        pdf.cell(0, 7, _sanitize_pdf_text(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font_size(10); pdf.set_text_color(107, 114, 128)
        pdf.set_x(22)
        pdf.cell(0, 6, _sanitize_pdf_text(f"성취도 {score:.0f}점  ·  {_score_band(score)}"),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0); pdf.set_font_size(11)
        pdf.ln(2)

        _pdf_safe_multicell(pdf, f"[수행 내용]\n{rec.get('symptom', '(없음)')}")
        pdf.ln(1)
        _pdf_safe_multicell(pdf, f"[나의 소감]\n{rec.get('reflection', '(없음)')}")
        pdf.ln(2)

        img_bytes = thumbnail_b64_to_bytes(rec.get("image_b64", ""))
        if img_bytes:
            try:
                pdf.set_x(15)
                pdf.image(BytesIO(img_bytes), w=55)
                pdf.ln(3)
            except Exception:
                pass

        pdf.set_draw_color(229, 231, 235)
        pdf.line(15, pdf.get_y(), 195, pdf.get_y())
        pdf.ln(4)

    return bytes(pdf.output(dest="S"))

# ───────────────────────────────────────────────────────────────────────────
# UI 렌더링 함수
# ───────────────────────────────────────────────────────────────────────────
def render_mission_card(text: str):
    with st.container(border=True):
        st.markdown("### 🧭 AI 진단 가이드")
        st.markdown(text)

def render_evaluation_card(text: str):
    with st.container(border=True):
        st.markdown("### 📝 실습 수행 평가")
        st.markdown(text)

def render_ncs_achievement(result_text: str, unit_name: str):
    st.subheader(f"📊 {unit_name} 성취도 분석")
    # 정규표현식이나 키워드 매칭을 통해 당일 단원의 점수만 계산하여 표시하는 로직
    score = 85 # 예시 점수
    st.progress(score / 100, text=f"오늘의 성취도: {score}점")

_MISSION_STEPS_CSS = """
<style>
.mission-card {
    border-radius: 14px; padding: 14px 18px; margin-bottom: 10px;
    border-left: 8px solid var(--step-color, #3B82F6);
    background: #FFFFFF; box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
.mission-card h4.mission-title {
    margin: 0 0 8px 0;
    font-size: 1.5rem;
    color: #1D4ED8 !important;
    font-weight: 800;
    letter-spacing: -0.2px;
}
.mission-card .body { line-height: 1.7; font-size: 1.1rem; color: #1F2937; }
.mission-card .reflect {
    margin-top: 8px; padding: 8px 12px; border-radius: 8px;
    background: #FEF3C7; color: #92400E; font-weight: 600;
}
.mission-progress {
    background: #EFF6FF; border-radius: 10px; padding: 12px 16px;
    margin: 6px 0 14px 0;
    color: #1D4ED8 !important;
    font-weight: 700; font-size: 1.15rem;
}
.mission-progress * { color: #1D4ED8 !important; }
.ai-chance-result {
    margin-top: 6px; padding: 14px 16px; border-radius: 12px;
    background: linear-gradient(135deg,#FEF3C7 0%,#FDE68A 100%);
    border-left: 6px solid #D97706; color: #78350F; font-size: 1.05rem;
    line-height: 1.7;
}
.ai-chance-result h5 { margin: 0 0 6px 0; color:#92400E; font-size:1.15rem; }
.ai-chance-badge {
    display:inline-block; padding:4px 10px; border-radius:999px;
    background:#FEF3C7; color:#92400E; font-weight:700; font-size:12px;
    margin-left:6px;
}

/* AI 찬스 관련 버튼들 — 옅은 하늘색 톤 + 한 줄 표시 (텍스트 잘림 방지) */
div[class*="st-key-open_chance_"] .stButton button,
div[class*="st-key-chance_yes_"]   .stButton button,
div[class*="st-key-chance_no_"]    .stButton button {
    background: linear-gradient(180deg, #DBEAFE 0%, #BFDBFE 100%) !important;
    color: #1E3A8A !important;
    border: 1.5px solid #60A5FA !important;
    font-weight: 700 !important;
    font-size: 1.15rem !important;
    padding: 12px 18px !important;
    border-radius: 12px !important;
    box-shadow: 0 4px 10px rgba(96,165,250,0.20) !important;
    white-space: nowrap !important;
    overflow: visible !important;
    min-width: max-content !important;
}
div[class*="st-key-open_chance_"] .stButton button:hover,
div[class*="st-key-chance_yes_"]   .stButton button:hover,
div[class*="st-key-chance_no_"]    .stButton button:hover {
    background: linear-gradient(180deg, #BFDBFE 0%, #93C5FD 100%) !important;
    transform: translateY(-1px);
    box-shadow: 0 8px 18px rgba(59,130,246,0.30) !important;
}

/* 다이얼로그 내부의 모든 버튼 텍스트도 줄바꿈 금지(긴 한국어 라벨 잘림 방지) */
div[role="dialog"] .stButton button,
div[data-testid="stModal"] .stButton button {
    white-space: nowrap !important;
    overflow: visible !important;
    min-width: max-content !important;
}

</style>
"""

@st.dialog("⚠ AI 찬스 사용 확인")
def _ai_chance_dialog(step_idx: int, selected_unit: str, step_title: str, step_body: str, api_key: str) -> None:
    st.warning(
        "더 많은 도움을 받을 순 있지만 수행평가 평가에서 **감점요소**로 작용합니다.\n\n"
        "그래도 진행하시겠습니까?"
    )
    st.caption(f"대상 단계: **{step_idx}단계 · {step_title}**")
    c1, c2 = st.columns([3, 2])
    with c1:
        if st.button("✅ 예, 사용할게요", use_container_width=True, key=f"chance_yes_{step_idx}"):
            with st.spinner("AI가 NCS 기반 심화 도움을 작성 중..."):
                advice = ask_gemini_step_help(
                    st.session_state.get("latest_symptom", ""),
                    selected_unit, step_idx, step_title, step_body, api_key,
                    student_step_note=(st.session_state.get(f"step_note_{step_idx}") or ""),
                )
            if (not advice) or advice.lstrip().startswith("❌"):
                st.error(advice or "AI 찬스 응답을 받지 못했습니다.")
                return
            st.session_state[f"ai_chance_used_{step_idx}"] = True
            st.session_state[f"ai_chance_text_{step_idx}"] = advice
            st.rerun()
    with c2:
        if st.button("❌ 취소", use_container_width=True, key=f"chance_no_{step_idx}"):
            st.rerun()

def _render_mission_steps_ui(selected_unit: str, api_key: str) -> None:
    st.markdown(_MISSION_STEPS_CSS, unsafe_allow_html=True)
    st.markdown("## 🧭 AI 진단 가이드 — 단계별 미션")
    st.caption("각 단계의 미션을 보고 직접 실습한 뒤, 진행 상황과 사진을 남겨주세요. AI는 정답을 알려주지 않고 힌트만 줘요.")

    # 재수행 흐름: 직전 AI 평가를 참고용으로 가이드 상단에 노출
    prev_eval = (st.session_state.get("previous_evaluation") or "").strip()
    if prev_eval:
        redo_count = int(st.session_state.get("redo_count", 0))
        st.warning(
            f"직전 AI 평가에서 보완할 점이 있었어요. (재수행 {redo_count}회차)\n"
            "4단계 메모와 사진을 보완한 뒤 아래에서 다시 평가받아 주세요."
        )
        st.markdown(_PORTFOLIO_CSS, unsafe_allow_html=True)
        with st.expander("이전 AI 평가 보기 (참고용)", expanded=False):
            prev_details = _parse_evaluation_details(prev_eval)
            prev_summary = (prev_details.get("summary") or "").strip()
            if prev_summary:
                st.markdown(
                    f"""
<div class="pf-summary">
  <span class="tag">직전 AI 한줄 요약</span>
  <div class="text">{_esc_html(prev_summary)}</div>
</div>""",
                    unsafe_allow_html=True,
                )
            prev_cats = prev_details.get("categories") or []
            if prev_cats:
                st.markdown(_render_category_boxes_html(prev_cats),
                            unsafe_allow_html=True)
            prev_overall = (prev_details.get("overall") or "").strip()
            if prev_overall:
                st.markdown(
                    f"""
<div class="pf-overall">
  <div class="head">직전 AI 종합 코멘트</div>
  {_esc_html(prev_overall)}
</div>""",
                    unsafe_allow_html=True,
                )
        st.markdown("---")

    parsed_steps = _parse_mission_steps(st.session_state.get("latest_guidance", ""))
    # 4개를 채우지 못하면 기본 메타로 패딩
    steps: list[dict] = []
    for i in range(4):
        meta = _MISSION_STEP_META[i]
        if i < len(parsed_steps):
            heading = parsed_steps[i]["heading"]
            body = "\n".join(parsed_steps[i]["body_lines"]).strip()
        else:
            heading = f"{i+1}️⃣ {meta['title']}"
            body = "(AI 가이드 파싱 실패 — 아래 원문을 참고하세요)"
        steps.append({"meta": meta, "heading": heading, "body": body})

    # 만약 파싱 자체가 완전 실패면 원본 텍스트라도 한 번 보여줌
    if not parsed_steps:
        with st.expander("🔎 AI 가이드 원문 보기", expanded=True):
            st.markdown(st.session_state.get("latest_guidance", ""))

    done_count = 0
    for i, step in enumerate(steps, 1):
        meta = step["meta"]
        is_done = bool(st.session_state.get(f"step_done_{i}", False))
        if is_done:
            done_count += 1
        status = "✅" if is_done else "⏳"

        # 미션 카드 (AI 안내)
        body_html = step["body"]
        # ✋ 줄을 따로 강조 처리
        body_lines, reflect_line = [], ""
        for ln in body_html.splitlines():
            if ln.strip().startswith("✋"):
                reflect_line = ln.strip()
            else:
                body_lines.append(ln)
        body_block = "<br>".join(l for l in body_lines if l.strip())
        reflect_block = (
            f'<div class="reflect">{reflect_line}</div>' if reflect_line else ""
        )

        st.markdown(
            f"""
<div class="mission-card" style="--step-color:{meta['color']};">
  <h4 class="mission-title">{i}단계 · {meta['title']}</h4>
  <div class="body">{body_block}</div>
  {reflect_block}
</div>
""",
            unsafe_allow_html=True,
        )

        # AI 찬스 — 더 자세한 힌트 요청 (감점 경고)
        chance_used = bool(st.session_state.get(f"ai_chance_used_{i}", False))
        chance_text = st.session_state.get(f"ai_chance_text_{i}", "")
        bcol1, bcol2 = st.columns([1, 3])
        with bcol1:
            if chance_used:
                st.markdown(
                    '<span class="ai-chance-badge">🆘 AI 찬스 사용함 (감점 적용)</span>',
                    unsafe_allow_html=True,
                )
            else:
                if st.button(
                    "🆘 AI 찬스 사용하기",
                    key=f"open_chance_{i}",
                    help="이 단계에서 더 자세한 힌트를 받아요. 단, 평가에서 감점됩니다.",
                ):
                    _ai_chance_dialog(i, selected_unit, meta["title"], step["body"], api_key)
        if chance_used and chance_text:
            safe_text = chance_text.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            st.markdown(
                f"""
<div class="ai-chance-result">
  <h5>🆘 AI 찬스 — {i}단계 추가 힌트</h5>
  {safe_text}
</div>
""",
                unsafe_allow_html=True,
            )

        # 학생 입력
        with st.container(border=True):
            st.text_area(
                f"📝 {i}단계 진행 상황",
                key=f"step_note_{i}",
                placeholder="이 단계에서 무엇을 확인했고, 어떤 값을 측정/관찰했는지 간단히 적어주세요.",
                height=90,
            )
            photo = st.file_uploader(
                f"📸 {i}단계 진행 사진 (선택)",
                type=["jpg", "jpeg", "png"],
                key=f"step_photo_{i}",
            )
            if photo is not None:
                try:
                    st.image(photo, width=260, caption=f"{i}단계 사진 미리보기")
                    # 썸네일 base64를 세션에 캐싱(저장 시 사용)
                    st.session_state[f"step_photo_b64_{i}"] = make_thumbnail_b64(photo)
                except Exception as _e:
                    logger.warning("단계 사진 미리보기 실패: %s", _e)
            st.checkbox(
                "이 단계를 완료했어요",
                key=f"step_done_{i}",
                help="모든 단계를 체크해야 평가받기 버튼이 활성화됩니다.",
            )

    # 진행률
    progress = done_count / 4
    used_chances = sum(1 for i in range(1, 5) if st.session_state.get(f"ai_chance_used_{i}"))
    chance_html = (
        f' · AI 찬스 사용 {used_chances}회' if used_chances else ""
    )
    st.markdown(
        f'<div class="mission-progress">📊 단계 진행률 &nbsp; <b>{done_count} / 4 단계 완료</b>{chance_html}</div>',
        unsafe_allow_html=True,
    )
    st.progress(progress)

    st.markdown("---")
    refl = st.text_area(
        "📝 오늘의 실습 소감",
        key="diag_reflection",
        placeholder="이번 실습에서 새로 알게 된 점, 어려웠던 점 등을 적어주세요.",
        height=110,
    )

    all_done = done_count == 4
    if not all_done:
        st.info("🔒 4단계를 모두 완료해야 결과 평가를 받을 수 있어요.")

    btn_label = (
        "다시 평가 받기" if prev_eval else "✅ 모든 단계 완료, AI 평가 받기"
    )
    if st.button(
        btn_label,
        type="primary",
        use_container_width=True,
        disabled=not all_done,
        key="run_evaluation_btn",
    ):
        # 단계별 메모 + AI 찬스 정보 통합
        reasoning_blocks = []
        photos_b64: dict[str, str] = {}
        ai_chance_steps: list[int] = []
        for i in range(1, 5):
            note = (st.session_state.get(f"step_note_{i}") or "").strip()
            photo_b64 = st.session_state.get(f"step_photo_b64_{i}") or ""
            meta = _MISSION_STEP_META[i - 1]
            chance_used = bool(st.session_state.get(f"ai_chance_used_{i}", False))
            chance_marker = "  ⚠ AI 찬스 사용" if chance_used else ""
            reasoning_blocks.append(
                f"[{i}단계 · {meta['emoji']} {meta['title']}]{chance_marker}\n"
                f"{note or '(메모 없음)'}"
            )
            if photo_b64:
                photos_b64[str(i)] = photo_b64
            if chance_used:
                ai_chance_steps.append(i)
        student_reasoning = "\n\n".join(reasoning_blocks)

        # AI 찬스 사용 단계당 -7점, 기본 80점, 최저 45점
        ncs_score = max(45.0, 80.0 - 7.0 * len(ai_chance_steps))

        if not api_key:
            st.error("❌ Gemini API 키가 설정되어 있지 않습니다. 선생님께 문의해 주세요.")
            return

        # 메인 사진 + 단계별 사진을 모두 모아 AI 평가에 첨부
        image_parts, image_descs = _gather_evaluation_images()
        photo_order_text = (
            "\n".join(f"- {idx + 1}번째 사진: {d}" for idx, d in enumerate(image_descs))
            if image_descs else "(첨부 사진 없음)"
        )

        with st.spinner("🤖 AI가 4단계 메모와 사진을 NCS 기준으로 분석 중이에요..."):
            eval_res = ask_gemini(
                st.session_state.latest_symptom, student_reasoning, None,
                api_key, selected_unit, "evaluation",
                st.session_state.latest_guidance,
                extra_image_parts=image_parts,
                photo_order_text=photo_order_text,
            )
        if (not eval_res) or eval_res.lstrip().startswith("❌"):
            st.error(eval_res or "AI 평가 응답을 받지 못했습니다.")
            return

        # 아직 저장하지 않는다. 검토(evaluation) 단계로 넘어가 학생이
        # "이대로 저장" 또는 "피드백 받고 다시 수행" 중 선택하도록 한다.
        import json as _json
        pending_record = {
            "record_id": str(uuid.uuid4()),
            "submitted_at": now_kst_display(),
            "student_id": st.session_state.student_id,
            "student_display_name": st.session_state.student_display_name,
            "subject": "자동차 전기전자제어",
            "unit": selected_unit,
            "mode": "학습 모드",
            "symptom": st.session_state.latest_symptom,
            "reasoning": student_reasoning,
            "result": "",  # 저장 시점에 채워짐
            "reflection": refl,
            "image_b64": st.session_state.latest_image_b64,
            "mission_step_photos_json":
                _json.dumps(photos_b64, ensure_ascii=False) if photos_b64 else "",
            "ai_chance_used_steps": ",".join(str(s) for s in ai_chance_steps),
            "teacher_feedback": "",
            "teacher_feedback_updated_at": "",
        }
        st.session_state["pending_record"] = pending_record
        st.session_state["pending_ncs_score"] = float(ncs_score)
        st.session_state.latest_evaluation = eval_res
        st.session_state.diag_step = "evaluation"
        st.rerun()

def _commit_pending_record() -> None:
    """평가 검토 화면에서 '이대로 저장하기'를 눌렀을 때 호출.
    pending_record + latest_evaluation을 합쳐 시트에 누적 저장하고 결과 단계로 이동."""
    record = st.session_state.get("pending_record")
    ncs_score = float(st.session_state.get("pending_ncs_score") or 0.0)
    eval_text = st.session_state.get("latest_evaluation", "")
    if not record:
        st.error("저장할 기록이 없습니다. 이전 단계로 돌아가 다시 진행해 주세요.")
        return

    # 평가 텍스트를 최종 result에 합치기 (가이드 + 평가)
    record["result"] = _compose_combined_result(
        st.session_state.get("latest_guidance", ""), eval_text
    )

    # 포트폴리오 즉시 반영용 로컬 record
    local_record = dict(record)
    local_record["ncs_score"] = str(round(float(ncs_score), 2))
    local_record["datetime"] = local_record.get("submitted_at", "")
    local_record["diagnosis_result"] = local_record.get("result", "")
    local_record["name"] = local_record.get("student_display_name", "")

    existing_records = list(st.session_state.get("my_history_records") or [])
    st.session_state["my_history_records"] = existing_records + [local_record]

    save_error: Optional[str] = None
    try:
        shb.append_history_from_record(record, ncs_score)
        shb.invalidate_all_sheet_caches()
    except Exception as _save_e:
        logger.exception("history 저장 실패: %s", _save_e)
        save_error = f"{type(_save_e).__name__}: {_save_e}"
        st.session_state["my_history_records"] = existing_records

    if save_error is None:
        try:
            refreshed = shb.filter_history_records_by_student(
                st.session_state.student_id
            )
            rid = record.get("record_id")
            if any((r.get("record_id") or "").strip() == rid for r in refreshed):
                st.session_state["my_history_records"] = refreshed
        except Exception as _refresh_e:
            logger.warning("history 재조회 실패(로컬 누적본 유지): %s", _refresh_e)

    if save_error:
        st.session_state["_last_save_error"] = save_error

    # 보류·재수행 상태 정리
    st.session_state["pending_record"] = None
    st.session_state["pending_ncs_score"] = 0.0
    st.session_state["previous_evaluation"] = ""
    st.session_state["redo_count"] = 0
    st.session_state.diag_step = "result"
    st.rerun()


def _render_evaluation_review(selected_unit: str, api_key: str) -> None:
    """AI 평가 결과 검토 화면. 저장 전에 학생이 결과를 살펴보고
    '피드백 받고 다시 수행' 또는 '이대로 저장' 중 하나를 선택한다."""
    st.markdown(_PORTFOLIO_CSS, unsafe_allow_html=True)
    eval_text = st.session_state.get("latest_evaluation", "") or ""

    st.markdown("## Today 수행평가 결과")
    st.caption(
        "AI가 오늘 작성한 4단계 메모와 첨부 사진을 NCS 수행준거와 비교해 분석한 결과예요. "
        "결과가 부족하다고 느끼면 다시 수행해 더 정확한 결과를 받을 수 있어요."
    )

    details = _parse_evaluation_details(eval_text)

    # 1) 한줄 요약 (강조 박스)
    summary = (details.get("summary") or "").strip()
    if summary:
        st.markdown(
            f"""
<div class="pf-summary">
  <span class="tag">AI 한줄 요약</span>
  <div class="text">{_esc_html(summary)}</div>
</div>""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
<div class="pf-summary muted">
  <span class="tag">AI 한줄 요약</span>
  <div class="text">요약을 추출하지 못했어요.</div>
</div>""",
            unsafe_allow_html=True,
        )

    # 2) 카테고리 4박스 (사실 / NCS 기준 / 보완 제안)
    cats = details.get("categories") or []
    if cats:
        st.markdown(_render_category_boxes_html(cats), unsafe_allow_html=True)

    # 3) 종합 코멘트
    overall = (details.get("overall") or "").strip()
    if overall:
        st.markdown(
            f"""
<div class="pf-overall">
  <div class="head">AI 종합 코멘트</div>
  {_esc_html(overall)}
</div>""",
            unsafe_allow_html=True,
        )

    # 4) 두 갈래 버튼 — 다시 수행 / 이대로 저장 (같은 크기로 통일)
    st.markdown("---")
    st.caption(
        "총평을 살펴봤어요. 결과가 마음에 들지 않으면 아래에서 다시 수행하고, "
        "지금까지의 결과를 그대로 기록하려면 '이대로 저장하기'를 눌러주세요."
    )
    st.markdown(
        """
<style>
/* 평가 검토 화면 하단의 두 버튼을 정확히 같은 크기로 통일 */
div[class*="st-key-redo_eval_btn"] .stButton button,
div[class*="st-key-save_eval_btn"] .stButton button {
    height: 72px !important;
    min-height: 72px !important;
    font-size: 1.2rem !important;
    font-weight: 800 !important;
    padding: 0 18px !important;
    white-space: nowrap !important;
    border-radius: 12px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
}
</style>
""",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        if st.button(
            "🔁 다시 수행하기",
            use_container_width=True,
            help="위 평가를 참고해 4단계 메모/사진을 보완한 뒤 다시 평가받을 수 있어요. (아직 저장되지 않음)",
            key="redo_eval_btn",
        ):
            # 직전 평가를 가이드 화면 상단에 참고용으로 노출
            st.session_state["previous_evaluation"] = eval_text
            st.session_state["redo_count"] = int(
                st.session_state.get("redo_count", 0)
            ) + 1
            # 다시 점검하도록 '단계 완료' 체크박스만 초기화 (메모·사진은 보존)
            for i in range(1, 5):
                st.session_state.pop(f"step_done_{i}", None)
            # 평가·저장 보류 상태 비움
            st.session_state["latest_evaluation"] = ""
            st.session_state["pending_record"] = None
            st.session_state["pending_ncs_score"] = 0.0
            st.session_state.diag_step = "guidance"
            st.rerun()
    with c2:
        if st.button(
            "💾 이대로 저장하기",
            type="primary",
            use_container_width=True,
            help="현재 평가 결과를 오늘의 수행 결과로 포트폴리오에 누적 저장합니다.",
            key="save_eval_btn",
        ):
            _commit_pending_record()


def _render_diagnosis_input_tab(selected_unit: str, api_key: str):
    diag_step = st.session_state.get("diag_step", "input")
    
    if diag_step == "input":
        # AI 가이드 받기 버튼을 크고 눈에 띄게 만드는 전용 스타일
        st.markdown(
            """
<style>
div.stButton > button[kind="primary"] {
    font-size: 1.35rem !important;
    font-weight: 800 !important;
    padding: 18px 28px !important;
    border-radius: 14px !important;
    background: linear-gradient(135deg, #2563EB 0%, #1D4ED8 100%) !important;
    color: #ffffff !important;
    border: none !important;
    box-shadow: 0 10px 24px rgba(37, 99, 235, 0.30) !important;
    transition: transform .18s ease, box-shadow .18s ease, filter .18s ease !important;
    letter-spacing: 0.5px;
}
div.stButton > button[kind="primary"]:hover {
    transform: translateY(-3px);
    box-shadow: 0 16px 32px rgba(37, 99, 235, 0.40) !important;
    filter: brightness(1.05);
}
div.stButton > button[kind="primary"]:active { transform: translateY(-1px); }
</style>
""",
            unsafe_allow_html=True,
        )
        with st.container(border=True):
            st.markdown(f"### 📝 오늘의 과제: {selected_unit}")
            hints = UNIT_INPUT_HINTS.get(selected_unit, {})
            t = st.text_input(
                "🔍 대상 부품",
                key="diag_target_part",
                placeholder=hints.get("target", "예: 운전석 도어 커넥터 E12"),
                help="오늘 실습할 부품 또는 장치의 이름을 정확히 적어주세요.",
            )
            s = st.text_area(
                "⚡ 현재 상태",
                key="diag_current_state",
                placeholder=hints.get("state", "예: 멀티미터 전압 0V, 점등되지 않음"),
                help="오늘 실습하고자 하는 부품이나 장치의 상태를 작성하세요.",
                height=110,
            )
            q = st.text_area(
                "❓ 학습 질문",
                key="diag_learning_question",
                placeholder=hints.get("question", "예: 단선 위치는 어떻게 점검하면 되나요?"),
                help="오늘 실습하는 부품이나 장치의 고장 및 진단, 정비 방법에 대해 자유롭게 질문하세요.",
                height=110,
            )
            img = st.file_uploader(
                "📸 사진 업로드",
                type=["jpg", "jpeg", "png"],
                help="실습 부품·계측기·회로도 등의 사진을 올리면 AI가 사진 단서까지 반영해 가이드를 만들어줘요.",
            )
            if img is not None:
                try:
                    st.image(img, caption=f"📷 업로드한 사진 미리보기 — {img.name}", width=360)
                except Exception as _e:
                    logger.warning("사진 미리보기 표시 실패: %s", _e)
                    st.caption("⚠ 사진 미리보기를 표시하지 못했어요. 파일 형식을 확인해 주세요.")

            st.markdown("")
            if st.button("🚀 AI 가이드 받기", type="primary", use_container_width=True):
                symptom = compose_structured_symptom(t, s, q)
                if not symptom.strip():
                    st.warning("⚠ 대상 부품·현재 상태·학습 질문 중 하나 이상은 입력해 주세요.")
                elif not api_key:
                    st.error(
                        "❌ Gemini API 키가 설정되어 있지 않습니다.\n\n"
                        "`.streamlit/secrets.toml`에 `GEMINI_API_KEY` 항목을 추가하고 앱을 다시 실행해 주세요."
                    )
                else:
                    with st.spinner("🤖 AI가 NCS 기반 가이드를 작성 중이에요..."):
                        guide = ask_gemini(symptom, "", img, api_key, selected_unit, "guidance")
                    if (not guide) or guide.lstrip().startswith("❌"):
                        st.error(guide or "AI 응답을 받지 못했습니다.")
                    else:
                        st.session_state.latest_guidance = guide
                        st.session_state.latest_symptom = symptom
                        st.session_state.latest_image_b64 = make_thumbnail_b64(
                            img, max_b64_chars=THUMB_B64_LIMIT_MAIN
                        )
                        st.session_state.diag_step = "guidance"
                        st.rerun()

    elif diag_step == "guidance":
        _render_mission_steps_ui(selected_unit, api_key)

    elif diag_step == "evaluation":
        _render_evaluation_review(selected_unit, api_key)

    elif diag_step == "result":
        save_err = st.session_state.pop("_last_save_error", None)
        if save_err:
            st.error(
                "⚠ AI 평가는 정상적으로 완료되었지만, **수행평가 기록을 포트폴리오에 저장하지 못했습니다.**\n\n"
                f"오류 내용: `{save_err}`\n\n"
                "선생님께 위 오류를 알려주세요. (구글 시트 권한 또는 일시적 네트워크 문제일 수 있어요.)"
            )
        else:
            st.success("🎉 오늘의 수행평가가 **포트폴리오에 누적 기록**되었습니다!")
        # 결과 단계에서는 한줄 요약 + 카테고리 박스 + 종합 코멘트로 다시 한 번 보여준다.
        eval_text = st.session_state.get("latest_evaluation", "") or ""
        if eval_text:
            details = _parse_evaluation_details(eval_text)
            summary = (details.get("summary") or "").strip()
            if summary:
                st.markdown(_PORTFOLIO_CSS, unsafe_allow_html=True)
                st.markdown(
                    f"""
<div class="pf-summary">
  <span class="tag">AI 한줄 요약</span>
  <div class="text">{_esc_html(summary)}</div>
</div>""",
                    unsafe_allow_html=True,
                )
            cats = details.get("categories") or []
            if cats:
                st.markdown(_render_category_boxes_html(cats),
                            unsafe_allow_html=True)
            overall = (details.get("overall") or "").strip()
            if overall:
                st.markdown(
                    f"""
<div class="pf-overall">
  <div class="head">AI 종합 코멘트</div>
  {_esc_html(overall)}
</div>""",
                    unsafe_allow_html=True,
                )
        if st.button("🔄 새 진단 시작"):
            reset_diagnosis_flow()
            st.rerun()

_PORTFOLIO_CSS = """
<style>
/* ── 헤더(히어로) ── */
.pf-hero {
    background: linear-gradient(135deg,#1E3A8A 0%,#3B82F6 100%);
    color:#fff; padding:24px 28px; border-radius:18px; margin-bottom:20px;
    box-shadow:0 6px 18px rgba(30,58,138,0.22);
}
.pf-hero h2 { margin:0; font-size:30px; font-weight:800; letter-spacing:-0.3px; }
.pf-hero p { margin:8px 0 0 0; opacity:0.92; font-size:16px; }

.pf-stats {
    display:grid; grid-template-columns: repeat(4, 1fr);
    gap:14px; margin-top:20px;
}
.pf-stat {
    background:rgba(255,255,255,0.18); padding:18px 16px; border-radius:14px;
    backdrop-filter:blur(4px); position:relative;
    border:1px solid rgba(255,255,255,0.25);
}
.pf-stat b { font-size:34px; display:block; font-weight:800; line-height:1.1; }
.pf-stat .label { font-size:15px; opacity:0.95; margin-top:6px; display:block; }
.pf-stat .help-mark {
    position:absolute; top:8px; right:10px;
    width:22px; height:22px; border-radius:50%;
    background:rgba(255,255,255,0.30); color:#fff;
    font-size:13px; font-weight:700; line-height:22px; text-align:center;
    cursor:help; user-select:none;
}
.pf-stat .help-mark:hover { background:rgba(255,255,255,0.55); color:#1E3A8A; }

/* ── 선생님 피드백 카드 ── */
.pf-fb-card {
    background:#FFF7E6; border-left:8px solid #FA8C16;
    border-radius:12px; padding:16px 22px; margin:10px 0;
    box-shadow:0 1px 4px rgba(0,0,0,0.05);
}
.pf-fb-head { display:flex; justify-content:space-between; align-items:center;
    color:#92400E; font-weight:700; margin-bottom:8px; font-size:18px; }
.pf-fb-body {
    color:#3F2200; line-height:1.75; font-size:19px;
    white-space:pre-wrap; font-weight:500;
}
.pf-fb-empty {
    background:#F3F4F6; border:1px dashed #D1D5DB; color:#6B7280;
    padding:16px; border-radius:10px; text-align:center; font-size:16px;
}

/* ── 단원별 진척도 그리드 (6단원) ── */
.pf-units-grid {
    display:grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    gap:12px; margin:6px 0 8px 0;
}
.pf-unit-cell {
    position:relative;
    box-sizing:border-box; padding:14px 16px;
    border-radius:14px; text-align:center;
    font-weight:700; font-size:17px;
    box-shadow:0 1px 2px rgba(0,0,0,0.04);
    word-break:keep-all; line-height:1.4;
}
.pf-unit-cell .count {
    display:block; margin-top:6px; font-size:13px; font-weight:500; opacity:0.85;
}
.pf-unit-cell .badge {
    position:absolute; top:8px; right:10px;
    font-size:12px; font-weight:800; padding:3px 8px; border-radius:999px;
}
.pf-unit-done { background:#ECFDF5; color:#065F46; border:1.5px solid #6EE7B7; }
.pf-unit-done .badge { background:#065F46; color:#ECFDF5; }
.pf-unit-todo { background:#FEF2F2; color:#991B1B; border:1.5px solid #FCA5A5; }
.pf-unit-todo .badge { background:#9CA3AF; color:#FFFFFF; }

/* ── 실습 기록 카드 ── */
.pf-record {
    border:1px solid #E5E7EB; border-radius:14px;
    padding:14px 18px; margin-bottom:12px; background:#fff;
    box-shadow:0 1px 3px rgba(0,0,0,0.04);
}
.pf-rec-head { display:flex; justify-content:space-between; align-items:center; gap:10px; }
.pf-rec-title { font-size:22px; font-weight:800; color:#111827; letter-spacing:-0.2px; }
.pf-rec-date { font-size:15px; color:#4B5563; margin-top:2px; font-weight:500; }
.pf-chip { display:inline-block; padding:4px 12px; border-radius:999px;
    font-size:13px; font-weight:700; margin-left:4px; }
.pf-chip-fb { background:#DBEAFE; color:#1D4ED8; }
.pf-chip-wait { background:#F3F4F6; color:#6B7280; }
.pf-chip-chance { background:#FEF3C7; color:#92400E; }
.pf-score {
    display:inline-block; padding:5px 14px; border-radius:10px;
    font-weight:800; font-size:14px; color:#fff;
}

/* ── 한줄 요약: 매우 크고 눈에 띄게 ── */
.pf-summary {
    background:linear-gradient(135deg,#1E40AF 0%,#2563EB 60%,#3B82F6 100%);
    border-radius:14px; padding:18px 22px; margin:14px 0 12px 0;
    color:#FFFFFF; box-shadow:0 4px 14px rgba(30,64,175,0.25);
}
.pf-summary .tag {
    display:inline-block; background:rgba(255,255,255,0.22);
    padding:3px 10px; border-radius:999px;
    font-size:12px; font-weight:700; letter-spacing:0.5px;
}
.pf-summary .text {
    margin-top:8px; font-size:22px; font-weight:800; line-height:1.45;
    letter-spacing:-0.2px;
}
.pf-summary.muted {
    background:#F3F4F6; color:#6B7280; box-shadow:none;
}
.pf-summary.muted .tag { background:#E5E7EB; color:#4B5563; }
.pf-summary.muted .text { color:#6B7280; font-weight:600; }

/* ── 카테고리 4박스: 숫자 강조 + 통과/보완 + 한줄 코멘트 (글자 1.5배) ── */
.pf-cat-grid {
    display:grid; grid-template-columns: repeat(2, minmax(0, 1fr));
    gap:12px; margin:12px 0 8px 0;
}
.pf-cat-box {
    border-radius:14px; padding:16px 18px;
    background:#FFFFFF; border:1.5px solid #E5E7EB;
    display:flex; gap:16px; align-items:flex-start;
    box-shadow:0 1px 2px rgba(0,0,0,0.04);
}
.pf-cat-num {
    flex:0 0 auto;
    width:56px; height:56px; border-radius:12px;
    display:flex; align-items:center; justify-content:center;
    font-size:28px; font-weight:800; color:#FFFFFF;
}
.pf-cat-body { flex:1 1 auto; min-width:0; }
.pf-cat-name { font-size:21px; font-weight:800; color:#1F2937; }
.pf-cat-row {
    display:flex; align-items:center; gap:10px; margin-top:6px; flex-wrap:wrap;
}
.pf-cat-status {
    display:inline-block; padding:5px 14px; border-radius:999px;
    font-size:18px; font-weight:800;
}
.pf-cat-pass { background:#ECFDF5; color:#065F46; }
.pf-cat-warn { background:#FFFBEB; color:#92400E; }
.pf-cat-none { background:#F3F4F6; color:#6B7280; }
.pf-cat-desc {
    font-size:19px; color:#374151; font-weight:500;
    line-height:1.55; flex:1 1 0; min-width:0;
}
.pf-cat-lines { margin-top:8px; }
.pf-cat-line {
    font-size:19px; color:#1F2937; line-height:1.6;
    margin-top:6px; word-break:keep-all;
}
.pf-cat-line b {
    display:inline-block; min-width:94px; text-align:center;
    color:#1E3A8A; font-weight:800; font-size:16px;
    margin-right:10px; padding:3px 12px; border-radius:8px;
    background:#EFF6FF; letter-spacing:0.3px; vertical-align:middle;
}
.pf-cat-line.fact b { background:#E0F2FE; color:#075985; }
.pf-cat-line.ncs  b { background:#EDE9FE; color:#5B21B6; }
.pf-cat-line.imp  b { background:#FEF3C7; color:#92400E; }

/* ── 종합 코멘트 박스 — 카테고리(흰 박스)와 명확히 구분되는 어두운 톤 ── */
.pf-overall {
    background: linear-gradient(135deg,#0F172A 0%,#1E293B 50%,#334155 100%);
    border-radius:16px;
    border-left:8px solid #F59E0B;
    padding:14px 22px 16px 22px;
    margin:22px 0 10px 0;
    color:#F8FAFC;
    box-shadow:0 10px 24px rgba(15,23,42,0.32);
    font-size:23px;
    line-height:1.55;
    white-space:pre-wrap;
    word-break:keep-all;
}
.pf-overall .head {
    display:inline-block;
    background:linear-gradient(135deg,#F59E0B 0%,#FBBF24 100%);
    color:#1F2937;
    font-size:16px;
    font-weight:900;
    padding:4px 14px;
    border-radius:999px;
    letter-spacing:0.6px;
    margin-bottom:8px;
    box-shadow:0 2px 6px rgba(245,158,11,0.35);
    white-space:nowrap;
}

/* 카테고리 박스 좌측 강조선 색 — 통과는 초록, 보완은 주황, 미평가는 회색 */
.pf-cat-box.pass { border-left:5px solid #10B981; }
.pf-cat-box.warn { border-left:5px solid #F59E0B; }
.pf-cat-box.none { border-left:5px solid #D1D5DB; }
.pf-cat-box.pass .pf-cat-num { background:#10B981; }
.pf-cat-box.warn .pf-cat-num { background:#F59E0B; }
.pf-cat-box.none .pf-cat-num { background:#9CA3AF; }

/* ── 작은 정보 블록(증상/소감 등) — 검은 코드블록 대체 ── */
.pf-info {
    background:#F9FAFB; border:1px solid #E5E7EB; border-radius:10px;
    padding:10px 14px; margin:4px 0 8px 0;
    color:#1F2937; font-size:14px; line-height:1.65;
    white-space:pre-wrap; word-break:break-word;
}
.pf-info-label {
    font-size:12px; font-weight:700; color:#6B7280;
    letter-spacing:0.3px; margin-bottom:2px;
}
</style>
"""

def _render_unit_progress_section(records: list[dict]) -> None:
    """6개 NCS 단원 각각 한 번이라도 수행평가를 완료했는지 한눈에 보여준다."""
    st.markdown("### 단원별 진척도")
    unit_counts: dict[str, int] = {}
    for r in records:
        u = (r.get("unit") or "").strip()
        if u:
            unit_counts[u] = unit_counts.get(u, 0) + 1

    done = sum(1 for u in NCS_UNITS if u in unit_counts)
    total = len(NCS_UNITS)
    st.caption(f"전체 {total}단원 중 **{done}단원** 완료 ({done * 100 // total}%)")
    st.progress(done / total if total else 0.0)

    grid = '<div class="pf-units-grid">'
    for unit in NCS_UNITS:
        cnt = unit_counts.get(unit, 0)
        if cnt > 0:
            grid += (
                '<div class="pf-unit-cell pf-unit-done">'
                '<span class="badge">완료</span>'
                f'<div>{_esc_html(unit)}</div>'
                f'<span class="count">{cnt}회 수행</span>'
                '</div>'
            )
        else:
            grid += (
                '<div class="pf-unit-cell pf-unit-todo">'
                '<span class="badge">미완료</span>'
                f'<div>{_esc_html(unit)}</div>'
                '<span class="count">아직 수행 전</span>'
                '</div>'
            )
    grid += "</div>"
    st.markdown(grid, unsafe_allow_html=True)


def _render_teacher_feedback_section(records: list[dict]) -> None:
    st.markdown("### 선생님의 피드백")
    feedback_recs = [r for r in records if (r.get("teacher_feedback") or "").strip()]
    if not feedback_recs:
        st.markdown(
            '<div class="pf-fb-empty">아직 도착한 피드백이 없습니다. '
            '실습 기록을 보고 선생님께서 피드백을 남기시면 이곳에 가장 먼저 표시돼요.</div>',
            unsafe_allow_html=True,
        )
        return
    feedback_recs.sort(
        key=lambda r: (r.get("teacher_feedback_updated_at") or r.get("submitted_at") or ""),
        reverse=True,
    )
    st.caption(f"총 {len(feedback_recs)}건의 피드백이 도착했어요. (최신순)")
    for r in feedback_recs:
        unit = r.get("unit", "")
        when = (r.get("teacher_feedback_updated_at") or r.get("submitted_at") or "")[:16]
        fb = (r.get("teacher_feedback") or "").strip()
        st.markdown(
            f"""
<div class="pf-fb-card">
  <div class="pf-fb-head">
    <span>{unit}</span>
    <span style="font-weight:500;font-size:14px;color:#9A6B00;">{when}</span>
  </div>
  <div class="pf-fb-body">{fb}</div>
</div>""",
            unsafe_allow_html=True,
        )

def _render_achievement_charts(records: list[dict]) -> None:
    st.markdown("### 분야별 성취도")
    if go is None:
        st.caption("그래프를 표시하려면 `plotly` 패키지가 필요합니다.")
        return

    unit_rows = _aggregate_unit_scores(records)
    cat_avgs = _aggregate_category_scores(records)

    # 모든 차트에 공통 적용할 색상 설정 (흰 배경 위에서 진한 글자, 1.5배 확대)
    text_color = "#1F2937"
    title_color = "#1E3A8A"
    axis_color = "#374151"
    grid_color = "#E5E7EB"
    FONT_TITLE = 26    # 기존 18 × 1.5
    FONT_BODY  = 21    # 기존 14 × 1.5
    FONT_TICK  = 20    # 기존 13~14 × 1.5
    FONT_RADIAL = 18   # 기존 12 × 1.5

    col1, col2 = st.columns(2)
    with col1:
        if unit_rows:
            units = [u for u, _s, _n in unit_rows]
            scores = [s for _u, s, _n in unit_rows]
            counts = [n for _u, _s, n in unit_rows]
            colors = [_score_color(s) for s in scores]
            fig = go.Figure(data=[go.Bar(
                x=scores, y=units, orientation="h",
                marker_color=colors,
                text=[f"{s:.0f}점 · {n}회" for s, n in zip(scores, counts)],
                textposition="outside",
                textfont=dict(color=text_color, size=FONT_BODY,
                              family="Malgun Gothic, sans-serif"),
                hovertemplate="%{y}<br>평균 %{x:.1f}점<extra></extra>",
            )])
            fig.update_layout(
                title=dict(text="단원별 평균 성취도",
                           font=dict(color=title_color, size=FONT_TITLE,
                                     family="Malgun Gothic, sans-serif")),
                font=dict(color=text_color, size=FONT_BODY,
                          family="Malgun Gothic, sans-serif"),
                xaxis=dict(
                    range=[0, 120],
                    title=dict(text="평균 점수",
                               font=dict(color=axis_color, size=FONT_BODY)),
                    tickfont=dict(color=axis_color, size=FONT_TICK),
                    gridcolor=grid_color, zerolinecolor=grid_color,
                ),
                yaxis=dict(
                    autorange="reversed",
                    tickfont=dict(color=axis_color, size=FONT_TICK),
                    gridcolor=grid_color,
                ),
                height=460, margin=dict(l=10, r=40, t=70, b=40),
                plot_bgcolor="#FFFFFF", paper_bgcolor="#FFFFFF",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("아직 단원별 점수 데이터가 부족해요.")

    with col2:
        if any(cat_avgs.values()):
            labels = [lab for _ico, lab in _CATEGORY_LABELS]
            vals = [cat_avgs[lab] for _ico, lab in _CATEGORY_LABELS]
            fig2 = go.Figure(data=go.Scatterpolar(
                r=vals + [vals[0]],
                theta=labels + [labels[0]],
                fill="toself",
                line=dict(color="#1E40AF", width=2),
                fillcolor="rgba(59,130,246,0.35)",
                marker=dict(color="#1E40AF", size=10),
                hovertemplate="%{theta}<br>%{r:.0f}점<extra></extra>",
            ))
            fig2.update_layout(
                title=dict(text="NCS 카테고리별 평균",
                           font=dict(color=title_color, size=FONT_TITLE,
                                     family="Malgun Gothic, sans-serif")),
                font=dict(color=text_color, size=FONT_BODY,
                          family="Malgun Gothic, sans-serif"),
                polar=dict(
                    bgcolor="#FFFFFF",
                    radialaxis=dict(
                        visible=True, range=[0, 100],
                        tickfont=dict(color=axis_color, size=FONT_RADIAL),
                        gridcolor=grid_color, linecolor=grid_color,
                    ),
                    angularaxis=dict(
                        tickfont=dict(color=axis_color, size=FONT_TICK),
                        gridcolor=grid_color, linecolor=grid_color,
                    ),
                ),
                height=460, margin=dict(l=70, r=70, t=70, b=40),
                paper_bgcolor="#FFFFFF",
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("AI 평가 결과에 카테고리 정보가 누적되면 레이더 차트가 표시돼요.")

def _extract_evaluation_only(result_text: str) -> str:
    """`_compose_combined_result`로 합쳐진 텍스트에서 AI 실습 평가 부분만 추출."""
    if not result_text:
        return ""
    # 평가 마커 이후만 사용
    m = re.search(r"##\s*[^\n]*?(AI 실습 평가|실습 평가|평가)\s*\n+", result_text)
    if m:
        return result_text[m.end():].strip()
    return result_text.strip()

def _parse_evaluation_summary(evaluation_text: str) -> dict:
    """기존 호출자 호환용 — 새 통합 파서에서 summary 부분만 돌려준다."""
    details = _parse_evaluation_details(evaluation_text)
    return {"summary": details.get("summary", "")}

def _parse_step_photos_json(raw: str) -> list[tuple[int, str]]:
    """mission_step_photos_json을 [(step_num, b64), ...] 형태로 안전 파싱."""
    if not raw:
        return []
    try:
        import json as _json
        data = _json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    out: list[tuple[int, str]] = []
    for k, v in data.items():
        try:
            n = int(str(k).strip())
        except (ValueError, TypeError):
            continue
        if 1 <= n <= 4 and isinstance(v, str) and v.strip():
            out.append((n, v))
    return sorted(out, key=lambda x: x[0])


def _parse_ai_chance_steps(rec: dict) -> list[int]:
    """기록에서 AI 찬스 사용 단계 번호 목록을 안전하게 파싱."""
    raw = (rec.get("ai_chance_used_steps") or "").strip()
    if not raw:
        return []
    out: list[int] = []
    for tok in re.split(r"[,\s]+", raw):
        try:
            n = int(tok)
            if 1 <= n <= 4:
                out.append(n)
        except ValueError:
            continue
    return sorted(set(out))

def _esc_html(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_category_boxes_html(cats: list[dict]) -> str:
    """카테고리 박스(4개)의 HTML을 한 덩어리로 만들어 반환.
    각 박스에 사실/NCS 기준/보완 제안 3줄 분석이 있으면 함께 표시한다."""
    box_html = '<div class="pf-cat-grid">'
    for c in cats:
        status = c.get("status", "미평가")
        if status == "통과":
            cls = "pass"; status_cls = "pf-cat-pass"
        elif status == "보완":
            cls = "warn"; status_cls = "pf-cat-warn"
        else:
            cls = "none"; status_cls = "pf-cat-none"

        fact = c.get("fact") or ""
        ncs = c.get("ncs") or ""
        improve = c.get("improve") or ""
        desc = c.get("desc") or ""

        body_html = f'<div class="pf-cat-name">{_esc_html(c["name"])}</div>'
        body_html += (
            '<div class="pf-cat-row">'
            f'<span class="pf-cat-status {status_cls}">{_esc_html(status)}</span>'
            '</div>'
        )
        if fact or ncs or improve:
            body_html += '<div class="pf-cat-lines">'
            if fact:
                body_html += (
                    f'<div class="pf-cat-line fact"><b>사실</b>{_esc_html(fact)}</div>'
                )
            if ncs:
                body_html += (
                    f'<div class="pf-cat-line ncs"><b>NCS 기준</b>{_esc_html(ncs)}</div>'
                )
            if improve:
                body_html += (
                    f'<div class="pf-cat-line imp"><b>보완 제안</b>{_esc_html(improve)}</div>'
                )
            body_html += '</div>'
        elif desc:
            body_html += (
                f'<div class="pf-cat-lines">'
                f'<div class="pf-cat-line"><span class="pf-cat-desc">{_esc_html(desc)}</span></div>'
                f'</div>'
            )

        box_html += (
            f'<div class="pf-cat-box {cls}">'
            f'<div class="pf-cat-num">{_esc_html(c["num"])}</div>'
            f'<div class="pf-cat-body">{body_html}</div>'
            '</div>'
        )
    box_html += '</div>'
    return box_html


def _render_record_card(rec: dict) -> None:
    unit = rec.get("unit", "")
    date = (rec.get("submitted_at") or "")[:10]
    score = _safe_float(rec.get("ncs_score"))
    color = _score_color(score)
    band = _score_band(score)
    has_fb = bool((rec.get("teacher_feedback") or "").strip())
    fb_chip = ('<span class="pf-chip pf-chip-fb">피드백 도착</span>'
               if has_fb else
               '<span class="pf-chip pf-chip-wait">피드백 대기</span>')
    chance_steps = _parse_ai_chance_steps(rec)
    chance_chip = (
        f'<span class="pf-chip pf-chip-chance">AI 찬스 {len(chance_steps)}회</span>'
        if chance_steps else ""
    )

    with st.container():
        # ── 카드 헤더 (단원·날짜·점수·뱃지) ──
        st.markdown(
            f"""
<div class="pf-record">
  <div class="pf-rec-head">
    <div>
      <div class="pf-rec-title">{_esc_html(unit)}</div>
      <div class="pf-rec-date">{_esc_html(date)}</div>
    </div>
    <div style="text-align:right;">
      <div class="pf-score" style="background:{color};">{score:.0f}점 · {band}</div>
      <div style="margin-top:6px;">{fb_chip} {chance_chip}</div>
    </div>
  </div>
</div>""",
            unsafe_allow_html=True,
        )

        with st.expander("상세 보기"):
            # ── 1) 한줄 요약 (가장 크고 강조) ──
            eval_only = _extract_evaluation_only(rec.get("result", ""))
            details = _parse_evaluation_details(eval_only)
            summary = details.get("summary") or ""
            if summary:
                st.markdown(
                    f"""
<div class="pf-summary">
  <span class="tag">AI 한줄 요약</span>
  <div class="text">{_esc_html(summary)}</div>
</div>""",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    """
<div class="pf-summary muted">
  <span class="tag">AI 한줄 요약</span>
  <div class="text">요약을 추출하지 못했어요.</div>
</div>""",
                    unsafe_allow_html=True,
                )

            # ── 2) 카테고리 4박스 (숫자 1~4 + 통과/보완 + 사실/NCS/보완 분석) ──
            cats = details.get("categories") or []
            if cats:
                st.markdown(_render_category_boxes_html(cats),
                            unsafe_allow_html=True)

            # ── 2-1) 종합 코멘트 ──
            overall = (details.get("overall") or "").strip()
            if overall:
                st.markdown(
                    f"""
<div class="pf-overall">
  <div class="head">AI 종합 코멘트</div>
  {_esc_html(overall)}
</div>""",
                    unsafe_allow_html=True,
                )

            # ── 3) 선생님 피드백 ──
            if has_fb:
                st.markdown(
                    f"""
<div class="pf-fb-card">
  <div class="pf-fb-head"><span>선생님 피드백</span></div>
  <div class="pf-fb-body">{_esc_html((rec.get('teacher_feedback') or '').strip())}</div>
</div>""",
                    unsafe_allow_html=True,
                )

            # ── 4) AI 찬스 사용 안내 ──
            if chance_steps:
                steps_str = ", ".join(f"{n}단계" for n in chance_steps)
                st.markdown(
                    f"""
<div style="background:#FFFBEB;border-left:5px solid #D97706;border-radius:10px;
            padding:10px 14px;margin:8px 0;color:#78350F;font-weight:600;font-size:14px;">
  AI 찬스 사용 단계: <b>{steps_str}</b> · 총 {len(chance_steps)}회 (감점 적용)
</div>""",
                    unsafe_allow_html=True,
                )

            # ── 5) 내가 입력한 내용 / 사진 / 소감 (접힌 채로) ──
            with st.expander("내가 입력한 내용 보기", expanded=False):
                if rec.get("symptom"):
                    st.markdown('<div class="pf-info-label">수행 내용</div>',
                                unsafe_allow_html=True)
                    st.markdown(
                        f'<div class="pf-info">{_esc_html(rec.get("symptom",""))}</div>',
                        unsafe_allow_html=True,
                    )
                if rec.get("reasoning"):
                    st.markdown('<div class="pf-info-label">내가 작성한 진단</div>',
                                unsafe_allow_html=True)
                    st.markdown(
                        f'<div class="pf-info">{_esc_html(rec.get("reasoning",""))}</div>',
                        unsafe_allow_html=True,
                    )
                refl = (rec.get("reflection") or "").strip()
                if refl:
                    st.markdown('<div class="pf-info-label">나의 소감</div>',
                                unsafe_allow_html=True)
                    st.markdown(
                        f'<div class="pf-info">{_esc_html(refl)}</div>',
                        unsafe_allow_html=True,
                    )
                img_bytes = thumbnail_b64_to_bytes(rec.get("image_b64"))
                if img_bytes:
                    st.image(img_bytes, width=260)

def _render_final_portfolio_section(records: list[dict]) -> None:
    """학기말 최종 포트폴리오 다운로드 영역. 6개 단원을 모두 완료해야 활성화된다."""
    st.markdown("### 학기말 최종 포트폴리오")

    completed_units = {(r.get("unit") or "").strip() for r in records if r.get("unit")}
    required_units = list(NCS_UNITS)
    done_units = [u for u in required_units if u in completed_units]
    missing_units = [u for u in required_units if u not in completed_units]
    progress = len(done_units) / len(required_units) if required_units else 0.0

    if missing_units:
        missing_str = ", ".join(missing_units)
        st.warning(
            f"학기말 최종 포트폴리오는 **6개 단원 모두 최소 1개씩 수행평가를 완료**해야 생성할 수 있어요. "
            f"현재 **{len(done_units)} / {len(required_units)} 단원** 완료했습니다."
        )
        st.progress(progress, text=f"단원 완료율 {progress * 100:.0f}%")
        st.caption(f"남은 단원: {missing_str}")
        st.button(
            "학기말 최종 포트폴리오 생성 (PDF)",
            type="primary", disabled=True, use_container_width=True,
            help="6개 단원의 수행평가를 모두 완료해야 활성화됩니다.",
        )
        return

    st.success("6개 단원의 수행평가를 모두 완료했어요! 학기말 최종 포트폴리오를 생성할 수 있습니다.")
    if st.button("학기말 최종 포트폴리오 생성 (PDF)", type="primary", use_container_width=True):
        with st.spinner("PDF를 생성하고 있어요..."):
            pdf_bytes = build_comprehensive_portfolio_pdf(
                st.session_state.student_id, st.session_state.student_display_name, records
            )
        if pdf_bytes:
            st.session_state["_final_pdf_bytes"] = pdf_bytes
        else:
            st.info(
                "잠시 후 다시 시도해 주세요. 일부 기록에 PDF가 지원하지 않는 문자가 포함되어 있을 수 있어요. "
                "문제가 계속되면 선생님께 문의하세요."
            )
    pdf_cached = st.session_state.get("_final_pdf_bytes")
    if pdf_cached:
        st.download_button(
            "PDF 다운로드", data=pdf_cached,
            file_name=f"Final_Portfolio_{st.session_state.student_id}.pdf",
            mime="application/pdf", use_container_width=True,
        )

def _render_portfolio_view():
    st.markdown(_PORTFOLIO_CSS, unsafe_allow_html=True)

    records = st.session_state.get("my_history_records", []) or []

    # ── 헤더 (요약 카드) ─────────────────────────────────
    if records:
        avg_score = sum(_safe_float(r.get("ncs_score")) for r in records) / len(records)
        fb_count = sum(1 for r in records if (r.get("teacher_feedback") or "").strip())
        unit_count = len({(r.get("unit") or "").strip() for r in records if r.get("unit")})
    else:
        avg_score, fb_count, unit_count = 0.0, 0, 0

    name = st.session_state.get("student_display_name", "학생")
    st.markdown(
        f"""
<div class="pf-hero">
  <h2>{name} 학생의 성장 일지</h2>
  <p>그동안의 자동차 전기전자제어 실습 기록을 한눈에 확인해 보세요.</p>
  <div class="pf-stats">
    <div class="pf-stat" title="지금까지 완료한 수행평가(실습)의 총 횟수입니다.">
      <span class="help-mark" title="지금까지 완료한 수행평가(실습)의 총 횟수입니다.">?</span>
      <b>{len(records)}</b><span class="label">총 실습 건수</span>
    </div>
    <div class="pf-stat" title="모든 실습의 NCS 평가 점수를 평균낸 값입니다. 100점 만점.">
      <span class="help-mark" title="모든 실습의 NCS 평가 점수를 평균낸 값입니다. 100점 만점.">?</span>
      <b>{avg_score:.1f}</b><span class="label">평균 성취도</span>
    </div>
    <div class="pf-stat" title="6개 NCS 단원 중 한 번이라도 실습을 완료한 단원 수입니다.">
      <span class="help-mark" title="6개 NCS 단원 중 한 번이라도 실습을 완료한 단원 수입니다.">?</span>
      <b>{unit_count}</b><span class="label">참여 단원 수</span>
    </div>
    <div class="pf-stat" title="선생님이 내 실습 기록에 남겨주신 피드백의 누적 개수입니다.">
      <span class="help-mark" title="선생님이 내 실습 기록에 남겨주신 피드백의 누적 개수입니다.">?</span>
      <b>{fb_count}</b><span class="label">받은 피드백</span>
    </div>
  </div>
</div>""",
        unsafe_allow_html=True,
    )

    if not records:
        st.info("아직 누적된 기록이 없습니다. 첫 실습을 완료해 보세요!")
        return

    # ── ① 교사 피드백 (최상단) ─────────────────────────
    _render_teacher_feedback_section(records)

    st.markdown("")
    # ── ② 단원별 진척도 (6단원 완료 현황) ────────────
    _render_unit_progress_section(records)

    st.markdown("")
    # ── ③ 분야별 성취도 그래프 ────────────────────────
    _render_achievement_charts(records)

    st.markdown("---")
    # ── ④ 실습 기록 카드 목록 ─────────────────────────
    st.markdown("### 실습 기록")
    sort_opt = st.radio(
        "정렬", ["최신순", "성취도 높은 순", "단원별"],
        horizontal=True, label_visibility="collapsed", key="pf_sort",
    )
    if sort_opt == "성취도 높은 순":
        sorted_recs = sorted(records, key=lambda r: _safe_float(r.get("ncs_score")), reverse=True)
    elif sort_opt == "단원별":
        sorted_recs = sorted(records, key=lambda r: (r.get("unit", ""), r.get("submitted_at", "")), reverse=False)
    else:
        sorted_recs = sorted(records, key=lambda r: r.get("submitted_at", ""), reverse=True)

    for rec in sorted_recs:
        _render_record_card(rec)

    st.markdown("---")
    # ── ⑤ 최종 PDF 다운로드 (6개 단원 모두 완료 시에만 활성화) ─────
    _render_final_portfolio_section(records)

def render_student_mode():
    st.sidebar.title("메뉴")
    view = st.sidebar.radio("이동", ["🧑‍🏫 학습 모드", "📓 나의 포트폴리오"])

    api_key = st.secrets.get("GEMINI_API_KEY", "")

    if view == "🧑‍🏫 학습 모드":
        unit = st.selectbox("단원 선택", NCS_UNITS)
        _render_diagnosis_input_tab(unit, api_key)
    else:
        _render_portfolio_view()

# ───────────────────────────────────────────────────────────────────────────
# 교사 모드 (학생별 기록 보기 + 피드백 작성)
# ───────────────────────────────────────────────────────────────────────────
def _get_teacher_password() -> str:
    try:
        return str(st.secrets.get("TEACHER_PASSWORD") or TEACHER_PASSWORD_DEFAULT)
    except Exception:
        return TEACHER_PASSWORD_DEFAULT

def render_teacher_login() -> None:
    st.markdown("### 🧑‍🏫 교사 로그인")
    with st.form("teacher_login_form"):
        name = st.text_input(
            "이름",
            key="teacher_login_name",
            placeholder="예: 홍길동",
            help="대시보드 상단에 표시될 선생님 성함을 입력해 주세요.",
        )
        pw = st.text_input(
            "교사 비밀번호", type="password",
            key="teacher_login_pw",
            help="기본값은 0000이며, secrets의 TEACHER_PASSWORD로 변경 가능합니다.",
        )
        ok = st.form_submit_button("로그인", type="primary")
    if ok:
        if not (name or "").strip():
            st.error("이름을 입력해 주세요.")
            return
        if pw == _get_teacher_password():
            st.session_state["teacher_logged_in"] = True
            st.session_state["teacher_display_name"] = name.strip()
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")

def _render_teacher_record_detail(rec: dict, student_name: str) -> None:
    """교사 모드에서 학생 한 건의 수행평가 결과를 5개 섹션으로 명확하게 보여준다.
    섹션 구성:
      1. 학생 수행평가 총평 (AI 한줄 요약, 100자 내외)
      2. AI 수행평가 결과 분석 및 평가 (종합 코멘트 + 평가 원문)
      3. 학생의 수행평가 입력 내용 및 사진 (입력·메모·소감·사진)
      4. 카테고리별 성취 수준 (4박스: 통과/보완 + 사실/NCS/보완 제안)
      5. 선생님의 피드백 및 평가 (기존 피드백 표시 + 작성/수정 + 저장)
    """
    eval_only = _extract_evaluation_only(rec.get("result", ""))
    details = _parse_evaluation_details(eval_only)
    summary = (details.get("summary") or "").strip()
    overall = (details.get("overall") or "").strip()
    cats = details.get("categories") or []
    chance_steps = _parse_ai_chance_steps(rec)
    unit = rec.get("unit", "")
    when = (rec.get("submitted_at") or "")[:16]
    score = _safe_float(rec.get("ncs_score"))

    # 헤더 메타 (점수·찬스)
    chance_tag = f" · AI 찬스 {len(chance_steps)}회" if chance_steps else ""
    st.caption(f"단원: {unit}  ·  제출 {when}  ·  성취도 {score:.0f}점{chance_tag}")

    # ── 섹션 1: 학생의 수행평가 총평 ──────────────────────
    st.markdown(f"### 1. {student_name} 학생의 수행평가 총평")
    if summary:
        st.markdown(
            f"""
<div class="pf-summary">
  <span class="tag">AI 한줄 요약</span>
  <div class="text">{_esc_html(summary)}</div>
</div>""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
<div class="pf-summary muted">
  <span class="tag">AI 한줄 요약</span>
  <div class="text">한줄 요약을 추출하지 못했습니다.</div>
</div>""",
            unsafe_allow_html=True,
        )

    # ── 섹션 2: AI 수행평가 결과 분석 및 평가 ────────────
    st.markdown("### 2. AI 수행평가 결과 분석 및 평가")
    if overall:
        st.markdown(
            f"""
<div class="pf-overall">
  <div class="head">AI 종합 코멘트</div>
  {_esc_html(overall)}
</div>""",
            unsafe_allow_html=True,
        )
    else:
        st.caption("AI 종합 코멘트가 없습니다.")
    if chance_steps:
        steps_str = ", ".join(f"{n}단계" for n in chance_steps)
        st.markdown(
            f"""
<div style="background:#FFFBEB;border-left:5px solid #D97706;border-radius:10px;
            padding:10px 14px;margin:8px 0 4px 0;color:#78350F;font-weight:600;
            font-size:15px;">
  학생이 AI 찬스를 사용한 단계: <b>{steps_str}</b> · 총 {len(chance_steps)}회 (감점 적용됨)
</div>""",
            unsafe_allow_html=True,
        )
    with st.expander("AI 평가 원문 보기", expanded=False):
        result_text = (rec.get("result") or "").strip() or "(AI 평가 없음)"
        st.markdown(result_text)

    # ── 섹션 3: 학생의 수행평가 입력 내용 및 사진 ────────
    st.markdown("### 3. 학생의 수행평가 입력 내용 및 사진")
    sym = (rec.get("symptom") or "").strip()
    if sym:
        st.markdown(
            '<div class="pf-info-label">수행 내용 (대상 부품 · 현재 상태 · 학습 질문)</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="pf-info">{_esc_html(sym)}</div>',
            unsafe_allow_html=True,
        )
    main_img = thumbnail_b64_to_bytes(rec.get("image_b64"))
    if main_img:
        st.markdown('<div class="pf-info-label">메인 사진</div>',
                    unsafe_allow_html=True)
        st.image(main_img, width=320)

    reasoning = (rec.get("reasoning") or "").strip()
    if reasoning:
        st.markdown('<div class="pf-info-label">4단계 진행 메모</div>',
                    unsafe_allow_html=True)
        st.markdown(
            f'<div class="pf-info">{_esc_html(reasoning)}</div>',
            unsafe_allow_html=True,
        )

    step_photos = _parse_step_photos_json(rec.get("mission_step_photos_json", ""))
    if step_photos:
        st.markdown('<div class="pf-info-label">단계별 진행 사진</div>',
                    unsafe_allow_html=True)
        cols = st.columns(min(4, len(step_photos)))
        for idx, (n, b64) in enumerate(step_photos):
            img_bytes = thumbnail_b64_to_bytes(b64)
            if not img_bytes:
                continue
            with cols[idx % len(cols)]:
                st.image(img_bytes, caption=f"{n}단계",
                         use_container_width=True)

    refl = (rec.get("reflection") or "").strip()
    if refl:
        st.markdown('<div class="pf-info-label">학생 소감</div>',
                    unsafe_allow_html=True)
        st.markdown(
            f'<div class="pf-info">{_esc_html(refl)}</div>',
            unsafe_allow_html=True,
        )
    if not (sym or main_img or reasoning or step_photos or refl):
        st.caption("학생이 입력한 내용이 없습니다.")

    # ── 섹션 4: 카테고리별 성취 수준 ──────────────────────
    st.markdown("### 4. 카테고리별 성취 수준")
    if cats:
        st.markdown(_render_category_boxes_html(cats), unsafe_allow_html=True)
    else:
        st.caption("카테고리별 분석을 추출하지 못했습니다.")

    # ── 섹션 5: 선생님의 피드백 및 평가 ──────────────────
    st.markdown("### 5. 선생님의 피드백 및 평가")
    rid = (rec.get("record_id") or "").strip()
    current_fb = (rec.get("teacher_feedback") or "").strip()

    if current_fb:
        updated = (rec.get("teacher_feedback_updated_at") or "")[:16]
        st.markdown(
            f"""
<div class="pf-fb-card">
  <div class="pf-fb-head">
    <span>현재 등록된 피드백</span>
    <span style="font-weight:500;font-size:14px;color:#9A6B00;">{_esc_html(updated)}</span>
  </div>
  <div class="pf-fb-body">{_esc_html(current_fb)}</div>
</div>""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="pf-fb-empty">아직 피드백이 등록되지 않았어요. 아래 칸에 작성해 주세요.</div>',
            unsafe_allow_html=True,
        )

    if not rid:
        st.warning("이 기록은 record_id가 없어 피드백 저장이 불가합니다.")
        return

    new_fb = st.text_area(
        "피드백 작성 / 수정", value=current_fb, key=f"fb_{rid}", height=140,
        placeholder="예: 멀티미터 측정 절차를 정확히 따랐어요. 다음에는 접지 측정도 추가해 보세요.",
    )
    save_col, info_col = st.columns([1, 3])
    with save_col:
        if st.button("💾 피드백 저장", key=f"save_{rid}", type="primary"):
            try:
                shb.update_teacher_feedback_in_sheet(
                    rid, new_fb.strip(), now_kst_display()
                )
                shb.invalidate_all_sheet_caches()
                st.success("피드백이 저장되었습니다.")
                st.rerun()
            except Exception as e:
                logger.exception("피드백 저장 실패: %s", e)
                st.error(f"저장 실패: {e}")
    with info_col:
        updated_at = (rec.get("teacher_feedback_updated_at") or "").strip()
        if updated_at:
            st.caption(f"최근 저장: {updated_at}")


def _render_teacher_final_assessment(
    student_id: str,
    student_name: str,
    records: list[dict],
    api_key: str,
) -> None:
    """교사가 학생의 학기말 최종 포트폴리오를 확인하고
    (1) 최종 수행평가 점수, (2) 최종 총평, (3) AI 과목별세부특기사항 초안을 작성·저장한다.
    저장은 시트의 final_assessments (학생별 1행)에 upsert 된다."""
    completed_units = {(r.get("unit") or "").strip() for r in records if r.get("unit")}
    required_units = list(NCS_UNITS)
    done_units = [u for u in required_units if u in completed_units]
    missing_units = [u for u in required_units if u not in completed_units]
    portfolio_ready = len(missing_units) == 0

    avg_score = (
        sum(_safe_float(r.get("ncs_score")) for r in records) / len(records)
        if records else 0.0
    )
    fb_count = sum(1 for r in records if (r.get("teacher_feedback") or "").strip())

    # ── 상단 요약 ─────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("단원 완료", f"{len(done_units)} / {len(required_units)}")
    c2.metric("총 실습", f"{len(records)} 건")
    c3.metric("평균 성취도", f"{avg_score:.1f} 점")
    c4.metric("내가 남긴 피드백", f"{fb_count} 건")

    if portfolio_ready:
        st.success("학생이 6개 단원의 수행평가를 모두 완료했습니다. 최종 포트폴리오 확인이 가능합니다.")
    else:
        miss_str = ", ".join(missing_units)
        st.warning(
            f"아직 {len(missing_units)}개 단원의 수행평가가 남아 있어요: {miss_str}\n"
            "최종 포트폴리오 PDF는 6개 단원이 모두 완료된 뒤에 생성할 수 있어요. "
            "단, 최종 점수·총평·과목별세부특기사항 초안은 지금도 작성·저장할 수 있습니다."
        )

    # ── 최종 포트폴리오 PDF 미리보기/다운로드 ─────
    pdf_session_key = f"_teacher_pdf_{student_id}"
    if portfolio_ready:
        bcol1, bcol2 = st.columns([1, 1])
        with bcol1:
            if st.button("📄 최종 포트폴리오 PDF 생성하기",
                         key=f"build_pdf_{student_id}", use_container_width=True):
                with st.spinner("PDF를 생성하고 있어요..."):
                    pdf_bytes = build_comprehensive_portfolio_pdf(
                        student_id, student_name, records
                    )
                if pdf_bytes:
                    st.session_state[pdf_session_key] = pdf_bytes
                    st.success("PDF가 생성되었습니다. 오른쪽 버튼으로 다운로드하세요.")
                else:
                    st.info("PDF 생성에 실패했어요. 잠시 후 다시 시도하거나 학생 기록을 확인해 주세요.")
        with bcol2:
            cached_pdf = st.session_state.get(pdf_session_key)
            if cached_pdf:
                st.download_button(
                    "📥 PDF 다운로드", data=cached_pdf,
                    file_name=f"Final_Portfolio_{student_id}.pdf",
                    mime="application/pdf",
                    key=f"dl_pdf_{student_id}",
                    use_container_width=True,
                )
            else:
                st.caption("왼쪽 버튼으로 먼저 PDF를 생성하세요.")

    st.markdown("---")

    # ── 기존 저장본 로드 ──────────────────────────
    try:
        existing = shb.get_final_assessment(student_id) or {}
    except Exception as e:
        logger.warning("최종 평가 로드 실패(빈 값으로 폴백): %s", e)
        existing = {}

    # ── 교사 최종 점수 + 총평 ─────────────────────
    st.markdown("### 🎯 교사 최종 수행평가")
    try:
        init_score = float(existing.get("final_score") or 0.0)
    except (ValueError, TypeError):
        init_score = 0.0
    score_col, info_col = st.columns([1, 2])
    with score_col:
        final_score = st.number_input(
            "최종 수행평가 점수 (0~100)",
            min_value=0.0, max_value=100.0,
            value=init_score, step=0.5,
            key=f"final_score_{student_id}",
            help="학기말 최종 포트폴리오를 바탕으로 교사가 부여하는 점수입니다.",
        )
    with info_col:
        prev_updated = existing.get("updated_at") or ""
        prev_by = existing.get("updated_by") or ""
        if prev_updated:
            st.caption(f"마지막 저장: {prev_updated} · {prev_by or '(교사)'}")
        else:
            st.caption("아직 저장된 최종 평가가 없습니다.")

    final_comment = st.text_area(
        "최종 총평 (교사 작성)",
        value=existing.get("teacher_overall_comment") or "",
        key=f"final_comment_{student_id}",
        height=130,
        placeholder="학기말 최종 포트폴리오를 바탕으로 한 종합 코멘트를 작성하세요.",
    )

    st.markdown("")

    # ── AI 과목별세부특기사항 초안 ────────────────
    st.markdown("### 🧾 AI 과목별세부특기사항 초안")
    st.caption(
        "학기 전체 실습 기록을 AI가 분석해 학교생활기록부 '과목별세부특기사항'에 "
        "옮겨 적을 수 있는 서술형 초안을 생성합니다. 생성 후 자유롭게 수정해 저장하세요."
    )
    specialty_session_key = f"_specialty_text_{student_id}"
    if specialty_session_key not in st.session_state:
        st.session_state[specialty_session_key] = (
            existing.get("subject_specialty_notes") or ""
        )

    gcol1, gcol2 = st.columns([1, 2])
    with gcol1:
        if st.button("🤖 AI 초안 생성/재생성",
                     key=f"gen_specialty_{student_id}",
                     use_container_width=True):
            if not records:
                st.error("누적된 실습 기록이 없어 분석할 수 없습니다.")
            elif not api_key:
                st.error("Gemini API 키가 설정되어 있지 않습니다.")
            else:
                with st.spinner("AI가 학기 전체 기록을 분석해 초안을 작성 중..."):
                    draft = ask_gemini_for_specialty_notes(
                        student_name, records, api_key
                    )
                if (not draft) or draft.lstrip().startswith("❌"):
                    st.error(draft or "AI 응답을 받지 못했습니다.")
                else:
                    st.session_state[specialty_session_key] = draft
                    st.rerun()
    with gcol2:
        st.caption(
            "버튼을 누르면 누적된 모든 수행평가 기록(단원·점수·한줄 요약·카테고리 상태)을 "
            "AI가 종합해 초안을 만들어 아래 칸에 채워줍니다."
        )

    specialty_text = st.text_area(
        "과목별세부특기사항 (수정 가능)",
        value=st.session_state.get(specialty_session_key, ""),
        key=f"final_specialty_{student_id}",
        height=200,
        placeholder="AI 초안을 만든 뒤 필요한 부분을 직접 다듬어 저장하세요.",
    )

    st.markdown("---")

    # ── 저장 버튼 ─────────────────────────────────
    save_col, _ = st.columns([1, 3])
    with save_col:
        if st.button("💾 최종 평가 저장",
                     type="primary",
                     key=f"save_final_{student_id}",
                     use_container_width=True):
            updated_by = (
                (st.session_state.get("teacher_display_name") or "").strip()
                or "(교사)"
            )
            try:
                shb.upsert_final_assessment(
                    student_id=student_id,
                    student_name=student_name,
                    final_score=f"{float(final_score):.1f}",
                    teacher_overall_comment=final_comment.strip(),
                    subject_specialty_notes=specialty_text.strip(),
                    updated_at=now_kst_display(),
                    updated_by=updated_by,
                )
                st.session_state[specialty_session_key] = specialty_text.strip()
                st.success("최종 평가가 저장되었습니다.")
                st.rerun()
            except Exception as e:
                logger.exception("최종 평가 저장 실패: %s", e)
                st.error(f"저장 실패: {e}")


def render_teacher_mode() -> None:
    teacher_name = (st.session_state.get("teacher_display_name") or "").strip()
    header_suffix = f" — {teacher_name} 선생님" if teacher_name else ""
    st.header(f"🧑‍🏫 교사 대시보드{header_suffix}")
    st.caption("학생들의 실습 기록을 확인하고 피드백을 남길 수 있습니다.")
    # 학생 포트폴리오와 같은 박스/요약/피드백 스타일을 그대로 사용
    st.markdown(_PORTFOLIO_CSS, unsafe_allow_html=True)

    try:
        df = shb.force_refresh_history()
    except Exception as e:
        logger.exception("history 시트 로드 실패: %s", e)
        st.error("학습 기록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.")
        return

    if df is None or df.empty:
        st.info("아직 누적된 학생 실습 기록이 없습니다.")
        return

    records = shb.history_df_to_records(df)

    # 학생 목록 구성
    students: dict[str, str] = {}
    for r in records:
        sid = (r.get("student_id") or "").strip()
        if not sid:
            continue
        name = (r.get("student_display_name") or "").strip()
        students[sid] = name or students.get(sid, "")

    if not students:
        st.info("학생 정보가 포함된 기록을 찾지 못했습니다.")
        return

    # 통계 요약
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("등록된 학생 수", f"{len(students)} 명")
    col2.metric("총 실습 기록", f"{len(records)} 건")
    fb_done = sum(1 for r in records if (r.get("teacher_feedback") or "").strip())
    col3.metric("피드백 완료", f"{fb_done} / {len(records)} 건")
    chance_total = sum(len(_parse_ai_chance_steps(r)) for r in records)
    chance_records = sum(1 for r in records if _parse_ai_chance_steps(r))
    col4.metric("AI 찬스 사용", f"{chance_records} 건 / {chance_total} 회")

    st.markdown("---")

    options = sorted(students.keys())
    labels = {sid: f"{students[sid] or '(이름 미상)'}  ·  {sid}" for sid in options}
    sel_sid = st.selectbox(
        "학생 선택", options=options,
        format_func=lambda s: labels.get(s, s),
    )
    if not sel_sid:
        return

    student_records = [r for r in records if (r.get("student_id") or "").strip() == sel_sid]
    student_records.sort(key=lambda r: r.get("submitted_at", ""), reverse=True)
    st.markdown(f"#### 📒 {students[sel_sid] or '(이름 미상)'} 학생의 실습 기록 ({len(student_records)}건)")

    student_name = students[sel_sid] or "(이름 미상)"
    for rec in student_records:
        unit = rec.get("unit", "")
        icon = UNIT_ICONS.get(unit, "📘")
        when = (rec.get("submitted_at") or "")[:16]
        score = _safe_float(rec.get("ncs_score"))
        has_fb = bool((rec.get("teacher_feedback") or "").strip())
        chance_steps = _parse_ai_chance_steps(rec)
        chance_tag = f" · 🆘 AI 찬스 {len(chance_steps)}회" if chance_steps else ""
        title = (
            f"{icon} {unit} · {when} · {score:.0f}점"
            f" {'✅ 피드백 완료' if has_fb else '⏳ 피드백 필요'}{chance_tag}"
        )
        with st.expander(title, expanded=False):
            _render_teacher_record_detail(rec, student_name)

    # ── 학기말 최종 포트폴리오 + 교사 최종 평가 + AI 과목별세부특기사항 ──
    st.markdown("---")
    st.markdown(f"## 🎓 {student_name} 학생의 학기말 최종 포트폴리오")
    api_key_teacher = st.secrets.get("GEMINI_API_KEY", "")
    _render_teacher_final_assessment(
        sel_sid, student_name, student_records, api_key_teacher
    )

# ───────────────────────────────────────────────────────────────────────────
# 랜딩(역할 선택) 페이지
# ───────────────────────────────────────────────────────────────────────────
def render_landing() -> None:
    st.markdown(
        """
<style>
.landing-wrap { max-width: 1000px; margin: 1.2rem auto 0 auto; text-align: center; }
.landing-hero { padding: 18px 0 10px 0; }
.landing-title {
    font-size: 2.8rem; font-weight: 800; color: #1e3a8a; margin: 0;
    letter-spacing: -0.5px;
}
.landing-sub { color:#475569; font-size: 1.15rem; margin: 10px 0 0 0; }
.landing-hint { color:#64748b; font-size: 1.05rem; margin: 24px 0 18px 0; line-height: 1.6; }

.mode-cards {
    display: flex; gap: 32px; justify-content: center; margin: 28px auto 0 auto;
    max-width: 920px; flex-wrap: wrap;
}
.mode-card {
    flex: 1 1 380px; min-width: 320px; max-width: 440px;
    padding: 56px 32px;
    border-radius: 24px; text-decoration: none !important;
    box-shadow: 0 10px 28px rgba(15,23,42,0.12);
    border: 3px solid transparent;
    display: block; text-align: center;
    transition: transform .22s ease, box-shadow .22s ease, filter .22s ease;
    cursor: pointer;
}
.mode-card:hover {
    transform: translateY(-6px) scale(1.02);
    box-shadow: 0 22px 44px rgba(15,23,42,0.20);
    filter: brightness(1.04);
}
.mode-card:active { transform: translateY(-2px) scale(1.01); }

.mode-card-teacher {
    background: linear-gradient(160deg,#fffde7 0%,#fff59d 40%,#fdd835 100%);
    border-color: #f9a825; color: #3e2723 !important;
}
.mode-card-student {
    background: linear-gradient(160deg,#e3f2fd 0%,#90caf9 45%,#42a5f5 100%);
    border-color: #1565c0; color: #0d47a1 !important;
}

.mode-card-icon { font-size: 4.5rem; line-height: 1; display: block; margin-bottom: 14px; }
.mode-card-label { font-size: 2.0rem; font-weight: 800; display: block; margin-bottom: 12px; }
.mode-card-desc { font-size: 1.1rem; opacity: 0.95; line-height: 1.55; display: block; }

.landing-foot { color:#94a3b8; font-size: 0.9rem; margin-top: 36px; }
</style>
<div class="landing-wrap">
  <div class="landing-hero">
    <h1 class="landing-title">🚗 자동차 고장진단 AI tutor</h1>
    <p class="landing-sub">자동차 전기전자제어 · NCS 수행준거 기반 학습 도우미</p>
  </div>
  <p class="landing-hint">
    아래 카드를 클릭해 역할을 선택해 주세요.<br/>
    선택한 역할은 세션 동안 유지되며, 사이드바에서 언제든 다시 바꿀 수 있어요.
  </p>

  <div class="mode-cards">
    <a class="mode-card mode-card-teacher" href="?role=teacher" target="_self">
      <span class="mode-card-icon">🧑‍🏫</span>
      <span class="mode-card-label">교사 모드</span>
      <span class="mode-card-desc">학생 실습 기록 확인<br/>· 피드백 작성 ·</span>
    </a>
    <a class="mode-card mode-card-student" href="?role=student" target="_self">
      <span class="mode-card-icon">🧑‍🎓</span>
      <span class="mode-card-label">학생 모드</span>
      <span class="mode-card-desc">고장진단 실습 진행<br/>· 포트폴리오 작성 ·</span>
    </a>
  </div>

  <p class="landing-foot">NCS 수행준거 기반 · 소크라테스식 AI 학습 지원</p>
</div>
""",
        unsafe_allow_html=True,
    )

# ───────────────────────────────────────────────────────────────────────────
# 메인 진입점
# ───────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="자동차 고장진단 AI tutor", page_icon="🚗", layout="wide")

# ── 전역 글자 크기 1.5배 업스케일 (사이드바 포함) ─────────────────
st.markdown(
    """
<style>
/* 본문 기본 폰트 1.5배 */
html, body, [class*="st-emotion"], .stApp { font-size: 1.5rem !important; line-height: 1.65; }

/* 헤딩 비례 확대 */
.stApp h1 { font-size: 2.8rem !important; }
.stApp h2 { font-size: 2.25rem !important; }
.stApp h3 { font-size: 1.85rem !important; }
.stApp h4 { font-size: 1.55rem !important; }
.stApp h5 { font-size: 1.35rem !important; }
.stApp h6 { font-size: 1.2rem !important; }

/* 본문 단락·리스트 */
.stMarkdown p, .stMarkdown li, .stMarkdown span,
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] span { font-size: 1.15rem !important; line-height: 1.7; }

/* 입력 위젯 (텍스트·텍스트영역·셀렉트·숫자·날짜) */
.stTextInput input, .stTextArea textarea,
.stSelectbox div[data-baseweb="select"] *,
.stNumberInput input, .stDateInput input,
.stMultiSelect div[data-baseweb="select"] * { font-size: 1.15rem !important; }

/* 위젯 라벨 */
.stTextInput label, .stTextArea label, .stSelectbox label,
.stNumberInput label, .stDateInput label, .stMultiSelect label,
.stRadio label, .stCheckbox label, .stFileUploader label,
.stSlider label, .stColorPicker label
{ font-size: 1.2rem !important; font-weight: 600 !important; }

/* 라디오 옵션 라벨 */
.stRadio div[role="radiogroup"] label p { font-size: 1.15rem !important; }

/* 버튼 */
.stButton button, .stDownloadButton button, .stFormSubmitButton button,
.stLinkButton button { font-size: 1.2rem !important; font-weight: 600; padding: 0.6rem 1.1rem !important; }

/* 탭 */
.stTabs [data-baseweb="tab"] { font-size: 1.25rem !important; font-weight: 600; }

/* 메트릭/캡션/얼럿 */
[data-testid="stMetricValue"] { font-size: 2.4rem !important; }
[data-testid="stMetricLabel"] { font-size: 1.15rem !important; }
[data-testid="stMetricDelta"] { font-size: 1.05rem !important; }
[data-testid="stCaptionContainer"], .stCaption, small { font-size: 1.0rem !important; }
[data-testid="stAlert"] p, [data-testid="stAlert"] div { font-size: 1.15rem !important; }
[data-testid="stExpander"] summary p { font-size: 1.2rem !important; font-weight: 600; }
[data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li { font-size: 1.15rem !important; }
.stDataFrame, .stTable { font-size: 1.05rem !important; }
.stCode, pre, code { font-size: 1.05rem !important; }

/* 사이드바 너비 확대 (메뉴 항목이 한 줄에 표시되도록) */
section[data-testid="stSidebar"] { width: 340px !important; min-width: 340px !important; }
section[data-testid="stSidebar"] > div:first-child { width: 340px !important; min-width: 340px !important; }
section[data-testid="stSidebar"] .stButton button,
section[data-testid="stSidebar"] .stRadio div[role="radiogroup"] label {
    white-space: nowrap !important;
}

/* 사이드바 전체 1.5배 확대 */
section[data-testid="stSidebar"] * { font-size: 1.15rem !important; }
section[data-testid="stSidebar"] h1 { font-size: 1.9rem !important; }
section[data-testid="stSidebar"] h2 { font-size: 1.6rem !important; }
section[data-testid="stSidebar"] h3 { font-size: 1.4rem !important; }
section[data-testid="stSidebar"] h4 { font-size: 1.25rem !important; }
section[data-testid="stSidebar"] .stRadio label,
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stTextInput label,
section[data-testid="stSidebar"] .stButton button { font-size: 1.2rem !important; }
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
section[data-testid="stSidebar"] small { font-size: 1.0rem !important; }

/* 진행 바 두께도 비례 확대 */
.stProgress > div > div > div { height: 18px !important; }
</style>
""",
    unsafe_allow_html=True,
)

shb.gsheets_available()

# 세션 상태 초기화
if "app_role" not in st.session_state:
    st.session_state["app_role"] = None
if "student_logged_in" not in st.session_state:
    st.session_state["student_logged_in"] = False
if "teacher_logged_in" not in st.session_state:
    st.session_state["teacher_logged_in"] = False

# 랜딩 카드 클릭(?role=teacher / ?role=student) → 세션에 반영 후 쿼리 정리
try:
    _qp_role = st.query_params.get("role")
    if _qp_role in ("teacher", "student") and st.session_state["app_role"] is None:
        st.session_state["app_role"] = _qp_role
        try:
            del st.query_params["role"]
        except Exception:
            pass
        st.rerun()
except Exception:
    pass

# ── 역할 미선택 → 랜딩 페이지 표시 ──────────────────────
if st.session_state["app_role"] is None:
    render_landing()
    st.stop()

# ── 사이드바: 현재 역할 표시 + 역할 재선택 ────────────
with st.sidebar:
    role = st.session_state["app_role"]
    role_label = "🧑‍🏫 교사" if role == "teacher" else "🧑‍🎓 학생"
    st.markdown(f"### {role_label} 모드")
    if st.button("🔄 역할 다시 선택", use_container_width=True):
        st.session_state["app_role"] = None
        st.session_state["teacher_logged_in"] = False
        st.session_state["teacher_display_name"] = ""
        reset_student_session_soft()
        st.rerun()
    st.markdown("---")

# ── 역할에 따른 화면 분기 ─────────────────────────────
if st.session_state["app_role"] == "teacher":
    if not st.session_state.get("teacher_logged_in"):
        render_teacher_login()
    else:
        render_teacher_mode()
else:
    if not st.session_state.get("student_logged_in"):
        st.markdown("### 🧑‍🎓 학생 로그인")
        with st.form("login"):
            sid = st.text_input("학번")
            name = st.text_input("이름")
            pw = st.text_input("비밀번호", type="password")
            if st.form_submit_button("로그인", type="primary"):
                st.session_state.student_id = sid
                st.session_state.student_display_name = name
                st.session_state.student_logged_in = True
                st.session_state["my_history_records"] = shb.filter_history_records_by_student(sid)
                st.rerun()
    else:
        render_student_mode()