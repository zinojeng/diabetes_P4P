"""
資料模型定義。

本模組定義收案資格判斷引擎所需的所有資料結構：病人的就醫紀錄、檢驗結果、
用藥紀錄、已申報照護碼紀錄、結案紀錄、醫師資格/停權狀態、CKD 分期評估，
以及彙整以上所有資料、供規則引擎讀取的 PatientEnrollmentState。

設計原則：
- 這是一個「查詢用」的唯讀資料模型（frozen dataclass 為主），由呼叫端
  （HIS/病歷系統介接層）負責從實際資料庫組裝出這些物件；本模組不做任何
  資料庫存取。
- PatientEnrollmentState 提供一組查詢輔助方法（count_claims、
  last_claim_date、lab_result_within 等），rules_p14 / rules_p7 一律透過
  這些方法取得資料，避免每個規則各自重寫一套日期/次數運算邏輯。
- 任何「未知」的關鍵欄位一律使用 Optional 並預設 None，而不是猜測一個
  布林值──規則引擎在讀到 None 時必須明確處理為「無法判斷」，不可靜默視為
  True 或 False（詳見 engine.py 的 TODO 註解與待釐清事項處理原則）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Iterable, Optional, Sequence


# ---------------------------------------------------------------------------
# 基礎就醫 / 診斷 / 用藥 / 檢驗紀錄
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosisRecord:
    """單筆診斷紀錄（附著在某次就診之下）。

    icd10_code 允許帶完整碼（如 "E11.9"），is_primary 對應規格書中
    「主診斷」欄位（P14 spec A.1 條件3：「當次收案須以主診斷收案」）。
    """

    icd10_code: str
    is_primary: bool = False

    @property
    def icd10_prefix3(self) -> str:
        return self.icd10_code[:3].upper()


@dataclass(frozen=True)
class MedicationOrder:
    """單筆用藥醫囑（附著在某次就診之下）。

    對應 P14 spec B.1：「ATC前3碼為 A10（糖尿病用藥）；用於 OS99 自動判斷
    是否觸發 P1407 收案邏輯」。
    """

    atc_code: str

    @property
    def atc_prefix3(self) -> str:
        return self.atc_code[:3].upper()


@dataclass(frozen=True)
class Encounter:
    """一次門診/住院就診紀錄。

    clinic_type_code 對應 P14 spec C.2 條件5 提到的「診別代碼」排除名單
    （洗腎相關診別 178/177/176/77/184，版本間有差異，見待釐清事項 Q5）。
    """

    encounter_id: str
    visit_date: date
    physician_id: str
    diagnoses: tuple[DiagnosisRecord, ...] = field(default_factory=tuple)
    medication_orders: tuple[MedicationOrder, ...] = field(default_factory=tuple)
    clinic_type_code: Optional[str] = None
    is_cancelled: bool = False

    def has_diagnosis_prefix(self, prefixes: Iterable[str], primary_only: bool = False) -> bool:
        prefixes_upper = {p.upper() for p in prefixes}
        for d in self.diagnoses:
            if primary_only and not d.is_primary:
                continue
            if d.icd10_prefix3 in prefixes_upper:
                return True
        return False

    def has_medication_prefix(self, prefix: str) -> bool:
        prefix_upper = prefix.upper()
        return any(m.atc_prefix3 == prefix_upper for m in self.medication_orders)


@dataclass(frozen=True)
class LabResult:
    """單筆檢驗報告結果。

    只保留 item_code 與 result_date，因為收案資格判斷只關心「該檢驗項目
    是否在有效窗口內存在報告」，不涉及檢驗數值本身的臨床判讀。
    """

    item_code: str
    result_date: date
    value: Optional[float] = None
    is_abnormal: Optional[bool] = None


# ---------------------------------------------------------------------------
# 已申報照護碼 / 結案 / 醫師資格 / CKD 評估
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeClaim:
    """一筆已成功申報的品質支付照護碼紀錄。

    code 使用健保申報層級代碼（如 "P1407C"、"P1408C"），不使用院內
    P140801/P140802/P140803 之細分碼——院內細分碼與健保代碼的對應是否
    完整，屬於 P14 spec 待釐清事項 Q7，本引擎一律以健保代碼 + claim_date
    排序來推導「第幾次」，不依賴外部系統已經分類好的次序。
    """

    code: str
    claim_date: date
    team_execution: bool = False  # 醫師+另一專業人員執行，點數打8折


@dataclass(frozen=True)
class ClosureRecord:
    """結案紀錄（同院所層級）。

    reason 對應 P14 spec A.6 結案條件：失聯>3個月／拒絕治療／醫師評估可
    自理／逾一年未執行管理。
    """

    closure_date: date
    reason: str


@dataclass
class PhysicianStatus:
    """醫師層級的資格與停權狀態（橫向規則，不屬於個案狀態機本身）。

    對應 P14 spec A.6：
      - 追蹤率<20%經輔導未改善 → 1年停權（P1407C~P1411C皆不可報）
      - HbA1C/LDL 登載不實連續2年 → 2年停權
    以及 P7 spec (a)：P70 系列須「同時具 DM 及初腎方案資格」之雙重資格醫師。
    """

    physician_id: str
    tracking_rate_suspended_until: Optional[date] = None
    falsification_suspended_until: Optional[date] = None
    is_stage2_qualified: bool = False  # 第二階段(P1410C/P1411C)醫師資格
    is_dm_ckd_dual_qualified: bool = False  # P70系列所需之DM+初腎雙重資格

    def suspension_reason(self, as_of: date) -> Optional[str]:
        if self.tracking_rate_suspended_until and as_of <= self.tracking_rate_suspended_until:
            return f"醫師 {self.physician_id} 因追蹤率<20%停權中（至 {self.tracking_rate_suspended_until}）"
        if self.falsification_suspended_until and as_of <= self.falsification_suspended_until:
            return f"醫師 {self.physician_id} 因登載不實停權中（至 {self.falsification_suspended_until}）"
        return None

    def is_suspended(self, as_of: date) -> bool:
        return self.suspension_reason(as_of) is not None


@dataclass(frozen=True)
class CKDAssessment:
    """CKD 分期所需之檢驗評估（供 P4301C 判斷使用）。

    對應 P7 spec CKD-ENROLL-1/2：
      Stage1: eGFR>=90 且 (UPCR>=150 或 糖尿病患UACR>=30)
      Stage2: eGFR 60~89.9 且 (UPCR>=150 或 UACR>=30)
      Stage3a: eGFR 45~59.9
    """

    assessment_date: date
    egfr: Optional[float] = None
    upcr: Optional[float] = None
    uacr: Optional[float] = None
    is_diabetic: bool = False

    def stage(self) -> Optional[str]:
        if self.egfr is None:
            return None
        proteinuria = (self.upcr is not None and self.upcr >= 150) or (
            self.is_diabetic and self.uacr is not None and self.uacr >= 30
        )
        if self.egfr >= 90 and proteinuria:
            return "1"
        if 60 <= self.egfr < 90 and proteinuria:
            return "2"
        if 45 <= self.egfr < 60:
            return "3a"
        return None

    def data_incomplete(self) -> bool:
        """True 表示 `stage()` 回傳 None 是因為「資料不足以判定」，而非
        「資料已齊全、確定不符合Stage1/2/3a」——呼叫端據此決定要分類為
        DATA_GAP（協助安排補做檢驗）還是 BLOCKED（確定不符合，非缺資料）。

        ★ 工程補充判斷，非規格書逐字條文：
          - 缺eGFR：資料不足（DATA_GAP）。
          - eGFR<45：Stage3a門檻(45~59.9)以下，資料已足以判定不符合
            （BLOCKED）——即使蛋白尿資料也缺，eGFR本身已排除Stage1/2/3a。
          - 45<=eGFR<60：Stage3a只看eGFR，不需蛋白尿佐證，資料已足夠
            （不會是data_incomplete，此時必為符合）。
          - eGFR>=60：Stage1/2需蛋白尿(UPCR>=150或糖尿病患UACR>=30)佐證；
            若UPCR缺、且(非糖尿病 或 UACR缺)，代表無法確定蛋白尿是否
            達標，資料不足（DATA_GAP）；若蛋白尿相關數值皆已測得但未
            達標，才是資料齊全、確定不符合（BLOCKED）。
        """
        if self.egfr is None:
            return True
        if self.egfr < 60:
            return False  # <45或45~59.9：Stage3a判定僅需eGFR，資料已足夠
        proteinuria_positive = (self.upcr is not None and self.upcr >= 150) or (
            self.is_diabetic and self.uacr is not None and self.uacr >= 30
        )
        if proteinuria_positive:
            return False  # 已符合，非缺資料（理論上 stage() 此時不會是 None）
        proteinuria_known = self.upcr is not None or (self.is_diabetic and self.uacr is not None)
        return not proteinuria_known


# ---------------------------------------------------------------------------
# 彙整病人狀態
# ---------------------------------------------------------------------------


@dataclass
class PatientEnrollmentState:
    """單一病人（於單一院所）之完整狀態快照，供規則引擎讀取。

    as_of_date：評估基準日，通常等於「當次就診日」。所有天數/年度計算皆以
    此日期為基準。

    vpn_other_institution_enrolled：對應 P14 spec A.1 排除條件「VPN查詢
    已被他院收案中(1年內有追蹤紀錄)」。刻意使用 Optional[bool]、預設
    None（未知）──呼叫端若尚未實際執行 VPN 查詢，不應臆測填入 False，
    engine 對 None 一律採保守處理（視為無法確認、不予收案），避免靜默
    假設「沒查就當作沒有」。

    pre_esrd_referral_confirmed：對應 P7 spec P7003C 前提「經轉診至
    Pre-ESRD計畫院所確認收案後方可申報」。同樣預設 None，需外部系統
    （人工回報或介接）明確回填 True 才能申報 P7003C／P4303C。
    """

    patient_id: str
    as_of_date: date
    encounters: list[Encounter] = field(default_factory=list)
    lab_results: list[LabResult] = field(default_factory=list)
    claims: list[CodeClaim] = field(default_factory=list)
    closure_records: list[ClosureRecord] = field(default_factory=list)
    ckd_assessments: list[CKDAssessment] = field(default_factory=list)
    vpn_other_institution_enrolled: Optional[bool] = None
    pre_esrd_referral_confirmed: Optional[bool] = None
    age_years: Optional[int] = None
    entered_stage2_date: Optional[date] = None  # 首次申報P1410C/P1411C之日期
    entered_p7_date: Optional[date] = None  # 首次申報P7001C/P7002C之日期

    # -- 就診/診斷輔助 -----------------------------------------------------

    def valid_encounters(self) -> list[Encounter]:
        return [e for e in self.encounters if not e.is_cancelled]

    def encounters_within(self, start: date, end: date) -> list[Encounter]:
        return [e for e in self.valid_encounters() if start <= e.visit_date <= end]

    # -- 照護碼申報紀錄輔助 --------------------------------------------------

    def claims_of(self, codes: str | Sequence[str], year: Optional[int] = None) -> list[CodeClaim]:
        code_set = {codes} if isinstance(codes, str) else set(codes)
        result = [c for c in self.claims if c.code in code_set]
        if year is not None:
            result = [c for c in result if c.claim_date.year == year]
        return sorted(result, key=lambda c: c.claim_date)

    def count_claims(self, codes: str | Sequence[str], year: Optional[int] = None) -> int:
        return len(self.claims_of(codes, year=year))

    def has_claim(self, code: str) -> bool:
        return self.count_claims(code) > 0

    def last_claim_date(self, codes: str | Sequence[str], before: Optional[date] = None) -> Optional[date]:
        matched = self.claims_of(codes)
        if before is not None:
            matched = [c for c in matched if c.claim_date < before]
        if not matched:
            return None
        return max(c.claim_date for c in matched)

    def first_claim_date(self, codes: str | Sequence[str]) -> Optional[date]:
        matched = self.claims_of(codes)
        if not matched:
            return None
        return min(c.claim_date for c in matched)

    # -- 結案輔助 ------------------------------------------------------------

    def latest_closure(self) -> Optional[ClosureRecord]:
        if not self.closure_records:
            return None
        return max(self.closure_records, key=lambda c: c.closure_date)

    def closed_within_days(self, as_of: date, days: int) -> bool:
        latest = self.latest_closure()
        if latest is None:
            return False
        return (as_of - latest.closure_date).days < days

    # -- 檢驗輔助 ------------------------------------------------------------

    def latest_lab_within(
        self, item_codes: Iterable[str], as_of: date, max_age_days: int
    ) -> Optional[LabResult]:
        """回傳指定項目群組（視為互為替代選項）中，在 as_of 往前
        max_age_days 天窗口內、日期最新的一筆報告；若無則回傳 None。
        """
        codes_upper = {c.upper() for c in item_codes}
        candidates = [
            lr
            for lr in self.lab_results
            if lr.item_code.upper() in codes_upper
            and 0 <= (as_of - lr.result_date).days <= max_age_days
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda lr: lr.result_date)


# ---------------------------------------------------------------------------
# 規則引擎輸出
# ---------------------------------------------------------------------------


@dataclass
class LabRequirement:
    """一組檢驗前提條件：alternatives 內任一項目在 max_age_days 天內有報告
    即視為滿足（例如 09006C HbA1C 與 09139C GA 互為替代；23501C 與 23502C
    互為替代）。
    """

    alternatives: tuple[str, ...]
    max_age_days: int
    description: str


class MissingReasonKind(str, Enum):
    """`EligibilityResult.missing_reasons` 每一項缺項的分類。

    設計動機：P4P 健保品質支付的精神是儘量不干擾醫療照護行為——一個以
    本引擎驅動的背景自動化流程，理想上應該「符合條件就靜默完成收案，
    只有真的缺項時才通知醫師或協助安排」。但 `missing_requirements` 這份
    純字串清單，長期把「距上次申報僅10天，未滿70天」這種完全正常、
    每天都會發生、什麼都不用做的排程狀態，跟「缺HbA1c檢驗」這種真正
    需要處理的缺項混在一起——若背景流程天真地對任何非空 missing_
    requirements 都發出通知，等於每天對每位病人洗版通知「還沒到期」，
    這正是規格精神想避免的干擾。本列舉讓呼叫端可以篩選掉純排程狀態。
    """

    TIMING = "timing"
    # 時間間隔/年度次數上限尚未到、已收案且尚未結案（重複收案被擋）、
    # 同院所1年內結案冷卻期、醫師停權（有明訂到期日）等——這些狀態的
    # 共通點是「以日期為準、屆期後自動解除，不需任何人介入」，且原因
    # 本身通常已在事件發生當下由個管/品管流程審閱記錄過，每次評估重複
    # 回報不會帶來新資訊，只會製造每日洗版通知。背景自動化流程應保持
    # 靜默，不通知、不中斷照護流程。★ 分類決策依據：「是否純以日期
    # 判定、屆期自動解除」，而非「原因是否重要」——重要但會自動解除的
    # 狀態（如醫師停權）仍歸TIMING，一次性事件通知應由狀態變更當下觸發
    # 的獨立事件機制負責，不由本引擎的每次評估重複承擔（2026-09-05
    # review 後定案，過去版本曾將醫師停權/結案冷卻期歸為BLOCKED，已更正）。
    DATA_GAP = "data_gap"
    # 缺檢驗/評估資料，或現有資料不足以判定是否符合條件（例如某個判定
    # 門檻需要兩項數值佐證、目前只測得一項）——背景自動化流程可考慮據此
    # 協助安排/開立所需檢驗項目（見 diabetes_P4P repo README〈架構與缺口〉
    # 分支C的討論；注意「資料不足」與「資料齊全但確定不符合」需分開判斷，
    # 後者應歸為BLOCKED，見 rules_p7.check_p4301_eligibility 的示範）。
    PREREQUISITE = "prerequisite"
    # 前置照護碼、累計就醫次數或累計申報次數尚未達成——需先完成前一階段
    # 才能繼續，非本次就診當下可單獨解決，但通常值得個管人員留意進度。
    BLOCKED = "blocked"
    # 排除條件命中，且需要人工查證/確認或資料修正才能解除（VPN他院收案
    # 查核、Pre-ESRD轉診確認、年齡、主診斷、用藥、掛號診別、醫師P70雙重
    # 資格未具備等）——不是單純日期問題，時間流逝本身不會讓這些狀態自動
    # 消失，需要醫師、個管人員或行政端實際做判斷/確認/修正資料。


@dataclass(frozen=True)
class MissingReason:
    """單一缺項的分類版本。`kind=TIMING` 時，背景自動化流程應保持完全
    靜默；其餘三類至少值得讓某個角色（個管/醫師）看到，實際要通知誰、
    是否可自動協助開立，routing 細節留給呼叫端決定——本引擎只負責分類，
    不做任何通知/開立動作（鐵律：判斷與動作分離）。"""

    kind: MissingReasonKind
    detail: str


@dataclass
class EligibilityResult:
    """單一照護碼的資格判斷結果。"""

    code: str
    eligible: bool
    points: Optional[int] = None
    reasons: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    # 新增：`missing_requirements` 的分類版本，內容與其一一對應（同序、
    # 同長度，`missing_requirements[i] == missing_reasons[i].detail`）——
    # 舊呼叫端讀 `missing_requirements` 完全不受影響，新呼叫端可用
    # `missing_reasons`/`actionable_missing_reasons()`/
    # `is_pending_timing_only()` 判斷是否該中斷照護流程。
    missing_reasons: list[MissingReason] = field(default_factory=list)

    def actionable_missing_reasons(self) -> list[MissingReason]:
        """排除 `kind=TIMING`（排程正常等待）的缺項——背景自動化流程應
        據此判斷是否需要通知醫師/協助開立，而非對每一筆「還沒到期」都
        中斷照護流程。"""
        return [r for r in self.missing_reasons if r.kind != MissingReasonKind.TIMING]

    def is_pending_timing_only(self) -> bool:
        """`eligible=False`，但缺項全部只是排程/次數尚未到（無其他真正
        需要處理的缺項）。背景自動化流程遇到這個狀態時應保持完全靜默，
        不通知任何人——這是本次資料模型擴充要解決的核心問題。"""
        return (
            not self.eligible
            and bool(self.missing_reasons)
            and all(r.kind == MissingReasonKind.TIMING for r in self.missing_reasons)
        )


@dataclass
class EligibilityReport:
    """引擎一次 evaluate() 呼叫的完整輸出。"""

    patient_id: str
    as_of_date: date
    results: list[EligibilityResult] = field(default_factory=list)
    quality_monitoring_alerts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def eligible_codes(self) -> list[str]:
        return [r.code for r in self.results if r.eligible]

    def get(self, code: str) -> Optional[EligibilityResult]:
        for r in self.results:
            if r.code == code:
                return r
        return None


@dataclass
class EligibilityConfig:
    """所有「規格書待釐清事項」在程式中的可調整保守預設值。

    每個欄位皆對應 P14/P7 規格書 (e)/(f) 節的一條待釐清事項，欄位旁註明
    對應問題編號與目前採用的保守預設理由。不在此處列出的規則一律直接依
    規格書明文實作，不需要旗標。
    """

    # --- P14 Q1: P1407→第1次P1408C 間隔，法規文字「七週」(49天) vs
    #     院內系統文件固定「70天」的落差。預設採「現行法規基準」49天，
    #     因規格書明文將 p14_core/p7_core 視為現行法規基準；如需比照院內
    #     OS99 現況（可能尚未同步）驗證申報件數，可切換為 legacy 70天。
    first_p1408_interval_days: int = 49
    LEGACY_FIRST_P1408_INTERVAL_DAYS: int = 70  # 供比對/測試用之院內舊值

    # --- P14 Q3: P1407C「上次看診」(較早一次) 是否仍免開立OHA用藥。
    #     預設 False（不要求）＝採106.05.01新規定之寬鬆版本；若健保署
    #     確認此但書已失效，改為 True。
    require_medication_on_earlier_visit: bool = False

    # --- P14 B.1 / C.2：院內系統實作之年齡下限，法規正文未見。
    #     預設仍執行此檢查（保守：與院內現況一致），年齡未知時視為不通過。
    enforce_age_18_plus: bool = True
    minimum_age_years: int = 18

    # --- P14 Q5 / P7 f.8：收案排除診別代碼清單，2017版5碼 vs 2023版4碼
    #     （差異在178碼）。預設採用較新的2023版4碼清單。
    excluded_clinic_type_codes: frozenset[str] = frozenset({"177", "176", "77", "184"})

    # --- P14 Q6：HbA1C 是否可由 GA(09139C) 替代。預設允許（院內2023年版
    #     已採用），但無健保署正式函釋依據，需與院方確認。
    allow_ga_as_hba1c_substitute: bool = True

    # --- P14 Q11 / P7 (b)：P1408C/P1410C/P7001C 年度合計上限，以及
    #     P1409C/P1411C/P7002C 之前置累計次數是否應跨代碼合併計算。
    #     預設 True，因 p7_core（111-1修正，較新法規）明文要求合計；
    #     院內OS99是否已同步實作則為另一問題，不影響本引擎的判斷邏輯。
    enforce_cross_program_caps: bool = True

    # --- P14 A.5 / P7 f.6：進入第二階段(P1410C/P1411C)或進入P7體系後，
    #     1年內鎖定不得再申報第一階段 P1408C/P1409C。規格書原文語境是
    #     「進入第二階段」，是否同樣適用「進入P7體系」未逐字明示。
    #     預設 True（保守：兩者皆鎖定，避免超額申報)。
    lock_stage1_after_stage2_or_p7: bool = True

    # --- P7 f.1：「須先有P4301才能收案P7001C/P7002C」為規格書推論而非
    #     逐字明文。預設 True（要求前提），因這是規格書的正式結論建議。
    require_p4301_before_p7: bool = True

    # --- P7 f.9：P7002C「距上次追蹤管理照護費≥十週」之起算基準，是否
    #     包含 P1407C/P4301C 新收案日本身。預設 False（僅採「追蹤管理
    #     照護費」字面意義：P1408C/P1410C/P4302C/P7001C），採較保守之
    #     字面解讀。
    p7002_interval_base_includes_new_enrollment: bool = False

    # --- P14 (d) 末段 / C.1：品質監測(180天強制檢驗排程)是否需要病人已
    #     進入P14收案狀態機。規格書明文指出兩者範圍「無論個案是否已進入
    #     P14收案狀態機」，故此為固定行為、非可調整項，僅在此註記依據，
    #     不提供旗標。

    # --- 【非規格書條文，工程實作補充假設】P4301C(CKD新收案)「當次以
    #     慢性腎臟疾病為主診斷」之判定，P7 spec (d) 僅文字描述「慢性腎臟
    #     疾病」，未列出對應的 ICD-10 碼範圍（不同於P14明確列出E08-E13）。
    #     N18 為國際慣用之慢性腎臟病ICD-10碼，此處採用純屬工程實作合理
    #     推定、非規格書明文，務必於串接HIS前與臨床端確認實際採用之
    #     ICD-10碼範圍是否與此一致。
    ckd_primary_diagnosis_icd10_prefixes: frozenset[str] = frozenset({"N18"})
