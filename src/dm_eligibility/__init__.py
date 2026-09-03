"""
dm_eligibility — 糖尿病 P14 / P7（DKD）品質支付收案資格判斷引擎。

對外主要介面請參見 engine.EligibilityEngine。資料模型定義於 models.py；
P14 規則實作於 rules_p14.py；P7 規則實作於 rules_p7.py。

本套件之規則依據 spec/P14_rules_spec.md 與 spec/P7_rules_spec.md
撰寫，架構與已知限制說明請見 docs/系統設計說明.md。
"""

from .engine import EligibilityEngine
from .models import (
    CKDAssessment,
    ClosureRecord,
    CodeClaim,
    DiagnosisRecord,
    EligibilityConfig,
    EligibilityReport,
    EligibilityResult,
    Encounter,
    LabRequirement,
    LabResult,
    MedicationOrder,
    PatientEnrollmentState,
    PhysicianStatus,
)

__all__ = [
    "EligibilityEngine",
    "EligibilityConfig",
    "EligibilityReport",
    "EligibilityResult",
    "PatientEnrollmentState",
    "Encounter",
    "DiagnosisRecord",
    "MedicationOrder",
    "LabResult",
    "LabRequirement",
    "CodeClaim",
    "ClosureRecord",
    "PhysicianStatus",
    "CKDAssessment",
]
