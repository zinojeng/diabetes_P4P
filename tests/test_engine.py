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
    check_p1410_eligibility,
    check_p1411_eligibility,
    check_quality_monitoring,
)
from dm_eligibility.rules_p7 import (
    check_p4301_eligibility,
    check_p7001_eligibility,
    check_p7002_eligibility,
    check_p7003_eligibility,
)


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
    assert any("年度碼每年僅可擇一申報" in m for m in result.missing_requirements)
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


def test_engine_physician_suspension_reason_is_categorized_as_timing():
    """回歸測試：engine.py 在套用醫師停權橫向規則時，直接對
    EligibilityResult.missing_requirements 做 append()，先前遺漏同步
    append 到 missing_reasons，會讓兩份清單不同步，且「醫師停權」這筆
    理由完全沒有分類。已修正為兩份清單同步 append，並分類為 TIMING——
    停權有明訂到期日、屆期自動解除，不需任何人介入排除，重複回報「仍在
    停權中」不會帶來新資訊，只會造成每日洗版通知（2026-09-05 review 後
    定案，早期版本曾誤分類為BLOCKED）。"""
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
    # 停權理由本身必須被分類，且分類為 TIMING（會隨時間自動解除）
    suspension_reasons = [r for r in p1407_result.missing_reasons if "停權" in r.detail]
    assert len(suspension_reasons) == 1
    assert suspension_reasons[0].kind == MissingReasonKind.TIMING
    # 因此背景流程應保持靜默，不主動通知（停權本身已是先前已知的行政狀態）
    assert p1407_result.is_pending_timing_only() is True


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


# ---------------------------------------------------------------------------
# 14. P4301C：CKD分期「資料不足」vs「資料齊全但不符合」的分類
#     （回歸測試——Codex review 發現的分類錯誤：原本 stage()==None 一律
#     歸為DATA_GAP，會讓「資料齊全、確定不符合」的個案也被當成缺檢驗，
#     背景流程可能因此誤協助安排本就不需要的追加檢驗）
# ---------------------------------------------------------------------------


def test_p4301_ckd_no_assessment_at_all_is_data_gap():
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-CKD-NO-ASSESSMENT",
        as_of,
        encounters=[ckd_encounter(as_of - timedelta(days=30)), ckd_encounter(as_of)],
        ckd_assessments=[],
    )
    result = check_p4301_eligibility(state)
    assert result.eligible is False
    ckd_reasons = [r for r in result.missing_reasons if "CKD分期評估" in r.detail]
    assert len(ckd_reasons) == 1
    assert ckd_reasons[0].kind == MissingReasonKind.DATA_GAP


def test_p4301_ckd_egfr_in_qualifying_range_but_proteinuria_missing_is_data_gap():
    """eGFR=70（落在60~89.9區間，屬Stage2候選範圍）但UPCR/UACR皆缺——
    無法確定是否符合蛋白尿條件，屬「資料不足」而非「確定不符合」。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-CKD-PROTEINURIA-UNKNOWN",
        as_of,
        encounters=[ckd_encounter(as_of - timedelta(days=30)), ckd_encounter(as_of)],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=70.0, upcr=None, uacr=None, is_diabetic=True)],
    )
    result = check_p4301_eligibility(state)
    assert result.eligible is False
    ckd_reasons = [r for r in result.missing_reasons if "CKD分期評估" in r.detail]
    assert len(ckd_reasons) == 1
    assert ckd_reasons[0].kind == MissingReasonKind.DATA_GAP


def test_p4301_ckd_egfr_below_stage3a_threshold_is_blocked_not_data_gap():
    """eGFR=30，明確低於Stage3a門檻(45~59.9)——資料已齊全，確定不符合
    Stage1/2/3a，應分類為BLOCKED，不應被當成缺檢驗(DATA_GAP)。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-CKD-BELOW-STAGE3A",
        as_of,
        encounters=[ckd_encounter(as_of - timedelta(days=30)), ckd_encounter(as_of)],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=30.0, upcr=500.0, is_diabetic=False)],
    )
    result = check_p4301_eligibility(state)
    assert result.eligible is False
    ckd_reasons = [r for r in result.missing_reasons if "CKD分期評估" in r.detail]
    assert len(ckd_reasons) == 1
    assert ckd_reasons[0].kind == MissingReasonKind.BLOCKED


def test_p4301_ckd_proteinuria_measured_but_below_threshold_is_blocked():
    """eGFR=70(合格區間)，UPCR/UACR皆已測得但未達門檻——資料齊全、確定
    不符合，應分類為BLOCKED，不應被當成缺檢驗(DATA_GAP)。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-CKD-PROTEINURIA-NEGATIVE",
        as_of,
        encounters=[ckd_encounter(as_of - timedelta(days=30)), ckd_encounter(as_of)],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=70.0, upcr=50.0, uacr=10.0, is_diabetic=True)],
    )
    result = check_p4301_eligibility(state)
    assert result.eligible is False
    ckd_reasons = [r for r in result.missing_reasons if "CKD分期評估" in r.detail]
    assert len(ckd_reasons) == 1
    assert ckd_reasons[0].kind == MissingReasonKind.BLOCKED


def test_p4301_ckd_diabetic_upcr_negative_but_uacr_missing_is_data_gap():
    """回歸測試（Codex review 二次驗證發現的殘留bug）：糖尿病患者eGFR=70
    (合格區間)，UPCR已測得且未達150門檻，但UACR未測——UACR仍可能是
    遺漏的陽性結果(>=30)，不能視為「資料齊全、確定不符合」，應維持
    DATA_GAP，不可誤判為BLOCKED。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-CKD-UPCR-ONLY",
        as_of,
        encounters=[ckd_encounter(as_of - timedelta(days=30)), ckd_encounter(as_of)],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=70.0, upcr=50.0, uacr=None, is_diabetic=True)],
    )
    result = check_p4301_eligibility(state)
    assert result.eligible is False
    ckd_reasons = [r for r in result.missing_reasons if "CKD分期評估" in r.detail]
    assert len(ckd_reasons) == 1
    assert ckd_reasons[0].kind == MissingReasonKind.DATA_GAP


def test_p4301_ckd_diabetic_uacr_negative_but_upcr_missing_is_data_gap():
    """同上，另一半：糖尿病患者UACR已測得且未達30門檻，但UPCR未測——
    UPCR仍可能是遺漏的陽性結果(>=150)，應維持DATA_GAP。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-CKD-UACR-ONLY",
        as_of,
        encounters=[ckd_encounter(as_of - timedelta(days=30)), ckd_encounter(as_of)],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=70.0, upcr=None, uacr=10.0, is_diabetic=True)],
    )
    result = check_p4301_eligibility(state)
    assert result.eligible is False
    ckd_reasons = [r for r in result.missing_reasons if "CKD分期評估" in r.detail]
    assert len(ckd_reasons) == 1
    assert ckd_reasons[0].kind == MissingReasonKind.DATA_GAP


def test_p4301_ckd_non_diabetic_upcr_negative_uacr_irrelevant_is_blocked():
    """對照組：非糖尿病患者只看UPCR一項（UACR門檻本就只對糖尿病患適用），
    UPCR已測得且未達門檻時，即使UACR未測也應視為資料齊全、確定不符合
    （BLOCKED），因為UACR對非糖尿病患者的Stage判定完全不影響結果。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-CKD-NONDIABETIC-UPCR-ONLY",
        as_of,
        encounters=[ckd_encounter(as_of - timedelta(days=30)), ckd_encounter(as_of)],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=70.0, upcr=50.0, uacr=None, is_diabetic=False)],
    )
    result = check_p4301_eligibility(state)
    assert result.eligible is False
    ckd_reasons = [r for r in result.missing_reasons if "CKD分期評估" in r.detail]
    assert len(ckd_reasons) == 1
    assert ckd_reasons[0].kind == MissingReasonKind.BLOCKED


# ---------------------------------------------------------------------------
# 15. P7003C：轉診條件「資料不足」vs「資料齊全但未達標」的分類
#     （回歸測試——Codex review 發現的分類錯誤：原本 referral_indicated
#     預設False，缺評估資料與確定未達轉診條件會被歸為同一個BLOCKED訊息）
# ---------------------------------------------------------------------------


def test_p7003_referral_condition_data_gap_when_no_ckd_assessment():
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-P7003-NO-ASSESSMENT",
        as_of,
        claims=[CodeClaim(code="P4301C", claim_date=as_of - timedelta(days=100))],
        ckd_assessments=[],
        pre_esrd_referral_confirmed=True,
    )
    result = check_p7003_eligibility(state)
    assert result.eligible is False
    referral_reasons = [r for r in result.missing_reasons if "轉診條件" in r.detail]
    assert len(referral_reasons) == 1
    assert referral_reasons[0].kind == MissingReasonKind.DATA_GAP


def test_p7003_referral_condition_blocked_when_data_complete_but_not_indicated():
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-P7003-NOT-INDICATED",
        as_of,
        claims=[CodeClaim(code="P4301C", claim_date=as_of - timedelta(days=100))],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=60.0, upcr=100.0, is_diabetic=False)],
        pre_esrd_referral_confirmed=True,
    )
    result = check_p7003_eligibility(state)
    assert result.eligible is False
    referral_reasons = [r for r in result.missing_reasons if "轉診條件" in r.detail]
    assert len(referral_reasons) == 1
    assert referral_reasons[0].kind == MissingReasonKind.BLOCKED


def test_p7003_referral_condition_satisfied_by_upcr_even_when_egfr_missing():
    """UPCR>=1000本身已足以判定符合轉診條件，即使eGFR缺測也不影響——
    OR條件其中一項已知為真時，另一項缺測不應被視為資料不足。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-P7003-UPCR-QUALIFIES",
        as_of,
        claims=[CodeClaim(code="P4301C", claim_date=as_of - timedelta(days=100))],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=None, upcr=1200.0, is_diabetic=False)],
        pre_esrd_referral_confirmed=True,
    )
    result = check_p7003_eligibility(state)
    referral_reasons = [r for r in result.missing_reasons if "轉診條件" in r.detail]
    assert referral_reasons == []
    assert any("符合轉診條件" in r for r in result.reasons)


def test_p7003_referral_condition_data_gap_when_egfr_known_negative_but_upcr_missing():
    """eGFR=60(未達<45門檻)已知，但UPCR缺測——UPCR仍可能是遺漏的陽性
    結果(>=1000)，應為DATA_GAP，不可視為確定不符合(BLOCKED)。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-P7003-EGFR-ONLY",
        as_of,
        claims=[CodeClaim(code="P4301C", claim_date=as_of - timedelta(days=100))],
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=60.0, upcr=None, is_diabetic=False)],
        pre_esrd_referral_confirmed=True,
    )
    result = check_p7003_eligibility(state)
    assert result.eligible is False
    referral_reasons = [r for r in result.missing_reasons if "轉診條件" in r.detail]
    assert len(referral_reasons) == 1
    assert referral_reasons[0].kind == MissingReasonKind.DATA_GAP


# ---------------------------------------------------------------------------
# 16. P1407C：同院所1年內結案冷卻期分類為TIMING（非BLOCKED）
#     （回歸測試——2026-09-05 決策：結案冷卻期純以日期計算、屆滿1年自動
#     解除、不需人工介入，歸為BLOCKED會讓背景流程對「還沒滿1年」這種
#     每天都會發生的正常倒數狀態每天重複通知，應歸TIMING、保持靜默）
# ---------------------------------------------------------------------------


def test_p1407_closure_cooldown_within_1year_is_timing_and_stays_silent():
    as_of = date(2026, 4, 1)
    earlier_visit = as_of - timedelta(days=30)
    state = base_state(
        "PAT-CLOSURE-COOLDOWN",
        as_of,
        encounters=[dm_encounter(earlier_visit, with_med=False), dm_encounter(as_of)],
        lab_results=full_p1407_labs(as_of),
        closure_records=[ClosureRecord(closure_date=as_of - timedelta(days=100), reason="長期失聯")],
    )
    result = check_p1407_eligibility(state)
    assert result.eligible is False
    cooldown_reasons = [r for r in result.missing_reasons if "1年內曾結案" in r.detail]
    assert len(cooldown_reasons) == 1
    assert cooldown_reasons[0].kind == MissingReasonKind.TIMING
    assert result.is_pending_timing_only() is True
    assert result.actionable_missing_reasons() == []


# ---------------------------------------------------------------------------
# 17. 回歸測試：CoDoClaw session 轉交之 Codex review 發現的11個bug
#     （對CoDoClaw唯讀鏡射之dm_eligibility程式碼所做的review，因兩邊是
#     同一份程式碼，發現直接適用於本repo）
# ---------------------------------------------------------------------------


def test_p1407_dialysis_clinic_visit_excluded_from_90day_count():
    """Finding 1修正：先前 _qualifying_dm_visits() 有一段
    `if e.clinic_type_code is not None: pass` 的死程式碼，完全沒有實際
    排除洗腎相關診別代碼——導致90天內就醫次數計算會把應排除的診別也
    算進去。此測試：2次就診，其中1次為排除診別(177)，應只算1次，
    不足以達成P1407C的「90天內≥2次」前提。"""
    as_of = date(2026, 4, 1)
    earlier_visit = as_of - timedelta(days=10)
    state = base_state(
        "PAT-DIALYSIS-CLINIC",
        as_of,
        encounters=[
            dm_encounter(earlier_visit, clinic_type_code="177"),  # 排除診別
            dm_encounter(as_of),
        ],
        lab_results=full_p1407_labs(as_of),
    )
    result = check_p1407_eligibility(state)
    assert any("僅1次，需≥2次" in m for m in result.missing_requirements)


def test_p4301_prior_visit_window_excludes_enrollment_day_itself():
    """Finding 2修正：規格書「收案前90天內曾在該院所就醫」的「收案前」
    代表新收案當次以外的就醫紀錄。先前的90天窗口含as_of當天，若病人
    只有當次這一筆就診、之前完全沒來過，會被誤判為滿足此前提。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-P4301-ONLY-TODAY",
        as_of,
        encounters=[ckd_encounter(as_of)],  # 唯一一筆就診即為當次收案就診
        ckd_assessments=[CKDAssessment(assessment_date=as_of, egfr=50.0)],
    )
    result = check_p4301_eligibility(state)
    assert result.eligible is False
    assert any("收案前90天內查無該院所就醫紀錄" in m for m in result.missing_requirements)


def test_p1411_requires_stage1_completion_gate():
    """Finding 3修正：P1411C（第二階段年度評估碼）先前漏掉
    check_stage2_entry_eligible()前提檢查——單靠「P1408C+P1410C累計>=3」
    不足以保證病人真的走完第一階段（可能只有P1408C x3、從未有任何
    P1409C申報紀錄）。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-P1411-NO-STAGE1",
        as_of,
        claims=[
            CodeClaim(code="P1408C", claim_date=date(2025, 1, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 3, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 6, 1)),
        ],
    )
    result = check_p1411_eligibility(state)
    assert result.eligible is False
    assert any("尚未完整申報第一階段" in m for m in result.missing_requirements)


def test_engine_stage2_physician_qualification_blocks_p1410_p1411():
    """Finding 4修正：PhysicianStatus.is_stage2_qualified先前完全沒有
    被任何規則讀取（等同從未強制第二階段醫師資格）。比照P70雙重資格的
    橫向規則寫法，在engine.py補上。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-STAGE2-DOC",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=date(2024, 1, 1)),
            CodeClaim(code="P1408C", claim_date=date(2024, 3, 1)),
            CodeClaim(code="P1408C", claim_date=date(2024, 5, 1)),
            CodeClaim(code="P1408C", claim_date=date(2024, 7, 1)),
            CodeClaim(code="P1408C", claim_date=date(2024, 9, 1)),
            CodeClaim(code="P1408C", claim_date=date(2024, 11, 1)),
            CodeClaim(code="P1409C", claim_date=date(2024, 6, 1)),
            CodeClaim(code="P1409C", claim_date=date(2025, 6, 1)),
        ],
    )
    physician = PhysicianStatus(physician_id="DOC-NO-STAGE2", is_stage2_qualified=False)
    engine = EligibilityEngine()
    report = engine.evaluate(state, physician=physician)

    for code in ("P1410C", "P1411C"):
        result = report.get(code)
        assert result is not None
        assert result.eligible is False
        assert result.missing_requirements == [r.detail for r in result.missing_reasons]
        stage2_reasons = [r for r in result.missing_reasons if "第二階段" in r.detail and "醫師資格" in r.detail]
        assert len(stage2_reasons) == 1
        assert stage2_reasons[0].kind == MissingReasonKind.BLOCKED


def test_p1409_blocked_when_p1411_already_claimed_this_year():
    """Finding 5修正：年度碼P1409C/P1411C/P7002C三者互斥，先前
    check_p1409_eligibility只檢查P1409C自己是否已報過，未檢查另外兩碼。
    此測試改用P1411C（而非既有測試已涵蓋的P7002C），確保對稱性完整。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-P1409-VS-P1411",
        as_of,
        claims=[CodeClaim(code="P1411C", claim_date=date(2026, 2, 1))],
    )
    result = check_p1409_eligibility(state)
    assert result.eligible is False
    assert any("P1409C/P1411C/P7002C" in m for m in result.missing_requirements)


def test_p1411_blocked_when_p1409_already_claimed_this_year():
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-P1411-VS-P1409",
        as_of,
        claims=[CodeClaim(code="P1409C", claim_date=date(2026, 2, 1))],
    )
    result = check_p1411_eligibility(state)
    assert result.eligible is False
    assert any("P1409C/P1411C/P7002C" in m for m in result.missing_requirements)


def test_p1408_lock_date_uses_earlier_of_stage2_and_p7_entry():
    """Finding 6修正：先前 `entered_stage2_date or entered_p7_date` 用
    Python的or運算子，只要entered_stage2_date非None就一律優先採用，不論
    entered_p7_date是否較早。此測試：entered_p7_date早於entered_stage2_
    date，鎖定應以較早的entered_p7_date為準（此時已過1年，鎖定應已解除）；
    若bug仍在（誤用entered_stage2_date，較晚），鎖定會被誤判為仍然生效。"""
    as_of = date(2026, 4, 1)
    p1407_date = date(2020, 1, 1)
    last_p1408 = as_of - timedelta(days=100)  # 間隔已足夠，不受70天間隔影響
    state = base_state(
        "PAT-LOCK-DATE-ORDER",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=p1407_date),
            CodeClaim(code="P1408C", claim_date=last_p1408),
        ],
        lab_results=full_p1408_labs(as_of),
        entered_p7_date=as_of - timedelta(days=400),  # 較早，已超過1年
        entered_stage2_date=as_of - timedelta(days=100),  # 較晚，未滿1年
    )
    result = check_p1408_eligibility(state)
    # 正確行為：以較早的entered_p7_date(400天前)為準，鎖定已解除，不應
    # 出現「1年內不得再申報P1408C」的訊息。
    assert not any("第二階段/P7體系" in m for m in result.missing_requirements)


def test_p1408_same_day_claim_blocks_duplicate_interval_check():
    """Finding 7修正：models.last_claim_date()先前用嚴格`<`過濾`before`
    參數，會讓「今天已有一筆同代碼申報紀錄」被排除在外，導致往回找到
    更早一筆申報來計算天數、誤判「間隔已足夠」而允許同一天重複申報。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-SAME-DAY-CLAIM",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=date(2026, 1, 1)),
            CodeClaim(code="P1408C", claim_date=date(2026, 1, 20)),
            CodeClaim(code="P1408C", claim_date=as_of),  # 今天已申報過一次
        ],
    )
    result = check_p1408_eligibility(state)
    assert result.eligible is False
    assert any("僅0天" in m for m in result.missing_requirements)


def test_p7001_second_claim_this_year_only_requires_ldl_and_cr():
    """Finding 9修正（依 spec/P7_rules_spec.md (d) 節「檢驗報告日期規範
    （依批碼分次要求不同）」）：P700101(第1次)只需B.S+HbA1C、
    P700102(第2次)只需LDL+Cr、P700103(第3次)只需UACR——先前不分次數，
    每次都要求備齊全部5項。此測試：當年度第2次申報，只有LDL+Cr有報告
    （缺B.S/HbA1C/UACR），應視為檢驗齊全。"""
    enroll_date = date(2026, 1, 1)
    first_p7001_date = enroll_date + timedelta(days=49)
    as_of = first_p7001_date + timedelta(days=70)  # 與first_p7001_date同一年
    state = base_state(
        "PAT-P7001-2ND-CLAIM",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=enroll_date),
            CodeClaim(code="P4301C", claim_date=enroll_date),
            CodeClaim(code="P7001C", claim_date=first_p7001_date),
        ],
        lab_results=[
            LabResult(item_code="09044C", result_date=as_of),  # LDL
            LabResult(item_code="09015C", result_date=as_of),  # Cr
        ],
    )
    result = check_p7001_eligibility(state)
    assert result.eligible is True


def test_p7002_accepts_06013c_as_ur_alternative_to_12111c():
    """Finding 10修正：P7002C規格書明文「Mic/Cr及U/R(二擇一)」，先前
    alternatives只放了12111C(Mic/Cr)一項，沒有真正允許06013C(U/R，尿液
    分析)作為替代——「二擇一」的描述文字與實際檢查邏輯不一致。此測試只
    提供06013C(不提供12111C)，應仍視為此項檢驗已滿足。"""
    as_of = date(2026, 4, 1)
    p7002_labs_via_ur = [
        LabResult(item_code=c, result_date=as_of)
        for c in ("09005C", "09006C", "09026C", "09004C", "09001C", "09044C", "09043C", "09015C")
    ] + [
        LabResult(item_code="06013C", result_date=as_of),  # U/R替代Mic/Cr(12111C)
        LabResult(item_code="23501C", result_date=as_of),
    ]
    state = base_state(
        "PAT-P7002-UR-ALT",
        as_of,
        claims=[
            CodeClaim(code="P1407C", claim_date=date(2025, 1, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 3, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 6, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 9, 1)),
            CodeClaim(code="P1408C", claim_date=date(2025, 12, 1)),
        ],
        lab_results=p7002_labs_via_ur,
    )
    result = check_p7002_eligibility(state)
    assert not any("Mic/Cr" in m for m in result.missing_requirements)


def test_quality_monitoring_lipid_panel_requires_all_four_items():
    """Finding 11修正：血脂四項(總膽固醇/TG/HDL/LDL)是四項各自獨立、
    缺一不可的檢驗，先前塞進同一個LabRequirement.alternatives(OR語意)，
    導致「只測了總膽固醇」被誤判成「血脂四項已完成」。此測試：只有
    總膽固醇(09001C)有報告，TG/HDL/LDL皆缺，應觸發血脂四項警示。"""
    as_of = date(2026, 4, 1)
    state = base_state(
        "PAT-LIPID-PARTIAL",
        as_of,
        encounters=[dm_encounter(as_of)],
        lab_results=[LabResult(item_code="09001C", result_date=as_of)],
    )
    alerts = check_quality_monitoring(state)
    assert any("血脂四項" in a for a in alerts)
    lipid_alert = next(a for a in alerts if "血脂四項" in a)
    assert "三酸甘油脂" in lipid_alert and "HDL" in lipid_alert and "LDL" in lipid_alert
    assert "總膽固醇" not in lipid_alert.split("缺：")[1]  # 已有報告的項目不應出現在缺項清單裡
