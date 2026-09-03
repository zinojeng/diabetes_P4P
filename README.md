# 糖尿病 P14 / P7 品質支付收案資格判斷引擎

本專案將童綜合醫院糖尿病品質支付方案 **P14**（糖尿病照護管理，
P1407C/P1408C/P1409C/P1410C/P1411C）與 **P7**（糖尿病合併初期慢性腎臟病
照護管理，P4301C/P7001C/P7002C/P7003C）的健保給付收案規則，整理成兩份
可查證出處的規格書，並實作成一個可測試、可維護的 Python「收案資格判斷
引擎」（eligibility engine），供醫院資訊室日後串接病歷/檢驗系統使用。

## 目的

- 把散落在健保署正式支付標準全文、附表、以及院內 OS99 系統歷年異動
  申請單裡的收案規則，整理成單一、逐字可查證出處的規格書。
- 把規格書轉譯成一套結構清楚、每條規則皆可獨立測試、規則參數與程式
  邏輯分離的 Python 程式，取代日後可能散落在各系統畫面裡的硬編條件
  判斷。
- 明確標記規格書中無法從現有文件確定、需要向健保署或院內個管單位
  進一步確認的「待釐清事項」，並記錄程式目前採用的保守預設處理方式，
  避免工程實作時靜默臆測。

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

執行測試：

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


---

## Part 2：臨床決策支援管線

本專案的 Part 2（臨床決策支援管線，`dm_care_pipeline`）已拆分至獨立 repo：

**https://github.com/zinojeng/CoDoClaw.git**

拆分原因：Part 2 是一個獨立可演進的臨床決策支援系統（資料整合→臨床趨勢→
併發症辨識→風險計算→Care Gap→Guideline Recommendation→醫師決策→病人衛教→
後續追蹤，另含 Layer1-3 擴充架構），與本 repo 聚焦的「P14/P7 收案資格判斷」
是不同層次的關注點，故不與 Part 1 混在同一個 repo 裡維護。CoDoClaw 因需要
呼叫 Part 1 的 `EligibilityEngine`，故附帶一份 `src/dm_eligibility/` 供
獨立運作，但正式維護以本 repo 為準。
