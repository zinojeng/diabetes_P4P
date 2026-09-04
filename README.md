# 糖尿病 P14 / P7 品質支付收案資格判斷引擎

**一句話說明**：P4P 健保品質支付的設計精神是儘量不干擾醫療照護行為——這個引擎是「背景自動化流程」該有的判斷核心：靜默讀取一位病人的就診/檢驗/收案歷史，逐條檢查健保 P14/P7 品質支付照護碼的收案條件；已符合的，外層系統可以此為依據自動完成收案、不驚動醫師；「天數未到、次數已達上限」這類純排程等待狀態，保持完全靜默、不通知任何人；只有真正值得處理的缺項時（例如缺檢驗、需人工查證的排除條件）才逐條列出，交由外層系統通知醫師或協助安排所需檢驗——各項規則本身的規格出處記錄在程式碼註解與 `spec/` 規格書中，`MissingReason` 物件本身只帶 `kind`（分類）與 `detail`（描述），不含規格章節引用。

本專案把童綜合醫院糖尿病品質支付方案 **P14**（糖尿病照護管理，
P1407C/P1408C/P1409C/P1410C/P1411C）與 **P7**（糖尿病合併初期慢性腎臟病
照護管理，P4301C/P7001C/P7002C/P7003C）的健保給付收案規則，整理成兩份
可逐條查證出處的規格書，並實作成一個可測試、可維護的 Python「收案資格
判斷引擎」（eligibility engine），供醫院資訊室日後串接病歷/檢驗系統使用。

## 這個 repo 是什麼、不是什麼

- ✅ 是：一套規則引擎，把散落在健保署正式支付標準、附表、院內 OS99 系統
  歷年異動申請單裡的收案規則，轉譯成結構清楚、每條規則皆可獨立測試的
  程式，取代未來可能散落在各系統畫面裡的硬編條件判斷。
- ✅ 是：每條規則、每個切點都標明出處（規格書章節或原始公文），無法
  從現有文件確定的「待釐清事項」會明確標記，並記錄程式目前採用的
  保守預設處理方式——不靜默臆測。
- ✅ 是：一個設計來**被自動化流程呼叫**的判斷核心——`evaluate()` 回傳的
  `eligible: bool` + `missing_reasons: list[MissingReason]`（每項附
  `kind` 分類：`TIMING`/`DATA_GAP`/`PREREQUISITE`/`BLOCKED`）正是驅動
  「靜默自動收案」vs「中斷通知醫師/協助開立檢驗」這兩條分支所需的全部
  資訊——`result.is_pending_timing_only()` 明確告訴呼叫端「這只是還沒到
  期，什麼都不用做」，`result.actionable_missing_reasons()` 篩掉純排程
  等待、只留下真正值得處理的缺項，外層排程系統可直接依此決定要不要打斷
  照護流程，不會被「還沒到期」這種每天都會發生的正常狀態洗版。
- ❌ 不是：會自己動手的自動化系統。**本 repo 目前只有「判斷」這一層，
  沒有「動作」這一層**——沒有背景排程、沒有自動送出申報的呼叫、也沒有
  自動開立檢驗醫令的呼叫。實際的排程觸發、健保申報 API、CPOE 開立檢驗，
  皆須由院內 HIS 整合層另行串接（見下方〈架構與缺口〉）。
- ❌ 不包含：臨床決策支援（風險評估、併發症辨識、醫師建議等）——本 repo
  只回答「收不收得到健保給付」，不涉及「臨床上該怎麼照顧這個病人」。

## 架構與缺口：從「判斷」到「背景自動化流程」

P4P 專案「儘可能不干擾醫療照護行為」的目標，具體展開是一個三段式流程：

```
① 背景排程觸發              ② 本 repo：判斷（已實作）           ③ 外層系統：動作（未實作）
（每次就診/檢驗結果回存時，  →  EligibilityEngine.evaluate()   →  分支 A：eligible=True 且尚未
 或每日批次掃描）               → EligibilityReport                       申報 → 靜默自動送出申報
                                   .results[i].eligible: bool             （不驚動醫師）
                                   .results[i].missing_reasons            分支 B：is_pending_timing_
                                     [{kind, detail}, ...]                  only()=True → 保持靜默，
                                   .results[i].is_pending_timing_only()     什麼都不做（不驚動醫師）
                                   .results[i].actionable_missing_        分支 C：actionable_missing_
                                     reasons()                              reasons() 非空 → 通知醫師，
                                   .quality_monitoring_alerts               kind=DATA_GAP 者可考慮協助
                                                                             開立所需檢驗醫令
```

**① 背景排程觸發**——目前不存在。需要決定：由 HIS 在每次就診/檢驗結果
寫回時同步呼叫（事件驅動），還是每日批次掃描在院病人（排程驅動）；本
repo 不預設任何一種，因為這屬於院內系統架構決策。

**② 本 repo：判斷**——已完整實作且有測試覆蓋（28 個測試）。
`EligibilityEngine.evaluate(state, physician)` 是唯一入口，純函式、無
副作用、不呼叫任何外部系統；`EligibilityReport.eligible_codes()` 給
分支 A 用。分支 B/C 的判斷曾經是本節的一個已知缺口——`missing_
requirements` 舊版是純字串清單，「距上次申報僅10天，未滿70天」這種
完全正常、每天都會發生的排程狀態，跟「缺HbA1c檢驗」這種真正需要處理的
缺項混在一起，天真地「非空即通知」會對每位病人每天洗版。現已改用
`EligibilityResult.missing_reasons: list[MissingReason]`（每項有
`kind: TIMING|DATA_GAP|PREREQUISITE|BLOCKED`），`is_pending_timing_
only()` 對應分支 B（純排程等待，保持靜默），`actionable_missing_
reasons()` 對應分支 C（值得通知/協助處理），其中 `kind=DATA_GAP` 是
「協助開立所需檢驗」的直接候選。舊的 `missing_requirements: list[str]`
欄位保留、與 `missing_reasons` 內容/順序同步不變（此不變式由測試保證，
見 `test_missing_requirements_matches_missing_reasons_details_in_order`），
向下相容既有呼叫端——但個別缺項的**文字描述本身**在後續修正分類錯誤
時可能變動（例如「查無CKD分期評估資料」曾在2026-09-05更正措辭），
若呼叫端對 `missing_requirements` 做字串完全比對（而非只檢查非空/
子字串），需留意這點。

**③ 外層系統：動作**——目前不存在，也**刻意不在本 repo 範圍內**：本
repo 沒有健保申報 API 的知識，也沒有院內 CPOE 開立檢驗醫令的介面資訊，
若在缺乏這些真實整合細節的情況下由本 repo 自行猜測實作，會產生「看起來
能動、實際打不進真正系統」的假自動化。這一層必須由熟悉院內 HIS/CPOE
介接規格的團隊另行開發，串接時直接消費 `EligibilityReport` 即可，不需
重新判斷資格。

若要推進到分支 A/C 實際自動執行，需要的下一步（非本 repo 目前工作範圍，
待與院內資訊室確認介接規格後才能設計；分支 B 不需要外接任何系統，呼叫端
只要檢查 `is_pending_timing_only()` 就能正確保持靜默）：
1. 決定排程觸發方式（事件驅動 or 批次），並決定失敗重試/監控機制。
2. 分支 A（自動申報）：確認健保申報 API 的呼叫方式與失敗處理，並決定
   是否仍要保留人工覆核步驟（例如先落地一筆「待送出」紀錄，由個管師
   批次確認後才真正送出，而非完全無人審閱）。
3. 分支 C（通知/協助開立檢驗）：確認要用什麼管道通知醫師（HIS 內建
   訊息/院內 App/其他），以及「協助開立」（`kind=DATA_GAP` 的缺項）是指
   跳出待簽核醫令草稿供醫師確認，還是別的整合方式——這一步涉及實際下
   醫囑，不建議做成完全無人審閱的全自動開立，應保留醫師簽核步驟。

## 目錄結構

```
p4p/
├── README.md                      本文件
├── requirements.txt                最小相依套件（pytest）
├── spec/
│   ├── P14_rules_spec.md            P14 收案規則規格書（含逐條出處與待釐清事項）
│   └── P7_rules_spec.md             P7 收案規則規格書（含逐條出處與待釐清事項）
├── src/dm_eligibility/
│   ├── __init__.py                  對外匯出介面
│   ├── models.py                    資料模型（Encounter/LabResult/CodeClaim等）+ EligibilityConfig
│   ├── rules_p14.py                 P1407C~P1411C 規則實作 + 品質監測(180天強制排程)
│   ├── rules_p7.py                  P4301C/P7001C/P7002C/P7003C 規則實作
│   └── engine.py                    EligibilityEngine 對外主要介面
├── tests/
│   ├── conftest.py                  pytest 路徑設定
│   └── test_engine.py               測試案例（邊界值/年度上限/缺檢驗/P7合併觸發等）
├── docs/
│   └── 系統設計說明.md               給資訊工程師看的架構說明文件
└── dm_p4p/                          規格書撰寫時所依據的原始/解析後文件（見下）
    ├── *.md                          由院內公文/健保署附表解析後的結構化 Markdown
    └── original files pdf doc/        對應之原始 PDF / Word 檔
```

## 資料來源文件說明

`dm_p4p/` 目錄保存規格書撰寫所依據的原始資料：

- `dm_p4p/*.md`：健保署正式支付標準全文（如「108年健保署更新 糖尿病品質
  P14.md」「111-1修正規定 糖尿病初腎.md」）與童綜合醫院 OS99 系統歷年
  異動申請單（105~112年多個版本），已解析為結構化 Markdown 供人工與
  AI 逐條比對條文出處。
- `dm_p4p/original files pdf doc/`：上述 Markdown 對應的原始 PDF / Word
  檔案，供需要核對原文排版、公文用印、附表格式等細節時查閱。

`spec/P14_rules_spec.md` 與 `spec/P7_rules_spec.md` 是綜合以上原始文件、
逐條標明出處後撰寫的規格書，是 `src/dm_eligibility/` 程式實作的直接依據；
**規則名稱、天數、次數等數字若與程式碼不一致，以規格書 + 程式碼註解的
出處對照為準，不要直接修改程式碼裡的數字而不回頭確認規格書**。

## 快速開始

安裝相依套件：

```bash
pip install -r requirements.txt
```

執行測試（28 個測試）：

```bash
pytest tests/ -q
```

基本用法：

```python
import sys
sys.path.insert(0, "src")  # 或將 src/ 加入 PYTHONPATH / 安裝為套件

from datetime import date
from dm_eligibility.engine import EligibilityEngine
from dm_eligibility.models import PatientEnrollmentState, Encounter, DiagnosisRecord, PhysicianStatus

state = PatientEnrollmentState(
    patient_id="P0001",
    as_of_date=date(2026, 4, 1),
    encounters=[
        Encounter(
            encounter_id="E1",
            visit_date=date(2026, 4, 1),
            physician_id="DOC1",
            diagnoses=(DiagnosisRecord(icd10_code="E11.9", is_primary=True),),
        ),
    ],
    vpn_other_institution_enrolled=False,
    age_years=60,
)
# physician 必須提供才會套用停權/P70雙重資格等橫向規則；physician=None時
# eligible=True 只代表「個案本身」符合資格，report.warnings 會明確提醒
# 尚未檢查醫師層級規則、不可直接視為可送核申報。
physician = PhysicianStatus(physician_id="DOC1")

engine = EligibilityEngine()
report = engine.evaluate(state, physician=physician)
for result in report.results:
    if result.eligible:
        print(result.code, "eligible — 可自動收案", result.points)
    elif result.is_pending_timing_only():
        pass  # 純排程等待（未到期/已達上限），保持靜默，不通知任何人
    else:
        # 真正值得處理的缺項——依 kind 決定要通知醫師還是協助開立檢驗
        for reason in result.actionable_missing_reasons():
            print(result.code, reason.kind.value, reason.detail)
```

更完整的架構說明、規則設計理由、已知限制與待釐清事項清單，請見
`docs/系統設計說明.md`。

## 上線前必讀

本引擎的規則邏輯來自現有文件的逐條比對，但仍有部分「待釐清事項」需
向健保署或院內個管單位正式確認後才能視為定案（完整清單見
`docs/系統設計說明.md`）。正式導入院內系統前，請先由品管/個管端逐條
覆核規則出處與保守預設值是否恰當。

## 授權 / License

本 repo 目前未附加授權條款；如需公開使用/散布，請先與作者確認授權方式。
