"""
對外主要介面：EligibilityEngine。

用法：

    from dm_eligibility.engine import EligibilityEngine
    from dm_eligibility.models import EligibilityConfig

    engine = EligibilityEngine(config=EligibilityConfig())
    report = engine.evaluate(patient_state, physician=physician_status)
    for result in report.results:
        if result.eligible:
            print(result.code, result.points)

設計原則：
- 本引擎僅回答「依目前資料判斷，此病人在 as_of_date 這一天，是否符合各
  照護碼的申報資格」，不負責實際送核申報、不寫回任何資料庫。
- P14 與 P7 規則實作於 rules_p14.py / rules_p7.py，engine.py 只負責：
  (1) 依序呼叫兩邊的規則函式並彙整結果、
  (2) 套用「醫師層級」橫向規則（停權、雙重資格）——這些規則不屬於個案
      狀態機本身，但會影響任一狀態下的可申報性（P14 spec (d) 末段），
  (3) 標記同一次就診彼此互斥的照護碼組合，交由人工判斷擇一（本引擎刻意
      不自動代為選擇，避免在規格未明確排定優先順序時做出臆測），
  (4) 附掛品質監測(180天強制檢驗排程)這條規格書明文獨立於狀態機之外的
      平行規則。

【規格書待釐清事項的處理方式】——見 models.EligibilityConfig 的逐欄註解，
以及 docs/系統設計說明.md「已知限制與待釐清事項」一節。所有無法從規格書
確定的規則，一律：
  1. 在 EligibilityConfig 開一個具名旗標，預設值選擇「保守」的一側
     （寧可少放行、不多放行，或是寧可要求人工確認、不靜默通過）；
  2. 在對應規則函式的註解中標明出處章節與待釐清事項編號；
  3. 絕不在資料缺漏時臆測填值（例如 VPN 查詢結果、Pre-ESRD 轉診確認
     狀態皆使用 Optional[bool]，None 一律視為「不可放行」）。
"""

from __future__ import annotations

from datetime import date

from . import rules_p14, rules_p7
from .models import (
    EligibilityConfig,
    EligibilityReport,
    EligibilityResult,
    MissingReason,
    MissingReasonKind,
    PatientEnrollmentState,
    PhysicianStatus,
)

# P14 系列照護碼——醫師「當季追蹤率<20%」或「登載不實」停權時，明文列出
# 不可申報的範圍。出處：P14 spec (a) A.6：「該醫師1年停權(P1407C~P1411C
# 皆不可報)」。
P14_CODES = ("P1407C", "P1408C", "P1409C", "P1410C", "P1411C")

# P7 系列照護碼。P14 spec A.6 之停權條文逐字僅列出 P1407C~P1411C，未提及
# P7 系列。本引擎採保守假設：同一位醫師停權時，其名下個案之 P7 系列亦一併
# 停權（因P7的DM追蹤本質上仍是同一位醫師執行的P14業務延伸）；此為工程實作
# 之保守推論，非規格書逐字條文，若健保署/院方確認P7不受P14醫師停權連動，
# 可調整此常數或改走獨立設定。
P7_CODES = ("P4301C", "P7001C", "P7002C", "P7003C")

ALL_CODES = P14_CODES + P7_CODES

# 同一次就診互斥之照護碼組合（不得同時申報）。
# 出處：
#   - P14 spec A.2 / P7 spec (b)：P1408C 不得與 P7001C 同時申報。
#   - P7 spec (d) P7001-2：P7001C 當次不得另申報 P1408C、P1410C、P4302C。
#   - P14 spec A.3 / P7 spec (d) P7002-3：P1409C 不得與 P7002C 同時申報；
#     P1411C 不得與 P7002C 同時申報。
#   - P7 spec (d) P7003C：不得與 P4303C 同時申報(P4303C不在本引擎範圍內，
#     故僅記錄需求，暫不加入下列組合)。
MUTUALLY_EXCLUSIVE_SAME_VISIT: tuple[frozenset[str], ...] = (
    frozenset({"P1408C", "P7001C"}),
    frozenset({"P1410C", "P7001C"}),
    frozenset({"P4302C", "P7001C"}),
    frozenset({"P1409C", "P7002C"}),
    frozenset({"P1411C", "P7002C"}),
)


class EligibilityEngine:
    """P14 + P7 收案資格判斷引擎的對外主要介面。"""

    def __init__(self, config: EligibilityConfig | None = None):
        self.config = config or EligibilityConfig()

    def evaluate(
        self,
        state: PatientEnrollmentState,
        physician: PhysicianStatus | None = None,
    ) -> EligibilityReport:
        as_of = state.as_of_date
        cfg = self.config

        raw_results: dict[str, EligibilityResult] = {
            "P1407C": rules_p14.check_p1407_eligibility(state, cfg),
            "P1408C": rules_p14.check_p1408_eligibility(state, cfg),
            "P1409C": rules_p14.check_p1409_eligibility(state, cfg),
            "P1410C": rules_p14.check_p1410_eligibility(state, cfg),
            "P1411C": rules_p14.check_p1411_eligibility(state, cfg),
            "P4301C": rules_p7.check_p4301_eligibility(state, cfg),
            "P7001C": rules_p7.check_p7001_eligibility(state, cfg),
            "P7002C": rules_p7.check_p7002_eligibility(state, cfg),
            "P7003C": rules_p7.check_p7003_eligibility(state, cfg),
        }

        warnings: list[str] = []

        # --- 醫師層級橫向規則：停權 ------------------------------------
        # 出處：P14 spec (a) A.6、(d) 狀態機末段「橫向規則」。
        if physician is not None:
            suspension_reason = physician.suspension_reason(as_of)
            if suspension_reason is not None:
                for code in ALL_CODES:
                    result = raw_results[code]
                    if result.eligible:
                        result.eligible = False
                        result.points = None
                    # ★ 修正：先前只 append 到 missing_requirements（純字串
                    # 清單），未同步 append 到 missing_reasons，會讓兩份清單
                    # 內容不同步、且這筆理由完全沒有分類——「醫師停權」需要
                    # 人工查證/處理，分類為 BLOCKED（不可能隨時間自動解除，
                    # 需要有人介入排除停權狀態）。
                    result.missing_requirements.append(suspension_reason)
                    result.missing_reasons.append(MissingReason(MissingReasonKind.BLOCKED, suspension_reason))

            # --- P7 系列醫師雙重資格 ------------------------------------
            # 出處：P7 spec (a) doc2-p70-doctor-eligibility：「符合可帶入
            # P70資格醫師：需同時具DM及初腎方案資格醫師」。
            if not physician.is_dm_ckd_dual_qualified:
                for code in ("P7001C", "P7002C", "P7003C"):
                    result = raw_results[code]
                    if result.eligible:
                        result.eligible = False
                        result.points = None
                    dual_qualification_reason = (
                        f"醫師 {physician.physician_id} 不具P70系列所需之DM+初腎雙重資格"
                    )
                    result.missing_requirements.append(dual_qualification_reason)
                    result.missing_reasons.append(MissingReason(MissingReasonKind.BLOCKED, dual_qualification_reason))
        else:
            warnings.append(
                "未提供醫師資格/停權資訊(physician=None)：本次評估未套用「追蹤率<20%停權」"
                "「登載不實停權」「P70雙重資格」等醫師層級橫向規則，僅代表個案本身之收案"
                "資格，實際可否送核申報仍需另行確認醫師資格狀態"
            )

        # --- 同一次就診互斥組合標記 -------------------------------------
        # 不自動選擇要留哪一碼，因規格書未訂出優先順序；一律標記為警告，
        # 交由人工（個管師/醫師）擇一申報。
        for pair in MUTUALLY_EXCLUSIVE_SAME_VISIT:
            codes_in_pair = sorted(pair)
            # P4302C（CKD追蹤管理照護費）不在本引擎評估範圍內（本引擎聚焦
            # P14+P7，P43系列未實作，見 docs/系統設計說明.md 已知限制），
            # 若組合中含未評估代碼則略過，避免 KeyError。
            if not all(c in raw_results for c in codes_in_pair):
                continue
            if all(raw_results[c].eligible for c in codes_in_pair):
                warnings.append(
                    f"{' 與 '.join(codes_in_pair)} 於同一次就診互斥、不得同時申報，"
                    f"依目前資料兩者皆符合資格，請人工擇一申報"
                )

        # --- 年度碼三選一之提示（P1409C/P1411C/P7002C）--------------------
        annual_codes = ("P1409C", "P1411C", "P7002C")
        eligible_annual = [c for c in annual_codes if raw_results[c].eligible]
        if len(eligible_annual) > 1:
            warnings.append(
                f"{'/'.join(eligible_annual)} 為年度評估碼，每年度僅可擇一申報，"
                f"依目前資料多筆同時符合資格，請人工擇一申報"
            )

        results = [raw_results[c] for c in ALL_CODES]

        # --- 品質監測（180天強制檢驗排程）——獨立平行規則 ------------------
        quality_alerts = rules_p14.check_quality_monitoring(state, cfg)

        return EligibilityReport(
            patient_id=state.patient_id,
            as_of_date=as_of,
            results=results,
            quality_monitoring_alerts=quality_alerts,
            warnings=warnings,
        )
