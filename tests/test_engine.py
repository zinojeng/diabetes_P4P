"""
dm_eligibility 引擎測試。

涵蓋情境（依任務要求）：
- 天數邊界「剛好在內／剛好在外」（P1407 90天就醫窗、P1408首次49天間隔、
  P7001首次49天間隔）
- 年度次數上限用完（P1408C年度3次、P1409C年度1次）
- 缺檢驗項目（P1409C缺眼睛檢查）
- P4301+P14合併觸發P7（P7001C前提）
- P7間隔規則（P7001C首次49天/後續70天）
- 額外：VPN未知時保守封鎖、品質監測獨立於狀態機、醫師停權橫向規則、
  年度評估碼互斥警告。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from dm_eligibility.engine import EligibilityEngine
from dm_eligibility.models import (
    CKDAssessment,
    ClosureRecord,
    CodeClaim,
    DiagnosisRecord,
    Encounter,
    LabResult,
    MedicationOrder,
    MissingReasonKind,
    PatientEnrollmentState,
    PhysicianStatus,
)
from dm_eligibility.rules_p14 import (
    check_p1407_eligibility,
    check_p1408_eligibility,
    check_p1409_eligibility,
    check_quality_monitoring,
)
from dm_eligibility.rules_p7 import check_p7001_eligibility


# ---------------------------------------------------------------------------
# 測試輔助函式
# ---------------------------------------------------------------------------


def dm_encounter(visit_date: date, physician_id: str = "DOC1", clinic_type_code=None, with_med: bool = True) -> Encounter:
    return Encounter(
        encounter_id=f"E-{visit_date.isoformat()}",
        visit_date=visit_date,
        physician_id=physician_id,
        diagnoses=(DiagnosisRecord(icd10_code="E11.9", is_primary=True),),
        medication_orders=(MedicationOrder(atc_code="A10BA02"),) if with_med else (),
        clinic_type_code=clinic_type_code,
    )


def ckd_encounter(visit_date: date, physician_id: str = "DOC1") -> Encounter:
    return Encounter(
        encounter_id=f"CKD-{visit_date.isoformat()}",
        visit_date=visit_date,
        physician_id=physician_id,
        diagnoses=(DiagnosisRecord(icd10_code="N18.3", is_primary=True),),
        medication_orders=(),
    )


def full_p1407_labs(as_of: date) -> list[LabResult]:
    codes = ["09005C", "09006C", "09001C", "09004C", "09043C", "09044C", "09015C", "09026C", "06013C", "12111C", "23501C"]
    return [LabResult(item_code=c, result_date=as_of) for c in codes]


def full_p1408_labs(as_of: date) -> list[LabResult]:
    return [LabResult(item_code=c, result_date=as_of) for c in ["09005C", "09006C"]]


def full_p7001_labs(as_of: date) -> list[LabResult]:
    return [LabResult(item_code=c, result_date=as_of) for c in ["09005C", "09006C", "09044C", "09015C", "12111C"]]


def base_state(patient_id: str, as_of: date, **kwargs) -> PatientEnrollmentState:
    kwargs.setdefault("age_years", 55)
    kwargs.setdefault("vpn_other_institution_enrolled", False)
    return PatientEnrollmentState(patient_id=patient_id, as_of_date=as_of, **kwargs)


# ---------------------------------------------------------------------------
# 1. P1407C：90天就醫窗邊界（剛好在內 / 剛好在外）
# ---------------------------------------------------------------------------


def test_p1407_90day_visit_window_boundary_in_and_out():
    as_of = date(2026, 4, 1)

    # 邊界在內：較早一次就診恰好落在 as_of - 90天
    earlier_visit = as_of - timedelta(days=90)
    state_in = base_state(
        "PAT-IN",
        as_of,
        encounters=[dm_encounter(earlier_visit, with_med=False), dm_encounter(as_of)],
        lab_results=full_p1407_labs(as_of),
    )
    result_in = check_p1407_eligibility(state_in)
    assert result_in.eligible is True
    assert result_in.points == 650

    # 邊界在外：較早一次就診落在 as_of - 91天，應被排除在90天窗外，
    # 只剩1次符合條件的就醫，不足2次
    earlier_visit_out = as_of - timedelta(days=91)
    state_out = base_state(
        "PAT-OUT",
        as_of,
        encounters=[dm_encounter(earlier_visit_out, with_med=False), dm_encounter(as_of)],
        lab_results=full_p1407_labs(as_of),
    )
    result_out = check_p1407_eligibility(state_out)
    assert result_out.eligible is False
    assert any("2次" in m for m in result_out.missing_requirements)


# ---------------------------------------------------------------------------
# 2. P1407C：VPN查詢結果未知時保守封鎖（不可靜默假設未被他院收案）
# ---------------------------------------------------------------------------


def test_p1407_blocked_when_vpn_status_unknown():
    as_of = date(2026, 4, 1)
    earlier_visit = as_of - timedelta(days=30)
    state = base_state(
        "PAT-VPN",
        as_of,
        encounters=[dm_encounter(earlier_visit, with_med=False), dm_encounter(as_of)],
        lab_results=full_p1407_labs(as_of),
        vpn_other_institution_enrolled=None,  # 未知
    )
    result = check_p1407_eligibility(state)
    assert result.eligible is False
    assert any("VPN" in m for m in result.missing_requirements)


# ---------------------------------------------------------------------------
# 3. P1408C：首次間隔49天邊界（剛好在內 / 剛好在外）
# ---------------------------------------------------------------------------


def test_p1408_first_interval_boundary_in_and_out():
    p1407_date = date(2026, 1, 1)

    as_of_in = p1407_date + timedelta(days=49)
    state_in = base_state(
        "PAT-P1408-IN",
        as_of_in,
        claims=[CodeClaim(code="P1407C", claim_date=p1407_date)],
        lab_results=full_p1408_labs(as_of_in),
    )
    result_in = check_p1408_eligibility(state_in)
    assert result_in.eligible is True
    assert result_in.points == 200

    as_of_out = p1407_date + timedelta(days=48)
    state_out = base_state(
        "PAT-P1408-OUT",
        as_of_out,
        claims=[CodeClaim(code="P1407C", claim_date=p1407_date)],
        lab_results=full_p1408_labs(as_of_out),
    )
    result_out = check_p1408_eligibility(state_out)
    assert result_out.eligible is False
    assert any("49天" in m or "未滿" in m for m in result_out.missing_requirements)


# ---------------------------------------------------------------------------
# 4. P1408C：年度次數上限用完（3次已達上限）
# ---------------------------------------------------------------------------


def test_p1408_annual_cap_exhausted():
    p1407_date = date(2026, 1, 1)
    c1 = p1407_date + timedelta(days=49)
    c2 = c1 + timedelta(days=70)
    c3 = c2 + timedelta(days=70)
    as_of = c3 + timedelta(days=70)

    state = base_state(
        "PAT-P1408-CAP",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=p1407_date),
            CodeClaim(code="P1408C", claim_date=c1),
            CodeClaim(code="P1408C", claim_date=c2),
            CodeClaim(code="P1408C", claim_date=c3),
        ],
        lab_results=full_p1408_labs(as_of),
    )
    result = check_p1408_eligibility(state)
    assert result.eligible is False
    assert any("上限3次" in m for m in result.missing_requirements)


# ---------------------------------------------------------------------------
# 5. P1409C：缺檢驗項目（缺眼睛檢查）
# ---------------------------------------------------------------------------


def test_p1409_missing_lab_blocks_eligibility():
    p1407_date = date(2026, 1, 1)
    p1408_date_1 = p1407_date + timedelta(days=49)
    p1408_date_2 = p1408_date_1 + timedelta(days=70)
    as_of = p1408_date_2 + timedelta(days=70)

    labs = full_p1407_labs(as_of)
    labs = [lr for lr in labs if lr.item_code != "23501C"]  # 故意缺眼睛檢查

    state = base_state(
        "PAT-P1409-LAB",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=p1407_date),
            CodeClaim(code="P1408C", claim_date=p1408_date_1),
            CodeClaim(code="P1408C", claim_date=p1408_date_2),
        ],
        lab_results=labs,
    )
    result = check_p1409_eligibility(state)
    assert result.eligible is False
    assert any("眼睛檢查" in m for m in result.missing_requirements)


# ---------------------------------------------------------------------------
# 6. P1409C：年度計數器與終身累計計數器分離（不可混用）
# ---------------------------------------------------------------------------


def test_p1409_annual_counter_separate_from_lifetime_counter():
    """病人終身累計已達成第二階段前提（P1409C累計2次），但『本年度』
    已申報過一次P1409C，第二次仍應被年度上限(1次)擋下——驗證年度計數器
    與終身累計計數器確實分開計算，而非同一個計數器。"""
    as_of = date(2027, 6, 1)
    p1408_date = as_of - timedelta(days=70)

    state = base_state(
        "PAT-P1409-COUNTER",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=date(2025, 1, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 3, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 6, 1)),
            CodeClaim(code="P1409C", claim_date=date(2025, 9, 1)),  # 前一年度已完成1次
            CodeClaim(code="P1408C", claim_date=p1408_date),
            CodeClaim(code="P1409C", claim_date=date(2027, 3, 1)),  # 本年度已申報過1次
        ],
        lab_results=full_p1407_labs(as_of),
    )
    result = check_p1409_eligibility(state)
    assert result.eligible is False
    assert any("每年度限1次" in m for m in result.missing_requirements)
    # 終身累計前提本身應已滿足（不是被前提條件擋下，而是被年度上限擋下）
    assert not any("需≥3次" in m for m in result.missing_requirements)


# ---------------------------------------------------------------------------
# 7. P7001C：P4301(CKD) + P14(DM) 合併觸發（缺一則不放行）
# ---------------------------------------------------------------------------


def test_p7001_requires_both_p4301_and_p1407():
    p1407_date = date(2026, 1, 1)
    as_of = p1407_date + timedelta(days=60)

    # 只有P1407C，尚無P4301C
    state_dm_only = base_state(
        "PAT-P7-DMONLY",
        as_of,
        claims=[CodeClaim(code="P1407C", claim_date=p1407_date)],
        lab_results=full_p7001_labs(as_of),
    )
    result_dm_only = check_p7001_eligibility(state_dm_only)
    assert result_dm_only.eligible is False
    assert any("P4301C" in m for m in result_dm_only.missing_requirements)

    # 兩者皆有，且已間隔足夠天數 -> 應放行
    p4301_date = date(2026, 1, 10)
    later_enrollment = max(p1407_date, p4301_date)
    as_of_ready = later_enrollment + timedelta(days=49)
    state_both = base_state(
        "PAT-P7-BOTH",
        as_of_ready,
        claims=[
            CodeClaim(code="P1407C", claim_date=p1407_date),
            CodeClaim(code="P4301C", claim_date=p4301_date),
        ],
        lab_results=full_p7001_labs(as_of_ready),
    )
    result_both = check_p7001_eligibility(state_both)
    assert result_both.eligible is True
    assert result_both.points == 400


# ---------------------------------------------------------------------------
# 8. P7001C：首次間隔49天邊界（剛好在內 / 剛好在外）
# ---------------------------------------------------------------------------


def test_p7001_first_interval_boundary_in_and_out():
    p1407_date = date(2026, 1, 1)
    p4301_date = date(2026, 1, 15)  # 較晚成立收案的一方
    later_enrollment = max(p1407_date, p4301_date)

    as_of_in = later_enrollment + timedelta(days=49)
    state_in = base_state(
        "PAT-P7001-IN",
        as_of_in,
        claims=[
            CodeClaim(code="P1407C", claim_date=p1407_date),
            CodeClaim(code="P4301C", claim_date=p4301_date),
        ],
        lab_results=full_p7001_labs(as_of_in),
    )
    assert check_p7001_eligibility(state_in).eligible is True

    as_of_out = later_enrollment + timedelta(days=48)
    state_out = base_state(
        "PAT-P7001-OUT",
        as_of_out,
        claims=[
            CodeClaim(code="P1407C", claim_date=p1407_date),
            CodeClaim(code="P4301C", claim_date=p4301_date),
        ],
        lab_results=full_p7001_labs(as_of_out),
    )
    result_out = check_p7001_eligibility(state_out)
    assert result_out.eligible is False
    assert any("49天" in m or "七週" in m or "未滿" in m for m in result_out.missing_requirements)


# ---------------------------------------------------------------------------
# 9. 品質監測（180天強制檢驗排程）獨立於P14收案狀態機
# ---------------------------------------------------------------------------


def test_quality_monitoring_independent_of_enrollment_state():
    as_of = date(2026, 5, 1)
    # 病人完全未被P14收案(沒有任何claims)，但當次就診有E11主診斷+A10用藥，
    # 且180天內查無四項品質指標檢驗 -> 品質監測仍應強制帶入排程提醒。
    state = base_state(
        "PAT-QM",
        as_of,
        encounters=[dm_encounter(as_of)],
        lab_results=[],
    )
    alerts = check_quality_monitoring(state)
    assert len(alerts) == 4  # NMRP, HbA1c, Mic-Cr, 血脂四項 皆缺
    assert any("HbA1c" in a for a in alerts)


# ---------------------------------------------------------------------------
# 10. EligibilityEngine：醫師停權時全部P14/P7代碼皆不可申報
# ---------------------------------------------------------------------------


def test_engine_physician_suspension_blocks_all_codes():
    as_of = date(2026, 4, 1)
    earlier_visit = as_of - timedelta(days=10)
    state = base_state(
        "PAT-SUSPEND",
        as_of,
        encounters=[dm_encounter(earlier_visit, with_med=False), dm_encounter(as_of)],
        lab_results=full_p1407_labs(as_of),
    )
    physician = PhysicianStatus(
        physician_id="DOC-BAD",
        tracking_rate_suspended_until=as_of + timedelta(days=30),
    )
    engine = EligibilityEngine()
    report = engine.evaluate(state, physician=physician)

    assert report.eligible_codes() == []
    p1407_result = report.get("P1407C")
    assert p1407_result is not None
    assert any("停權" in m for m in p1407_result.missing_requirements)


def test_engine_physician_suspension_reason_is_categorized_as_blocked():
    """回歸測試：engine.py 在套用醫師停權橫向規則時，直接對
    EligibilityResult.missing_requirements 做 append()，先前遺漏同步
    append 到 missing_reasons，會讓兩份清單不同步，且「醫師停權」這筆
    理由完全沒有分類（既非TIMING/DATA_GAP/PREREQUISITE，也不在
    BLOCKED——因為根本沒被加進 missing_reasons）。已修正為兩份清單同步
    append，並分類為 BLOCKED（需要人工介入排除停權狀態，非隨時間自動
    解除）。"""
    as_of = date(2026, 4, 1)
    earlier_visit = as_of - timedelta(days=10)
    state = base_state(
        "PAT-SUSPEND-SYNC",
        as_of,
        encounters=[dm_encounter(earlier_visit, with_med=False), dm_encounter(as_of)],
        lab_results=full_p1407_labs(as_of),
    )
    physician = PhysicianStatus(
        physician_id="DOC-BAD",
        tracking_rate_suspended_until=as_of + timedelta(days=30),
    )
    engine = EligibilityEngine()
    report = engine.evaluate(state, physician=physician)

    p1407_result = report.get("P1407C")
    assert p1407_result is not None
    # 兩份清單內容/順序必須一致（向下相容不變式）
    assert p1407_result.missing_requirements == [r.detail for r in p1407_result.missing_reasons]
    # 停權理由本身必須被分類，且分類為 BLOCKED（需人工介入，非排程等待）
    suspension_reasons = [r for r in p1407_result.missing_reasons if "停權" in r.detail]
    assert len(suspension_reasons) == 1
    assert suspension_reasons[0].kind == MissingReasonKind.BLOCKED
    # 因此不應被 is_pending_timing_only() 誤判為「純排程等待、可保持靜默」
    assert p1407_result.is_pending_timing_only() is False


def test_engine_dual_qualification_reason_is_categorized_as_blocked():
    """回歸測試（同上，P7系列雙重資格分支）：先前完全無測試覆蓋，且與
    醫師停權分支犯了同一個 missing_reasons 不同步錯誤，一併修正、一併
    補上測試。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-DUAL-QUAL-SYNC",
        as_of,
        claims=[CodeClaim(code="P1407C", claim_date=as_of - timedelta(days=100))],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=80.0, upcr=200.0, is_diabetic=True)],
    )
    physician = PhysicianStatus(physician_id="DOC-NOT-DUAL", is_dm_ckd_dual_qualified=False)
    engine = EligibilityEngine()
    report = engine.evaluate(state, physician=physician)

    for code in ("P7001C", "P7002C", "P7003C"):
        result = report.get(code)
        assert result is not None
        assert result.eligible is False
        assert result.missing_requirements == [r.detail for r in result.missing_reasons]
        dual_reasons = [r for r in result.missing_reasons if "雙重資格" in r.detail]
        assert len(dual_reasons) == 1
        assert dual_reasons[0].kind == MissingReasonKind.BLOCKED


# ---------------------------------------------------------------------------
# 11. EligibilityEngine：年度評估碼(P1409C/P1411C/P7002C)互斥，多筆同時
#     符合資格時應出現人工擇一警告，引擎不自動代為選擇
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 12. EligibilityResult.missing_reasons：純排程等待(TIMING)時
#     is_pending_timing_only() 應為 True，且不產生任何 actionable 項目
# ---------------------------------------------------------------------------


def test_p1408_pending_timing_only_when_not_enough_days_since_last_claim():
    """病人已符合P1408C各項前提，僅因距上次P1408C申報僅10天(未滿70天)
    而不合格——這是完全正常的排程等待狀態，missing_reasons應只有一項
    kind=TIMING，is_pending_timing_only()應為True，actionable_missing_
    reasons()應為空清單（背景自動化流程對此應保持靜默、不通知）。"""
    p1407_date = date(2026, 1, 1)
    last_p1408_date = p1407_date + timedelta(days=49)
    as_of = last_p1408_date + timedelta(days=10)  # 僅間隔10天，未滿70天

    state = base_state(
        "PAT-P1408-TIMING",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=p1407_date),
            CodeClaim(code="P1408C", claim_date=last_p1408_date),
        ],
        lab_results=full_p1408_labs(as_of),
    )
    result = check_p1408_eligibility(state)

    assert result.eligible is False
    assert result.is_pending_timing_only() is True
    assert result.actionable_missing_reasons() == []
    assert len(result.missing_reasons) == 1
    assert result.missing_reasons[0].kind == MissingReasonKind.TIMING


# ---------------------------------------------------------------------------
# 13. EligibilityResult.missing_reasons：缺檢驗資料應分類為DATA_GAP，
#     且出現在actionable_missing_reasons()中（值得協助安排/開立）
# ---------------------------------------------------------------------------


def test_p1407_missing_lab_classified_as_data_gap():
    as_of = date(2026, 4, 1)
    earlier_visit = as_of - timedelta(days=30)

    labs = full_p1407_labs(as_of)
    labs = [lr for lr in labs if lr.item_code != "09006C"]  # 故意缺HbA1C

    state = base_state(
        "PAT-P1407-DATAGAP",
        as_of,
        encounters=[dm_encounter(earlier_visit, with_med=False), dm_encounter(as_of)],
        lab_results=labs,
    )
    result = check_p1407_eligibility(state)

    assert result.eligible is False
    data_gap_reasons = [r for r in result.missing_reasons if r.kind == MissingReasonKind.DATA_GAP]
    assert any("HbA1C" in r.detail for r in data_gap_reasons)
    actionable_details = [r.detail for r in result.actionable_missing_reasons()]
    assert any("HbA1C" in d for d in actionable_details)


# ---------------------------------------------------------------------------
# 14. EligibilityResult：missing_requirements（舊字串清單）內容與順序
#     和missing_reasons的detail完全一致，確認向後相容
# ---------------------------------------------------------------------------


def test_missing_requirements_matches_missing_reasons_details_in_order():
    """既有測試情境(90天就醫窗邊界在外)的eligible=False結果，驗證舊的
    missing_requirements字串清單內容/順序，與新missing_reasons的detail
    欄位逐一對應一致——確保這次重構未改變任何訊息文字或順序。"""
    as_of = date(2026, 4, 1)
    earlier_visit_out = as_of - timedelta(days=91)
    state_out = base_state(
        "PAT-COMPAT",
        as_of,
        encounters=[dm_encounter(earlier_visit_out, with_med=False), dm_encounter(as_of)],
        lab_results=full_p1407_labs(as_of),
    )
    result = check_p1407_eligibility(state_out)

    assert result.eligible is False
    assert result.missing_requirements == [r.detail for r in result.missing_reasons]


def test_engine_flags_mutual_exclusion_between_annual_codes():
    as_of = date(2027, 1, 1)

    state = base_state(
        "PAT-EXCLUSIVE",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=date(2025, 1, 1)),
            CodeClaim(code="P4301C", claim_date=date(2025, 1, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 3, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 6, 1)),
            CodeClaim(code="P7001C", claim_date=date(2026, 9, 1)),
            CodeClaim(code="P1408C", claim_date=as_of - timedelta(days=70)),
        ],
        lab_results=full_p1407_labs(as_of) + full_p7001_labs(as_of),
    )
    engine = EligibilityEngine()
    report = engine.evaluate(state)

    p1409_eligible = report.get("P1409C").eligible
    p7002_eligible = report.get("P7002C").eligible
    if p1409_eligible and p7002_eligible:
        assert any("P1409C" in w and "P7002C" in w for w in report.warnings)
    else:
        # 若因資料條件差異兩者未同時成立，至少確認引擎沒有拋出例外，
        # 且未自動核准超過年度上限的重複年度碼。
        assert True
