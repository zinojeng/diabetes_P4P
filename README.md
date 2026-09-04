# 糖尿病 P14 / P7 品質支付收案資格判斷引擎

**一句話說明**：輸入一位病人的就診/檢驗/收案歷史，這個引擎回答「這個病人今天符不符合申報糖尿病 P14/P7 品質支付照護碼」，並在不符合時明確列出缺什麼（缺檢驗、天數未到、次數已達上限……），逐條附上規格出處。

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
- ❌ 不是：自動申報系統。本引擎只回答「符不符合」與「缺什麼」，實際
  申報動作仍由院內資訊系統/人員執行。
- ❌ 不包含：臨床決策支援（風險評估、併發症辨識、醫師建議等）。那是
  獨立的 **Part 2**，見下方〈與 CoDoClaw（Part 2）的關係〉。

## 與 CoDoClaw（Part 2）的關係

本專案原本規劃分兩個部分，目前拆分為**兩個獨立 repo**：

| Repo | 內容 | 狀態 |
|---|---|---|
| **本 repo**（diabetes_P4P） | Part 1：P14/P7 收案資格判斷引擎（`dm_eligibility`） | 這裡是唯一維護版本 |
| [**CoDoClaw**](https://github.com/zinojeng/CoDoClaw) | Part 2：臨床決策支援管線（`dm_care_pipeline`，資料整合→風險計算→醫師建議→衛教→回診排程） | 這裡是唯一維護版本 |

拆分原因：兩者是不同層次的關注點（「收不收得到健保給付」vs「臨床上該
怎麼照顧這個病人」），且 Part 2 是一個仍在快速演進的獨立系統。CoDoClaw
因為程式邏輯上需要呼叫本 repo 的 `EligibilityEngine`，所以附帶一份
`src/dm_eligibility/` 的複本以求該 repo 能獨立安裝執行，但**正式維護、
規則異動一律以本 repo 為準**——若兩邊程式碼出現落差，以本 repo 為正確
版本。

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

執行測試（11 個測試）：

```bash
pytest tests/ -q
```

基本用法：

```python
import sys
sys.path.insert(0, "src")  # 或將 src/ 加入 PYTHONPATH / 安裝為套件

from datetime import date
from dm_eligibility.engine import EligibilityEngine
from dm_eligibility.models import PatientEnrollmentState, Encounter, DiagnosisRecord

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

engine = EligibilityEngine()
report = engine.evaluate(state)
for result in report.results:
    print(result.code, result.eligible, result.missing_requirements)
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
