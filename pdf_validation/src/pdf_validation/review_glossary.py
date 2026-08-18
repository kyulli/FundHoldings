"""Human-facing labels and glossary for the mapping review UI.

Prefer PDF schedule names and Excel/CSV column names over internal code ids.
"""

from __future__ import annotations

from typing import Any

# Excel / holdings CSV column glossary (business dictionary).
FIELD_GLOSSARY: list[dict[str, str]] = [
    {
        "id": "source_asset",
        "excel": "Source Asset",
        "pdf": "公司 / Portfolio company（Schedule of Investments）",
        "explanation": "Excel 里的标的名称。审批页要把 PDF 公司名映射到这一列，才能做金额对账。",
        "calculation": "无公式；是主键名称字段。",
    },
    {
        "id": "as_at_date",
        "excel": "As At Date",
        "pdf": "报告日 / As of date（封面或表头）",
        "explanation": "Excel 切片日期。必须与 PDF 报告日一致，否则不对金额。",
        "calculation": "日期精确匹配（规范化后 ISO 比较）。",
    },
    {
        "id": "current_cost",
        "excel": "Current Cost",
        "pdf": "Cost / Cost（remaining）",
        "explanation": "仍持有仓位的成本。已全部退出时通常为 0。",
        "calculation": "PDF Cost（remaining） == Excel Current Cost",
    },
    {
        "id": "unrealized_value",
        "excel": "Unrealized Value",
        "pdf": "Fair Value",
        "explanation": "仍持有仓位的公允价值（未实现）。",
        "calculation": "PDF Fair Value（remaining） == Excel Unrealized Value",
    },
    {
        "id": "capital_invested",
        "excel": "Capital Invested",
        "pdf": "通常由 Cost 推导，PDF 不一定单独披露",
        "explanation": "累计投入成本（含已实现部分）。",
        "calculation": "Capital Invested = Current Cost + Realized Cost",
    },
    {
        "id": "realized_proceeds",
        "excel": "Realized Proceeds",
        "pdf": "Realized Gain/(Loss) 表中的 Cash Proceeds",
        "explanation": "已实现处置收到的现金（公司合计）。",
        "calculation": "对各 realized lot 的 Cash Proceeds 求和",
    },
    {
        "id": "total_value",
        "excel": "Total Value",
        "pdf": "通常由 Fair Value + Proceeds 推导",
        "explanation": "未实现市值与已实现回款合计。",
        "calculation": "Total Value = Unrealized Value + Realized Proceeds",
    },
    {
        "id": "deal_status",
        "excel": "Deal Status",
        "pdf": "PDF 通常不直接写；由金额规则推断",
        "explanation": "Current / Partial Exit / Full Exit 等状态。",
        "calculation": "例如：Unrealized Value > 0 且 Realized Proceeds ≤ 0 → Current",
    },
    {
        "id": "fund_allocator_id",
        "excel": "Fund Allocator ID",
        "pdf": "不从 PDF 猜测；由配置显式指定",
        "explanation": "基金身份边界。没有正确 Fund ID 绝不自动对账。",
        "calculation": "配置里的 vendor_fund_id 必须等于 Excel 该列。",
    },
]

STATUS_LABELS: dict[str, dict[str, str]] = {
    "auto_accept": {
        "label": "建议自动通过",
        "explanation": "名称完全一致，或已有确认别名，且没有一对多冲突。可批量接受。",
    },
    "accept": {
        "label": "已接受",
        "explanation": "你已确认：PDF 公司名对应所选 Excel「Source Asset」。",
    },
    "needs_review": {
        "label": "需要你确认",
        "explanation": "相似度不够高、有风险词、或多个 PDF 名称抢同一个 Excel 名称。请人工选择。",
    },
    "unmapped": {
        "label": "暂无合适匹配",
        "explanation": "没有合格 Excel「Source Asset」候选。可标为非持仓、拒绝或保持未映射。",
    },
    "reject": {
        "label": "已拒绝推荐",
        "explanation": "你拒绝了系统推荐；该 PDF 名称不会写入 mapping。",
    },
    "not_vendor_holding": {
        "label": "非 Excel 持仓",
        "explanation": "PDF 出现但不应对照 holdings Excel（例如基金自身、合计行）。",
    },
    "leave_unmapped": {
        "label": "保持未映射",
        "explanation": "本次不对这家公司做金额对账。",
    },
}

MATCH_TYPE_LABELS: dict[str, dict[str, str]] = {
    "exact": {
        "label": "名称完全一致",
        "explanation": "去掉法律后缀/城市括号等规范化后，PDF 公司名与 Excel「Source Asset」唯一一一对应。",
    },
    "alias": {
        "label": "已有别名",
        "explanation": "此前已确认过的 PDF→Excel 名称别名（基金级或全局）。",
    },
    "fuzzy": {
        "label": "名称近似",
        "explanation": "字符串相似度排序得到的候选；高分且领先第二名足够时才建议自动通过。",
    },
    "fuzzy+llm": {
        "label": "近似 + 模型同意",
        "explanation": "模糊匹配与 LLM 排名指向同一 Excel「Source Asset」。仍只是建议，不自动写正式配置。",
    },
    "exact_ambiguous": {
        "label": "完全一致但有歧义",
        "explanation": "规范化后对应多个 Excel「Source Asset」，必须人工选。",
    },
    "none": {
        "label": "无匹配方式",
        "explanation": "系统没有找到可用的 Excel「Source Asset」推荐。",
    },
}

RISK_LABELS: dict[str, dict[str, str]] = {
    "location_paren": {
        "label": "名称含城市/地区括号",
        "explanation": "例如「Homeward, Inc. (Austin, TX)」。系统会去掉括号再比名称，但仍请确认不是另一家同名公司。",
    },
    "risk:lp": {
        "label": "名称含 LP / 合伙企业字样",
        "explanation": "可能是基金、SPV 或 feeder，而不一定是 portfolio company。",
    },
    "spv": {
        "label": "可能是 SPV",
        "explanation": "特殊目的载体，常与经营实体不同名。",
    },
    "feeder": {
        "label": "可能是 Feeder",
        "explanation": "联接基金实体，通常不应直接映射为 portfolio Source Asset。",
    },
    "multi_pdf_same_vendor": {
        "label": "多个 PDF 名称指向同一 Excel 名称",
        "explanation": "一对多冲突，必须人工决定保留哪一个。",
    },
}

EXTRACTION_MODE_LABELS: dict[str, dict[str, str]] = {
    "position_level": {
        "label": "已知模板抽取",
        "explanation": "命中已登记的 schedule 模板族，按既定列规则抽取。",
    },
    "position_level_inferred": {
        "label": "推断版式抽取",
        "explanation": "未见模板，按表头推断 Cost / Fair Value 列后抽取。金额对账前建议确认列含义。",
    },
    "fund_aggregate_only": {
        "label": "仅基金合计",
        "explanation": "PDF 只有合计，没有逐公司持仓明细。",
    },
    "manual_review": {
        "label": "需人工处理抽取",
        "explanation": "未能稳定抽出公司级 Cost / Fair Value。",
    },
    "blocked_narrative": {
        "label": "非持仓 PDF",
        "explanation": "策略信函 / 展望材料，没有公司持仓明细表。",
    },
    "scanned_financial_statements": {
        "label": "扫描件 OCR 抽取",
        "explanation": "PDF 为扫描件，经 OCR 后抽取。",
    },
}

AMOUNT_STATUS_LABELS: dict[str, str] = {
    "match": "一致",
    "mismatch": "不一致",
    "pdf_missing": "PDF 缺数",
    "csv_missing": "Excel 该列为空",
    "both_missing": "两侧都缺",
    "entity_unresolved": "实体未对齐",
}

# Still-held positions: reviewers mainly care about these.
PRIMARY_AMOUNT_FIELDS = {"current_cost", "unrealized_value", "deal_status"}


def label_status(raw: str | None) -> dict[str, str]:
    key = str(raw or "")
    return STATUS_LABELS.get(key) or {"label": key or "未知", "explanation": "内部状态码：" + key}


def label_match_type(raw: str | None) -> dict[str, str]:
    key = str(raw or "none")
    return MATCH_TYPE_LABELS.get(key) or {"label": key, "explanation": "内部匹配类型：" + key}


def label_risk(raw: str | None) -> dict[str, str]:
    key = str(raw or "")
    if key in RISK_LABELS:
        return RISK_LABELS[key]
    if key.startswith("risk:"):
        token = key.split(":", 1)[1]
        return {
            "label": f"名称含风险词「{token}」",
            "explanation": "可能表示法律实体/载体差异，请确认是否同一经营主体。",
        }
    return {"label": key or "未知风险", "explanation": "内部风险标记：" + key}


def label_extraction_mode(raw: str | None) -> dict[str, str]:
    key = str(raw or "")
    return EXTRACTION_MODE_LABELS.get(key) or {
        "label": key or "未知抽取方式",
        "explanation": "内部 extraction_mode：" + key,
    }


def format_score_pct(score: Any) -> str | None:
    if score is None:
        return None
    try:
        return f"{round(float(score) * 100):.0f}%"
    except (TypeError, ValueError):
        return None


def enrich_entity_for_ui(
    entity: dict[str, Any],
    *,
    company: dict[str, Any] | None,
    amounts: list[dict[str, Any]],
    fund_name: str | None = None,
) -> dict[str, Any]:
    """Attach human labels and PDF/Excel display fields for one entity card."""
    status_key = entity.get("user_decision") or entity.get("status")
    status = label_status(status_key)
    match = label_match_type(entity.get("match_type"))
    risks = [label_risk(r) for r in (entity.get("risk_flags") or [])]
    score_pct = format_score_pct(entity.get("score"))
    margin = entity.get("top2_margin")
    margin_pct = format_score_pct(margin) if margin is not None else None

    pdf_cost = (company or {}).get("cost_reported_normalized")
    pdf_fv = (company or {}).get("fair_value_reported_normalized")
    pages = (company or {}).get("pages") or (entity.get("evidence") or {}).get("pages")
    pdf_name = str(entity.get("pdf_company_name") or "")

    # Detect fund-self line (reporting entity mistaken as a portfolio company).
    is_fund_self = False
    fund_hint = ""
    if fund_name and pdf_name:
        fn = fund_name.lower()
        pn = pdf_name.lower()
        if pn in fn or fn.startswith(pn) or pn.replace(",", "") in fn.replace(",", ""):
            is_fund_self = True
            fund_hint = fund_name
    if pdf_name.lower() in {"era ventures lp"} or (pdf_name.lower().endswith(" lp") and status_key == "unmapped" and not entity.get("recommended_vendor_source_asset")):
        # Strong heuristic when cover fund name contains this LP and Excel has no match.
        if fund_name and pdf_name.lower() in fund_name.lower():
            is_fund_self = True
            fund_hint = fund_name

    primary_rows: list[dict[str, Any]] = []
    secondary_rows: list[dict[str, Any]] = []
    for a in amounts:
        logical = str(a.get("logical_field") or "")
        vendor_field = a.get("vendor_field") or logical
        pdf_field_human = {
            "current_cost": "Cost",
            "unrealized_value": "Fair Value",
            "realized_cost": "Realized Cost",
            "capital_invested": "Cost + Realized Cost (derived)",
            "realized_proceeds": "Cash Proceeds",
            "total_value": "Fair Value + Proceeds (derived)",
            "deal_status": "Deal Status (derived)",
        }.get(logical, a.get("pdf_field") or "PDF field")
        status_code = str(a.get("status") or "")
        status_label = AMOUNT_STATUS_LABELS.get(status_code, status_code)
        # Prefer English short calc for UI; Chinese remains in glossary.
        calc = next((f["calculation"] for f in FIELD_GLOSSARY if f["excel"] == vendor_field), a.get("notes") or "")
        if status_code == "csv_missing":
            calc = (
                f"{calc} Excel cell for 「{vendor_field}」 is blank (NaN) on this row — "
                "not a PDF extraction error, and usually not a primary reconcile failure. "
                "For still-held positions, focus on Current Cost / Unrealized Value."
            )
            if logical in {"realized_cost", "realized_proceeds"} and str(a.get("pdf_value")) in {"0", "0.0"}:
                calc += " PDF shows 0 because this name is not on the realized schedule (still held)."
        row = {
            "logical_field": logical,
            "excel_column": vendor_field,
            "pdf_column": pdf_field_human,
            "pdf_value": a.get("pdf_value"),
            "excel_value": a.get("csv_value"),
            "status": status_code,
            "status_label": status_label,
            "difference": a.get("difference"),
            "notes": a.get("notes"),
            "calculation": calc,
            "is_primary": logical in PRIMARY_AMOUNT_FIELDS,
        }
        (primary_rows if row["is_primary"] else secondary_rows).append(row)

    user_decision = entity.get("user_decision")
    needs_action = status_key in {"needs_review", "unmapped"}
    if user_decision in {"accept", "reject", "not_vendor_holding", "leave_unmapped"}:
        needs_action = False
    if status_key == "auto_accept" and not user_decision:
        needs_action = False

    why_parts = []
    if is_fund_self:
        why_parts.append(
            f"This looks like the reporting fund itself ({fund_hint or pdf_name}), "
            "not a portfolio company. Excel Source Asset list has no matching holding — "
            "mark as “Not an Excel holding”."
        )
    if match["label"]:
        why_parts.append(match["explanation"])
    if score_pct:
        why_parts.append(f"Name similarity {score_pct}" + (f" (lead over #2: {margin_pct})" if margin_pct else "") + ".")
    if risks:
        why_parts.append("Risks: " + "; ".join(r["label"] for r in risks) + ".")

    identity_en = "Likely the fund itself (reporting entity), not a portfolio co."
    identity_zh = "更像是本基金本身（报告主体），不是被投公司。"
    if not is_fund_self:
        identity_en = "Appears as a portfolio company name on the PDF schedule."
        identity_zh = "PDF 持仓表里的公司名行。"

    return {
        **entity,
        "ui": {
            "status_label": status["label"],
            "status_label_en": {
                "auto_accept": "Auto-matched",
                "accept": "Accepted",
                "needs_review": "Needs your decision",
                "unmapped": "No Excel match",
                "reject": "Rejected",
                "not_vendor_holding": "Not an Excel holding",
                "leave_unmapped": "Left unmapped",
            }.get(str(status_key), str(status_key or "")),
            "status_label_zh": status["label"],
            "status_explanation": status["explanation"],
            "match_label": match["label"],
            "match_label_en": {
                "exact": "Exact name match",
                "alias": "Known alias",
                "fuzzy": "Fuzzy name match",
                "fuzzy+llm": "Fuzzy + model agree",
                "exact_ambiguous": "Exact but ambiguous",
                "none": "No match method",
            }.get(str(entity.get("match_type") or "none"), str(entity.get("match_type") or "")),
            "match_label_zh": match["label"],
            "match_explanation": match["explanation"],
            "score_pct": score_pct,
            "margin_pct": margin_pct,
            "risks": risks,
            "why": " ".join(why_parts).strip(),
            "identity_en": identity_en,
            "identity_zh": identity_zh,
            "is_fund_self": is_fund_self,
            "suggested_action": "not_vendor_holding" if is_fund_self else None,
            "pdf_name_label": "PDF company name (Schedule of Investments)",
            "excel_name_label": 'Excel "Source Asset"',
            "pdf_cost_label": "PDF Cost",
            "pdf_fair_value_label": "PDF Fair Value",
            "pdf_cost": pdf_cost,
            "pdf_fair_value": pdf_fv,
            "pages": pages,
            "amount_rows": primary_rows,
            "primary_amount_rows": primary_rows,
            "secondary_amount_rows": secondary_rows,
            "needs_action": needs_action,
        },
    }


def glossary_payload() -> dict[str, Any]:
    return {
        "fields": FIELD_GLOSSARY,
        "statuses": STATUS_LABELS,
        "match_types": MATCH_TYPE_LABELS,
        "risks": RISK_LABELS,
        "extraction_modes": EXTRACTION_MODE_LABELS,
    }
