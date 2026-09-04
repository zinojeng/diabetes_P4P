"""
P7（糖尿病合併初期慢性腎臟病，DKD）系列規則：P4301C / P7001C / P7002C / P7003C。

命名提醒（出處：P7 spec 開頭「重要代碼命名提醒」）：本模組所有「P7」皆指
健保署 DKD 方案（P7001C/P7002C/P7003C），與部分院內舊文件將「P7」當作
P1407C 內部簡稱的用法無關。

每個函式最上方註解皆標明對應 spec/P7_rules_spec.md 的節次與條文出處。
"""

from __future__ import annotations

from datetime import timedelta

from . import rules_p14
from .models import (
    EligibilityConfig,
    EligibilityResult,
    LabRequirement,
    MissingReason,
    MissingReasonKind,
    PatientEnrollmentState,
)

# P7001C 首次申報之最短間隔（七週）。
# 出處：P7 spec (b) P7001-1：「至少須間隔任一方案之新收案七週後才能申報
# P7001C」。此數值本身在法規文件中並無新舊對照（不像P1408C有「10週改7週」
# 的異動紀錄可查），來源的確定性略低於P1408C，詳見待釐清事項Q4，但數值
# 本身於現行 p7_core（111-1修正）已明確為7週，故直接採用，不另設旗標。
P7001_FIRST_INTERVAL_DAYS = 49
P7001_SUBSEQUENT_INTERVAL_DAYS = 70

# P7001C 必要檢驗項目。出處：P7 spec (d)「檢驗報告日期規範（依批碼分次
# 要求不同）」：
#   P700101（當年度第1次）：B.S ≤40天、HbA1C或GA ≤40天。
#   P700102（當年度第2次）：LDL ≤60天、Cr ≤60天。
#   P700103（當年度第3次）：UACR(0933) ≤60天。
# ★ 修正（CoDoClaw session 轉交之 Codex review 發現）：先前不分次數，
# 每次申報都要求備齊全部5項檢驗——但規格書明文是「依批碼分次要求不同
# 項目」，5項檢驗分散在當年度3次申報裡，不是每次都要全部備齊。改為依
# 「當年度第幾次申報」查對應子集，見 _p7001_lab_requirements_for_claim_number()。
P7001_LAB_REQUIREMENTS_BY_CLAIM_NUMBER: dict[int, tuple[LabRequirement, ...]] = {
    1: (  # P700101
        LabRequirement(("09005C",), 40, "B.S(09005系列)"),
        LabRequirement(("09006C",), 40, "HbA1C(或GA)"),
    ),
    2: (  # P700102
        LabRequirement(("09044C",), 60, "LDL"),
        LabRequirement(("09015C",), 60, "Cr(血清肌酸酐)"),
    ),
    3: (  # P700103
        LabRequirement(("12111C",), 60, "UACR(微量白蛋白)"),
    ),
}


def _p7001_lab_requirements_for_claim_number(claim_number: int) -> tuple[LabRequirement, ...]:
    """回傳「當年度第 claim_number 次」P7001C申報所需檢驗子集。
    claim_number clamp 到 1~3——年度上限本就是3次(見check_p7001_eligibility
    的年度上限檢查)，超過3的情形理論上已被年度上限擋下，此處保守回傳
    第3次的檢驗需求作為fallback，不代表規格書對第4次以上有明文規定。
    """
    clamped = min(max(claim_number, 1), 3)
    return P7001_LAB_REQUIREMENTS_BY_CLAIM_NUMBER[clamped]

# P7002C 必要檢驗項目。出處：P7 spec (d)：「B.S、HbA1C或GA、SGPT、TG、
# CHO、LDL、HDL、Cr、Mic/Cr及U/R(二擇一)、NMRP」。
# ★ 修正（CoDoClaw session 轉交之 Codex review 發現）：Mic/Cr(12111C)
# 與 U/R(06013C，尿液分析，即P1407/P1409用的同一個代碼) 依規格書是
# 二擇一，先前 alternatives 只放了12111C一項，等於沒有真正允許U/R這個
# 替代選項，「二擇一」的描述文字與實際檢查邏輯不一致。
P7002_LAB_REQUIREMENTS_BASE: tuple[LabRequirement, ...] = (
    LabRequirement(("09005C",), 40, "B.S"),
    LabRequirement(("09006C",), 60, "HbA1C(或GA)"),
    LabRequirement(("09026C",), 40, "SGPT"),
    LabRequirement(("09004C",), 40, "TG"),
    LabRequirement(("09001C",), 40, "CHO"),
    LabRequirement(("09044C",), 40, "LDL"),
    LabRequirement(("09043C",), 40, "HDL"),
    LabRequirement(("09015C",), 60, "Cr"),
    LabRequirement(("12111C", "06013C"), 60, "Mic/Cr(12111C)、U/R(06013C)(二擇一)"),
    LabRequirement(("23501C", "23502C"), 180, "NMRP(眼底檢查)"),
)


def check_p4301_eligibility(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> EligibilityResult:
    """P4301C — 初期慢性腎臟病新收案管理照護費。

    出處：P7 spec (a) CKD-ENROLL-1/2、(d) 開頭表列 trigger_condition：
      - CKD Stage1(eGFR>=90且UPCR>=150或糖尿病患UACR>=30)、
        Stage2(eGFR 60~89.9且UPCR>=150或UACR>=30)、Stage3a(eGFR 45~59.9)之一。
      - 收案前90天內曾在該院所就醫。
      - 新收案當次以慢性腎臟疾病為主診斷（ICD-10碼範圍見
        models.EligibilityConfig.ckd_primary_diagnosis_icd10_prefixes 之
        說明——此為工程實作假設，非規格書逐字條文）。
    """
    cfg = config or EligibilityConfig()
    as_of = state.as_of_date
    reasons: list[str] = []
    missing: list[MissingReason] = []

    latest_assessment = None
    if state.ckd_assessments:
        latest_assessment = max(state.ckd_assessments, key=lambda a: a.assessment_date)

    if latest_assessment is None:
        missing.append(MissingReason(MissingReasonKind.DATA_GAP, "查無CKD分期評估資料(eGFR/UPCR/UACR)"))
    elif latest_assessment.stage() is None:
        # ★ Codex review 發現的分類錯誤修正：stage()==None 同時涵蓋「資料
        # 不足以判定」與「資料已齊全但不符合Stage1/2/3a」兩種情形，不可
        # 一律歸為DATA_GAP（後者屬確定排除，應為BLOCKED，否則背景流程會
        # 誤協助安排本就不需要的追加檢驗）。見 CKDAssessment.data_incomplete()。
        if latest_assessment.data_incomplete():
            missing.append(
                MissingReason(
                    MissingReasonKind.DATA_GAP,
                    "CKD分期評估資料不足，無法判定是否符合Stage1/2/3a"
                    "(缺eGFR，或eGFR在需蛋白尿佐證之範圍但UPCR/UACR皆未測得——"
                    "糖尿病患者UPCR或UACR任一項已測得即足夠，非糖尿病患者需UPCR)",
                )
            )
        else:
            missing.append(
                MissingReason(MissingReasonKind.BLOCKED, "CKD分期評估資料齊全，但不符合Stage1/2/3a條件")
            )
    else:
        reasons.append(f"CKD分期評估符合Stage{latest_assessment.stage()}")

    # ★ 修正（CoDoClaw session 轉交之 Codex review 發現）：規格書明文是
    # 「收案前九十天內曾在該院所就醫」——「收案前」代表這是新收案當次
    # 就診「以外」、更早的就醫紀錄。先前用 encounters_within(as_of-90,
    # as_of) 含 as_of 當天，若病人只有當次新收案這一筆就診、之前完全
    # 沒來過，會把「當次就診本身」誤算成滿足「收案前曾就醫」，等於這條
    # 前提永遠不會擋人。改為窗口不含 as_of 當天。
    window_start = as_of - timedelta(days=90)
    prior_visits = state.encounters_within(window_start, as_of - timedelta(days=1))
    if len(prior_visits) == 0:
        missing.append(MissingReason(MissingReasonKind.PREREQUISITE, "收案前90天內查無該院所就醫紀錄"))
    else:
        reasons.append(f"收案前90天內共{len(prior_visits)}次就醫紀錄")

    today_visit = next((e for e in state.valid_encounters() if e.visit_date == as_of), None)
    if today_visit is None:
        missing.append(MissingReason(MissingReasonKind.BLOCKED, "當次就診(as_of_date)資料不存在，無法收案"))
    elif not today_visit.has_diagnosis_prefix(cfg.ckd_primary_diagnosis_icd10_prefixes, primary_only=True):
        missing.append(MissingReason(MissingReasonKind.BLOCKED, "當次就診之主診斷非慢性腎臟疾病"))
    else:
        reasons.append("當次就診主診斷為慢性腎臟疾病")

    if state.has_claim("P4301C") and state.latest_closure() is None:
        missing.append(MissingReason(MissingReasonKind.TIMING, "病人已有P4301C收案紀錄且尚未結案，不可重複收案"))

    eligible = len(missing) == 0
    return EligibilityResult(
        code="P4301C",
        eligible=eligible,
        points=None,
        reasons=reasons,
        missing_requirements=[r.detail for r in missing],
        missing_reasons=missing,
    )


def check_p7001_eligibility(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> EligibilityResult:
    """P7001C — 糖尿病合併初期慢性腎臟病追蹤管理照護費（400點，團隊80%）。

    出處：P7 spec (a) 規格結論、(b) P7001-1/2/4、(d)。
    前提：
      - 已有 P1407C 收案（DM/P14收案中）——恆常要求，因P7001C定義即為
        「同一次就診完成DM及CKD追蹤」，缺少DM收案即無從談起。
      - 已有 P4301C 收案（CKD收案中）——是否為硬性前提屬規格書推論而非
        逐字明文（見待釐清事項 Q1），由 config.require_p4301_before_p7
        控制，預設 True（採規格書建議結論）。
    間隔：
      - 首次：距「任一方案之新收案日」(P1407C與P4301C皆已收案時取兩者
        中較晚成立收案之日期) >= 49天(七週)。
      - 第2、3次：距上次P7001C申報 >= 70天(十週)。
    年度上限：3次，且與P1408C/P1410C合計、與P4302C合計皆不得超過3次
    （config.enforce_cross_program_caps 控制是否納入跨方案合計）。
    """
    cfg = config or EligibilityConfig()
    as_of = state.as_of_date
    reasons: list[str] = []
    missing: list[MissingReason] = []

    if not state.has_claim("P1407C"):
        missing.append(MissingReason(MissingReasonKind.PREREQUISITE, "尚未申報P1407C(DM收案)，不符合P7001C前提"))

    if cfg.require_p4301_before_p7:
        if not state.has_claim("P4301C"):
            missing.append(
                MissingReason(
                    MissingReasonKind.PREREQUISITE,
                    "尚未申報P4301C(CKD收案)，依規格書建議結論視為P7001C前提(待釐清事項Q1)",
                )
            )

    last_p7001 = state.last_claim_date("P7001C", before=as_of)
    if last_p7001 is None:
        enrollment_dates = [d for d in (state.first_claim_date("P1407C"), state.first_claim_date("P4301C")) if d is not None]
        if not enrollment_dates:
            missing.append(MissingReason(MissingReasonKind.PREREQUISITE, "查無P1407C/P4301C新收案日期，無法計算P7001C首次間隔起算點"))
        else:
            base_date = max(enrollment_dates)  # 取「任一方案之新收案」中較晚成立者
            days_since = (as_of - base_date).days
            if days_since < P7001_FIRST_INTERVAL_DAYS:
                missing.append(
                    MissingReason(
                        MissingReasonKind.TIMING,
                        f"距最晚成立之新收案日({base_date})僅{days_since}天，未滿{P7001_FIRST_INTERVAL_DAYS}天(七週)",
                    )
                )
            else:
                reasons.append(f"距最晚成立之新收案日已{days_since}天，達首次P7001C間隔門檻")
    else:
        days_since = (as_of - last_p7001).days
        if days_since < P7001_SUBSEQUENT_INTERVAL_DAYS:
            missing.append(
                MissingReason(
                    MissingReasonKind.TIMING,
                    f"距上次P7001C申報僅{days_since}天，未滿{P7001_SUBSEQUENT_INTERVAL_DAYS}天(十週)",
                )
            )
        else:
            reasons.append(f"距上次P7001C申報已{days_since}天，達十週間隔門檻")

    if cfg.enforce_cross_program_caps:
        combined_dm = state.count_claims(("P1408C", "P1410C", "P7001C"), year=as_of.year)
        if combined_dm >= 3:
            missing.append(MissingReason(MissingReasonKind.TIMING, f"當年度P1408C+P1410C+P7001C合計已{combined_dm}次，達上限3次"))
        combined_ckd = state.count_claims(("P4302C", "P7001C"), year=as_of.year)
        if combined_ckd >= 3:
            missing.append(MissingReason(MissingReasonKind.TIMING, f"當年度P4302C+P7001C合計已{combined_ckd}次，達上限3次"))
    else:
        if state.count_claims("P7001C", year=as_of.year) >= 3:
            missing.append(MissingReason(MissingReasonKind.TIMING, "當年度P7001C已達上限3次"))

    claim_number_this_year = state.count_claims("P7001C", year=as_of.year) + 1
    lab_requirements = _p7001_lab_requirements_for_claim_number(claim_number_this_year)
    ok, lab_missing = rules_p14.check_lab_requirements(
        state, rules_p14._with_ga_substitute(lab_requirements, cfg), as_of
    )
    missing.extend(lab_missing)
    if ok:
        reasons.append(f"P7001C(當年度第{min(claim_number_this_year, 3)}次)必要檢驗齊全")

    eligible = len(missing) == 0
    return EligibilityResult(
        code="P7001C",
        eligible=eligible,
        points=400 if eligible else None,
        reasons=reasons,
        missing_requirements=[r.detail for r in missing],
        missing_reasons=missing,
    )


def check_p7002_eligibility(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> EligibilityResult:
    """P7002C — 糖尿病合併初期慢性腎臟病年度評估管理照護費（800點，團隊80%）。

    出處：P7 spec (d) P7002-1/2/3。
    前提：P1407C、P1408C、P1410C、P4301C、P4302C、P7001C 合計達3次(含)
    以上（本引擎依終身累計計算，比照P1409C之計算精神——規格書未逐字寫
    「累計」二字，但語境與P1409C相同，皆為「合計達3次以上始得申報」，
    此為工程實作類比推論，非逐字明文，請於實作前與健保署確認是否應改
    為「當年度」計算）。
    間隔：距上次「追蹤管理照護費」申報 >= 70天(十週)。「任一追蹤管理
    照護費」之範圍依 config.p7002_interval_base_includes_new_enrollment
    決定是否納入 P1407C/P4301C 新收案日本身（待釐清事項Q9，預設不納入）。
    年度上限：1次；且與P1409C/P1411C互斥，年度僅可三者擇一申報。
    """
    cfg = config or EligibilityConfig()
    as_of = state.as_of_date
    reasons: list[str] = []
    missing: list[MissingReason] = []

    prereq_codes = ("P1407C", "P1408C", "P1410C", "P4301C", "P4302C", "P7001C")
    lifetime_count = state.count_claims(prereq_codes)
    if lifetime_count < 3:
        missing.append(MissingReason(MissingReasonKind.PREREQUISITE, f"{'+'.join(prereq_codes)} 累計僅{lifetime_count}次，需≥3次"))
    else:
        reasons.append(f"{'+'.join(prereq_codes)} 累計{lifetime_count}次，達前提門檻")

    interval_base_codes = ["P1408C", "P1410C", "P4302C", "P7001C"]
    if cfg.p7002_interval_base_includes_new_enrollment:
        interval_base_codes += ["P1407C", "P4301C"]
    last_tracking = state.last_claim_date(tuple(interval_base_codes), before=as_of)
    if last_tracking is None:
        missing.append(MissingReason(MissingReasonKind.PREREQUISITE, "尚無追蹤管理照護費申報紀錄，無法計算P7002C間隔"))
    else:
        days_since = (as_of - last_tracking).days
        if days_since < 70:
            missing.append(MissingReason(MissingReasonKind.TIMING, f"距上次追蹤管理照護費申報僅{days_since}天，未滿70天(十週)"))
        else:
            reasons.append(f"距上次追蹤管理照護費申報已{days_since}天，達十週間隔門檻")

    if state.count_claims(("P1409C", "P1411C", "P7002C"), year=as_of.year) > 0:
        missing.append(
            MissingReason(MissingReasonKind.TIMING, "本年度已申報過P1409C/P1411C/P7002C三者之一，年度碼每年僅可擇一申報1次")
        )

    ok, lab_missing = rules_p14.check_lab_requirements(state, rules_p14._with_ga_substitute(P7002_LAB_REQUIREMENTS_BASE, cfg), as_of)
    missing.extend(lab_missing)
    if ok:
        reasons.append("P7002C必要檢驗齊全")

    eligible = len(missing) == 0
    return EligibilityResult(
        code="P7002C",
        eligible=eligible,
        points=800 if eligible else None,
        reasons=reasons,
        missing_requirements=[r.detail for r in missing],
        missing_reasons=missing,
    )


# 結案原因中，屬於P7003C明文排除申報之關鍵字。
# 出處：P7 spec (d) P7003C：「結案原因為恢復正常、長期失聯(≥180天)、
# 拒絕治療或死亡者不可申報」。以子字串比對（大小寫不拘），因來源系統之
# 結案原因為自由文字、非固定代碼（見待釐清事項 f.10 關聯說明），實作時
# 建議改為固定代碼枚舉以避免比對失準。
P7003_DISQUALIFYING_CLOSURE_KEYWORDS = ("恢復正常", "失聯", "拒絕", "死亡")


def check_p7003_eligibility(
    state: PatientEnrollmentState, config: EligibilityConfig | None = None
) -> EligibilityResult:
    """P7003C — 糖尿病合併初期慢性腎臟病轉診照護獎勵費（200點，限申報一次）。

    出處：P7 spec (d) P7003C。
    條件：
      - 已有 P4301C 收案。
      - 符合轉診條件：UPCR>=1000 或 eGFR<45。
      - 經轉診至Pre-ESRD計畫院所確認收案（state.pre_esrd_referral_confirmed
        必須明確為 True；None(未知)一律視為不可申報，不可靜默假設已確認，
        對應待釐清事項 f.10）。
      - 每人限申報一次。
      - 結案原因非「恢復正常/長期失聯/拒絕治療/死亡」。
    """
    as_of = state.as_of_date
    reasons: list[str] = []
    missing: list[MissingReason] = []

    if not state.has_claim("P4301C"):
        missing.append(MissingReason(MissingReasonKind.PREREQUISITE, "尚未申報P4301C，不符合P7003C前提"))

    latest_assessment = None
    if state.ckd_assessments:
        latest_assessment = max(state.ckd_assessments, key=lambda a: a.assessment_date)
    # ★ Codex review 發現的分類錯誤修正：原本 referral_indicated 預設
    # False，「查無評估資料」與「評估資料齊全但確定未達轉診條件」都會被
    # 歸為同一個BLOCKED訊息——前者其實是缺檢驗(DATA_GAP)，不是確定排除。
    if latest_assessment is None:
        missing.append(MissingReason(MissingReasonKind.DATA_GAP, "查無CKD評估資料(UPCR/eGFR)，無法判定是否符合轉診條件"))
    else:
        upcr_qualifies = latest_assessment.upcr is not None and latest_assessment.upcr >= 1000
        egfr_qualifies = latest_assessment.egfr is not None and latest_assessment.egfr < 45
        if upcr_qualifies or egfr_qualifies:
            reasons.append("符合轉診條件(UPCR>=1000或eGFR<45)")
        elif latest_assessment.upcr is None or latest_assessment.egfr is None:
            missing.append(
                MissingReason(
                    MissingReasonKind.DATA_GAP,
                    "UPCR或eGFR資料不全，無法確定是否符合轉診條件(UPCR>=1000或eGFR<45)",
                )
            )
        else:
            missing.append(
                MissingReason(MissingReasonKind.BLOCKED, "UPCR與eGFR資料齊全，但未達轉診條件(UPCR>=1000或eGFR<45)")
            )

    if state.pre_esrd_referral_confirmed is None:
        missing.append(MissingReason(MissingReasonKind.BLOCKED, "Pre-ESRD計畫收案確認狀態未知，需人工查證後方可判斷"))
    elif not state.pre_esrd_referral_confirmed:
        missing.append(MissingReason(MissingReasonKind.BLOCKED, "尚未經Pre-ESRD計畫院所確認收案"))
    else:
        reasons.append("已確認轉診至Pre-ESRD計畫院所並收案")

    if state.has_claim("P7003C"):
        missing.append(MissingReason(MissingReasonKind.TIMING, "P7003C每人限申報一次，已申報過"))

    latest_closure = state.latest_closure()
    if latest_closure is not None and any(kw in latest_closure.reason for kw in P7003_DISQUALIFYING_CLOSURE_KEYWORDS):
        missing.append(MissingReason(MissingReasonKind.BLOCKED, f"結案原因「{latest_closure.reason}」屬P7003C排除申報情形"))

    eligible = len(missing) == 0
    return EligibilityResult(
        code="P7003C",
        eligible=eligible,
        points=200 if eligible else None,
        reasons=reasons,
        missing_requirements=[r.detail for r in missing],
        missing_reasons=missing,
    )
