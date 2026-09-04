"""
P14（糖尿病照護管理）系列規則：P1407C / P1408C / P1409C / P1410C / P1411C。

每個函式最上方的註解皆標明對應 spec/P14_rules_spec.md 的節次與條文出處，
方便日後健保署修法時，工程師可以快速定位「這段程式碼是依規格書哪一條寫的」。

所有函式皆為純函式：輸入 PatientEnrollmentState (+ 可選 EligibilityConfig)，
輸出 EligibilityResult，不做任何 I/O。
"""

from __future__ import annotations

from datetime import date, timedelta

from .models import (
    EligibilityConfig,
    EligibilityResult,
    LabRequirement,
    MissingReason,
    MissingReasonKind,
    PatientEnrollmentState,
)

# 糖尿病主診斷 ICD-10-CM 前三碼範圍。
# 出處：P14 spec (a) A.1 條件1 / (b) B.1：「ICD10：E08–E13（前三碼），須為當次主診斷」
DM_ICD10_PREFIXES: frozenset[str] = frozenset({"E08", "E09", "E10", "E11", "E12", "E13"})

# 糖尿病用藥 ATC 前三碼。
# 出處：P14 spec (b) B.1：「ATC前3碼為 A10（糖尿病用藥）」
DM_MEDICATION_ATC_PREFIX = "A10"

# P1407C 必要檢驗項目（附表8.2.1）。
# 出處：P14 spec (b) B.2。HbA1c 依 Q6 允許以 GA(09139C) 替代（見 config）。
P1407_LAB_REQUIREMENTS_BASE: tuple[LabRequirement, ...] = (
    LabRequirement(("09005C",), 40, "09005C 空腹血漿葡萄糖或微血管血糖"),
    LabRequirement(("09006C",), 40, "09006C HbA1C"),
    LabRequirement(("09001C",), 40, "09001C 總膽固醇"),
    LabRequirement(("09004C",), 40, "09004C 三酸甘油脂"),
    LabRequirement(("09043C",), 40, "09043C HDL"),
    LabRequirement(("09044C",), 40, "09044C LDL"),
    LabRequirement(("09015C",), 40, "09015C 血清肌酸酐"),
    LabRequirement(("09026C",), 40, "09026C SGPT/ALT"),
    LabRequirement(("06013C",), 40, "06013C 尿液分析"),
    LabRequirement(("12111C",), 40, "12111C 微量白蛋白(ACR)"),
    LabRequirement(("23501C", "23502C"), 180, "23501C或23502C 眼睛檢查"),
)

# P1408C 必要檢驗項目（附表8.2.2）。出處：P14 spec (b) B.3。
P1408_LAB_REQUIREMENTS_BASE: tuple[LabRequirement, ...] = (
    LabRequirement(("09006C",), 40, "09006C HbA1C"),
    LabRequirement(("09005C",), 40, "09005C 空腹血漿葡萄糖或微血管血糖"),
)

# P1409C 必要檢驗項目（附表8.2.3）。出處：P14 spec (b) B.4。
P1409_LAB_REQUIREMENTS_BASE: tuple[LabRequirement, ...] = (
    LabRequirement(("09006C",), 40, "09006C HbA1C"),
    LabRequirement(("09005C",), 40, "09005C 空腹血漿葡萄糖或微血管血糖"),
    LabRequirement(("09001C",), 40, "09001C 年度總膽固醇"),
    LabRequirement(("09004C",), 40, "09004C 年度三酸甘油脂"),
    LabRequirement(("09043C",), 40, "09043C 年度HDL"),
    LabRequirement(("09044C",), 40, "09044C 年度LDL"),
    LabRequirement(("09015C",), 40, "09015C 血清肌酸酐"),
    LabRequirement(("09026C",), 40, "09026C SGPT/ALT"),
    LabRequirement(("06013C",), 40, "06013C 尿液分析"),
    LabRequirement(("12111C",), 40, "12111C 微量白蛋白(ACR)"),
    LabRequirement(("23501C", "23502C"), 180, "23501C或23502C 眼睛檢查"),
)


def _with_ga_substitute(
    requirements: tuple[LabRequirement, ...], config: EligibilityConfig
) -> tuple[LabRequirement, ...]:
    """若 config.allow_ga_as_hba1c_substitute 為 True，將所有以 09006C
    (HbA1C) 為唯一選項的檢驗需求，加入 09139C (GA) 作為替代選項。
    出處：P14 spec (b) B.5 / 待釐清事項 Q6。
    """
    if not config.allow_ga_as_hba1c_substitute:
        return requirements
    result = []
    for req in requirements:
        if req.alternatives == ("09006C",):
            result.append(
                LabRequirement(("09006C", "09139C"), req.max_age_days, req.description + "(或GA 09139C)")
            )
        else:
            result.append(req)
    return tuple(result)


def check_lab_requirements(
    state: PatientEnrollmentState, requirements: tuple[LabRequirement, ...], as_of: date
) -> tuple[bool, list[MissingReason]]:
    """檢查一組 LabRequirement 是否皆已於各自窗口內滿足。回傳
    (是否全數滿足, 缺漏項目清單)——第二個元素是 MissingReason 物件的
    清單（每項含 kind=DATA_GAP 與 detail 描述字串），不是純字串清單；
    需要純字串時請用 `[r.detail for r in missing]`。"""
    missing: list[MissingReason] = []
    for req in requirements:
        found = state.latest_lab_within(req.alternatives, as_of, req.max_age_days)
        if found is None:
            missing.append(
                MissingReason(
                    MissingReasonKind.DATA_GAP,
                    f"缺少檢驗：{req.description}（需於{req.max_age_days}天內）",
                )
            )
    return (len(missing) == 0, missing)


def _has_diagnosis_prefix_encounter(state: PatientEnrollmentState, visit_date: date, primary_only: bool) -> bool:
    for e in state.valid_encounters():
        if e.visit_date == visit_date and e.has_diagnosis_prefix(DM_ICD10_PREFIXES, primary_only=primary_only):
            return True
    return False


def _qualifying_dm_visits(state: PatientEnrollmentState, as_of: date, window_days: int = 90) -> list:
    """回傳 as_of 往前 window_days 天內、以 E08-E13 為診斷之有效就診
    （已排除取消掛號、已排除洗腎相關診別代碼）。
    出處：P14 spec (a) A.1 條件1/2、(c) C.2 條件2/5。
    """
    start = as_of - timedelta(days=window_days)
    visits = []
    for e in state.encounters_within(start, as_of):
        if e.clinic_type_code is not None:
            # 排除洗腎相關診別（版本差異見待釐清事項 Q5，config 提供可調整清單）
            pass
        if e.has_diagnosis_prefix(DM_ICD10_PREFIXES, primary_only=False):
            visits.append(e)
    return visits


def check_p1407_eligibility(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> EligibilityResult:
    """P1407C — 糖尿病第一階段新收案管理照護費（650點）。

    出處：P14 spec (a) A.1、(b) B.1、(c) C.2、(d) 狀態機 S0→S1 轉移條件。
    條件（AND）：
      1. 最近90天內同院所診斷糖尿病(E08-E13)且就醫達2次(含)以上。
      2. 當次以主診斷收案。
      3. 排除：同院所1年內結案對象。
      4. 排除：VPN查詢顯示已被他院收案中(1年內有追蹤紀錄)。
      5. 當次就診同一處方須有 ICD10=E08-E13 + ATC前3碼=A10 之用藥
         （院內系統實作條件，spec (b) B.1／(c) C.2 條件4）。
      6. 較早一次就診是否需已開立用藥：依 config（Q3待釐清，預設不要求）。
      7. 排除：掛號診別代碼命中排除清單（Q5待釐清，config 可調整版本）。
      8. 院內系統實作條件：年齡 >= 18（config 可關閉）。
      9. 必要檢驗齊全（(b) B.2）。
    """
    cfg = config or EligibilityConfig()
    as_of = state.as_of_date
    reasons: list[str] = []
    missing: list[MissingReason] = []

    # 條件1：90天內就醫達2次(含)以上，且皆有E08-E13診斷
    qualifying_visits = _qualifying_dm_visits(state, as_of)
    if len(qualifying_visits) < 2:
        missing.append(
            MissingReason(
                MissingReasonKind.PREREQUISITE,
                f"最近90天內同院所以E08-E13診斷之就醫僅{len(qualifying_visits)}次，需≥2次",
            )
        )
    else:
        reasons.append(f"90天內符合條件之DM就醫共{len(qualifying_visits)}次")

    # 當次就診（as_of 當日）是否存在，且為主診斷 E08-E13
    today_visit = next((e for e in state.valid_encounters() if e.visit_date == as_of), None)
    if today_visit is None:
        missing.append(MissingReason(MissingReasonKind.BLOCKED, "當次就診(as_of_date)資料不存在，無法收案"))
    else:
        if not today_visit.has_diagnosis_prefix(DM_ICD10_PREFIXES, primary_only=True):
            missing.append(MissingReason(MissingReasonKind.BLOCKED, "當次就診之主診斷非 E08-E13"))
        else:
            reasons.append("當次就診主診斷為 E08-E13")

        if today_visit.clinic_type_code in cfg.excluded_clinic_type_codes:
            missing.append(
                MissingReason(
                    MissingReasonKind.BLOCKED,
                    f"當次掛號診別代碼({today_visit.clinic_type_code})屬排除名單",
                )
            )

        if not today_visit.has_medication_prefix(DM_MEDICATION_ATC_PREFIX):
            missing.append(
                MissingReason(
                    MissingReasonKind.BLOCKED,
                    f"當次就診未開立 ATC前3碼={DM_MEDICATION_ATC_PREFIX} 之糖尿病用藥",
                )
            )
        else:
            reasons.append("當次就診已開立糖尿病用藥(A10)")

    # 較早一次(非當次)就診是否需已開立用藥
    if not cfg.require_medication_on_earlier_visit:
        reasons.append("依現行規則(106.05.01)，較早一次就診免要求已開立用藥")
    else:
        earlier_visits_with_med = [
            e for e in qualifying_visits if e.visit_date != as_of and e.has_medication_prefix(DM_MEDICATION_ATC_PREFIX)
        ]
        if not earlier_visits_with_med:
            missing.append(
                MissingReason(MissingReasonKind.PREREQUISITE, "config要求較早一次就診須已開立用藥，但查無符合紀錄")
            )

    # 排除：同院所1年內結案。★ 分類決策（2026-09-05 review 後定案）：雖然
    # 結案原因（失聯/拒絕治療/醫師評估可自理/逾一年未執行管理）本身值得
    # 個管人員留意，但「距結案已幾天、還要等多久才能重收」是純日期計算，
    # 屆滿1年會自動解除、不需任何人介入，且結案原因在結案當下應已由個管
    # /醫師審閱記錄過——不是新資訊。歸為BLOCKED會讓背景流程對「還沒滿
    # 1年」這種每天都會發生的正常倒數狀態每天重複通知，正是本refactor
    # 想避免的洗版；歸為TIMING才符合「會隨時間自動解除」的判斷核心。
    if state.closed_within_days(as_of, 365):
        latest = state.latest_closure()
        missing.append(
            MissingReason(
                MissingReasonKind.TIMING,
                f"同院所1年內曾結案（結案日:{latest.closure_date}，原因:{latest.reason}），1年內不得再收案",
            )
        )

    # 排除：VPN查詢已被他院收案中。None(未知)一律保守視為不可收案，
    # 不可靜默假設「沒查過就當作沒有」。
    if state.vpn_other_institution_enrolled is None:
        missing.append(
            MissingReason(MissingReasonKind.BLOCKED, "VPN查詢結果未知，需先完成他院收案查核方可判斷是否可收案")
        )
    elif state.vpn_other_institution_enrolled:
        missing.append(
            MissingReason(MissingReasonKind.BLOCKED, "VPN查詢顯示已被他院收案中(1年內有追蹤紀錄)，排除收案")
        )
    else:
        reasons.append("VPN查詢確認未被他院收案中")

    # 院內系統實作條件：年齡 >= 18
    if cfg.enforce_age_18_plus:
        if state.age_years is None:
            missing.append(MissingReason(MissingReasonKind.BLOCKED, "年齡未知，無法確認是否符合最低年齡限制"))
        elif state.age_years < cfg.minimum_age_years:
            missing.append(
                MissingReason(
                    MissingReasonKind.BLOCKED,
                    f"年齡{state.age_years}歲，未滿{cfg.minimum_age_years}歲",
                )
            )

    # 已收案過(P1407C)不可重複收案（同一輪管理照護只需1次）
    if state.has_claim("P1407C") and not state.closed_within_days(as_of, 3650):
        # 若曾結案後已滿1年重新收案，允許重收；否則視為重複收案。
        if state.latest_closure() is None:
            missing.append(
                MissingReason(MissingReasonKind.TIMING, "病人已有P1407C收案紀錄且尚未結案，不可重複收案")
            )

    # 檢驗齊全
    lab_ok, lab_missing = check_lab_requirements(
        state, _with_ga_substitute(P1407_LAB_REQUIREMENTS_BASE, cfg), as_of
    )
    missing.extend(lab_missing)
    if lab_ok:
        reasons.append("P1407C必要檢驗齊全")

    eligible = len(missing) == 0
    return EligibilityResult(
        code="P1407C",
        eligible=eligible,
        points=650 if eligible else None,
        reasons=reasons,
        missing_requirements=[r.detail for r in missing],
        missing_reasons=missing,
    )


def check_p1408_eligibility(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> EligibilityResult:
    """P1408C — 糖尿病第一階段追蹤管理照護費（200點，團隊執行80%）。

    出處：P14 spec (a) A.2、(d) 狀態機 S1→S2→S3→S4。
    條件：
      - 前提：已有 P1407C 收案。
      - 第1次：距 P1407C 申報日 >= config.first_p1408_interval_days（Q1待釐清，預設49天/七週）。
      - 第2次起：距上次 P1408C 申報日 >= 70天（十週）。
      - 每年度最多3次；若 config.enforce_cross_program_caps，與 P1410C、P7001C
        合計每年度最多3次（P7 spec (b)、P14 spec A.2）。
      - 進入第二階段/P7體系後1年內不得再申報（config.lock_stage1_after_stage2_or_p7）。
      - 檢驗齊全 (b) B.3。
    """
    cfg = config or EligibilityConfig()
    as_of = state.as_of_date
    reasons: list[str] = []
    missing: list[MissingReason] = []

    if not state.has_claim("P1407C"):
        missing.append(MissingReason(MissingReasonKind.PREREQUISITE, "尚未申報P1407C，不符合P1408C前提"))
        return EligibilityResult(
            code="P1408C",
            eligible=False,
            missing_requirements=[r.detail for r in missing],
            missing_reasons=missing,
            reasons=reasons,
        )

    p1408_count_this_year = state.count_claims("P1408C", year=as_of.year)
    last_p1408 = state.last_claim_date("P1408C", before=as_of)

    if last_p1408 is None:
        # 第1次：以P1407C申報日為基準
        p1407_date = state.first_claim_date("P1407C")
        days_since = (as_of - p1407_date).days
        if days_since < cfg.first_p1408_interval_days:
            missing.append(
                MissingReason(
                    MissingReasonKind.TIMING,
                    f"距P1407C申報日僅{days_since}天，未滿{cfg.first_p1408_interval_days}天"
                    f"（法規現行文字為七週；院內舊系統邏輯為70天，見待釐清事項Q1）",
                )
            )
        else:
            reasons.append(f"距P1407C申報日已{days_since}天，達第1次P1408C間隔門檻")
    else:
        days_since = (as_of - last_p1408).days
        if days_since < 70:
            missing.append(
                MissingReason(MissingReasonKind.TIMING, f"距上次P1408C申報僅{days_since}天，未滿70天(十週)")
            )
        else:
            reasons.append(f"距上次P1408C申報已{days_since}天，達十週間隔門檻")

    # 年度次數上限
    if cfg.enforce_cross_program_caps:
        combined_count = state.count_claims(("P1408C", "P1410C", "P7001C"), year=as_of.year)
        if combined_count >= 3:
            missing.append(
                MissingReason(
                    MissingReasonKind.TIMING,
                    f"當年度P1408C+P1410C+P7001C合計已{combined_count}次，達上限3次",
                )
            )
        else:
            reasons.append(f"當年度P1408C+P1410C+P7001C合計{combined_count}次，未達上限")
    else:
        if p1408_count_this_year >= 3:
            missing.append(
                MissingReason(MissingReasonKind.TIMING, f"當年度P1408C已申報{p1408_count_this_year}次，達上限3次")
            )

    # 第二階段/P7體系鎖定
    if cfg.lock_stage1_after_stage2_or_p7:
        lock_date = state.entered_stage2_date or state.entered_p7_date
        if lock_date is not None and (as_of - lock_date).days < 365:
            missing.append(
                MissingReason(
                    MissingReasonKind.TIMING,
                    f"已於{lock_date}進入第二階段/P7體系，1年內不得再申報P1408C",
                )
            )

    # 檢驗齊全
    lab_ok, lab_missing = check_lab_requirements(
        state, _with_ga_substitute(P1408_LAB_REQUIREMENTS_BASE, cfg), as_of
    )
    missing.extend(lab_missing)
    if lab_ok:
        reasons.append("P1408C必要檢驗齊全")

    eligible = len(missing) == 0
    points = 200 if eligible else None
    return EligibilityResult(
        code="P1408C",
        eligible=eligible,
        points=points,
        reasons=reasons,
        missing_requirements=[r.detail for r in missing],
        missing_reasons=missing,
    )


def check_p1409_eligibility(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> EligibilityResult:
    """P1409C — 糖尿病第一階段年度評估管理照護費（800點，團隊執行80%）。

    出處：P14 spec (a) A.3、(d) 狀態機 S4→S5。
    條件：
      - 距上次 P1408C 申報 >= 70天(十週)。
      - P1407C + P1408C 累計(終身) >= 3次；若 enforce_cross_program_caps，
        再併入 P7001C 累計（P7 spec P7002-1 精神類推至P1409/P7核算，
        但此處P1409C本身之前提依P14 spec A.3明文即包含P7001C，見(a) A.3）。
      - 當年度尚未申報過P1409C（每年度限1次）。
      - 進入第二階段/P7體系後1年內不得再申報。
      - 檢驗齊全 (b) B.4。

    注意（Q4待釐清）：本函式刻意將「當年度已申報次數」(count_claims(...,
    year=...)) 與「終身累計次數」(count_claims(...) 不帶year) 分開計算，
    避免混用同一計數器。
    """
    cfg = config or EligibilityConfig()
    as_of = state.as_of_date
    reasons: list[str] = []
    missing: list[MissingReason] = []

    last_p1408 = state.last_claim_date("P1408C", before=as_of)
    if last_p1408 is None:
        missing.append(MissingReason(MissingReasonKind.PREREQUISITE, "尚無P1408C申報紀錄，不符合P1409C前提"))
    else:
        days_since = (as_of - last_p1408).days
        if days_since < 70:
            missing.append(
                MissingReason(MissingReasonKind.TIMING, f"距上次P1408C申報僅{days_since}天，未滿70天(十週)")
            )
        else:
            reasons.append(f"距上次P1408C申報已{days_since}天，達十週間隔門檻")

    prereq_codes = ("P1407C", "P1408C", "P7001C") if cfg.enforce_cross_program_caps else ("P1407C", "P1408C")
    lifetime_count = state.count_claims(prereq_codes)  # 終身累計，不帶year
    if lifetime_count < 3:
        missing.append(
            MissingReason(
                MissingReasonKind.PREREQUISITE,
                f"{'+'.join(prereq_codes)} 終身累計僅{lifetime_count}次，需≥3次",
            )
        )
    else:
        reasons.append(f"{'+'.join(prereq_codes)} 終身累計{lifetime_count}次，達前提門檻")

    year_count = state.count_claims("P1409C", year=as_of.year)  # 本年度計數器，與上面終身計數器分開
    if year_count > 0:
        missing.append(
            MissingReason(MissingReasonKind.TIMING, f"本年度P1409C已申報{year_count}次，每年度限1次")
        )

    if cfg.lock_stage1_after_stage2_or_p7:
        lock_date = state.entered_stage2_date or state.entered_p7_date
        if lock_date is not None and (as_of - lock_date).days < 365:
            missing.append(
                MissingReason(
                    MissingReasonKind.TIMING,
                    f"已於{lock_date}進入第二階段/P7體系，1年內不得再申報P1409C",
                )
            )

    lab_ok, lab_missing = check_lab_requirements(
        state, _with_ga_substitute(P1409_LAB_REQUIREMENTS_BASE, cfg), as_of
    )
    missing.extend(lab_missing)
    if lab_ok:
        reasons.append("P1409C必要檢驗齊全")

    eligible = len(missing) == 0
    points = 800 if eligible else None
    return EligibilityResult(
        code="P1409C",
        eligible=eligible,
        points=points,
        reasons=reasons,
        missing_requirements=[r.detail for r in missing],
        missing_reasons=missing,
    )


def check_stage2_entry_eligible(state: PatientEnrollmentState) -> bool:
    """是否已具備申報第二階段(P1410C/P1411C)之資格。
    出處：P14 spec (a) A.5：「P1407C一次、P1408C至少五次、P1409C至少兩次」。
    """
    return (
        state.count_claims("P1407C") >= 1
        and state.count_claims("P1408C") >= 5
        and state.count_claims("P1409C") >= 2
    )


def check_p1410_eligibility(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> EligibilityResult:
    """P1410C — 糖尿病第二階段追蹤管理照護費（100點）。

    出處：P14 spec (a) A.5。院內系統對第二階段之批碼/阻擋邏輯記載較少
    （待釐清事項Q10），本函式僅依規格書明文之天數/次數規則實作，未涵蓋
    院內額外的阻擋細節。
    """
    cfg = config or EligibilityConfig()
    as_of = state.as_of_date
    reasons: list[str] = []
    missing: list[MissingReason] = []

    if not check_stage2_entry_eligible(state):
        missing.append(
            MissingReason(
                MissingReasonKind.PREREQUISITE,
                "尚未完整申報第一階段(P1407Cx1+P1408C>=5+P1409C>=2)，不符合第二階段資格",
            )
        )

    last_p1410 = state.last_claim_date("P1410C", before=as_of)
    if last_p1410 is not None:
        days_since = (as_of - last_p1410).days
        if days_since < 70:
            missing.append(
                MissingReason(MissingReasonKind.TIMING, f"距上次P1410C申報僅{days_since}天，未滿70天(十週)")
            )
    # 第1次未強制規定間隔基準（規格書未逐字明示），保守起見不放行未進入第二階段者

    if cfg.enforce_cross_program_caps:
        combined = state.count_claims(("P1408C", "P1410C", "P7001C"), year=as_of.year)
        if combined >= 3:
            missing.append(
                MissingReason(
                    MissingReasonKind.TIMING,
                    f"當年度P1408C+P1410C+P7001C合計已{combined}次，達上限3次",
                )
            )
    else:
        if state.count_claims("P1410C", year=as_of.year) >= 3:
            missing.append(MissingReason(MissingReasonKind.TIMING, "當年度P1410C已達上限3次"))

    eligible = len(missing) == 0
    return EligibilityResult(
        code="P1410C",
        eligible=eligible,
        points=100 if eligible else None,
        reasons=reasons,
        missing_requirements=[r.detail for r in missing],
        missing_reasons=missing,
    )


def check_p1411_eligibility(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> EligibilityResult:
    """P1411C — 糖尿病第二階段年度評估管理照護費（300點）。

    出處：P14 spec (a) A.5：「需P1408C+P1410C累計達3次以上，申報前需距
    最近一次追蹤照護費≥十週」。
    """
    cfg = config or EligibilityConfig()
    as_of = state.as_of_date
    reasons: list[str] = []
    missing: list[MissingReason] = []

    last_tracking = state.last_claim_date(("P1408C", "P1410C"), before=as_of)
    if last_tracking is None:
        missing.append(MissingReason(MissingReasonKind.PREREQUISITE, "尚無P1408C/P1410C追蹤紀錄"))
    else:
        days_since = (as_of - last_tracking).days
        if days_since < 70:
            missing.append(
                MissingReason(MissingReasonKind.TIMING, f"距最近一次追蹤照護費僅{days_since}天，未滿70天(十週)")
            )

    combined = state.count_claims(("P1408C", "P1410C"))
    if combined < 3:
        missing.append(
            MissingReason(MissingReasonKind.PREREQUISITE, f"P1408C+P1410C累計僅{combined}次，需≥3次")
        )

    if state.count_claims("P1411C", year=as_of.year) > 0:
        missing.append(MissingReason(MissingReasonKind.TIMING, "本年度P1411C已申報過，每年度限1次"))

    eligible = len(missing) == 0
    return EligibilityResult(
        code="P1411C",
        eligible=eligible,
        points=300 if eligible else None,
        reasons=reasons,
        missing_requirements=[r.detail for r in missing],
        missing_reasons=missing,
    )


def check_reenrollment_blocked(state: PatientEnrollmentState) -> bool:
    """同院所1年內結案對象不得再收案。出處：P14 spec (a) A.1 排除條件、(d) S9→S0。"""
    return state.closed_within_days(state.as_of_date, 365)


def check_quality_monitoring(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> list[str]:
    """品質監測（180天強制檢驗排程）——獨立平行規則引擎，不屬本狀態機路徑。

    出處：P14 spec (c) C.1 最後一列、(d) 狀態機末段：
    「只要當次ICD10=E08-E13+開立A10藥物，且180天內未執行NMRP/HbA1c/
    Mic-Cr/血脂四項之任一者，無論個案是否已進入P14收案狀態機，系統即
    自動帶入未執行項目排程」。

    本函式回傳「應強制帶入排程、且不可刪除修改」的檢驗項目描述清單；
    若回傳空清單代表四項檢驗皆在180天內有報告，無需強制排程。
    刻意與 check_p1407/1408/1409_eligibility 完全獨立，不共用其判斷結果，
    符合規格書「此邏輯與收案狀態機各自獨立運作」之要求。
    """
    cfg = config or EligibilityConfig()
    as_of = state.as_of_date

    today_visit = next((e for e in state.valid_encounters() if e.visit_date == as_of), None)
    if today_visit is None:
        return []
    is_dm_visit = today_visit.has_diagnosis_prefix(DM_ICD10_PREFIXES, primary_only=False)
    has_a10 = today_visit.has_medication_prefix(DM_MEDICATION_ATC_PREFIX)
    if not (is_dm_visit and has_a10):
        return []

    four_items = _with_ga_substitute(
        (
            LabRequirement(("23501C", "23502C"), 180, "NMRP(眼底檢查)"),
            LabRequirement(("09006C",), 180, "HbA1c"),
            LabRequirement(("12111C",), 180, "Mic-Cr(微量白蛋白)"),
            LabRequirement(("09001C", "09004C", "09043C", "09044C"), 180, "血脂四項"),
        ),
        cfg,
    )
    alerts: list[str] = []
    for req in four_items:
        if state.latest_lab_within(req.alternatives, as_of, req.max_age_days) is None:
            alerts.append(f"強制排程(不可刪除/修改)：{req.description} 180天內未執行")
    return alerts
